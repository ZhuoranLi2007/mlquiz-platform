"""Auditable teacher-targeted retest rounds and deterministic mastery rules."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import attempt_service
from backend.app.services.score_policy import admissible_score


ACTIVE_STATUSES = {"published", "opened", "scoring_pending"}
FINAL_STATUSES = {"passed", "not_passed", "practice_completed", "baseline_completed"}


def _cycles(db: Session, source_attempt_id: str, student_id: str) -> list[E.TargetedRetestCycle]:
    return list(db.execute(select(E.TargetedRetestCycle).where(
        E.TargetedRetestCycle.source_attempt_id == source_attempt_id,
        E.TargetedRetestCycle.student_id == student_id,
    ).order_by(E.TargetedRetestCycle.round_no)).scalars())


def next_round(db: Session, *, source_attempt_id: str, student_id: str) -> int:
    rows = _cycles(db, source_attempt_id, student_id)
    if rows:
        refresh_cycle(db, rows[-1], commit=True)
        if rows[-1].status in ACTIVE_STATUSES:
            raise ValueError(f"第{rows[-1].round_no}轮复测仍待学生完成或评分，请勿重复发布")
    return (rows[-1].round_no + 1) if rows else 1


def create_cycle(
    db: Session, *, student_id: str, source_attempt_id: str,
    source_assessment_version_id: str, diagnosis_id: str, release_id: str,
    workflow_run_id: str, round_no: int, question_ids: list[str],
    chapter_ids: list[str], kp_ids: list[str], concept_names: list[str],
    untested_kp_ids: list[str] | None = None,
) -> E.TargetedRetestCycle:
    existing = db.execute(select(E.TargetedRetestCycle).where(
        E.TargetedRetestCycle.source_attempt_id == source_attempt_id,
        E.TargetedRetestCycle.student_id == student_id,
        E.TargetedRetestCycle.round_no == round_no,
    )).scalars().first()
    if existing:
        if existing.release_id != release_id:
            raise ValueError("该轮复测已经发布，不能重复创建")
        return existing
    cycle = E.TargetedRetestCycle(
        cycle_id=E.new_id("retestcycle"), student_id=student_id,
        source_attempt_id=source_attempt_id,
        source_assessment_version_id=source_assessment_version_id,
        diagnosis_id=diagnosis_id, release_id=release_id,
        workflow_run_id=workflow_run_id, round_no=round_no,
        status="published",
        threshold=cfg.TARGETED_RETEST_ACCURACY_THRESHOLD,
        question_count=len(question_ids), chapter_ids=chapter_ids,
        kp_ids=kp_ids, concept_names=concept_names,
        generated_question_ids=question_ids,
        untested_kp_ids=list(dict.fromkeys(untested_kp_ids or [])),
        remaining_kp_ids=list(dict.fromkeys([*kp_ids, *(untested_kp_ids or [])])),
        published_at=E.utcnow(), updated_at=E.utcnow(),
    )
    db.add(cycle)
    db.flush()
    return cycle


def _recipient(db: Session, cycle: E.TargetedRetestCycle) -> E.ReleaseRecipient | None:
    if not cycle.release_id:
        return None
    return db.execute(select(E.ReleaseRecipient).where(
        E.ReleaseRecipient.release_id == cycle.release_id,
        E.ReleaseRecipient.student_id == cycle.student_id,
    )).scalars().first()


def _score_rows(db: Session, attempt: E.Attempt) -> tuple[list[E.PaperItem], dict[str, E.AnswerScore]]:
    items = list(db.execute(select(E.PaperItem).where(
        E.PaperItem.paper_id == attempt.paper_id
    ).order_by(E.PaperItem.position)).scalars())
    scores = list(db.execute(select(E.AnswerScore).where(
        E.AnswerScore.attempt_id == attempt.attempt_id)).scalars())
    by_item = {row.paper_item_id: row for row in scores if row.paper_item_id}
    by_question = {(row.question_id, row.question_version): row for row in scores}
    return items, {
        item.item_id: by_item.get(item.item_id) or
        by_question.get((item.question_id, item.question_version))
        for item in items
    }


def refresh_cycle(db: Session, cycle: E.TargetedRetestCycle, *, commit: bool = False) -> E.TargetedRetestCycle:
    recipient = _recipient(db, cycle)
    if recipient and recipient.attempt_id:
        cycle.retest_attempt_id = recipient.attempt_id
    attempt = db.get(E.Attempt, cycle.retest_attempt_id) if cycle.retest_attempt_id else None
    if not attempt or attempt.status != "submitted":
        release = db.get(E.AssessmentRelease, cycle.release_id) if cycle.release_id else None
        from backend.app.services.release_service import _is_expired
        cycle.status = ('cancelled' if cycle.release_id and (not release or release.status != 'published'
                         or _is_expired(release.due_at)) else
                        'opened' if (attempt and attempt.status == 'in_progress') or
                        (recipient and recipient.status == 'opened') else 'published')
        cycle.updated_at = E.utcnow()
        if commit:
            db.commit()
        return cycle

    items, scores = _score_rows(db, attempt)
    version = db.get(E.AssessmentVersion, attempt.assessment_version_id)
    reliable = [scores.get(item.item_id) for item in items]
    all_scored = (
        len(items) == cycle.question_count
        and cycle.question_count > 0
        and all(row is not None and row.correct is not None
                and admissible_score(attempt, row, version)
                for row in reliable)
    )
    cycle.submitted_at = attempt.submitted_at
    cycle.score = attempt.score
    cycle.max_score = attempt.max_score
    if not all_scored:
        cycle.status = "scoring_pending"
        cycle.correct_count = None
        cycle.accuracy = None
        cycle.remaining_kp_ids = list(dict.fromkeys(
            [*(cycle.kp_ids or []), *(cycle.untested_kp_ids or [])]))
        cycle.completed_at = ""
        cycle.updated_at = E.utcnow()
        if commit:
            db.commit()
        return cycle

    correct_count = sum(1 for row in reliable if row and row.correct is True)
    accuracy = correct_count / cycle.question_count
    cycle.correct_count = correct_count
    cycle.accuracy = round(accuracy, 6)
    cycle.status = "passed" if accuracy >= cycle.threshold else "not_passed"
    if cycle.rule_version != 'legacy':
        answers = attempt_service._saved_answers(db, attempt.attempt_id)
        independent = all((answers.get(item.question_id) is not None and
            answers[item.question_id].is_independent_evidence and
            not answers[item.question_id].hints_used and not answers[item.question_id].solution_viewed
            and not answers[item.question_id].seen_before) for item in items)
        if cycle.retest_kind == 'baseline':
            cycle.status = 'baseline_completed'
        elif cycle.retest_kind == 'practice' or not independent or cycle.question_count != 5:
            cycle.status = 'practice_completed'
    cycle.completed_at = cycle.completed_at or E.utcnow()
    covered = {str(item.snapshot.get("main_kp_id") or scores[item.item_id].kp_id or "")
               for item in items}
    cycle.remaining_kp_ids = list(dict.fromkeys([
        *(cycle.untested_kp_ids or []),
        *(kp for kp in (cycle.kp_ids or []) if kp not in covered),
        *(str(row.kp_id or item.snapshot.get("main_kp_id") or "")
          for item, row in zip(items, reliable) if row.correct is False),
    ]))
    cycle.remaining_kp_ids = [kp for kp in cycle.remaining_kp_ids if kp]
    cycle.updated_at = E.utcnow()
    if commit:
        db.commit()
    return cycle


def record_submission(db: Session, *, release_id: str, attempt_id: str) -> dict | None:
    attempt = db.get(E.Attempt, attempt_id)
    if attempt is None:
        raise ValueError("复测答卷不存在")
    cycle = db.execute(select(E.TargetedRetestCycle).where(
        E.TargetedRetestCycle.release_id == release_id,
        E.TargetedRetestCycle.student_id == attempt.student_id,
    )).scalars().first()
    if cycle is None:
        return None
    if cycle.retest_attempt_id and cycle.retest_attempt_id != attempt_id:
        raise ValueError("同一轮复测不能关联多份答卷")
    cycle.retest_attempt_id = attempt_id
    refresh_cycle(db, cycle, commit=True)
    return cycle_detail(db, cycle, include_items=False)


def _question_items(db: Session, cycle: E.TargetedRetestCycle) -> list[dict[str, Any]]:
    if not cycle.retest_attempt_id:
        return []
    attempt = db.get(E.Attempt, cycle.retest_attempt_id)
    if not attempt or attempt.status != "submitted":
        return []
    state = attempt_service.attempt_state(db, attempt.attempt_id)
    answers = state.get("answers") or {}
    scores = state.get("scores") or {}
    result = []
    for position, question in enumerate(state.get("questions") or [], start=1):
        question_id = str(question.get("question_id") or "")
        answer = answers.get(question_id) or {}
        score = scores.get(question_id) or {}
        result.append({
            **question,
            "position": question.get("position") or position,
            "response": answer.get("response"),
            "independent_evidence": bool(answer and answer.get('independent_evidence')
                and not answer.get('hints_used') and not answer.get('solution_viewed')
                and not answer.get('seen_before')),
            "correct": score.get("correct"),
            "awarded_score": score.get("awarded_score"),
            "max_score": score.get("max_score"),
            "scoring_status": score.get("scoring_status"),
            "evidence_status": ("scored" if score.get("correct") is not None
                                else "ungraded"),
            "answer_reference": {
                "available": bool(question.get("answer_payload")),
                "payload": question.get("answer_payload"),
                "explanation": question.get("explanation") or "",
                "rubric": question.get("rubric") or {},
            },
            "chapter": {
                "chapter_id": question.get("chapter_id") or "",
                "title": question.get("chapter_title") or
                         question.get("chapter_id") or "针对性复测",
            },
        })
    return result


def cycle_detail(db: Session, cycle: E.TargetedRetestCycle, *, include_items: bool = True) -> dict:
    refresh_cycle(db, cycle, commit=False)
    release = db.get(E.AssessmentRelease, cycle.release_id) if cycle.release_id else None
    items = _question_items(db, cycle) if include_items else []
    attempt = db.get(E.Attempt, cycle.retest_attempt_id) if cycle.retest_attempt_id else None
    return {
        "cycle_id": cycle.cycle_id,
        "initiator_user_id": cycle.initiator_user_id,
        "retest_kind": cycle.retest_kind,
        "rule_version": cycle.rule_version,
        "evidence_manifest": cycle.evidence_manifest or {},
        "source_counts": cycle.source_counts or {},
        "delayed_from": cycle.delayed_from or {},
        "started_at": attempt.started_at if attempt else '',
        "round": cycle.round_no,
        "status": cycle.status,
        "passed": cycle.status == "passed",
        "result_determinable": cycle.status in FINAL_STATUSES,
        "threshold": cycle.threshold,
        "question_count": cycle.question_count,
        "correct_count": cycle.correct_count,
        "accuracy": cycle.accuracy,
        "score": cycle.score,
        "max_score": cycle.max_score,
        "chapter_ids": cycle.chapter_ids or [],
        "kp_ids": cycle.kp_ids or [],
        "concept_names": cycle.concept_names or [],
        "remaining_kp_ids": cycle.remaining_kp_ids or [],
        "untested_kp_ids": cycle.untested_kp_ids or [],
        "release_id": cycle.release_id,
        "release_title": release.title if release else "针对性复测",
        "retest_attempt_id": cycle.retest_attempt_id,
        "published_at": cycle.published_at,
        "submitted_at": cycle.submitted_at,
        "completed_at": cycle.completed_at,
        "items": items,
        "wrong_items": [item for item in items if item.get("correct") is False],
        "correct_items": [item for item in items if item.get("correct") is True],
    }


def list_cycles(db: Session, *, source_attempt_id: str, student_id: str) -> list[dict]:
    rows = _cycles(db, source_attempt_id, student_id)
    result = [cycle_detail(db, row) for row in rows]
    if rows:
        db.commit()
    return result


def enrich_analysis(analysis: dict, history: list[dict]) -> dict:
    """Add structured post-retest fields without letting a model decide pass/fail."""
    result = dict(analysis or {})
    original = [str(row.get("name") or row.get("concept") or "")
                for row in result.get("concept_hypotheses") or []]
    result["original_weak_concepts"] = [value for value in original if value]
    if not history:
        result.update({
            "retest_performance": [], "remaining_concepts": [],
            "mastery_status": "not_started",
            "retest_summary": None,
            "next_step": "可根据本次真实错题生成5道针对性新题进行复测。",
        })
        return result
    latest = history[-1]
    result["retest_performance"] = [{
        "round": row["round"], "status": row["status"],
        "correct_count": row.get("correct_count"),
        "question_count": row.get("question_count"),
        "accuracy": row.get("accuracy"),
    } for row in history]
    remaining = list(dict.fromkeys([*(latest.get("remaining_kp_ids") or []), *(
        str(item.get("main_kp_id") or item.get("kp_id") or "")
        for item in latest.get("wrong_items") or []
        if item.get("main_kp_id") or item.get("kp_id")
    )]))
    result["remaining_concepts"] = remaining
    result["mastery_status"] = latest["status"]
    summary = ""
    if latest["status"] in {"published", "opened"}:
        summary = f"第{latest['round']}轮复测已发布，等待学生完成。"
        result["next_step"] = "等待学生提交后再依据真实评分更新结论。"
    elif latest["status"] == "scoring_pending":
        summary = f"第{latest['round']}轮复测已提交，等待完整评分后判断是否达标。"
        result["next_step"] = "完成全部题目评分后再执行80%达标判定。"
    elif latest["status"] == "passed":
        pct = round(float(latest.get("accuracy") or 0) * 100)
        summary = (
            f"第{latest['round']}轮复测答对"
            f"{latest.get('correct_count')}/{latest.get('question_count')}题，"
            f"正确率{pct}%，本轮已达标。"
        )
        result["next_step"] = ("仍有未验证或答错的知识点，请继续复习并安排后续复测。"
                               if remaining else "本轮目标已达标，后续可安排间隔复测验证稳定性。")
    elif latest["status"] in {"practice_completed", "baseline_completed"}:
        summary = '练习完成，当前证据不升级为知识点达标。' if latest['status'] == 'practice_completed' else '基线自测完成，可继续用新题验证。'
        result['next_step'] = '可继续发起新题复测。'
    elif latest["status"] == "not_passed":
        pct = round(float(latest.get("accuracy") or 0) * 100)
        wrong_positions = [str(item.get("position")) for item in latest.get("wrong_items") or []
                           if item.get("position")]
        focus = ("继续练习本轮第" + "、".join(wrong_positions[:3]) + "题对应概念。"
                 if wrong_positions else "继续练习本轮错题对应概念。")
        summary = (
            f"第{latest['round']}轮复测答对"
            f"{latest.get('correct_count')}/{latest.get('question_count')}题，"
            f"正确率{pct}%，尚未达到{round(float(latest['threshold']) * 100)}%目标。{focus}"
        )
        result["next_step"] = "复习最新错题后，由教师点击“再次生成复测”进入下一轮。"
    untested = latest.get("untested_kp_ids") or []
    if untested:
        summary += f"另有{len(untested)}个原薄弱知识点本轮未覆盖，不能视为掌握。"
    result["untested_kp_ids"] = untested
    result["retest_summary"] = {
        "status": latest["status"], "round": latest["round"],
        "accuracy": latest.get("accuracy"), "text": summary,
    }
    return result
