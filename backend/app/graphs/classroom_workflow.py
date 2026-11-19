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
    target_kp_ids: list[str]
    remaining_kp_ids: list[str]
    date_from: str
    date_to: str


def diagnosis_items(payload: dict) -> list[dict]:
    """Normalize all three diagnosis contracts at the workflow boundary."""
    rows = (payload.get("items") or payload.get("wrong_items") or
            payload.get("high_frequency_errors") or [])
    return [{**row,
             "kp_id": row.get("kp_id") or (row.get("knowledge_point") or {}).get("kp_id", ""),
             "chapter_id": row.get("chapter_id") or (row.get("chapter") or {}).get("chapter_id", "")}
            for row in rows]


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
    from backend.app.graphs.learning_loop import build
    return build(db, CHECKPOINTER)


def run_teacher_intervention(db: Session, *, actor_user_id: str,
                             student_id: str = "", assessment_version_id: str = "",
                             classroom_id: str = "", scope_type: str = "student_attempt",
                             attempt_id: str = "", date_from: str = "", date_to: str = "",
                             count: int = 5, thread_id: str = "", kp_ids=None,
                             practice: bool = False, prefer_bank: bool = False, retry_generation: bool = False,
                             _scopes=None, _scope_keys=None) -> dict:
    if scope_type not in {"student_attempt", "student_history", "class_assessment"}:
        raise ValueError("不支持的诊断范围")
    thread_id = thread_id or f"teacher-intervention:{uuid.uuid4().hex[:12]}"
    request = {"student_id":student_id,"assessment_version_id":assessment_version_id,
        "classroom_id":classroom_id,"scope_type":scope_type,"attempt_id":attempt_id,
        "date_from":date_from,"date_to":date_to,"count":5,"kp_ids":kp_ids or [],
        "practice":practice,"prefer_bank":prefer_bank,"retry_generation":retry_generation,"_scopes":_scopes or []}
    final = build_teacher_intervention_graph(db).invoke(
        {"actor_user_id":actor_user_id,"thread_id":thread_id,"request":request,"count":5},
        config={"configurable":{"thread_id":thread_id}})
    return {"thread_id":thread_id,"graph":"teacher_intervention_graph",
            "status":final.get("status"),"stages":final.get("stages",[]),
            "outputs":final.get("outputs",{}),"errors":final.get("errors",[]),
            "checkpoint_available":CHECKPOINTER.get_tuple({"configurable":{"thread_id":thread_id}}) is not None}


def build_post_submission_graph(db: Session):
    def diagnose_attempt(s: State) -> dict:
        result = run_skill(db, "diagnose_classroom", {
            "scope_type": "student_attempt",
            "student_id": s["student_id"],
            "assessment_version_id": s["assessment_version_id"],
            "attempt_id": s["attempt_id"],
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
        verification = run_skill(db, "verify_learning", {
            "attempt_id": s["attempt_id"]}, thread_id=s["thread_id"],
            user_id=s["actor_user_id"]) if source else {"status": "not_a_retest"}
        result = (compare_attempts(db, source, s["attempt_id"])
                  if source else {"status": "not_a_retest"})
        return {"stages": _stage(s, "compare_retest", {
                    "source_attempt_id": source or None,
                    "comparable_count": len(result.get(
                        "comparable_knowledge_points") or [])}),
                "outputs": {**_out(s, "retest_comparison", result),
                            "verification": verification}}

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
