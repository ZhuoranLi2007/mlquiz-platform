"""Real LangGraph workflows for the teacher/student classroom loop."""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.graphs import workflows
from backend.app.models import entities as E
from backend.app.services import answer_pipeline, targeted_retest
from backend.app.services.skill_runtime import run_skill
from backend.app.services.classroom_diagnosis import _valid_score
from backend.app.services.automatic_retest import validate_and_publish
from backend.app.services.evidence_strength import item_evidence, summarize_independence

CHECKPOINTER = MemorySaver()


class State(TypedDict, total=False):
    actor_user_id: str
    student_id: str
    classroom_id: str
    assessment_version_id: str
    attempt_id: str
    source_attempt_id: str
    count: int
    round_no: int
    thread_id: str
    stages: list[dict[str, Any]]
    outputs: dict[str, Any]
    weak_kp_ids: list[str]
    weak_chapter_ids: list[str]
    errors: list[str]
    status: str
    scope_type: str


def _stage(s: State, name: str, detail: Any = None) -> list[dict[str, Any]]:
    return [*(s.get("stages") or []), {"node": name, "detail": detail}]


def _out(s: State, key: str, value: Any) -> dict:
    return {**(s.get("outputs") or {}), key: value}


def _score_kp(db: Session, score: E.AnswerScore) -> str:
    if score.kp_id:
        return score.kp_id
    item = db.get(E.PaperItem, score.paper_item_id) if score.paper_item_id else None
    return str((item.snapshot or {}).get("main_kp_id") or "") if item else ""


def _score_chapter(db: Session, score: E.AnswerScore) -> str:
    if getattr(score, "chapter_id", ""):
        return str(score.chapter_id)
    item = db.get(E.PaperItem, score.paper_item_id) if score.paper_item_id else None
    if item is None:
        return ""
    return str(item.chapter_id or (item.snapshot or {}).get("chapter_id") or "")


def refresh_student_knowledge_states(db: Session, student_id: str) -> dict:
    """Rebuild derived states from admissible item-level evidence only."""
    attempts = db.execute(select(E.Attempt).where(
        E.Attempt.student_id == student_id,
        E.Attempt.status == "submitted",
        E.Attempt.attendance_status == "attended",
        E.Attempt.is_effective.is_(True),
    )).scalars().all()
    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"evidence": 0, "correct": 0, "versions": set(), "latest": "", "refs": []})
    refs: list[dict[str, str]] = []
    for attempt in attempts:
        version = (db.get(E.AssessmentVersion, attempt.assessment_version_id)
                   if attempt.assessment_version_id else None)
        if not version:
            continue
        scores = db.execute(select(E.AnswerScore).where(
            E.AnswerScore.attempt_id == attempt.attempt_id,
            E.AnswerScore.correct.is_not(None),
        )).scalars().all()
        for score in scores:
            if (not _valid_score(version, attempt, score)
                    or not score.paper_item_id or float(score.max_score or 0) <= 0):
                continue
            kp_id = _score_kp(db, score)
            if not kp_id:
                continue
            bucket = buckets[kp_id]
            bucket["evidence"] += 1
            bucket["correct"] += 1 if score.correct is True else 0
            bucket["versions"].add(version.assessment_id)
            bucket["latest"] = max(bucket["latest"], score.computed_at or "")
            item = db.get(E.PaperItem, score.paper_item_id)
            bucket["refs"].append(item_evidence(item, score, attempt, assessment_id=version.assessment_id))
            refs.append({"attempt_id": attempt.attempt_id,
                         "score_id": score.score_id, "kp_id": kp_id})

    # Reset stale derived states as well, including states previously based on inference.
    for old in db.execute(select(E.StudentKnowledgeState).where(
            E.StudentKnowledgeState.student_id == student_id)).scalars().all():
        if old.kp_id not in buckets:
            old.state = "证据不足"
            old.evidence_count = old.correct_count = old.scored_assessment_count = 0
            old.independent_family_count = 0
            old.last_evidence_at = ""
            old.explanation = "当前没有可用于正式掌握率的可靠评分证据。"
            old.updated_at = E.utcnow()
    items = []
    for kp_id, bucket in buckets.items():
        evidence = int(bucket["evidence"])
        correct = int(bucket["correct"])
        assessments = len(bucket["versions"])
        accuracy = correct / evidence if evidence else None
        strength = summarize_independence(bucket["refs"])
        state = strength["state"]
        row = db.get(E.StudentKnowledgeState, (student_id, kp_id))
        if row is None:
            row = E.StudentKnowledgeState(student_id=student_id, kp_id=kp_id)
            db.add(row)
        row.state = state
        row.evidence_count = evidence
        row.independent_family_count = strength["independent_family_count"]
        row.scored_assessment_count = assessments
        row.correct_count = correct
        row.last_evidence_at = bucket["latest"]
        row.explanation = (f"{assessments} 份可评分试卷、{evidence} 条逐题证据；"
                           f"正确 {correct} 条；独立题族 {strength['independent_family_count']} 个。{strength['observation']}。{strength['rule']}")
        row.updated_at = E.utcnow()
        items.append({"kp_id": kp_id, "state": state, "evidence_strength": strength,
                      "evidence_count": evidence, "correct_count": correct,
                      "assessment_count": assessments,
                      "accuracy": round(accuracy, 4) if accuracy is not None else None})
    db.commit()
    items.sort(key=lambda x: (x["state"] != "需加强", x["kp_id"]))
    return {"student_id": student_id, "items": items, "evidence_refs": refs,
            "rule": "只使用已提交、到场、有效且答案键可评分的逐题证据。"}


def compare_attempts(db: Session, before_attempt_id: str,
                     after_attempt_id: str) -> dict:
    def summary(attempt_id: str) -> dict:
        attempt = db.get(E.Attempt, attempt_id)
        if not attempt:
            return {"attempt_id": attempt_id, "exists": False, "kp": {}}
        buckets: dict[str, dict[str, float]] = defaultdict(
            lambda: {"correct": 0.0, "count": 0.0,
                     "awarded": 0.0, "maximum": 0.0})
        chapter_buckets: dict[str, dict[str, float]] = defaultdict(
            lambda: {"correct": 0.0, "count": 0.0,
                     "awarded": 0.0, "maximum": 0.0})
        for score in db.execute(select(E.AnswerScore).where(
                E.AnswerScore.attempt_id == attempt_id,
                E.AnswerScore.correct.is_not(None))).scalars().all():
            version = db.get(E.AssessmentVersion, attempt.assessment_version_id)
            if not version or not _valid_score(version, attempt, score):
                continue
            kp_id = _score_kp(db, score)
            chapter_id = _score_chapter(db, score)
            if kp_id:
                bucket = buckets[kp_id]
                bucket["count"] += 1
                bucket["correct"] += 1 if score.correct is True else 0
                bucket["awarded"] += float(score.awarded_score or 0)
                bucket["maximum"] += float(score.max_score or 0)
            if chapter_id:
                chapter_bucket = chapter_buckets[chapter_id]
                chapter_bucket["count"] += 1
                chapter_bucket["correct"] += 1 if score.correct is True else 0
                chapter_bucket["awarded"] += float(score.awarded_score or 0)
                chapter_bucket["maximum"] += float(score.max_score or 0)
        normalized = {}
        for kp_id, bucket in buckets.items():
            normalized[kp_id] = {**bucket,
                "accuracy": (round(bucket["correct"] / bucket["count"], 4)
                             if bucket["count"] else None),
                "score_rate": (round(bucket["awarded"] / bucket["maximum"], 4)
                               if bucket["maximum"] else None)}
        normalized_chapters = {}
        for chapter_id, bucket in chapter_buckets.items():
            normalized_chapters[chapter_id] = {**bucket,
                "accuracy": (round(bucket["correct"] / bucket["count"], 4)
                             if bucket["count"] else None),
                "score_rate": (round(bucket["awarded"] / bucket["maximum"], 4)
                               if bucket["maximum"] else None)}
        totals = chapter_buckets if chapter_buckets else buckets
        return {"attempt_id": attempt_id, "exists": True,
                "score": sum(b["awarded"] for b in totals.values()),
                "max_score": sum(b["maximum"] for b in totals.values()),
                "kp": normalized, "chapters": normalized_chapters}

    before, after = summary(before_attempt_id), summary(after_attempt_id)
    comparable = []
    for kp_id in sorted(set(before.get("kp", {})) & set(after.get("kp", {}))):
        old, new = before["kp"][kp_id], after["kp"][kp_id]
        comparable.append({"kp_id": kp_id,
            "before_accuracy": old["accuracy"], "after_accuracy": new["accuracy"],
            "delta": (round(new["accuracy"] - old["accuracy"], 4)
                      if old["accuracy"] is not None and new["accuracy"] is not None
                      else None),
            "before_evidence": int(old["count"]),
            "after_evidence": int(new["count"])})
    comparable_chapters = []
    for chapter_id in sorted(set(before.get("chapters", {})) &
                             set(after.get("chapters", {}))):
        old = before["chapters"][chapter_id]
        new = after["chapters"][chapter_id]
        chapter = db.get(E.Chapter, chapter_id)
        comparable_chapters.append({
            "chapter_id": chapter_id,
            "title": chapter.title if chapter else chapter_id,
            "before_score_rate": old["score_rate"],
            "after_score_rate": new["score_rate"],
            "delta": (round(new["score_rate"] - old["score_rate"], 4)
                      if old["score_rate"] is not None and
                      new["score_rate"] is not None else None),
            "before_evidence": int(old["count"]),
            "after_evidence": int(new["count"]),
        })
    return {"before": before, "after": after,
            "comparable_knowledge_points": comparable,
            "comparable_chapters": comparable_chapters,
            "conclusion_limit": ("变化只描述两次可评分作答表现；若题量、难度或题型"
                                 "不同，不能单独证明稳定掌握或教学因果。")}


def build_teacher_intervention_graph(db: Session):
    def diagnose(s: State) -> dict:
        history_mode = s.get("scope_type") == "student_history"
        class_mode = s.get("scope_type") == "class_assessment"
        request = {
            "scope_type": ("class_assessment" if class_mode else
                           "student_history" if history_mode else "student_attempt"),
        }
        if class_mode:
            request["classroom_id"] = s["classroom_id"]
            request["assessment_version_id"] = s["assessment_version_id"]
        elif history_mode:
            request["student_id"] = s["student_id"]
            request["classroom_id"] = s["classroom_id"]
        else:
            request["student_id"] = s["student_id"]
            request["assessment_version_id"] = s["assessment_version_id"]
            request["attempt_id"] = s.get("attempt_id", "")
        result = run_skill(
            db, "diagnose_classroom", request,
            thread_id=s["thread_id"], user_id=s["actor_user_id"])
        payload = result.get("result") or {}
        weak = list(payload.get("weak_kp_ids") or [])
        weak_chapters = list(payload.get("weak_chapter_ids") or [])
        errors = list(s.get("errors") or [])
        if result.get("status") != "succeeded":
            errors.append(
                f"课堂作答诊断未完成：{result.get('status') or 'unknown'}")
        resolved_attempt_id = (payload.get("attempt_id") or
                               (payload.get("retest_plan") or {}).get("anchor_attempt_id") or
                               s.get("attempt_id", ""))
        round_no = int(s.get("round_no") or 1)
        if s.get("scope_type") == "student_attempt" and resolved_attempt_id:
            source_attempt = db.get(E.Attempt, resolved_attempt_id)
            if source_attempt is not None:
                round_no = targeted_retest.next_round(
                    db, source_attempt_id=source_attempt.attempt_id,
                    student_id=s.get("student_id", ""))
        return {"stages": _stage(s, "diagnose", {
                    "diagnosis_id": (result.get("extra") or {}).get(
                        "diagnosis_id"),
                    "status": result.get("status"),
                    "evidence_count": len(result.get("evidence_refs") or []),
                    "weak_kp_ids": weak,
                    "weak_chapter_ids": weak_chapters}),
                "outputs": _out(s, "diagnosis", result),
                "weak_kp_ids": weak, "weak_chapter_ids": weak_chapters,
                "attempt_id": resolved_attempt_id, "round_no": round_no,
                "errors": errors}

    def recommend(s: State) -> dict:
        result = run_skill(db, "recommend_resources",
            {"kp_ids": s.get("weak_kp_ids") or [],
             "attempt_id": s.get("attempt_id", "")},
            thread_id=s["thread_id"], user_id=s["actor_user_id"])
        count = len((result.get("result") or {}).get("recommendations") or [])
        errors = list(s.get("errors") or [])
        if result.get("status") != "succeeded":
            errors.append(
                f"资源推荐未完成：{result.get('status') or 'unknown'}")
        return {"stages": _stage(s, "recommend_resources", {
                    "status": result.get("status"), "count": count}),
                "outputs": _out(s, "recommendations", result),
                "errors": errors}

    def generate(s: State) -> dict:
        weak_chapters = s.get("weak_chapter_ids") or []
        diagnosis = (s.get("outputs") or {}).get("diagnosis") or {}
        diagnosed_payload = diagnosis.get("result") or {}
        diagnosed_items = (diagnosed_payload.get("items") or
                            diagnosed_payload.get("wrong_items") or
                            diagnosed_payload.get("high_frequency_errors") or [])
        weak = sorted({
            str(item.get("kp_id")) for item in diagnosed_items
            if item.get("chapter_id") in weak_chapters and item.get("kp_id")
        })
        prior_stems = [
            str(item.get("stem") or "").strip()
            for item in diagnosed_items
            if item.get("correct") is False and item.get("stem")
        ][:3]
        if not weak_chapters:
            result = {"status": "skipped", "result": {"candidates": []},
                      "message": "没有由可评分证据支持的低分章节。"}
        elif not weak:
            result = {"status": "skipped", "result": {"candidates": []},
                      "message": "低分章节缺少可供生成器检索材料的内部主题映射。"}
        else:
            target = max(1, min(int(s.get("count") or
                                    cfg.TARGETED_RETEST_DEFAULT_QUESTION_COUNT), 5))
            # Keep a meaningful candidate reserve.  Independent solving and
            # similarity checks deliberately reject ambiguous or near-duplicate
            # items; generation must replenish those candidates instead of
            # silently publishing a short retest.  The published paper remains
            # exactly ``target`` questions.
            pool_target = min(12, target + 7)
            by_kp: dict[str, list[dict]] = {kp_id: [] for kp_id in weak[:4]}
            attempts: list[dict] = []
            per_kp_target = max(2, (pool_target + len(by_kp) - 1) // len(by_kp))
            generation_round = 0
            for kp_id, kp_rows in by_kp.items():
                for _ in range(3):
                    missing = per_kp_target - len(kp_rows)
                    if missing <= 0:
                        break
                    generation_round += 1
                    one = workflows.run_generation(db, {
                        "count": min(5, missing),
                        "kp_ids": [kp_id],
                        "question_types": ["single_choice", "multi_choice", "true_false"],
                        "mode": "teacher_retest_candidate",
                        "instruction": (
                            f"生成用于第{s.get('round_no', 1)}轮复测的新题，共需{missing}道；"
                            f"本批只考查知识点 {kp_id}，必须给出可核验答案、解析与来源引用。"
                            "使用不同于原题的真实情境、数据或判断角度，不得只替换数字、主语或措辞。"
                            "以下原错题只用于避重，不得复述：" + " | ".join(prior_stems)
                        )}, user_id=s["actor_user_id"])
                    rows = (one.get("result") or {}).get("candidates") or []
                    known = {str(row.get("question_id")) for rows_ in by_kp.values() for row in rows_}
                    kp_rows.extend(row for row in rows
                                   if row.get("question_id") and
                                   str(row.get("question_id")) not in known)
                    attempts.append({"round": generation_round, "kp_id": kp_id,
                                     "status": one.get("status"),
                                     "engine": one.get("engine"),
                                     "candidate_count": len(rows)})
                    if one.get("status") != "succeeded" and not rows:
                        break
            # Round-robin ordering makes the independent validator encounter
            # every weak concept before filling the five-question paper.
            combined = []
            for index in range(per_kp_target):
                for kp_rows in by_kp.values():
                    if index < len(kp_rows):
                        combined.append(kp_rows[index])
            engines = list(dict.fromkeys(
                str(row.get("engine") or "") for row in attempts
                if row.get("engine")))
            result = {
                "status": "succeeded" if len(combined) >= target else "insufficient_candidates",
                "engine": "+".join(engines) if engines else "not_run",
                "result": {
                    "candidates": combined[:pool_target],
                    "generation_attempts": attempts,
                    "message": (f"已生成{len(combined[:pool_target])}道候选题，发布时自动筛选{target}道。" if len(combined) >= target
                                else f"需要{target}道题，多次生成后仅有{len(combined)}道合格题。"),
                },
            }
        candidates = (result.get("result") or {}).get("candidates") or []
        return {"stages": _stage(s, "generate_retest_candidates", {
                    "status": result.get("status"),
                    "candidate_count": len(candidates)}),
                "outputs": _out(s, "generation", result)}

    def prepare(s: State) -> dict:
        generation = (s.get("outputs") or {}).get("generation") or {}
        candidates = (generation.get("result") or {}).get("candidates") or []
        errors = list(s.get("errors") or [])
        result = {"status": "no_candidate", "candidate_question_ids": []}
        if generation.get("status") == "succeeded" and candidates:
            try:
                diagnosis = (s.get("outputs") or {}).get("diagnosis") or {}
                payload = diagnosis.get("result") or {}
                weak_chapters = list(s.get("weak_chapter_ids") or [])
                chapter_by_kp = {}
                for item in (payload.get("items") or payload.get("wrong_items") or
                             payload.get("high_frequency_errors") or []):
                    if item.get("chapter_id") in weak_chapters and item.get("kp_id"):
                        chapter_by_kp.setdefault(str(item["kp_id"]), set()).add(
                            str(item["chapter_id"]))
                # Generated questions are new candidate data.  Attach a chapter
                # only when the diagnosis supplies one unambiguous mapping for
                # the candidate's internal retrieval topic.
                for candidate in candidates:
                    q = answer_pipeline.latest_question(
                        db, str(candidate.get("question_id") or ""))
                    choices = chapter_by_kp.get(str(q.main_kp_id), set()) if q else set()
                    if q is not None and not q.chapter_id and len(choices) == 1:
                        q.chapter_id = next(iter(choices))
                recipient_ids = None
                if s.get("scope_type") == "class_assessment":
                    member_rows = db.execute(
                        select(E.Student)
                        .join(E.ClassroomStudent,
                              E.ClassroomStudent.student_id == E.Student.student_id)
                        .where(E.ClassroomStudent.classroom_id == payload["classroom"]["classroom_id"],
                               E.ClassroomStudent.status == "active",
                               E.Student.active.is_(True))
                    ).scalars().all()
                    recipient_ids = []
                    for student in member_rows:
                        account = (db.get(E.User, student.linked_user_id)
                                   if student.linked_user_id else None)
                        if account and account.active and account.role == "student":
                            recipient_ids.append(student.student_id)
                    if not recipient_ids:
                        raise ValueError("班级没有可接收在线试卷的学生账号")
                result = validate_and_publish(db,
                    candidate_ids=[c["question_id"] for c in candidates if c.get("question_id")],
                    kp_ids=s.get("weak_kp_ids") or [],
                    chapter_ids=weak_chapters, actor_user_id=s["actor_user_id"],
                    student_id=s.get("student_id", ""), student_ids=recipient_ids,
                    classroom_id=(payload.get("classroom_id") or
                                  (payload.get("classroom") or {}).get("classroom_id", "")),
                    source_attempt_id=s.get("attempt_id", ""),
                    diagnosis_id=(diagnosis.get("extra") or {}).get("diagnosis_id", ""),
                    thread_id=s["thread_id"], round_no=int(s.get("round_no") or 1),
                    required_count=int(s.get("count") or
                                       cfg.TARGETED_RETEST_DEFAULT_QUESTION_COUNT))
            except Exception as exc:
                db.rollback()
                reason = str(exc)
                result = {"status": "validation_or_publication_failed",
                          "candidate_question_ids": [], "reason": reason}
                errors.append("自动复测未发布：" + reason)
        elif generation.get("status") != "skipped":
            errors.append("复测生成未产生可用新题：" + str(generation.get("status")))
        return {"stages": _stage(s, "validate_and_publish_retest", result),
                "outputs": _out(s, "retest", result), "errors": errors,
                "status": "partial" if errors else "succeeded"}

    graph = StateGraph(State)
    for name, fn in (("diagnose", diagnose), ("recommend_resources", recommend),
                     ("generate_retest_candidates", generate),
                     ("validate_and_publish_retest", prepare)):
        graph.add_node(name, fn)
    graph.add_edge(START, "diagnose")
    graph.add_edge("diagnose", "recommend_resources")
    graph.add_edge("recommend_resources", "generate_retest_candidates")
    graph.add_edge("generate_retest_candidates", "validate_and_publish_retest")
    graph.add_edge("validate_and_publish_retest", END)
    return graph.compile(checkpointer=CHECKPOINTER)


def run_teacher_intervention(db: Session, *, actor_user_id: str,
                             student_id: str = "",
                             assessment_version_id: str = "",
                             classroom_id: str = "",
                             scope_type: str = "student_attempt",
                             attempt_id: str = "",
                             count: int = cfg.TARGETED_RETEST_DEFAULT_QUESTION_COUNT) -> dict:
    if scope_type == "student_history" and (not classroom_id or not student_id):
        raise ValueError("多卷复测需要 classroom_id")
    if scope_type == "class_assessment" and (not classroom_id or not assessment_version_id):
        raise ValueError("班级复测需要 classroom_id 与 assessment_version_id")
    if scope_type == "student_attempt" and (not assessment_version_id or not student_id):
        raise ValueError("单卷复测需要 assessment_version_id")
    # The mastery threshold is defined on one fixed five-item round.  Keep the
    # request field for backward compatibility, but do not let an old client
    # silently publish a one- or two-item "targeted retest" with the same 80%
    # semantics.
    count = cfg.TARGETED_RETEST_DEFAULT_QUESTION_COUNT
    round_no = 1
    thread_id = f"teacher-intervention:{uuid.uuid4().hex[:12]}"
    initial: State = {"actor_user_id": actor_user_id, "student_id": student_id,
        "assessment_version_id": assessment_version_id,
        "classroom_id": classroom_id, "scope_type": scope_type, "count": count,
        "attempt_id": attempt_id, "round_no": round_no,
        "thread_id": thread_id, "stages": [], "outputs": {},
        "weak_kp_ids": [], "weak_chapter_ids": [],
        "errors": [], "status": "queued"}
    final = build_teacher_intervention_graph(db).invoke(
        initial, config={"configurable": {"thread_id": thread_id}})
    return {"thread_id": thread_id, "graph": "teacher_intervention_graph",
            "status": final.get("status"), "stages": final.get("stages", []),
            "outputs": final.get("outputs", {}), "errors": final.get("errors", []),
            "checkpoint_available": CHECKPOINTER.get_tuple(
                {"configurable": {"thread_id": thread_id}}) is not None}


def build_post_submission_graph(db: Session):
    def diagnose_attempt(s: State) -> dict:
        result = run_skill(db, "diagnose_classroom", {
            "scope_type": "student_attempt",
            "student_id": s["student_id"],
            "assessment_version_id": s["assessment_version_id"],
        }, thread_id=s["thread_id"], user_id=s["actor_user_id"])
        payload = result.get("result") or {}
        errors = list(s.get("errors") or [])
        if result.get("status") != "succeeded":
            errors.append(
                f"交卷诊断未完成：{result.get('status') or 'unknown'}")
        return {"stages": _stage(s, "diagnose_submitted_attempt", {
                    "diagnosis_id": (result.get("extra") or {}).get(
                        "diagnosis_id"),
                    "status": result.get("status")}),
                "outputs": _out(s, "student_attempt", result),
                "weak_kp_ids": list(payload.get("weak_kp_ids") or []),
                "weak_chapter_ids": list(payload.get("weak_chapter_ids") or []),
                "errors": errors}

    def diagnose_history(s: State) -> dict:
        result = run_skill(db, "diagnose_classroom", {
            "scope_type": "student_history",
            "student_id": s["student_id"],
            "classroom_id": s["classroom_id"],
        }, thread_id=s["thread_id"], user_id=s["actor_user_id"])
        errors = list(s.get("errors") or [])
        if result.get("status") != "succeeded":
            errors.append(
                f"历史诊断未完成：{result.get('status') or 'unknown'}")
        return {"stages": _stage(s, "update_longitudinal_diagnosis", {
                    "diagnosis_id": (result.get("extra") or {}).get(
                        "diagnosis_id"),
                    "status": result.get("status")}),
                "outputs": _out(s, "student_history", result),
                "errors": errors}

    def update_state(s: State) -> dict:
        result = refresh_student_knowledge_states(db, s["student_id"])
        return {"stages": _stage(s, "refresh_knowledge_state", {
                    "knowledge_point_count": len(result["items"])}),
                "outputs": _out(s, "knowledge_state", result)}

    def compare(s: State) -> dict:
        source = s.get("source_attempt_id", "")
        result = (compare_attempts(db, source, s["attempt_id"])
                  if source else {"status": "not_a_retest"})
        return {"stages": _stage(s, "compare_retest", {
                    "source_attempt_id": source or None,
                    "comparable_count": len(result.get(
                        "comparable_knowledge_points") or [])}),
                "outputs": _out(s, "retest_comparison", result)}

    def recommend(s: State) -> dict:
        result = run_skill(db, "recommend_resources",
            {"kp_ids": s.get("weak_kp_ids") or [], "attempt_id": s["attempt_id"]},
            thread_id=s["thread_id"], user_id=s["actor_user_id"])
        errors = list(s.get("errors") or [])
        if result.get("status") != "succeeded":
            errors.append(
                f"资源推荐未完成：{result.get('status') or 'unknown'}")
        return {"stages": _stage(s, "refresh_recommendations", {
                    "status": result.get("status")}),
                "outputs": _out(s, "recommendations", result),
                "errors": errors,
                "status": "partial" if errors else "succeeded"}

    graph = StateGraph(State)
    nodes = (("diagnose_submitted_attempt", diagnose_attempt),
             ("update_longitudinal_diagnosis", diagnose_history),
             ("refresh_knowledge_state", update_state),
             ("compare_retest", compare),
             ("refresh_recommendations", recommend))
    for name, fn in nodes:
        graph.add_node(name, fn)
    graph.add_edge(START, nodes[0][0])
    for current, following in zip(nodes, nodes[1:]):
        graph.add_edge(current[0], following[0])
    graph.add_edge(nodes[-1][0], END)
    return graph.compile(checkpointer=CHECKPOINTER)


def run_post_submission(db: Session, *, actor_user_id: str,
                        attempt_id: str) -> dict:
    attempt = db.get(E.Attempt, attempt_id)
    if not attempt or not attempt.student_id or not attempt.assessment_version_id:
        raise ValueError("答卷没有完整的学生和试卷版本关联")
    version = db.get(E.AssessmentVersion, attempt.assessment_version_id)
    assessment = db.get(E.Assessment, version.assessment_id) if version else None
    if not assessment:
        raise ValueError("答卷关联的试卷版本不存在")
    thread_id = f"post-submit:{attempt_id}:{uuid.uuid4().hex[:8]}"
    initial: State = {"actor_user_id": actor_user_id,
        "student_id": attempt.student_id, "classroom_id": assessment.classroom_id,
        "assessment_version_id": attempt.assessment_version_id,
        "attempt_id": attempt.attempt_id,
        "source_attempt_id": attempt.retest_of_attempt_id or "",
        "thread_id": thread_id, "stages": [], "outputs": {},
        "weak_kp_ids": [], "weak_chapter_ids": [],
        "errors": [], "status": "queued"}
    final = build_post_submission_graph(db).invoke(
        initial, config={"configurable": {"thread_id": thread_id}})
    return {"thread_id": thread_id, "graph": "post_submission_graph",
            "status": final.get("status"), "stages": final.get("stages", []),
            "outputs": final.get("outputs", {}), "errors": final.get("errors", []),
            "checkpoint_available": CHECKPOINTER.get_tuple(
                {"configurable": {"thread_id": thread_id}}) is not None}


def graph_status(db: Session) -> dict:
    graphs = {"teacher_intervention_graph": build_teacher_intervention_graph(db),
              "post_submission_graph": build_post_submission_graph(db)}
    return {"langgraph_available": True,
            "checkpointer": type(CHECKPOINTER).__name__,
            "graphs": {name: {"compiled_type": type(graph).__name__,
                               "nodes": sorted(graph.get_graph().nodes.keys())}
                       for name, graph in graphs.items()}}
