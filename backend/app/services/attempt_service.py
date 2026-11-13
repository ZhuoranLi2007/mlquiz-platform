"""Attempt lifecycle: autosave, refresh recovery, idempotent submission,
deterministic scoring, wrong-book creation and fact events.

Idempotency approach: an atomic conditional UPDATE (status in_progress ->
submitted) is the compare-and-swap gate. Only the winner scores and writes
downstream records. Duplicate clicks / network retries see status=submitted
and receive the stored result without creating a second answer, score,
wrong-book entry, or job.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services import bank_service
from backend.app.services import scope_service
from backend.app.services.question_dto import student_view
from backend.app.services.scoring import independent_evidence, score_one
from backend.app.services.subjective_scoring import score_one_with_model


class ConflictError(RuntimeError):
    pass


def snapshot_score_authority(snapshot: dict) -> str:
    """Immutable answer provenance, not paper existence, determines reliability."""
    origin = str(snapshot.get("answer_origin") or "")
    status = str(snapshot.get("answer_status") or "")
    ai = (str(snapshot.get("origin") or "").startswith("ai")
          or origin in {"deepseek_generated", "ai_generated", "ai_candidate"})
    if origin == "teacher_verified_ai" and status == "verified":
        return "teacher_key"
    if ai:
        return "ai_auto_checked" if status == "ai_auto_checked" else "inferred_unverified"
    if status in {"candidate", "pending", "pending_key", "needs_attention"}:
        return "pending_key"
    return "paper_snapshot"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _saved_answers(db: Session, attempt_id: str) -> dict[str, E.Answer]:
    rows = db.execute(
        select(E.Answer).where(E.Answer.attempt_id == attempt_id)
    ).scalars().all()
    return {r.question_id: r for r in rows}


def _seen_ids(db: Session, user_id: str, exclude_attempt_id: str = "") -> set[str]:
    """Questions the user has already met in a PREVIOUS attempt.

    Two independent sources count as "seen":
      1. a question already answered (or at least attempted) before;
      2. an `AnswerReveal`, i.e. the user actually opened the AI answers or
         downloaded the answer PDF for that question. Generation alone never
         writes a reveal, so automatic answer creation does not mark anything
         as seen.
    """
    stmt = (select(E.Answer.question_id)
            .join(E.Attempt, E.Attempt.attempt_id == E.Answer.attempt_id)
            .where(E.Attempt.user_id == user_id))
    if exclude_attempt_id:
        stmt = stmt.where(E.Answer.attempt_id != exclude_attempt_id)
    seen = set(db.execute(stmt).scalars().all())
    revealed = db.execute(select(E.AnswerReveal.question_id).where(
        E.AnswerReveal.user_id == user_id)).scalars().all()
    return seen | set(revealed)


def create_attempt(db: Session, paper_id: str, user_id: str = "local_user",
                   mode: str | None = None, *, student_id: str = "",
                   assessment_version_id: str = "") -> dict:
    paper = db.get(E.Paper, paper_id)
    if not paper:
        raise KeyError(f"试卷不存在: {paper_id}")
    items = bank_service.get_paper_items(db, paper_id)
    if not items:
        raise ValueError("试卷没有题目，无法开始作答")
    a = E.Attempt(
        attempt_id=E.new_id("attempt"), paper_id=paper_id, user_id=user_id,
        mode=mode or paper.mode, status="in_progress",
        server_started_ms=_now_ms(), student_id=student_id,
        assessment_version_id=assessment_version_id,
        attendance_status="attended", grading_status="unknown",
    )
    db.add(a)
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=user_id, event_type="attempt_started",
        target_id=a.attempt_id, payload={"paper_id": paper_id, "mode": a.mode,
                                         "item_count": len(items)},
    ))
    db.commit()
    return {"attempt_id": a.attempt_id, "paper_id": paper_id,
            "mode": a.mode, "status": a.status,
            "server_started_ms": a.server_started_ms,
            "item_count": len(items)}


def save_answer(db: Session, attempt_id: str, question_id: str,
                response: dict, *, hints_used: int = 0,
                solution_viewed: bool = False) -> dict:
    a = db.get(E.Attempt, attempt_id)
    if not a:
        raise KeyError(attempt_id)
    if a.status == "submitted":
        raise ConflictError("已交卷，不能再保存作答")
    items = {it.question_id: it for it in bank_service.get_paper_items(db, a.paper_id)}
    if question_id not in items:
        raise KeyError(f"题目不在本卷中: {question_id}")
    if a.mode == "quiz" and solution_viewed:
        # Quiz mode hard rule: solution reveal is disabled before submission.
        solution_viewed = False
    existing = db.execute(
        select(E.Answer).where(E.Answer.attempt_id == attempt_id,
                               E.Answer.question_id == question_id)
    ).scalars().first()
    # "Seen before" must mean a PREVIOUS exposure: answers given in THIS attempt
    # (including an earlier re-save of the same question) are excluded, while an
    # explicit answer reveal for the question still counts.
    seen = question_id in _seen_ids(db, a.user_id, exclude_attempt_id=attempt_id)
    if existing:
        existing.response = response or {}
        existing.save_count += 1
        existing.updated_at = E.utcnow()
        existing.hints_used = max(existing.hints_used, int(hints_used))
        existing.solution_viewed = bool(existing.solution_viewed or solution_viewed)
        existing.seen_before = bool(existing.seen_before or seen)
        existing.is_independent_evidence = independent_evidence({
            "hints_used": existing.hints_used,
            "solution_viewed": existing.solution_viewed,
            "seen_before": existing.seen_before,
        })
        existing.paper_item_id = existing.paper_item_id or items[question_id].item_id
        existing.response_status = "answered" if existing.response else "unanswered"
        row = existing
    else:
        row = E.Answer(
            answer_id=E.new_id("ans"), attempt_id=attempt_id,
            question_id=question_id,
            question_version=items[question_id].question_version,
            paper_item_id=items[question_id].item_id,
            response=response or {}, hints_used=int(hints_used),
            solution_viewed=bool(solution_viewed), seen_before=seen,
            response_status="answered" if response else "unanswered",
        )
        row.is_independent_evidence = independent_evidence({
            "hints_used": row.hints_used, "solution_viewed": row.solution_viewed,
            "seen_before": row.seen_before,
        })
        db.add(row)
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=a.user_id,
        event_type="answer_saved", target_id=attempt_id,
        payload={"question_id": question_id, "save_count": row.save_count,
                 "hints_used": row.hints_used,
                 "independent_evidence": row.is_independent_evidence},
    ))
    db.commit()
    return {"attempt_id": attempt_id, "question_id": question_id,
            "save_count": row.save_count, "saved_at": row.updated_at,
            "independent_evidence": row.is_independent_evidence}


def grading_summary(db: Session, attempt: E.Attempt) -> dict:
    """Separate nominal paper total, usable scored denominator and pending items."""
    items = bank_service.get_paper_items(db, attempt.paper_id)
    scores = list(db.execute(select(E.AnswerScore).where(E.AnswerScore.attempt_id == attempt.attempt_id)).scalars())
    by_item = {x.paper_item_id: x for x in scores if x.paper_item_id}
    by_q = {(x.question_id, x.question_version): x for x in scores}
    from backend.app.services.score_policy import admissible_score
    version = db.get(E.AssessmentVersion, attempt.assessment_version_id) if attempt.assessment_version_id else None
    scored = []; missing = []; inferred = []
    for item in items:
        score = by_item.get(item.item_id) or by_q.get((item.question_id, item.question_version))
        if admissible_score(attempt, score, version):
            scored.append((score, item))
        else:
            missing.append(item)
            if score is not None and score.authority_status == "inferred_unverified": inferred.append(item)
    maximum = sum(float(s.max_score or item.score or 0) for s, item in scored)
    awarded = sum(float(s.awarded_score or 0) for s, _ in scored)
    status = "partial" if scored and missing else "graded" if scored else "awaiting_grading"
    return {"score": round(awarded, 4) if scored else None,
            "max_score": round(maximum, 4) if scored else None,
            "scored_max_score": round(maximum, 4),
            "nominal_max_score": round(sum(float(x.score or 0) for x in items), 4),
            "ungraded_max_score": round(sum(float(x.score or 0) for x in missing), 4),
            "scored_item_count": len(scored), "ungraded_item_count": len(missing),
            "inferred_item_count": len(inferred), "grading_status": status,
            "grading_note": "只展示已可靠判分部分；未评分/未核验题不按零分计入分母，整卷名义满分另列。"}


def attempt_state(db: Session, attempt_id: str) -> dict:
    a = db.get(E.Attempt, attempt_id)
    if not a:
        raise KeyError(attempt_id)
    submitted = a.status == "submitted"
    questions = bank_service.get_paper_student_view(
        db, a.paper_id, reveal=submitted, mode=a.mode)
    answers = _saved_answers(db, attempt_id)
    answer_map = {
        qid: {"response": r.response, "save_count": r.save_count,
              "saved_at": r.updated_at, "hints_used": r.hints_used,
              "solution_viewed": r.solution_viewed,
              "seen_before": r.seen_before,
              "independent_evidence": r.is_independent_evidence}
        for qid, r in answers.items()
    }
    scores: dict[str, dict] = {}
    if submitted:
        for s in db.execute(
                select(E.AnswerScore).where(E.AnswerScore.attempt_id == attempt_id)
        ).scalars().all():
            scores[s.question_id] = {
                "correct": s.correct, "awarded_score": s.awarded_score,
                "max_score": s.max_score, "scoring_status": s.scoring_status,
                "detail": s.detail,
            }
    return {
        "attempt_id": attempt_id, "paper_id": a.paper_id, "user_id": a.user_id,
        "student_id": a.student_id,
        "assessment_version_id": a.assessment_version_id,
        "mode": a.mode, "status": a.status,
        "attendance_status": a.attendance_status,
        "grading_status": a.grading_status,
        "started_at": a.started_at, "submitted_at": a.submitted_at,
        "server_started_ms": a.server_started_ms,
        "server_elapsed_ms": _now_ms() - a.server_started_ms,
        "score": a.score, "max_score": a.max_score,
        "max_score_reliable": a.max_score_reliable,
        "objective_score": a.objective_score,
        "manual_pending_count": a.manual_pending_count,
        "diagnosis_status": a.diagnosis_status,
        "questions": questions, "answers": answer_map, "scores": scores,
        **(grading_summary(db, a) if submitted else {}),
        "scope_breakdown": (
            scope_service.attempt_scope_breakdown(db, a.paper_id, attempt_id)
            if submitted else {}),
        "note": ("客观题已确定性评分；文本型/答案为未知的题目标记为待评分，"
                 "未计入已得分数" if submitted else "测验模式下不返回答案、解析与评分细则"),
    }


def _misconception_candidates(snap: dict, resp: dict, sc: dict) -> list[str]:
    """Candidate explanations only. Never claim a psychological cause."""
    qt = snap.get("question_type")
    out: list[str] = []
    if qt == "single_choice":
        out.append(f"选择了 {resp.get('key', '（空）')}，与记录的答案不一致；"
                   "可能是概念混淆或记忆偏差（候选，需新题验证）")
    elif qt == "multi_choice":
        if sc["detail"].get("partial_ratio", 1) < 1:
            out.append("存在漏选；可能未完整掌握该知识点的适用条件（候选）")
        else:
            out.append("含错误选项；可能是边界条件判断偏差（候选）")
    elif qt == "numeric":
        out.append("数值结果与记录的答案不符；可能为计算、单位或舍入处理问题（候选）")
    elif qt == "true_false":
        out.append("判断与记录的答案不一致；可能混淆了命题的适用范围（候选）")
    return out


def _submitted_result(db: Session, a: E.Attempt) -> dict:
    state = attempt_state(db, a.attempt_id)
    state["idempotent_replay"] = True
    return state


def submit_attempt(db: Session, attempt_id: str,
                   request_key: str | None = None) -> tuple[dict, bool]:
    """Returns (result, was_newly_submitted). Safe to call repeatedly."""
    a = db.get(E.Attempt, attempt_id)
    if not a:
        raise KeyError(attempt_id)
    if a.status == "submitted":
        return _submitted_result(db, a), False

    key = request_key or f"submit:{attempt_id}"
    try:
        # A client-supplied idempotency key belongs to ONE submit request.
        # Reusing it for a different attempt is a client error: without this
        # guard the DB unique constraint surfaces as an opaque 500 (and the
        # whole submission is rolled back). Reject it explicitly as 409.
        if request_key:
            owner = db.execute(
                select(E.Attempt.attempt_id).where(
                    E.Attempt.submit_request_key == key,
                    E.Attempt.attempt_id != attempt_id)
            ).first()
            if owner:
                raise ConflictError(
                    f"request_key 已被其他答卷使用（{owner[0]}），"
                    "请为本次交卷使用新的请求键")
        # --- atomic CAS gate ---
        cas = db.execute(
            update(E.Attempt)
            .where(E.Attempt.attempt_id == attempt_id,
                   E.Attempt.status == "in_progress")
            .values(status="submitted", submitted_at=E.utcnow(),
                    submit_request_key=key)
        )
        if cas.rowcount != 1:
            db.rollback()
            again = db.get(E.Attempt, attempt_id)
            if again and again.status == "submitted":
                return _submitted_result(db, again), False
            raise ConflictError("并发提交冲突，请重试")
        db.expire(a)

        items = bank_service.get_paper_items(db, a.paper_id)
        answers = _saved_answers(db, attempt_id)
        total = 0.0
        max_total = 0.0
        manual_pending = 0
        wrong_count = 0
        for it in items:
            snap = it.snapshot
            ans_row = answers.get(it.question_id)
            resp = (ans_row.response if ans_row else {}) or {}
            # Objective items stay deterministic; text/derivation items are
            # scored against the FIXED rubric in the paper snapshot.
            from backend.app.services.classroom_content import source_content
            if source_content(snap).get("media_incomplete"):
                sc = {"correct": None, "awarded_score": 0.0, "max_score": float(it.score),
                      "scoring_status": "needs_manual", "detail": {"reason": "原题媒体不完整，不作可靠判分"}}
            else:
                sc = score_one_with_model(db, snap, resp, float(it.score))
            score_row = db.execute(
                select(E.AnswerScore).where(
                    E.AnswerScore.attempt_id == attempt_id,
                    E.AnswerScore.question_id == it.question_id)
            ).scalars().first()
            if score_row is None:
                score_row = E.AnswerScore(
                    score_id=E.new_id("score"), attempt_id=attempt_id,
                    question_id=it.question_id,
                    question_version=it.question_version,
                    paper_item_id=it.item_id,
                    kp_id=snap.get("main_kp_id", ""),
                    family_id=snap.get("question_family_id", ""),
                )
                db.add(score_row)
            else:
                score_row.paper_item_id = score_row.paper_item_id or it.item_id
            score_row.correct = sc["correct"]
            score_row.awarded_score = sc["awarded_score"]
            score_row.max_score = sc["max_score"]
            score_row.scoring_status = sc["scoring_status"]
            score_row.score_provenance = "platform_scored"
            score_row.authority_status = snapshot_score_authority(snap)
            score_row.teacher_verified = score_row.authority_status == "teacher_key"
            score_row.source_ref = "paper_item:" + it.item_id
            score_row.detail = {**(sc["detail"] or {}), "answer_provenance": {
                "origin": snap.get("answer_origin"), "status": snap.get("answer_status"),
                "authority_status": score_row.authority_status,
                "answer_meta": snap.get("answer_meta") or {},
                "source_refs": snap.get("source_refs") or []}}
            score_row.computed_at = E.utcnow()
            if ans_row is not None:
                ans_row.grading_status = sc["scoring_status"]
            from backend.app.services.score_policy import admissible_score
            version = db.get(E.AssessmentVersion, a.assessment_version_id) if a.assessment_version_id else None
            usable = admissible_score(a, score_row, version)
            if usable:
                total += sc["awarded_score"]
                max_total += sc["max_score"]
            if not usable:
                manual_pending += 1
            elif sc["correct"] is False:
                exists = db.execute(
                    select(E.WrongBookEntry).where(
                        E.WrongBookEntry.attempt_id == attempt_id,
                        E.WrongBookEntry.question_id == it.question_id)
                ).scalars().first()
                if exists is None:
                    db.add(E.WrongBookEntry(
                        entry_id=E.new_id("wrong"), user_id=a.user_id,
                        attempt_id=attempt_id, question_id=it.question_id,
                        question_version=it.question_version,
                        kp_id=snap.get("main_kp_id", ""),
                        family_id=snap.get("question_family_id", ""),
                        original_response=resp,
                        lost_points=round(float(it.score) - sc["awarded_score"], 4),
                        misconception_candidates=_misconception_candidates(snap, resp, sc),
                        material_refs=snap.get("source_refs", []),
                    ))
                    wrong_count += 1

        a.score = round(total, 4)
        a.max_score = round(max_total, 4)
        a.objective_score = round(total, 4)
        a.manual_pending_count = manual_pending
        a.attendance_status = "attended"
        a.grading_status = ("pending_key" if a.grading_status == "pending_key" else "awaiting_grading" if manual_pending == len(items) else
                            "partial" if manual_pending else "graded")
        a.source_reported_total = a.score
        a.max_score_reliable = True
        db.add(E.LearningEvent(
            event_id=E.new_id("evt"), user_id=a.user_id,
            event_type="attempt_submitted", target_id=attempt_id,
            payload={"paper_id": a.paper_id, "score": a.score,
                     "max_score": a.max_score,
                     "manual_pending_count": manual_pending,
                     "wrong_count": wrong_count, "submit_request_key": key},
        ))
        db.commit()
        # 客观成绩已保存；诊断交给后台补齐，模型/图失败不会丢失答卷。
        try:
            from backend.app.workers.worker import create_job
            create_job(db, "diagnosis", {"attempt_id": attempt_id},
                       user_id=a.user_id,
                       idempotency_key=f"diag:{attempt_id}")
            if a.mode == "retest":
                task = db.execute(
                    select(E.RetestTask).where(
                        E.RetestTask.paper_id == a.paper_id,
                        E.RetestTask.status == "pending")).scalars().first()
                if task:
                    create_job(db, "verify_learning",
                               {"retest_task_id": task.task_id,
                                "attempt_id": attempt_id},
                               user_id=a.user_id,
                               idempotency_key=f"verify:{attempt_id}")
        except Exception as _job_err:  # 不影响已提交的成绩
            state_note = f"后台任务登记失败（不影响成绩）: {_job_err}"
        else:
            state_note = ""
        state = attempt_state(db, attempt_id)
        state["idempotent_replay"] = False
        state["wrong_count"] = wrong_count
        if state_note:
            state["warning"] = state_note
        return state, True
    except IntegrityError as e:
        # Two different attempts may race past the key-owner check above; the
        # unique constraint is the final arbiter. Translate it to a clear 409
        # instead of leaking a 500 / HTML error page to the client.
        db.rollback()
        raise ConflictError("交卷请求键冲突或并发提交冲突，请重试") from e
    except Exception:
        db.rollback()
        raise


def regrade_question(db: Session, question_id: str,
                     new_answer_payload: dict, reason: str, *,
                     new_rubric: dict | None = None,
                     invalidate_diagnoses: bool = True) -> dict:
    """Re-score historical attempts after an answer version is corrected.

    Supports the '答案有疑问' workflow: regrade every affected answer with the
    corrected answer/rubric, then queue diagnosis recomputation so the three
    diagnostic layers and knowledge states are rebuilt from the new facts.
    Paper item snapshots are NOT modified (published papers keep their own
    fixed version); the regrade is recorded as a separate fact.
    """
    rows = db.execute(
        select(E.AnswerScore).where(E.AnswerScore.question_id == question_id)
    ).scalars().all()
    changed = 0
    affected_attempts: set[str] = set()
    for s in rows:
        attempt = db.get(E.Attempt, s.attempt_id)
        if not attempt:
            continue
        item = db.execute(
            select(E.PaperItem).where(
                E.PaperItem.paper_id == attempt.paper_id,
                E.PaperItem.question_id == question_id)
        ).scalars().first()
        if not item:
            continue
        snap = dict(item.snapshot)
        snap["answer_payload"] = new_answer_payload
        if new_rubric is not None:
            snap["rubric"] = new_rubric
        ans = db.execute(
            select(E.Answer).where(E.Answer.attempt_id == s.attempt_id,
                                   E.Answer.question_id == question_id)
        ).scalars().first()
        sc = score_one_with_model(
            db, snap, (ans.response if ans else {}) or {}, float(item.score))
        before = {"correct": s.correct, "awarded_score": s.awarded_score,
                  "detail": s.detail}
        detail = dict(sc["detail"])
        detail["regrade_previous"] = before
        detail["regrade_reason"] = reason
        s.correct, s.awarded_score = sc["correct"], sc["awarded_score"]
        s.scoring_status = "rescored"
        s.detail = detail
        changed += 1
        affected_attempts.add(s.attempt_id)
        db.add(E.LearningEvent(
            event_id=E.new_id("evt"), user_id="local_user",
            event_type="answer_regraded", target_id=s.attempt_id,
            payload={"question_id": question_id, "before": before,
                     "after": {"correct": s.correct,
                               "awarded_score": s.awarded_score},
                     "reason": reason},
        ))

    jobs_queued = 0
    if invalidate_diagnoses and affected_attempts:
        from backend.app.workers.worker import create_job
        for aid in sorted(affected_attempts):
            r = create_job(
                db, "diagnosis",
                {"attempt_id": aid, "reason": f"answer_regraded:{reason}"},
                user_id="local_user",
                idempotency_key=f"regrade:{question_id}:{E.new_id('rg')}")
            if not r.get("reused"):
                jobs_queued += 1
        db.add(E.LearningEvent(
            event_id=E.new_id("evt"), user_id="local_user",
            event_type="diagnoses_invalidated", target_id=question_id,
            payload={"reason": reason, "attempts": sorted(affected_attempts),
                     "diagnosis_jobs_queued": jobs_queued}))
    db.commit()
    return {"question_id": question_id, "rescored_rows": changed,
            "affected_attempts": len(affected_attempts),
            "diagnosis_jobs_queued": jobs_queued,
            "note": "原试卷快照未改动；相关作答已重评，诊断与知识状态已排队重算"}
