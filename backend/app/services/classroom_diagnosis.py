"""Deterministic teacher-side classroom diagnosis.

The classroom domain deliberately separates a learner (``Student``) from a
login/account (``User``).  This module is read-only except for the explicit
``run_diagnosis`` entry point, which persists an auditable ``Diagnosis`` row.

Evidence rules used by every aggregate in this module:

* an absent or superseded attempt is never a denominator;
* an assessment/attempt/score whose answer key is pending is never a
  denominator;
* ``AnswerScore.correct is NULL`` is ungraded evidence and is never a
  denominator;
* a real zero score is retained (no truthiness checks on awarded scores);
* question metadata always comes from the immutable ``PaperItem.snapshot``.

Class-level responses expose anonymous codes and scoring facts only.  They
never include ``Answer.response``, ``Answer.raw_response``, student profiles,
external identity keys, or imported raw payloads.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
import re
from statistics import median
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services.classroom_content import source_content, is_search_entry
from backend.app.services.evidence_strength import item_evidence, aggregate_reference, summarize_independence
from backend.app.services import model_provider
from backend.app.services.diagnosis_analysis_v2 import SCIENCE_GUIDANCE, evidence_analysis
from backend.app.services.score_policy import admissible_score


SKILL_VERSION = "classroom-chapter-diagnosis-v6"
PENDING_SCORE_STATUSES = {
    "pending_key", "needs_manual", "needs_model_scoring", "ungraded",
}


def _public_ai_text(value: Any) -> str:
    """Remove implementation vocabulary from teacher-facing model prose."""
    text = str(value or "").strip()
    replacements = {
        "question_id": "题目",
        "occurrences": "作答记录",
        "all_or_nothing": "整题计分",
        "correct=NULL": "待评分题目",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(
        r"(?<![A-Za-z0-9_])(?:paper|assessv|assessment|attempt|cattempt|item|cq|kp)_[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])",
        "对应记录",
        text,
        flags=re.IGNORECASE,
    )
    return text


class ClassroomDiagnosisError(ValueError):
    """A caller supplied an invalid classroom diagnosis request."""


class ClassroomDiagnosisNotFound(ClassroomDiagnosisError):
    """A requested classroom entity or canonical attempt does not exist."""


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _rate(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return _round(numerator / denominator)


def _assessment_version(db: Session, version_id: str) -> E.AssessmentVersion:
    row = db.get(E.AssessmentVersion, version_id)
    if row is None:
        raise ClassroomDiagnosisNotFound(f"考核版本不存在: {version_id}")
    return row


def _assessment(db: Session, assessment_id: str) -> E.Assessment:
    row = db.get(E.Assessment, assessment_id)
    if row is None:
        raise ClassroomDiagnosisNotFound(f"考核不存在: {assessment_id}")
    return row


def _classroom(db: Session, classroom_id: str) -> E.Classroom:
    row = db.get(E.Classroom, classroom_id)
    if row is None:
        raise ClassroomDiagnosisNotFound(f"班级不存在: {classroom_id}")
    return row


def official_classroom(db: Session) -> E.Classroom:
    """Resolve the one teacher-facing class without hard-coding its id.

    The imported workbook class is authoritative.  Explicit metadata written
    by the migration wins, followed by the historical ``teacher_workbook``
    source.  Demo fixtures remain queryable by id but never become the default
    teacher context.
    """
    rows = list(db.execute(select(E.Classroom).order_by(
        E.Classroom.created_at, E.Classroom.classroom_id)).scalars().all())
    if not rows:
        raise ClassroomDiagnosisNotFound("尚未建立正式教学班级")
    marked = [row for row in rows if (row.details or {}).get("official") is True]
    candidates = marked or [row for row in rows
                            if row.source == "teacher_workbook"]
    if not candidates:
        candidates = [row for row in rows
                      if row.source not in {"demo_account", "test_fixture"}]
    if not candidates:
        raise ClassroomDiagnosisNotFound("未找到正式教学班级")
    # Stable preference for the class with the most imported assessments.
    def evidence_count(row: E.Classroom) -> tuple[int, str]:
        count = len(db.execute(select(E.Assessment.assessment_id).where(
            E.Assessment.classroom_id == row.classroom_id,
            E.Assessment.source == "teacher_workbook",
        )).scalars().all())
        return count, row.classroom_id
    return sorted(candidates, key=evidence_count, reverse=True)[0]


def _student(db: Session, student_id: str) -> E.Student:
    row = db.get(E.Student, student_id)
    if row is None:
        raise ClassroomDiagnosisNotFound(f"学生不存在: {student_id}")
    return row


def _version_context(
    db: Session, version_id: str,
) -> tuple[E.AssessmentVersion, E.Assessment, E.Classroom]:
    version = _assessment_version(db, version_id)
    assessment = _assessment(db, version.assessment_id)
    classroom = _classroom(db, assessment.classroom_id)
    return version, assessment, classroom


def _members(db: Session, classroom_id: str) -> list[tuple[E.ClassroomStudent, E.Student]]:
    rows = db.execute(
        select(E.ClassroomStudent, E.Student)
        .join(E.Student, E.Student.student_id == E.ClassroomStudent.student_id)
        .where(
            E.ClassroomStudent.classroom_id == classroom_id,
            E.ClassroomStudent.status == "active",
            E.Student.active.is_(True),
        )
        .order_by(E.Student.display_code, E.Student.student_id)
    ).all()
    return [(membership, student) for membership, student in rows]


def _assert_member(db: Session, classroom_id: str, student_id: str) -> E.Student:
    student = _student(db, student_id)
    membership = db.execute(
        select(E.ClassroomStudent).where(
            E.ClassroomStudent.classroom_id == classroom_id,
            E.ClassroomStudent.student_id == student_id,
            E.ClassroomStudent.status == "active",
        )
    ).scalars().first()
    if membership is None:
        raise ClassroomDiagnosisNotFound("学生不属于该班级")
    return student


def _paper_items(db: Session, version: E.AssessmentVersion) -> list[E.PaperItem]:
    return list(db.execute(
        select(E.PaperItem)
        .where(E.PaperItem.paper_id == version.paper_id)
        .order_by(E.PaperItem.position, E.PaperItem.item_id)
    ).scalars().all())


def _snapshot_view(
    item: E.PaperItem,
    *,
    include_answer_reference: bool = False,
    version: E.AssessmentVersion | None = None,
) -> dict[str, Any]:
    """Return only teacher-safe immutable question metadata.

    In particular this intentionally does not return ``answer_payload`` or the
    complete snapshot dictionary.
    """
    snap = item.snapshot or {}
    kp_id = str(snap.get("main_kp_id") or snap.get("kp_id") or "")
    aux_kp_ids = [str(k) for k in (snap.get("aux_kp_ids") or []) if k]
    view = {
        "paper_item_id": item.item_id,
        "position": item.position,
        "question_id": item.question_id,
        "question_version": item.question_version,
        **source_content(snap),
        "options": snap.get("options") or [],
        "stem": str(snap.get("stem_zh") or snap.get("stem") or ""),
        "question_type": str(snap.get("question_type") or ""),
        "chapter_id": str(item.chapter_id or snap.get("chapter_id") or ""),
        "kp_id": kp_id,
        "kp_ids": list(dict.fromkeys([k for k in [kp_id, *aux_kp_ids] if k])),
        "aux_kp_ids": aux_kp_ids,
        "difficulty": str(snap.get("difficulty") or ""),
        "cognitive_level": str(snap.get("cognitive_level") or ""),
        "max_score": _round(float(item.score or 0)),
    }
    if include_answer_reference:
        payload = snap.get("answer_payload") or {}
        source = str(version.answer_key_source or "") if version else ""
        if version and version.teacher_verified:
            authority = "teacher_verified"
            authority_label = "教师核验答案"
        elif source.startswith("reverse_"):
            authority = "inferred_unverified"
            authority_label = "历史反推答案键（未教师核验）"
        elif version and version.scoring_status == "pending_key":
            authority = "pending_key"
            authority_label = "答案键待确认"
        else:
            authority = "snapshot_rule"
            authority_label = "固定试卷答案键"
        view["answer_reference"] = {
            "available": bool(payload),
            "payload": payload if payload else None,
            "explanation": str(snap.get("explanation") or ""),
            "rubric": snap.get("rubric"),
            "authority_status": authority,
            "authority_label": authority_label,
            "source": source,
        }
    return view


def _attempts_for_version(db: Session, version_id: str) -> list[E.Attempt]:
    return list(db.execute(
        select(E.Attempt).where(
            E.Attempt.assessment_version_id == version_id,
            E.Attempt.is_effective.is_(True),
        ).order_by(E.Attempt.submitted_at.desc(), E.Attempt.started_at.desc())
    ).scalars().all())


def _attempt_map(db: Session, version_id: str) -> dict[str, E.Attempt]:
    """Choose one effective canonical attempt per learner deterministically."""
    out: dict[str, E.Attempt] = {}
    for attempt in _attempts_for_version(db, version_id):
        if not attempt.student_id:
            continue
        previous = out.get(attempt.student_id)
        if previous is None:
            out[attempt.student_id] = attempt
            continue
        # Submitted evidence wins over an unfinished row.  Ordering above
        # already makes the newest row win inside the same status.
        if previous.status != "submitted" and attempt.status == "submitted":
            out[attempt.student_id] = attempt
    return out


def _attempt_for_student(
    db: Session, student_id: str, version_id: str,
) -> E.Attempt | None:
    return _attempt_map(db, version_id).get(student_id)


def _answers_by_item(
    db: Session, attempt: E.Attempt, items: Iterable[E.PaperItem],
) -> dict[str, E.Answer]:
    rows = list(db.execute(select(E.Answer).where(
        E.Answer.attempt_id == attempt.attempt_id,
    )).scalars().all())
    exact = {r.paper_item_id: r for r in rows if r.paper_item_id}
    # Backward-compatible fallback is exact question *version*, never the
    # current/latest question row.  The fallback cannot represent duplicate
    # occurrences, hence new classroom imports must populate paper_item_id.
    by_qv = {(r.question_id, r.question_version): r for r in rows}
    return {
        item.item_id: exact.get(item.item_id)
        or by_qv.get((item.question_id, item.question_version))
        for item in items
        if exact.get(item.item_id)
        or by_qv.get((item.question_id, item.question_version))
    }


def _scores_by_item(
    db: Session, attempt: E.Attempt, items: Iterable[E.PaperItem],
) -> dict[str, E.AnswerScore | E.AnswerRegrade]:
    rows = list(db.execute(select(E.AnswerScore).where(
        E.AnswerScore.attempt_id == attempt.attempt_id,
    )).scalars().all())
    exact: dict[str, E.AnswerScore | E.AnswerRegrade] = {
        r.paper_item_id: r for r in rows if r.paper_item_id}
    # A regrade is an append-only overlay.  Select the newest completed run
    # containing this attempt, even when a newer run covers another class.
    latest_run = db.execute(select(E.ScoringRun).join(
        E.AnswerRegrade,
        E.AnswerRegrade.scoring_run_id == E.ScoringRun.scoring_run_id,
    ).where(
        E.ScoringRun.status == "completed",
        E.AnswerRegrade.attempt_id == attempt.attempt_id,
    ).order_by(
        E.ScoringRun.completed_at.desc(), E.ScoringRun.scoring_run_id.desc()
    ).limit(1)).scalars().first()
    if latest_run is not None:
        for row in db.execute(select(E.AnswerRegrade).where(
                E.AnswerRegrade.scoring_run_id == latest_run.scoring_run_id,
                E.AnswerRegrade.attempt_id == attempt.attempt_id)
        ).scalars().all():
            if row.paper_item_id:
                exact[row.paper_item_id] = row
    by_qv = {(r.question_id, r.question_version): r for r in rows}
    return {
        item.item_id: exact.get(item.item_id)
        or by_qv.get((item.question_id, item.question_version))
        for item in items
        if exact.get(item.item_id)
        or by_qv.get((item.question_id, item.question_version))
    }


def _attempt_is_pending(attempt: E.Attempt | None) -> bool:
    return bool(attempt and attempt.grading_status == "pending_key")


def _valid_score(
    version: E.AssessmentVersion,
    attempt: E.Attempt | None,
    score: E.AnswerScore | E.AnswerRegrade | None,
) -> bool:
    if isinstance(score, E.AnswerRegrade):
        return bool(
            attempt is not None and attempt.status == "submitted"
            and attempt.is_effective and attempt.attendance_status != "absent"
            and score.correct is not None
            and score.scoring_status not in PENDING_SCORE_STATUSES
            and score.authority_status in {
                "teacher_key", "paper_snapshot", "snapshot_rule",
                "ai_auto_checked", "verified"})
    return admissible_score(attempt, score, version)


def _source_summary(evidence_refs) -> dict:
    counts = defaultdict(int)
    for ref in evidence_refs:
        counts[ref.get("authority_status") or "snapshot_rule"] += 1
    return {"evidence_count": sum(counts.values()), "by_authority": dict(counts),
            "ai_auto_checked_count": counts.get("ai_auto_checked", 0),
            "teacher_key_count": counts.get("teacher_key", 0),
            "snapshot_rule_count": sum(counts.get(k, 0) for k in ("snapshot_rule", "paper_snapshot", "verified")),
            "note": "AI 自动校验评分可按当前策略计入统计，但不是教师权威标准答案；保留来源与证据数量。历史快照不自动视为教师核验。"}


def _inferred_summary(version, attempt, scores, items=()) -> dict:
    """Retain uncertain historical scores as reference, never formal mastery."""
    rows = [s for s in scores if s is not None and s.authority_status == "inferred_unverified"
            and s.correct is not None and s.scoring_status not in PENDING_SCORE_STATUSES
            and attempt is not None and attempt.status == "submitted" and attempt.is_effective
            and attempt.attendance_status != "absent" and not _attempt_is_pending(attempt)
            and version.scoring_status != "pending_key"]
    item_map = {item.item_id: item for item in items}
    refs = [item_evidence(item_map.get(score.paper_item_id), score, attempt, assessment_id=version.assessment_id) for score in rows]
    reference = aggregate_reference([{"evidence_refs": refs}])
    return {"basis": "未核验推断评分，仅供参考，不计入正式掌握率", "scored_items": len(rows),
            "correct_items": sum(s.correct is True for s in rows),
            "awarded_score": sum(float(s.awarded_score or 0) for s in rows),
            "max_score": sum(float(s.max_score or 0) for s in rows),
            "evidence_refs": refs, "reference_diagnosis": reference}



def _excluded_reason(
    version: E.AssessmentVersion,
    attempt: E.Attempt | None,
    score: E.AnswerScore | E.AnswerRegrade | None,
) -> str:
    if attempt is None:
        return "no_submission"
    if attempt.attendance_status == "absent":
        return "absent"
    if not attempt.is_effective:
        return "superseded"
    is_regrade = isinstance(score, E.AnswerRegrade)
    if not is_regrade and (version.scoring_status == "pending_key" or _attempt_is_pending(attempt)):
        return "pending_key"
    if score is None:
        return "ungraded"
    if score.scoring_status in PENDING_SCORE_STATUSES:
        return "pending_key" if score.scoring_status == "pending_key" else "ungraded"
    if score.authority_status == "pending_key":
        return "pending_key"
    if score.authority_status == "inferred_unverified":
        return "inferred_unverified"
    if score.correct is None:
        return "correct_null"
    if attempt.status != "submitted":
        return "not_submitted"
    if not _valid_score(version, attempt, score):
        return "unverified_authority"
    return "included"


def _score_max(score: E.AnswerScore, item: E.PaperItem) -> float:
    value = float(score.max_score or 0)
    return value if value > 0 else float(item.score or 0)


def _kp_id(item: E.PaperItem, score: E.AnswerScore | None = None) -> str:
    snap = item.snapshot or {}
    # A score may contain a normalized classroom KP.  Otherwise use the exact
    # paper snapshot; never fetch a later Question version.
    return str((score.kp_id if score else "")
               or snap.get("main_kp_id") or snap.get("kp_id") or "")


def _chapter_id(item: E.PaperItem,
                score: E.AnswerScore | E.AnswerRegrade | None = None) -> str:
    snap = item.snapshot or {}
    return str((getattr(score, "chapter_id", "") if score else "")
               or item.chapter_id or snap.get("chapter_id") or "")


def _chapter_labels(db: Session, chapter_ids: Iterable[str]) -> dict[str, str]:
    ids = sorted({chapter_id for chapter_id in chapter_ids if chapter_id})
    if not ids:
        return {}
    return {row.chapter_id: row.title for row in db.execute(
        select(E.Chapter).where(E.Chapter.chapter_id.in_(ids))
    ).scalars().all()}


def _kp_labels(db: Session, kp_ids: Iterable[str]) -> dict[str, str]:
    ids = sorted({k for k in kp_ids if k})
    if not ids:
        return {}
    return {row.kp_id: row.name for row in db.execute(
        select(E.KnowledgePoint).where(E.KnowledgePoint.kp_id.in_(ids))
    ).scalars().all()}


def _resources(db: Session, kp_ids: Iterable[str], limit: int = 12) -> list[dict]:
    from backend.app.services.learning_resources import recommend
    return recommend(db, kp_ids, limit=limit)


def _answer_value(payload: Any) -> Any:
    """Extract a display answer without guessing missing semantics."""
    if not isinstance(payload, dict):
        return payload
    for key in ("selected_keys", "keys", "key", "values", "parts", "value",
                "answer", "raw_text", "text", "reference_answer"):
        if key in payload and payload[key] not in (None, "", []):
            return payload[key]
    return payload


def _answer_keys(payload: Any) -> list[str]:
    value = _answer_value(payload)
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("key", item.get("value", ""))
            text = str(item).strip().upper()
            if text:
                out.append(text)
        return out
    text = str(value if value is not None else "").strip().upper()
    return [text] if text else []


def _item_error_analysis(item: dict[str, Any]) -> dict[str, Any]:
    """Describe an observable answer mismatch, never an invented mental cause."""
    position = item.get("position")
    qtype = str(item.get("question_type") or "")
    answer = (item.get("answer_reference") or {}).get("payload") or {}
    student_keys = set(_answer_keys(item.get("response")))
    correct_keys = set(_answer_keys(answer))
    label = "作答与参考答案不一致"
    evidence = f"第 {position} 题学生作答与可核验答案不一致。"
    details: dict[str, Any] = {}
    if qtype in {"single_choice", "multiple_choice", "multi_choice"}:
        missed = sorted(correct_keys - student_keys)
        extra = sorted(student_keys - correct_keys)
        if qtype in {"multiple_choice", "multi_choice"}:
            label = "多选漏选或错选"
            evidence = (f"第 {position} 题漏选 {', '.join(missed) or '无'}；"
                        f"错选 {', '.join(extra) or '无'}。")
        else:
            label = "选择了错误选项"
            evidence = (f"第 {position} 题选择 {', '.join(sorted(student_keys)) or '未作答'}，"
                        f"正确选项为 {', '.join(sorted(correct_keys)) or '未提供'}。")
        details = {"missed_options": missed, "extra_options": extra}
    elif qtype == "true_false":
        label, evidence = "判断与参考结论相反", f"第 {position} 题的判断结果与可核验答案相反。"
    elif qtype in {"numeric", "calculation"}:
        label = "数值或计算结果不符"
        tolerance = answer.get("tol", answer.get("tolerance")) if isinstance(answer, dict) else None
        evidence = f"第 {position} 题提交数值与标准结果不符"
        if tolerance is not None:
            evidence += f"（允许误差 {tolerance}）"
        evidence += "。"
        details = {"tolerance": tolerance}
    elif qtype in {"fill_blank", "formula_fill"}:
        label, evidence = "填空内容未匹配可接受答案", f"第 {position} 题至少有一个空未匹配参考答案或等价表达。"
    elif qtype in {"short_answer", "term", "derivation", "comprehensive"}:
        label, evidence = "主观题评分要点未充分覆盖", f"第 {position} 题未取得满分；应结合评分要点与模型评分理由核对。"
    return {
        "status": "evidence_grounded", "label": label,
        "evidence": evidence, "question_positions": [position],
        "details": details,
        "limit": "这里只描述可观察到的作答差异，具体认知原因仍需后续复测确认。",
    }


def _class_comparison(
    db: Session, *, classroom_id: str, version: E.AssessmentVersion,
    items: list[E.PaperItem], selected_attempt: E.Attempt,
) -> dict[str, Any]:
    """Compare only complete reliable scores from the same immutable version."""
    members = _members(db, classroom_id)
    attempts = _attempt_map(db, version.assessment_version_id)
    attempts[selected_attempt.student_id] = selected_attempt
    attendee_count = 0
    rows: list[dict[str, Any]] = []
    per_item = {item.item_id: {"denominator": 0, "correct": 0} for item in items}
    snapshot_max = _round(sum(float(item.score or 0) for item in items)) or 0.0
    for _membership, student in members:
        attempt = attempts.get(student.student_id)
        if (attempt is None or attempt.attendance_status == "absent"
                or attempt.status != "submitted" or not attempt.is_effective):
            continue
        attendee_count += 1
        scores = _scores_by_item(db, attempt, items)
        awarded = maximum = 0.0
        scored = 0
        for item in items:
            score = scores.get(item.item_id)
            if not _valid_score(version, attempt, score):
                continue
            assert score is not None
            scored += 1
            awarded += float(score.awarded_score or 0)
            maximum += _score_max(score, item)
            per_item[item.item_id]["denominator"] += 1
            per_item[item.item_id]["correct"] += 1 if score.correct is True else 0
        rows.append({
            "student_id": student.student_id, "scored_items": scored,
            "awarded_score": _round(awarded), "max_score": _round(maximum),
            "score_rate": _rate(awarded, maximum),
            "fully_scored": scored == len(items) and maximum > 0,
        })
    # Some historical paper snapshots did not preserve per-item points, while
    # the append-only regrade rows do.  In that case, derive the common full
    # score from complete regrades instead of treating the paper as a zero-point
    # exam.  Different totals remain non-comparable.
    full_totals = {
        round(float(row["max_score"] or 0), 6)
        for row in rows if row["fully_scored"]
    }
    nominal_max = snapshot_max
    if nominal_max <= 0 and len(full_totals) == 1:
        nominal_max = next(iter(full_totals))
    for row in rows:
        row["fully_comparable"] = bool(
            row["fully_scored"] and nominal_max > 0
            and abs(float(row["max_score"] or 0) - nominal_max) < 1e-6
        )
    comparable = [row for row in rows if row["fully_comparable"]]
    selected = next((row for row in rows
                     if row["student_id"] == selected_attempt.student_id), None)
    rates = [{
        "paper_item_id": item.item_id, "position": item.position,
        "denominator": per_item[item.item_id]["denominator"],
        "correct_count": per_item[item.item_id]["correct"],
        "correct_rate": _rate(per_item[item.item_id]["correct"],
                              per_item[item.item_id]["denominator"]),
    } for item in items]
    status, reason, rank = "available", "同一固定试卷版本、全部题目可靠评分且满分一致。", None
    if selected is None or selected["scored_items"] == 0:
        status, reason = "no_reliable_score", "当前学生没有可用于班级比较的可靠逐题评分。"
    elif not selected["fully_comparable"]:
        status, reason = "partial_scoring", "当前成绩只覆盖部分题目，不能与完整评分答卷作正式排名。"
    elif len(comparable) <= 1:
        status, reason = "single_participant", "同一固定版本只有一份完整可靠评分答卷，暂无正式排名。"
    else:
        selected_score = float(selected["awarded_score"] or 0)
        rank = 1 + sum(1 for row in comparable
                       if float(row["awarded_score"] or 0) > selected_score)
    values = [float(row["awarded_score"] or 0) for row in comparable]
    return {
        "status": status, "reason": reason, "rank": rank,
        "ranking_rule": "竞赛排名：同分并列，后续名次按实际占位顺延。",
        "participant_count": len(comparable), "attendee_count": attendee_count,
        "class_size": len(members), "absent_count": max(0, len(members) - attendee_count),
        "average_score": _round(sum(values) / len(values)) if values else None,
        "highest_score": _round(max(values)) if values else None,
        "max_score": nominal_max,
        "selected_score": selected.get("awarded_score") if selected else None,
        "selected_score_rate": selected.get("score_rate") if selected else None,
        "question_correct_rates": rates,
    }


def _deterministic_ai_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    score = payload.get("score_summary") or {}
    score_text = ""
    if score.get("awarded_score") is not None and score.get("max_score") is not None:
        score_text = f"本次得分 {score.get('awarded_score')}/{score.get('max_score')}。"
    wrong = [item for item in payload.get("items", [])
             if item.get("evidence_status") == "scored"
             and item.get("correct") is False]
    if not wrong:
        return {
            "status": "no_wrong_items", "source": "evidence_rules",
            "summary": (score_text +
                        "本次已评分题目全部答对，对本卷考查内容表现良好。"
                        "建议通过后续不同题型继续检验掌握情况。"),
            "error_types": [], "concept_hypotheses": [],
            "recommendations": ["保留本次全对记录，并通过后续不同题型继续验证稳定性。"],
            "retest_questions": [], "limits": "单次全对不等于长期稳定掌握。",
        }
    error_types = []
    concepts: dict[str, dict[str, Any]] = {}
    for item in wrong:
        analysis = item.get("error_analysis") or _item_error_analysis(item)
        error_types.append({
            "label": analysis["label"],
            "question_positions": [item.get("position")],
            "evidence": analysis["evidence"],
        })
        name = (item.get("knowledge_point") or {}).get("name") or "本题对应概念"
        chapter = (item.get("chapter") or {}).get("title") or "未映射章节"
        row = concepts.setdefault(name, {
            "name": name, "chapter": chapter, "question_positions": [],
            "evidence": [], "confidence": "待复测确认",
        })
        row["question_positions"].append(item.get("position"))
        row["evidence"].append(analysis["evidence"])
    positions = "、".join(str(item.get("position")) for item in wrong)
    chapters = sorted({(item.get("chapter") or {}).get("title") or "未映射章节"
                       for item in wrong})
    return {
        "status": "evidence_grounded_fallback", "source": "evidence_rules",
        "summary": (score_text + f"主要失分在第 {positions} 题，涉及{'、'.join(chapters)}。"
                    "建议先回看这些题目的答案差异，再用同类新题确认薄弱环节。"),
        "error_types": error_types,
        "concept_hypotheses": list(concepts.values()),
        "recommendations": [
            f"逐题回看第 {positions} 题的题干、学生答案、正确答案与解析。",
            f"围绕{'、'.join(chapters)}使用不同题族复测，区分概念理解与计算或审题失误。",
        ],
        "retest_questions": [f"使用不同表述复测第 {positions} 题涉及的核心概念。"],
        "limits": "概念判断来自本次错题，仍可通过后续练习继续确认。",
    }


def _model_ai_analysis(db: Session, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Ask the configured model for constrained qualitative analysis."""
    fallback = _deterministic_ai_analysis(payload)
    wrong = [item for item in payload.get("items", [])
             if item.get("evidence_status") == "scored"
             and item.get("correct") is False]
    if not wrong:
        return fallback
    evidence = [{
        "position": item.get("position"),
        "question_type": item.get("question_type"),
        "chapter": (item.get("chapter") or {}).get("title"),
        "concept": (item.get("knowledge_point") or {}).get("name"),
        "stem": item.get("stem"), "options": item.get("options") or [],
        "student_answer": item.get("response"),
        "correct_answer": (item.get("answer_reference") or {}).get("payload"),
        "explanation": (item.get("answer_reference") or {}).get("explanation"),
        "score": {"awarded": item.get("awarded_score"),
                  "maximum": item.get("max_score")},
        "observable_mismatch": item.get("error_analysis"),
    } for item in wrong]
    messages = [{
        "role": "system",
        "content": ("你是机器学习课程助教。只能依据给出的真实错题证据做中文定性分析，"
                    "不得编造题目、答案、学生心理状态或掌握概率。单题证据只能表述为待复测假设。"
                    "严格返回 JSON 对象，字段为 summary、error_types、concept_hypotheses、"
                    "recommendations、retest_questions、limits。每条判断必须带 question_positions。"),
    }, {"role": "user", "content": json.dumps({"wrong_items": evidence}, ensure_ascii=False)}]
    try:
        response = model_provider.chat(
            db, messages=messages, skill_name="diagnose_classroom",
            prompt_version="student-attempt-errors-v1", run_id=run_id,
            temperature=0.1, max_tokens=1200, timeout=60.0)
        if response.get("finish_reason") == "length":
            raise model_provider.ModelUnavailable("truncated", "模型输出被截断")
        parsed = model_provider.parse_json_content(response.get("content") or "")
        summary = _public_ai_text(parsed.get("summary")) if isinstance(parsed, dict) else ""
        if not isinstance(parsed, dict) or not summary:
            raise model_provider.ModelUnavailable("invalid_output", "模型未返回有效结构")
        model_errors = [{
            "label": _public_ai_text(row.get("label") or row.get("name") or row.get("type")),
            "question_positions": row.get("question_positions") or [],
            "evidence": _public_ai_text(row.get("evidence") or row.get("description")),
        } for row in (parsed.get("error_types") or [])
          if isinstance(row, dict)
          and str(row.get("label") or row.get("name") or row.get("type") or "").strip()]
        model_concepts = [{
            "name": _public_ai_text(row.get("name") or row.get("concept")),
            "chapter": str(row.get("chapter") or "").strip(),
            "question_positions": row.get("question_positions") or [],
            "evidence": _public_ai_text(row.get("evidence") or row.get("hypothesis")),
        } for row in (parsed.get("concept_hypotheses") or [])
          if isinstance(row, dict)
          and str(row.get("name") or row.get("concept") or "").strip()]
        model_advice = [
            row if isinstance(row, str) else
            str(row.get("message") or row.get("advice") or row.get("action") or "")
            for row in (parsed.get("recommendations") or [])
            if isinstance(row, str) or isinstance(row, dict)
        ]
        model_advice = [_public_ai_text(row) for row in model_advice if _public_ai_text(row)]
        model_retest = [
            row if isinstance(row, str) else
            str(row.get("question") or row.get("message") or "")
            for row in (parsed.get("retest_questions") or [])
            if isinstance(row, str) or isinstance(row, dict)
        ]
        model_retest = [_public_ai_text(row) for row in model_retest if _public_ai_text(row)]
        return {
            "status": "completed", "source": "deepseek",
            "model": response.get("model"), "call_id": response.get("call_id"),
            "summary": summary,
            "error_types": model_errors or fallback["error_types"],
            "concept_hypotheses": model_concepts or fallback["concept_hypotheses"],
            "recommendations": model_advice or fallback["recommendations"],
            "retest_questions": model_retest or fallback["retest_questions"],
            "limits": _public_ai_text(parsed.get("limits")) or fallback["limits"],
        }
    except model_provider.ModelUnavailable as exc:
        return {**fallback, "model_status": exc.status, "model_message": exc.message}


def _submission_statuses(
    db: Session, version_id: str,
) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    rows = db.execute(select(E.ImportedSubmission).where(
        E.ImportedSubmission.assessment_version_id == version_id,
    )).scalars().all()
    for row in rows:
        out[row.student_id].add(row.status)
    return out


def overview(db: Session, classroom_id: str) -> dict:
    classroom = _classroom(db, classroom_id)
    members = _members(db, classroom_id)
    assessments = list(db.execute(select(E.Assessment).where(
        E.Assessment.classroom_id == classroom_id,
    )).scalars().all())
    assessment_ids = [a.assessment_id for a in assessments]
    versions = list(db.execute(select(E.AssessmentVersion).where(
        E.AssessmentVersion.assessment_id.in_(assessment_ids)
    )).scalars().all()) if assessment_ids else []
    version_ids = [v.assessment_version_id for v in versions]
    attempts = list(db.execute(select(E.Attempt).where(
        E.Attempt.assessment_version_id.in_(version_ids),
        E.Attempt.is_effective.is_(True),
    )).scalars().all()) if version_ids else []
    absences = set()
    if version_ids:
        for row in db.execute(select(E.ImportedSubmission).where(
            E.ImportedSubmission.assessment_version_id.in_(version_ids),
            E.ImportedSubmission.status == "absent",
        )).scalars().all():
            absences.add((row.student_id, row.assessment_version_id))
    submitted = [a for a in attempts
                 if a.status == "submitted" and a.attendance_status != "absent"]
    submitted_ids = {a.attempt_id for a in submitted}
    historical_assessments = [row for row in assessments
                              if row.source == "teacher_workbook"]
    online_assessments = [row for row in assessments
                          if row.source != "teacher_workbook"]
    historical_attempts = [row for row in submitted
                           if bool(row.source_import_run_id)]
    online_attempts = [row for row in submitted
                       if not row.source_import_run_id]
    release_rows = list(db.execute(
        select(E.AssessmentRelease, E.ReleaseRecipient)
        .join(E.AssessmentVersion,
              E.AssessmentVersion.assessment_version_id ==
              E.AssessmentRelease.assessment_version_id)
        .join(E.Assessment,
              E.Assessment.assessment_id == E.AssessmentVersion.assessment_id)
        .join(E.ReleaseRecipient,
              E.ReleaseRecipient.release_id == E.AssessmentRelease.release_id)
        .where(E.Assessment.classroom_id == classroom_id)
    ).all())
    published_release_ids = {
        release.release_id for release, _recipient in release_rows
        if release.status == "published"
    }
    pending_recipients = [
        recipient for release, recipient in release_rows
        if release.status == "published"
        and recipient.status in {"assigned", "opened"}
    ]
    latest_run = db.execute(select(E.ScoringRun).where(
        E.ScoringRun.status == "completed").order_by(
            E.ScoringRun.completed_at.desc(),
            E.ScoringRun.scoring_run_id.desc())).scalars().first()
    regrade_summaries = []
    regrade_rows = []
    if latest_run is not None and submitted_ids:
        regrade_summaries = list(db.execute(select(E.AttemptRegradeSummary).where(
            E.AttemptRegradeSummary.scoring_run_id == latest_run.scoring_run_id,
            E.AttemptRegradeSummary.attempt_id.in_(submitted_ids),
        )).scalars().all())
        regrade_rows = list(db.execute(select(E.AnswerRegrade).where(
            E.AnswerRegrade.scoring_run_id == latest_run.scoring_run_id,
            E.AnswerRegrade.attempt_id.in_(submitted_ids),
        )).scalars().all())
    return {
        "classroom": {
            "classroom_id": classroom.classroom_id,
            "name": classroom.name,
            "term": classroom.term,
        },
        "counts": {
            "students": len(members),
            "assessments": len(assessments),
            "historical_assessments": len(historical_assessments),
            "online_assessments": len(online_assessments),
            "assessment_versions": len(versions),
            "submitted_attempts": len(submitted),
            "historical_submitted_attempts": len(historical_attempts),
            "online_submitted_attempts": len(online_attempts),
            "online_releases": len(published_release_ids),
            "pending_online_tasks": len(pending_recipients),
            "pending_key_attempts": sum(
                1 for a in submitted if a.grading_status == "pending_key"),
            "recorded_absences": len(absences),
        },
        "regrade_summary": {
            "regraded_attempts": len(regrade_summaries),
            "regraded_fully_scored_attempts": sum(
                1 for row in regrade_summaries if row.status == "graded"),
            "regraded_formal_item_rows": sum(
                1 for row in regrade_rows if row.correct is not None),
            "regraded_blocked_item_rows": sum(
                1 for row in regrade_rows if row.correct is None),
            "scoring_run_id": (
                latest_run.scoring_run_id if latest_run is not None else ""),
        },
        "data_rule": (
            "历史上报状态与最新审计重评分分开保留；缺考和 correct=NULL 不进入"
            "正确率分母，真实 0 分保留。"
        ),
    }


def list_students(db: Session, classroom_id: str) -> dict:
    classroom = _classroom(db, classroom_id)
    versions = db.execute(
        select(E.AssessmentVersion.assessment_version_id)
        .join(E.Assessment,
              E.Assessment.assessment_id == E.AssessmentVersion.assessment_id)
        .where(E.Assessment.classroom_id == classroom_id)
    ).scalars().all()
    attempts = list(db.execute(select(E.Attempt).where(
        E.Attempt.assessment_version_id.in_(versions),
        E.Attempt.is_effective.is_(True),
    )).scalars().all()) if versions else []
    by_student: dict[str, list[E.Attempt]] = defaultdict(list)
    for attempt in attempts:
        by_student[attempt.student_id].append(attempt)
    absences: dict[str, set[str]] = defaultdict(set)
    if versions:
        for row in db.execute(select(E.ImportedSubmission).where(
            E.ImportedSubmission.assessment_version_id.in_(versions),
            E.ImportedSubmission.status == "absent",
        )).scalars().all():
            absences[row.student_id].add(row.assessment_version_id)
    items = []
    for membership, student in _members(db, classroom_id):
        rows = by_student.get(student.student_id, [])
        submitted = [a for a in rows if a.status == "submitted"
                     and a.attendance_status != "absent"]
        linked_user = (db.get(E.User, student.linked_user_id)
                       if student.linked_user_id else None)
        online_receivable = bool(
            student.active and linked_user and linked_user.active
            and linked_user.role == "student"
        )
        if online_receivable:
            binding_status = "online"
        elif not student.linked_user_id:
            binding_status = "unbound"
        elif linked_user and linked_user.role != "student":
            binding_status = "role_mismatch"
        else:
            binding_status = "inactive"
        items.append({
            "student_id": student.student_id,
            "display_code": student.display_code,
            "linked_account": (linked_user.user_id if online_receivable else ""),
            "linked_display_name": (linked_user.display_name
                                    if online_receivable else ""),
            "online_receivable": online_receivable,
            "binding_status": binding_status,
            "source": student.source,
            "membership_status": membership.status,
            "submitted_assessments": len({a.assessment_version_id for a in submitted}),
            "graded_assessments": len({a.assessment_version_id for a in submitted
                                       if a.grading_status != "pending_key"}),
            "pending_key_assessments": len({a.assessment_version_id for a in submitted
                                            if a.grading_status == "pending_key"}),
            "absent_assessments": len(absences.get(student.student_id, set())),
        })
    return {
        "classroom_id": classroom.classroom_id,
        "total": len(items),
        "items": items,
        "privacy": ("历史课堂仅返回匿名代码；只有明确绑定且启用的学生登录账号"
                    "会返回绑定登录账号，便于教师验证发布流程。"),
    }


def list_assessments(db: Session, classroom_id: str) -> dict:
    classroom = _classroom(db, classroom_id)
    assessments = list(db.execute(select(E.Assessment).where(
        E.Assessment.classroom_id == classroom_id,
    ).order_by(E.Assessment.scheduled_at, E.Assessment.quiz_code)).scalars().all())
    out = []
    for assessment in assessments:
        versions = list(db.execute(select(E.AssessmentVersion).where(
            E.AssessmentVersion.assessment_id == assessment.assessment_id,
        ).order_by(E.AssessmentVersion.version_no)).scalars().all())
        vout = []
        for version in versions:
            attempts = _attempts_for_version(db, version.assessment_version_id)
            statuses = _submission_statuses(db, version.assessment_version_id)
            vout.append({
                "assessment_version_id": version.assessment_version_id,
                "version_no": version.version_no,
                "paper_id": version.paper_id,
                "content_hash": version.content_hash,
                "scoring_status": version.scoring_status,
                "answer_key_source": version.answer_key_source,
                "teacher_verified": version.teacher_verified,
                "item_count": len(_paper_items(db, version)),
                "submitted_count": sum(
                    1 for a in attempts if a.status == "submitted"
                    and a.attendance_status != "absent"),
                "pending_key_count": sum(
                    1 for a in attempts if a.grading_status == "pending_key"),
                "absent_count": sum(1 for s in statuses.values() if "absent" in s),
            })
        out.append({
            "assessment_id": assessment.assessment_id,
            "quiz_code": assessment.quiz_code,
            "title": assessment.title,
            "topic": assessment.topic,
            "scheduled_at": assessment.scheduled_at,
            "versions": vout,
        })
    return {"classroom_id": classroom.classroom_id, "total": len(out), "items": out}


def question_analysis(
    db: Session,
    assessment_version_id: str,
    paper_item_id: str = "",
    question_id: str = "",
) -> dict:
    version, assessment, classroom = _version_context(db, assessment_version_id)
    items = _paper_items(db, version)
    item = next((i for i in items if paper_item_id and i.item_id == paper_item_id), None)
    if item is None and question_id:
        matches = [i for i in items if i.question_id == question_id]
        if len(matches) > 1:
            raise ClassroomDiagnosisError(
                "同一题在卷内出现多次，请使用 paper_item_id 精确定位")
        item = matches[0] if matches else None
    if item is None:
        raise ClassroomDiagnosisNotFound("卷内题项不存在")

    attempts = _attempt_map(db, assessment_version_id)
    statuses = _submission_statuses(db, assessment_version_id)
    excluded: dict[str, int] = defaultdict(int)
    outcomes = []
    valid_scores: list[E.AnswerScore] = []
    evidence_refs = []
    reference_summaries = []
    for _membership, student in _members(db, classroom.classroom_id):
        attempt = attempts.get(student.student_id)
        if attempt is None and "absent" in statuses.get(student.student_id, set()):
            reason = "absent"
            score = None
        else:
            score = (_scores_by_item(db, attempt, [item]).get(item.item_id)
                     if attempt is not None else None)
            reason = _excluded_reason(version, attempt, score)
        reference_summaries.append(_inferred_summary(version, attempt, [score], [item]))
        if reason != "included":
            excluded[reason] += 1
            outcomes.append({
                "display_code": student.display_code,
                "status": reason,
                "correct": None,
                "awarded_score": None,
                "max_score": _round(float(item.score or 0)),
            })
            continue
        assert score is not None
        valid_scores.append(score)
        awarded = float(score.awarded_score)  # zero is intentionally retained
        maximum = _score_max(score, item)
        outcomes.append({
            "display_code": student.display_code,
            "status": "scored",
            "correct": bool(score.correct),
            "awarded_score": _round(awarded),
            "max_score": _round(maximum),
        })
        evidence_refs.append({
            "score_id": score.score_id,
            "attempt_id": score.attempt_id,
            "paper_item_id": item.item_id,
            "provenance": score.score_provenance,
            "authority_status": score.authority_status,
            "source_ref": score.source_ref,
        })

    correct = sum(1 for score in valid_scores if score.correct is True)
    zero_scores = sum(1 for score in valid_scores
                      if float(score.awarded_score) == 0.0)
    kp = _kp_id(item, valid_scores[0] if valid_scores else None)
    labels = _kp_labels(db, [kp])
    chapter_id = _chapter_id(item, valid_scores[0] if valid_scores else None)
    chapter_labels = _chapter_labels(db, [chapter_id])
    return {
        "classroom_id": classroom.classroom_id,
        "assessment_id": assessment.assessment_id,
        "assessment_version_id": version.assessment_version_id,
        "question": _snapshot_view(item),
        "knowledge_point": {"kp_id": kp, "name": labels.get(kp, kp)},
        "chapter": {"chapter_id": chapter_id,
                    "title": chapter_labels.get(chapter_id, chapter_id)},
        "denominator": len(valid_scores),
        "correct_count": correct,
        "incorrect_count": len(valid_scores) - correct,
        "zero_score_count": zero_scores,
        "correct_rate": _rate(correct, len(valid_scores)),
        "mean_awarded_score": _round(
            sum(float(s.awarded_score) for s in valid_scores) / len(valid_scores)
            if valid_scores else None),
        "excluded": dict(sorted(excluded.items())),
        "anonymous_outcomes": outcomes,
        "reference_diagnosis": aggregate_reference(reference_summaries),
        "evidence_refs": evidence_refs,
        "score_sources": _source_summary(evidence_refs),
        "resources": _resources(db, [kp]),
        "cause_analysis": {
            "status": "not_inferred",
            "note": "结构化得分只能说明对错/得分，不能据此编造共同错因。",
        },
        "privacy": "不返回任何学生原始答案或导入原始行。",
    }


def _student_attempt_absent(
    version: E.AssessmentVersion,
    assessment: E.Assessment,
    classroom: E.Classroom,
    student: E.Student,
    items: list[E.PaperItem],
) -> dict:
    return {
        "classroom_id": classroom.classroom_id,
        "assessment_id": assessment.assessment_id,
        "assessment_version_id": version.assessment_version_id,
        "student": {"student_id": student.student_id,
                    "display_code": student.display_code},
        "attendance_status": "absent",
        "grading_status": "not_applicable",
        "score_summary": {"scored_items": 0, "correct_items": 0,
                          "awarded_score": None, "max_score": None,
                          "score_rate": None},
        "items": [{**_snapshot_view(
            item, include_answer_reference=True, version=version),
            "evidence_status": "absent"}
                  for item in items],
        "chapters": [],
        "chapter_overview": [],
        "knowledge_points": [],
        "evidence_refs": [],
        "resources": [],
        "coverage": {
            "total_items": len(items),
            "reliable_scored_items": 0,
            "reference_scored_items": 0,
            "covered_kp_count": len({
                _kp_id(item) for item in items if _kp_id(item)}),
            "weak_kp_count": 0,
            "unmapped_item_count": sum(1 for item in items if not _kp_id(item)),
            "scoring_coverage_rate": 0.0,
            "allocation_rule": "只将得分计入题目的主知识点；辅助知识点仅展示关联，不重复计分。",
        },
        "retest_plan": {
            "ready": False,
            "reason": "学生缺考，不能据此生成针对性复测。",
            "chapter_ids": [],
            "chapters": [],
            "knowledge_points": [],
            "suggested_types": [],
            "item_count": 0,
        },
        "uncertainties": ["缺考不等于零分，也不进入任何正确率分母。"],
    }


def student_attempt(
    db: Session, student_id: str, assessment_version_id: str,
    attempt_id: str = "",
) -> dict:
    version, assessment, classroom = _version_context(db, assessment_version_id)
    student = _assert_member(db, classroom.classroom_id, student_id)
    items = _paper_items(db, version)
    attempt = db.get(E.Attempt, attempt_id) if attempt_id else None
    if attempt_id and (
        attempt is None
        or attempt.student_id != student_id
        or attempt.assessment_version_id != assessment_version_id
        or not attempt.is_effective
    ):
        raise ClassroomDiagnosisNotFound(
            "指定答卷不存在，或不属于该学生与固定试卷版本")
    if attempt is None:
        attempt = _attempt_for_student(db, student_id, assessment_version_id)
    statuses = _submission_statuses(db, assessment_version_id).get(student_id, set())
    if attempt is None:
        if "absent" in statuses:
            return _student_attempt_absent(version, assessment, classroom,
                                           student, items)
        raise ClassroomDiagnosisNotFound("该学生没有标准作答记录")
    if attempt.attendance_status == "absent":
        return _student_attempt_absent(version, assessment, classroom,
                                       student, items)

    answers = _answers_by_item(db, attempt, items)
    scores = _scores_by_item(db, attempt, items)
    result_items = []
    kp_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"items": 0, "correct": 0, "awarded": 0.0,
                 "maximum": 0.0, "evidence_refs": []})
    chapter_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"items": 0, "correct": 0, "awarded": 0.0,
                 "maximum": 0.0, "positions": [], "wrong_positions": [],
                 "evidence_refs": []})
    evidence_refs = []
    for item in items:
        answer = answers.get(item.item_id)
        score = scores.get(item.item_id)
        valid = _valid_score(version, attempt, score)
        reason = "included" if valid else _excluded_reason(version, attempt, score)
        kp = _kp_id(item, score)
        chapter_id = _chapter_id(item, score)
        evidence = None
        if valid and score is not None:
            awarded = float(score.awarded_score)  # keep zero
            maximum = _score_max(score, item)
            evidence = {
                "score_id": score.score_id,
                "attempt_id": attempt.attempt_id,
                "paper_item_id": item.item_id,
                "provenance": score.score_provenance,
                "authority_status": score.authority_status,
                "teacher_verified": score.teacher_verified,
                "chapter_id": chapter_id,
                "source_ref": score.source_ref,
            }
            evidence.update(item_evidence(item, score, attempt, assessment_id=version.assessment_id))
            evidence_refs.append(evidence)
            if kp:
                bucket = kp_buckets[kp]
                bucket["items"] += 1
                bucket["correct"] += 1 if score.correct is True else 0
                bucket["awarded"] += awarded
                bucket["maximum"] += maximum
                bucket["evidence_refs"].append(evidence)
            if chapter_id:
                chapter_bucket = chapter_buckets[chapter_id]
                chapter_bucket["items"] += 1
                chapter_bucket["correct"] += 1 if score.correct is True else 0
                chapter_bucket["awarded"] += awarded
                chapter_bucket["maximum"] += maximum
                chapter_bucket["positions"].append(item.position)
                if score.correct is False:
                    chapter_bucket["wrong_positions"].append(item.position)
                chapter_bucket["evidence_refs"].append(evidence)
        reference_score = None
        if (not valid and score is not None
                and score.authority_status == "inferred_unverified"
                and score.correct is not None
                and score.scoring_status not in PENDING_SCORE_STATUSES):
            reference_score = {
                "correct": bool(score.correct),
                "awarded_score": _round(float(score.awarded_score or 0)),
                "max_score": _round(_score_max(score, item)),
                "scoring_status": score.scoring_status,
                "score_provenance": score.score_provenance,
                "authority_status": score.authority_status,
                "source_ref": score.source_ref,
                "detail": score.detail or {},
                "note": "历史反推评分仅作答卷参考，不进入正式掌握率。",
            }
        if valid and score is not None and score.correct is True:
            note = "该题在当前可核验评分证据下判定正确。"
        elif valid:
            note = "该题在当前可核验评分证据下未答对；现有记录不足以确定具体错因。"
        elif reference_score is not None:
            note = "该题只有历史反推评分参考；可展示来源，但不能作为正式掌握结论。"
        else:
            note = "该题没有可进入诊断分母的评分证据。"
        question_view = _class_scoring_reference(
            db, item,
            _snapshot_view(item, include_answer_reference=True, version=version),
            [score] if score else [],
        )
        result_items.append({
            **question_view,
            "kp_id": kp,
            "chapter_id": chapter_id,
            "response_status": (answer.response_status if answer else "unanswered"),
            # This endpoint is explicitly scoped to the selected learner.
            "response": (answer.response if answer else None),
            "evidence_status": "scored" if valid else reason,
            "correct": (bool(score.correct) if valid and score is not None else None),
            "awarded_score": (_round(float(score.awarded_score))
                              if valid and score is not None else None),
            "max_score": (_round(_score_max(score, item))
                          if valid and score is not None else
                          question_view.get("max_score")),
            "reference_score": reference_score,
            "score_evidence": evidence,
            "cause_status": "not_inferred",
            "diagnostic_note": note,
        })

    all_mapped_kp_ids = {
        str(item.get("kp_id") or "") for item in result_items
        if item.get("kp_id")
    }
    labels = _kp_labels(db, set(kp_buckets.keys()) | all_mapped_kp_ids)
    for item in result_items:
        kp = str(item.get("kp_id") or "")
        item["knowledge_point"] = {
            "kp_id": kp,
            "name": labels.get(kp, kp) if kp else "未映射知识点",
            "allocation": (
                "主知识点计分"
                if kp else "未映射，不进入知识点统计"),
        }
    kp_rows = []
    for kp, bucket in kp_buckets.items():
        kp_rows.append({
            "kp_id": kp,
            "name": labels.get(kp, kp),
            "scored_items": bucket["items"],
            "correct_items": bucket["correct"],
            "accuracy": _rate(bucket["correct"], bucket["items"]),
            "awarded_score": _round(bucket["awarded"]),
            "max_score": _round(bucket["maximum"]),
            "score_rate": _rate(bucket["awarded"], bucket["maximum"]),
            "evidence_refs": bucket["evidence_refs"],
            "evidence_strength": summarize_independence(bucket["evidence_refs"]),
        })
    kp_rows.sort(key=lambda x: (
        x["accuracy"] is None,
        x["accuracy"] if x["accuracy"] is not None else 2,
        x["kp_id"],
    ))
    scored = [row for row in result_items if row["evidence_status"] == "scored"]
    reference_scored = [
        row for row in result_items if row.get("reference_score") is not None
    ]
    awarded_total = sum(float(row["awarded_score"]) for row in scored)
    maximum_total = sum(float(row["max_score"]) for row in scored)
    chapter_labels = _chapter_labels(db, {
        str(row.get("chapter_id") or "") for row in result_items
        if row.get("chapter_id")})
    for row in result_items:
        chapter_id = str(row.get("chapter_id") or "")
        row["chapter"] = {
            "chapter_id": chapter_id,
            "title": chapter_labels.get(chapter_id, chapter_id)
                     if chapter_id else "未映射章节",
            "allocation": "按章节计分" if chapter_id else "未映射，不进入章节统计",
        }
        if row.get("evidence_status") == "scored" and row.get("correct") is False:
            row["error_analysis"] = _item_error_analysis(row)
    chapter_rows = []
    for chapter_id, bucket in chapter_buckets.items():
        score_rate = _rate(bucket["awarded"], bucket["maximum"])
        if score_rate is None and bucket["wrong_positions"]:
            performance = "存在可核验错题，历史分值字段不完整"
        elif score_rate is None:
            performance = "本次无可评分证据"
        elif score_rate >= .75:
            performance = "本次表现较好"
        elif score_rate < .60:
            performance = "本次需要复习"
        else:
            performance = "本次表现一般"
        chapter_rows.append({
            "chapter_id": chapter_id,
            "title": chapter_labels.get(chapter_id, chapter_id),
            "scored_items": bucket["items"],
            "correct_items": bucket["correct"],
            "accuracy": _rate(bucket["correct"], bucket["items"]),
            "awarded_score": _round(bucket["awarded"]),
            "max_score": _round(bucket["maximum"]),
            "score_rate": score_rate,
            "question_positions": sorted(bucket["positions"]),
            "wrong_question_positions": sorted(bucket["wrong_positions"]),
            "performance": performance,
            "long_term_status": "待更多考试或复测确认",
            "observation": ("该结论只描述本次试卷的实际表现，不代表长期稳定掌握程度。"),
            "evidence_refs": bucket["evidence_refs"],
        })
    chapter_rows.sort(key=lambda row: row["chapter_id"])
    eligible_wrong_items = [
        row for row in result_items
        if row.get("evidence_status") == "scored"
        and row.get("correct") is False
        and (row.get("answer_reference") or {}).get("available")
        and (row.get("chapter_id") or row.get("kp_id"))
    ]
    evidence_weak_chapters = {
        str(row.get("chapter_id") or "") for row in eligible_wrong_items
        if row.get("chapter_id")
    }
    weak_chapters = [row for row in chapter_rows
                     if ((row["score_rate"] is not None and row["score_rate"] < .60)
                         or row["chapter_id"] in evidence_weak_chapters)]
    weak_chapter_ids = [row["chapter_id"] for row in weak_chapters]
    weak_kps = list(dict.fromkeys([
        *[row["kp_id"] for row in kp_rows
          if row["accuracy"] is not None and row["accuracy"] < 0.6],
        *[str(row.get("kp_id") or "") for row in eligible_wrong_items
          if row.get("kp_id")],
    ]))
    kp_overview = []
    for kp in sorted(all_mapped_kp_ids):
        mapped_items = [row for row in result_items if row.get("kp_id") == kp]
        formal = next((row for row in kp_rows if row["kp_id"] == kp), None)
        references = [
            row["reference_score"] for row in mapped_items
            if row.get("reference_score") is not None
        ]
        scored_items = int((formal or {}).get("scored_items") or 0)
        score_rate = (formal or {}).get("score_rate")
        if scored_items >= 2 and score_rate is not None:
            state = "表现较好" if score_rate >= .75 else (
                "需要复习" if score_rate < .6 else "证据不足")
        else:
            state = "证据不足"
        if scored_items == 1:
            observation = "仅有 1 道可靠评分题，证据有限；本卷表现可用于安排复习，但不能断定长期掌握。"
        elif scored_items > 1:
            observation = f"本卷有 {scored_items} 道可靠评分题；结论只描述本卷表现。"
        elif references:
            observation = (
                f"仅有 {len(references)} 道历史反推评分参考，"
                "不进入正式掌握判断。")
        else:
            observation = "本卷没有可进入正式诊断的可靠评分证据。"
        reference_max = sum(float(row.get("max_score") or 0) for row in references)
        reference_awarded = sum(
            float(row.get("awarded_score") or 0) for row in references)
        kp_overview.append({
            "kp_id": kp,
            "name": labels.get(kp, kp),
            "item_count": len(mapped_items),
            "question_positions": [row["position"] for row in mapped_items],
            "scored_items": scored_items,
            "awarded_score": (formal or {}).get("awarded_score"),
            "max_score": (formal or {}).get("max_score"),
            "score_rate": score_rate,
            "reference_scored_items": len(references),
            "reference_awarded_score": (
                _round(reference_awarded) if references else None),
            "reference_max_score": (
                _round(reference_max) if references else None),
            "reference_score_rate": (
                _rate(reference_awarded, reference_max)
                if references else None),
            "diagnosis_status": state,
            "needs_review": bool(
                score_rate is not None and score_rate < .6),
            "observation": observation,
            "allocation_rule": "仅主知识点计分；辅助知识点不重复计分。",
        })
    evidence_gaps = [
        {
            "kp_id": row["kp_id"],
            "name": row["name"],
            "question_positions": row["question_positions"],
            "message": row["observation"],
        }
        for row in kp_overview if row["diagnosis_status"] == "证据不足"
    ]
    if any(not row.get("kp_id") for row in result_items):
        positions = [
            row["position"] for row in result_items if not row.get("kp_id")
        ]
        evidence_gaps.append({
            "kp_id": "",
            "name": "未映射知识点",
            "question_positions": positions,
            "message": (
                f"第 {'、'.join(map(str, positions))} 题尚未建立知识点映射，"
                "不能纳入知识点诊断。"),
        })
    weak_rows = [row for row in kp_overview if row["kp_id"] in weak_kps]
    weak_positions = sorted({
        item["position"] for item in result_items
        if item in eligible_wrong_items
    })
    suggested_types = sorted({
        str(item.get("question_type") or "") for item in result_items
        if item.get("chapter_id") in weak_chapter_ids and item.get("correct") is False
        and item.get("question_type")
    })
    if not suggested_types and weak_chapter_ids:
        suggested_types = ["single_choice", "true_false"]
    legacy_kp_retest = not chapter_rows and bool(weak_kps)
    retest_ready = bool(weak_chapter_ids) or legacy_kp_retest
    retest_plan = {
        "ready": retest_ready,
        "reason": (
            "将根据本次错题生成同章节的新题，并自动完成题目检查。"
            if retest_ready else
            "当前没有适合生成复测的错题。"
        ),
        "chapter_ids": weak_chapter_ids,
        "chapters": [{"chapter_id": row["chapter_id"], "title": row["title"]}
                     for row in weak_chapters],
        "kp_ids": weak_kps,
        "knowledge_points": [
            {"kp_id": row["kp_id"], "name": row["name"]}
            for row in weak_rows
        ],
        "suggested_types": suggested_types,
        "item_count": (cfg.TARGETED_RETEST_DEFAULT_QUESTION_COUNT
                       if retest_ready else 0),
        "accuracy_threshold": cfg.TARGETED_RETEST_ACCURACY_THRESHOLD,
        "objective": (
            "使用不同题族的新题验证本卷低分章节，复测结果回写后再比较。"
            if retest_ready else
            "先补齐或核验评分证据，再形成可发布的复测目标。"
        ),
        "evidence_question_positions": weak_positions,
    }
    class_comparison = _class_comparison(
        db, classroom_id=classroom.classroom_id, version=version,
        items=items, selected_attempt=attempt)
    question_rates = {
        row["paper_item_id"]: row
        for row in class_comparison["question_correct_rates"]
    }
    for row in result_items:
        row["class_comparison"] = question_rates.get(row["paper_item_id"], {})
    wrong_count = sum(1 for row in scored if row.get("correct") is False)
    partial_count = sum(
        1 for row in scored
        if row.get("correct") is False
        and 0 < float(row.get("awarded_score") or 0) < float(row.get("max_score") or 0))
    payload = {
        "classroom_id": classroom.classroom_id,
        "assessment_id": assessment.assessment_id,
        "assessment_version_id": version.assessment_version_id,
        "student": {"student_id": student.student_id,
                    "display_code": student.display_code},
        "assessment": {
            "assessment_id": assessment.assessment_id,
            "title": assessment.title,
            "quiz_code": assessment.quiz_code,
            "topic": assessment.topic,
            "scheduled_at": assessment.scheduled_at,
            "version_no": version.version_no,
            "answer_key_source": version.answer_key_source,
            "teacher_verified": version.teacher_verified,
        },
        "attempt_id": attempt.attempt_id,
        "submitted_at": attempt.submitted_at,
        "attendance_status": attempt.attendance_status,
        "grading_status": attempt.grading_status,
        "historical_reported_result": {
            "score": _round(attempt.source_reported_total),
            "basis": "老师历史上报总分（原值保留，不参与逐题重算）",
        },
        "regraded_result": {
            "basis": "最新完成的可审计重评分运行",
            "score": _round(awarded_total) if scored else None,
            "max_score": _round(maximum_total) if scored else None,
            "coverage": _rate(len(scored), len(items)),
        },
        "score_summary": {
            "nominal_max_score": _round(sum(float(item.score or 0) for item in items)),
            "ungraded_item_count": len(items) - len(scored),
            "ungraded_max_score": _round(sum(float(item.score or 0) for item in items) - maximum_total),
            "grading_status": "partial" if scored and len(scored) < len(items) else "graded" if scored else "awaiting_grading",
            "scored_items": len(scored),
            "correct_items": sum(1 for row in scored if row["correct"] is True),
            "wrong_items": wrong_count,
            "partial_items": partial_count,
            "awarded_score": _round(awarded_total) if scored else None,
            "max_score": _round(maximum_total) if scored else None,
            "score_rate": _rate(awarded_total, maximum_total),
        },
        "items": result_items,
        "class_comparison": class_comparison,
        "chapters": chapter_rows,
        "chapter_overview": chapter_rows,
        "chapter_coverage": {
            "covered_chapter_count": len(chapter_rows),
            "weak_chapter_count": len(weak_chapter_ids),
        },
        "knowledge_points": kp_rows,
        "knowledge_point_overview": kp_overview,
        "inferred_summary": _inferred_summary(version, attempt, scores.values(), items),
        "unmapped_item_count": sum(1 for item in result_items if not item["kp_id"]),
        "weak_kp_ids": weak_kps,
        "weak_chapter_ids": weak_chapter_ids,
        "evidence_refs": evidence_refs,
        "score_sources": _source_summary(evidence_refs),
        "resources": _resources(db, weak_kps),
        "coverage": {
            "total_items": len(items),
            "reliable_scored_items": len(scored),
            "reference_scored_items": len(reference_scored),
            "covered_kp_count": len(all_mapped_kp_ids),
            "weak_kp_count": len(weak_kps),
            "unmapped_item_count": sum(
                1 for item in result_items if not item.get("kp_id")),
            "scoring_coverage_rate": _rate(len(scored), len(items)),
            "reference_coverage_rate": _rate(
                len(reference_scored), len(items)),
            "allocation_rule": "只将得分计入题目的主知识点；辅助知识点仅展示关联，不重复计分。",
        },
        "diagnostic_conclusions": {
            "weak_points": [{
                "chapter_id": row["chapter_id"],
                "name": row["title"],
                "question_positions": row["wrong_question_positions"],
                "message": (
                    (f"{row['title']}在本卷可评分题中的得分率为"
                     f"{round(float(row['score_rate']) * 100)}%；只说明本次表现，"
                     "不能据此断定长期稳定掌握程度。")
                    if row.get("score_rate") is not None else
                    (f"{row['title']}的第"
                     f"{'、'.join(map(str, row['wrong_question_positions']))}题有可靠判错证据；"
                     "历史分值字段不完整，因此不计算原卷章节得分率，但可据此生成新题复测。")
                ),
            } for row in weak_chapters],
            "evidence_gaps": [{
                "chapter_id": item.get("chapter_id") or "",
                "name": item.get("chapter", {}).get("title") or "未映射章节",
                "question_positions": [item["position"]],
                "message": (
                    f"第 {item['position']} 题未进入正式评分："
                    f"{item.get('diagnostic_note') or item.get('evidence_status')}"),
            } for item in result_items if item.get("evidence_status") != "scored"],
            "knowledge_point_evidence_gaps": evidence_gaps,
            "advice": [{
                "chapter_id": row["chapter_id"],
                "name": row["title"],
                "message": (
                    f"回看第 {'、'.join(map(str, row['wrong_question_positions']))} 题，"
                    f"复习{row['title']}的定义、适用条件与解题步骤，再用不同题族复测。"),
            } for row in weak_chapters],
            "explanation_status": "deterministic",
            "explanation_note": (
                "当前结论由可追溯评分与题目映射确定性生成；"
                "没有可靠证据时不调用 AI 编造错因。"),
        },
        "ai_analysis": _deterministic_ai_analysis({"items": result_items}),
        "retest_plan": retest_plan,
        "uncertainties": [
            "逐题得分不能单独证明概念、计算或审题等心理错因。",
            "正式指标排除未核验推断评分、待确认答案键与 correct=NULL；推断评分单列参考，不代表正式掌握率。",
        ],
    }
    from backend.app.services import targeted_retest
    retest_history = targeted_retest.list_cycles(
        db, source_attempt_id=attempt.attempt_id, student_id=student_id)
    payload["retest_history"] = retest_history
    payload["ai_analysis"] = targeted_retest.enrich_analysis(
        _v2_fallback("student_attempt", payload), retest_history)
    if retest_history:
        latest_retest = retest_history[-1]
        payload["retest_plan"]["latest_status"] = latest_retest["status"]
        payload["retest_plan"]["latest_round"] = latest_retest["round"]
        payload["retest_plan"]["next_round"] = latest_retest["round"] + 1
        if latest_retest["status"] in {"published", "opened"}:
            payload["retest_plan"].update({
                "ready": False,
                "reason": f"第{latest_retest['round']}轮复测已发布，等待学生完成。",
            })
        elif latest_retest["status"] == "scoring_pending":
            payload["retest_plan"].update({
                "ready": False,
                "reason": f"第{latest_retest['round']}轮复测仍有题目待可靠评分，暂不能继续发布。",
            })
        elif latest_retest["status"] == "not_passed":
            payload["retest_plan"].update({
                "ready": True, "recommended": True,
                "reason": "上一轮尚未达到80%，复习最新错题后可再次生成5道新题。",
            })
        elif latest_retest["status"] == "passed":
            payload["retest_plan"].update({
                "ready": True, "recommended": False,
                "reason": "最新一轮已达标，默认无需继续复测；教师仍可主动安排巩固测验。",
            })
    return payload


def latest_diagnosis(
    db: Session,
    *,
    actor_user_id: str,
    scope_type: str,
    classroom_id: str = "",
    student_id: str = "",
    assessment_version_id: str = "",
    paper_item_id: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """Return the latest persisted diagnosis for one exact selection."""
    stmt = select(E.Diagnosis).where(
        E.Diagnosis.user_id == actor_user_id,
        E.Diagnosis.scope_type == scope_type,
        E.Diagnosis.skill_version == SKILL_VERSION,
    )
    if classroom_id:
        stmt = stmt.where(E.Diagnosis.classroom_id == classroom_id)
    if student_id:
        stmt = stmt.where(E.Diagnosis.student_id == student_id)
    if assessment_version_id:
        stmt = stmt.where(
            E.Diagnosis.assessment_version_id == assessment_version_id)
    rows = db.execute(stmt.order_by(
        E.Diagnosis.created_at.desc(),
        E.Diagnosis.diagnosis_id.desc(),
    )).scalars().all()
    row = None
    for candidate in rows:
        payload_filters = (candidate.payload or {}).get("filters") or {}
        if scope_type == "student_history" and (
            str(payload_filters.get("date_from") or "") != str(date_from or "")
            or str(payload_filters.get("date_to") or "") != str(date_to or "")
            or str(payload_filters.get("assessment_version_id") or "")
               != str(assessment_version_id or "")
        ):
            continue
        row = candidate
        break
    if row is None:
        raise ClassroomDiagnosisNotFound("当前对象没有已保存的历史诊断")
    payload = row.payload or {}
    if paper_item_id:
        question = payload.get("question") or {}
        if question.get("paper_item_id") != paper_item_id:
            raise ClassroomDiagnosisNotFound("当前题目没有已保存的历史诊断")
    return {
        "diagnosis_id": row.diagnosis_id,
        "run_id": row.run_id,
        "scope_type": row.scope_type,
        "level": row.level,
        "created_at": row.created_at,
        "engine": row.engine,
        "skill_version": row.skill_version,
        "result": payload,
        "evidence_refs": row.evidence_refs or [],
        "uncertainties": row.uncertainties or [],
        "next_actions": row.next_actions or [],
    }


def _assessment_source_label(assessment: E.Assessment, attempt: E.Attempt) -> str:
    if attempt.retest_of_attempt_id or assessment.source == "retest":
        return "针对性复测"
    if assessment.source == "teacher_workbook" or attempt.source_import_run_id:
        return "历史课堂小测"
    return "在线考试"


def _history_ai_fallback(payload: dict[str, Any]) -> dict[str, Any]:
    concepts = list(payload.get("weak_concepts") or [])[:5]
    attempts = [row for row in payload.get("attempts") or []
                if row.get("score_rate") is not None]
    if not attempts:
        return {
            "status": "insufficient_evidence", "source": "evidence_rules",
            "summary": "当前范围内没有已评分的考试，暂时无法分析长期表现。",
            "concept_hypotheses": [],
            "recommendations": ["完成考试评分后再查看多卷分析。"],
            "limits": "",
        }
    if not concepts:
        return {
            "status": "no_wrong_items", "source": "evidence_rules",
            "summary": (
                f"已读取 {len(attempts)} 次考试，当前记录中没有错题。"
                "整体表现良好，可用后续不同题型继续检验掌握情况。"),
            "concept_hypotheses": [],
            "recommendations": ["保留当前记录，并用后续不同题型继续验证章节表现。"],
            "limits": "不同试卷难度可能不同，趋势仅用于观察成绩变化。",
        }
    chapter_names = list(dict.fromkeys(
        str(row.get("chapter") or "未映射章节") for row in concepts))
    repeated = [row for row in concepts if int(row.get("error_events") or 0) >= 2]
    repeated_note = (
        "其中" + "、".join(str(row.get("name")) for row in repeated[:3])
        + "在多个真实错题事件中出现，需要复测确认。"
        if repeated else "目前没有同一具体概念的多次错误事件，薄弱判断仍需复测确认。")
    concept_rows = [{
        "name": row.get("name") or "未映射概念",
        "chapter": row.get("chapter") or "未映射章节",
        "question_positions": row.get("question_refs") or [],
        "error_events": row.get("error_events") or 0,
        "evidence": row.get("analysis") or "由真实错题事件归纳，具体认知原因待复测确认。",
        "confidence": "待复测确认",
    } for row in concepts]
    return {
        "status": "evidence_grounded_fallback", "source": "evidence_rules",
        "summary": (
            f"{len(attempts)} 次考试中，错误主要分布在{'、'.join(chapter_names[:4])}。"
            + repeated_note),
        "concept_hypotheses": concept_rows,
        "recommendations": [
            f"按章节复习{'、'.join(chapter_names[:3])}，逐题核对题干条件、学生答案与参考解析。",
            "对错误题次较多的概念使用不同题族做专项练习，避免只记住原题答案。",
            "完成针对性复测后再比较同章节表现，并保留难度与覆盖范围差异说明。",
        ],
        "limits": "不同试卷难度可能不同，趋势仅用于观察成绩变化。",
    }


def _history_model_ai_analysis(
    db: Session, payload: dict[str, Any], run_id: str,
) -> dict[str, Any]:
    fallback = _history_ai_fallback(payload)
    wrong = list(payload.get("wrong_items") or [])
    if not wrong:
        return fallback
    evidence = []
    # Keep the most important evidence within a predictable context window.
    # Sending every historical stem and occurrence made otherwise valid JSON
    # responses hit the output limit on students with many exams.
    for item in wrong[:12]:
        occurrences = [{
            "exam": row.get("assessment_title"),
            "quiz_code": row.get("quiz_code"),
            "position": row.get("position"),
            "time": row.get("exam_time"),
            "student_answer": row.get("response"),
            "score": {"awarded": row.get("awarded_score"),
                      "maximum": row.get("max_score")},
        } for row in (item.get("occurrences") or [])]
        evidence.append({
            "question_id": item.get("question_id"),
            "stem": item.get("stem"),
            "question_type": item.get("question_type"),
            "chapter": (item.get("chapter") or {}).get("title"),
            "concept": (item.get("knowledge_point") or {}).get("name"),
            "correct_answer": (item.get("answer_reference") or {}).get("payload"),
            "explanation": (item.get("answer_reference") or {}).get("explanation"),
            "error_events": item.get("error_count"),
            "occurrences": occurrences,
        })
    messages = [{
        "role": "system",
        "content": (
            "你是机器学习课程助教。只能依据给出的跨试卷真实错题、作答、评分和时间证据做中文定性分析。"
            "不同题目属于同一章节不等于同一道重复错题；只有 evidence 中同一 question_id 的 occurrences "
            "才能称为同题重复错误。可把不同题归纳为同类概念问题，但必须列出对应考试或题号。"
            "不得编造掌握概率、资源链接或持续提高结论。严格返回 JSON 对象：summary、"
            "concept_hypotheses、recommendations、limits。concept_hypotheses 每项包含 name、chapter、"
            "question_positions、error_events、evidence；证据不足时明确写需要复测。"
            "summary 不超过 120 个汉字，概念保留 3 至 5 个，建议保留 2 至 4 条，表达简洁。"),
    }, {"role": "user", "content": json.dumps({
        "statistics": payload.get("statistics") or {},
        "attempts": [{k: row.get(k) for k in (
            "quiz_code", "title", "source_label", "exam_time", "score_rate")}
            for row in payload.get("attempts") or []],
        "wrong_items": evidence,
    }, ensure_ascii=False)}]
    try:
        response = model_provider.chat(
            db, messages=messages, skill_name="diagnose_classroom",
            prompt_version="student-history-errors-v1", run_id=run_id,
            temperature=0.1, max_tokens=1800, timeout=60.0)
        if response.get("finish_reason") == "length":
            raise model_provider.ModelUnavailable("truncated", "模型输出被截断")
        parsed = model_provider.parse_json_content(response.get("content") or "")
        summary = _public_ai_text(parsed.get("summary")) if isinstance(parsed, dict) else ""
        if not isinstance(parsed, dict) or not summary:
            raise model_provider.ModelUnavailable("invalid_output", "模型未返回有效结构")
        concepts = []
        for row in parsed.get("concept_hypotheses") or []:
            if not isinstance(row, dict):
                continue
            name = _public_ai_text(row.get("name") or row.get("concept"))
            if not name:
                continue
            concepts.append({
                "name": name,
                "chapter": str(row.get("chapter") or "").strip(),
                "question_positions": row.get("question_positions") or [],
                "error_events": int(row.get("error_events") or 0),
                "evidence": _public_ai_text(row.get("evidence") or "需要进一步练习确认。"),
            })
        advice = [
            row if isinstance(row, str) else str(
                row.get("message") or row.get("advice") or row.get("action") or "")
            for row in (parsed.get("recommendations") or [])
            if isinstance(row, (str, dict))
        ]
        return {
            "status": "completed", "source": "deepseek",
            "summary": summary,
            "concept_hypotheses": concepts or fallback["concept_hypotheses"],
            "recommendations": [_public_ai_text(row) for row in advice if _public_ai_text(row)] or fallback["recommendations"],
            "limits": _public_ai_text(parsed.get("limits")) or fallback["limits"],
        }
    except model_provider.ModelUnavailable as exc:
        return {**fallback, "model_status": exc.status,
                "model_message": exc.message}


def student_history(
    db: Session, student_id: str, classroom_id: str, *,
    date_from: str = "", date_to: str = "",
    assessment_version_id: str = "",
) -> dict:
    classroom = _classroom(db, classroom_id)
    student = _assert_member(db, classroom_id, student_id)
    versions = list(db.execute(
        select(E.AssessmentVersion, E.Assessment)
        .join(E.Assessment,
              E.Assessment.assessment_id == E.AssessmentVersion.assessment_id)
        .where(E.Assessment.classroom_id == classroom_id)
        .order_by(E.Assessment.scheduled_at, E.Assessment.quiz_code,
                  E.AssessmentVersion.version_no)
    ).all())
    histories: dict[str, list[dict]] = defaultdict(list)
    chapter_histories: dict[str, list[dict]] = defaultdict(list)
    attempt_summaries: list[dict[str, Any]] = []
    all_evidence: list[dict[str, Any]] = []
    wrong_events: list[dict[str, Any]] = []
    ungraded_events: list[dict[str, Any]] = []
    for version, assessment in versions:
        if (assessment_version_id
                and version.assessment_version_id != assessment_version_id):
            continue
        attempt = _attempt_for_student(db, student_id,
                                       version.assessment_version_id)
        if attempt is None or attempt.attendance_status == "absent":
            continue
        exam_time = str(assessment.scheduled_at or attempt.submitted_at or "")
        exam_day = exam_time[:10] if len(exam_time) >= 10 else ""
        if date_from and (not exam_day or exam_day < date_from):
            continue
        if date_to and (not exam_day or exam_day > date_to):
            continue
        items = _paper_items(db, version)
        answers = _answers_by_item(db, attempt, items)
        scores = _scores_by_item(db, attempt, items)
        per_kp: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"scored": 0, "correct": 0, "awarded": 0.0,
                     "maximum": 0.0, "evidence": []})
        per_chapter: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"scored": 0, "correct": 0, "awarded": 0.0,
                     "maximum": 0.0, "evidence": []})
        overall_scored = 0
        overall_correct = 0
        overall_awarded = 0.0
        overall_maximum = 0.0
        attempt_wrong = 0
        attempt_ungraded = 0
        for item in items:
            answer = answers.get(item.item_id)
            score = scores.get(item.item_id)
            if not _valid_score(version, attempt, score):
                reason = _excluded_reason(version, attempt, score)
                if reason not in {"no_submission", "absent", "superseded"}:
                    question_view = _snapshot_view(
                        item, include_answer_reference=True, version=version)
                    ungraded_events.append({
                        **question_view,
                        "attempt_id": attempt.attempt_id,
                        "assessment_id": assessment.assessment_id,
                        "assessment_version_id": version.assessment_version_id,
                        "assessment_title": assessment.title,
                        "quiz_code": assessment.quiz_code,
                        "source": assessment.source,
                        "source_label": _assessment_source_label(assessment, attempt),
                        "exam_time": exam_time,
                        "response": answer.response if answer else None,
                        "response_status": answer.response_status if answer else "unanswered",
                        "evidence_status": reason,
                        "diagnostic_note": "该题没有可进入正式统计的可靠评分，未当作错题。",
                    })
                    attempt_ungraded += 1
                continue
            assert score is not None
            overall_scored += 1
            overall_correct += 1 if score.correct is True else 0
            overall_awarded += float(score.awarded_score)
            overall_maximum += _score_max(score, item)
            kp = _kp_id(item, score)
            chapter_id = _chapter_id(item, score)
            evidence = {"score_id": score.score_id,
                        "attempt_id": attempt.attempt_id,
                        "paper_item_id": item.item_id,
                        "authority_status": score.authority_status}
            evidence.update(item_evidence(item, score, attempt, assessment_id=version.assessment_id))
            all_evidence.append(evidence)
            if kp:
                bucket = per_kp[kp]
                bucket["scored"] += 1
                bucket["correct"] += 1 if score.correct is True else 0
                bucket["awarded"] += float(score.awarded_score)
                bucket["maximum"] += _score_max(score, item)
                bucket["evidence"].append(evidence)
            if chapter_id:
                chapter_bucket = per_chapter[chapter_id]
                chapter_bucket["scored"] += 1
                chapter_bucket["correct"] += 1 if score.correct is True else 0
                chapter_bucket["awarded"] += float(score.awarded_score)
                chapter_bucket["maximum"] += _score_max(score, item)
                chapter_bucket["evidence"].append(evidence)
            if score.correct is False:
                question_view = _snapshot_view(
                    item, include_answer_reference=True, version=version)
                if isinstance(score, E.AnswerRegrade):
                    key_question = db.execute(select(E.Question).where(
                        E.Question.question_id == score.question_id,
                        E.Question.question_version == score.answer_key_question_version,
                    )).scalars().first()
                    if key_question is not None:
                        question_view["answer_reference"] = {
                            "available": bool(key_question.answer_payload),
                            "payload": key_question.answer_payload or None,
                            "explanation": key_question.explanation or "",
                            "rubric": key_question.rubric or {},
                            "authority_status": score.authority_status,
                            "authority_label": (
                                "AI独立解题与规则校验答案"
                                if score.authority_status == "ai_auto_checked"
                                else "答案仍不可用于正式评分"),
                            "source": score.source_ref,
                            "teacher_verified": False,
                        }
                wrong_events.append({
                    **question_view,
                    "attempt_id": attempt.attempt_id,
                    "assessment_id": assessment.assessment_id,
                    "assessment_version_id": version.assessment_version_id,
                    "assessment_title": assessment.title,
                    "quiz_code": assessment.quiz_code,
                    "source": assessment.source,
                    "source_label": _assessment_source_label(assessment, attempt),
                    "exam_time": exam_time,
                    "submitted_at": attempt.submitted_at,
                    "kp_id": kp,
                    "chapter_id": chapter_id,
                    "response": answer.response if answer else None,
                    "response_status": answer.response_status if answer else "unanswered",
                    "evidence_status": "scored",
                    "correct": False,
                    "awarded_score": _round(float(score.awarded_score)),
                    "max_score": _round(_score_max(score, item)),
                    "score_evidence": evidence,
                    "family_id": str(getattr(score, "family_id", "") or
                                     (item.snapshot or {}).get("question_family_id") or ""),
                })
                attempt_wrong += 1
        total_scored = overall_scored
        total_correct = overall_correct
        total_awarded = overall_awarded
        total_maximum = overall_maximum
        attempt_summaries.append({
            "attempt_id": attempt.attempt_id,
            "assessment_id": assessment.assessment_id,
            "assessment_version_id": version.assessment_version_id,
            "paper_id": version.paper_id,
            "quiz_code": assessment.quiz_code,
            "title": assessment.title,
            "scheduled_at": assessment.scheduled_at,
            "submitted_at": attempt.submitted_at,
            "exam_time": exam_time,
            "source": assessment.source,
            "source_label": _assessment_source_label(assessment, attempt),
            "scored_items": total_scored,
            "correct_items": total_correct,
            "wrong_items": attempt_wrong,
            "ungraded_items": attempt_ungraded,
            "awarded_score": _round(total_awarded) if total_scored else None,
            "max_score": _round(total_maximum) if total_scored else None,
            "accuracy": _rate(total_correct, total_scored),
            "score_rate": _rate(total_awarded, total_maximum),
            "grading_status": attempt.grading_status,
            "questions": [_snapshot_view(item) for item in items],
            "inferred_summary": _inferred_summary(version, attempt, scores.values(), items),
        })
        for kp, bucket in per_kp.items():
            histories[kp].append({
                "assessment_id": assessment.assessment_id,
                "assessment_version_id": version.assessment_version_id,
                "quiz_code": assessment.quiz_code,
                "title": assessment.title,
                "scheduled_at": assessment.scheduled_at,
                "submitted_at": attempt.submitted_at,
                "scored_items": bucket["scored"],
                "correct_items": bucket["correct"],
                "accuracy": _rate(bucket["correct"], bucket["scored"]),
                "score_rate": _rate(bucket["awarded"], bucket["maximum"]),
                "evidence_refs": bucket["evidence"],
            })
        for chapter_id, bucket in per_chapter.items():
            chapter_histories[chapter_id].append({
                "assessment_id": assessment.assessment_id,
                "assessment_version_id": version.assessment_version_id,
                "quiz_code": assessment.quiz_code,
                "title": assessment.title,
                "scheduled_at": assessment.scheduled_at,
                "submitted_at": attempt.submitted_at,
                "scored_items": bucket["scored"],
                "correct_items": bucket["correct"],
                "accuracy": _rate(bucket["correct"], bucket["scored"]),
                "awarded_score": _round(bucket["awarded"]),
                "max_score": _round(bucket["maximum"]),
                "score_rate": _rate(bucket["awarded"], bucket["maximum"]),
                "evidence_refs": bucket["evidence"],
            })
    attempt_summaries.sort(key=lambda row: (
        str(row.get("exam_time") or ""), str(row.get("assessment_version_id") or "")))
    for order, row in enumerate(attempt_summaries, start=1):
        row["exam_order"] = order

    all_kp_ids = set(histories.keys()) | {
        str(row.get("kp_id") or "") for row in wrong_events if row.get("kp_id")}
    labels = _kp_labels(db, all_kp_ids)
    comparable, insufficient = [], []
    for kp, series in histories.items():
        row = {"kp_id": kp, "name": labels.get(kp, kp),
               "assessment_count": len({x["assessment_id"] for x in series}),
               "series": series,
               "evidence_strength": summarize_independence([ref for x in series for ref in x["evidence_refs"]])}
        (comparable if row["assessment_count"] >= 2 else insufficient).append(row)
    comparable.sort(key=lambda x: x["kp_id"])
    insufficient.sort(key=lambda x: x["kp_id"])
    latest_weak = []
    for row in comparable:
        last = row["series"][-1]
        if last["accuracy"] is not None and last["accuracy"] < 0.6:
            latest_weak.append(row["kp_id"])
    all_chapter_ids = set(chapter_histories.keys()) | {
        str(row.get("chapter_id") or "") for row in wrong_events
        if row.get("chapter_id")}
    chapter_labels = _chapter_labels(db, all_chapter_ids)
    chapter_rows = []
    for chapter_id, series in chapter_histories.items():
        series.sort(key=lambda point: (
            str(point.get("submitted_at") or point.get("scheduled_at") or ""),
            str(point.get("assessment_version_id") or "")))
        assessment_count = len({x["assessment_id"] for x in series})
        chapter_rows.append({
            "chapter_id": chapter_id,
            "title": chapter_labels.get(chapter_id, chapter_id),
            "assessment_count": assessment_count,
            "series": series,
            "comparison_status": ("可比较" if assessment_count >= 2
                                  else "仅一次测量，长期趋势待确认"),
            "evidence_strength": summarize_independence(
                [ref for point in series for ref in point["evidence_refs"]]),
        })
    chapter_rows.sort(key=lambda row: row["chapter_id"])

    for event in [*wrong_events, *ungraded_events]:
        kp = str(event.get("kp_id") or "")
        chapter_id = str(event.get("chapter_id") or "")
        event["knowledge_point"] = {
            "kp_id": kp, "name": labels.get(kp, kp) if kp else "未映射知识点"}
        event["chapter"] = {
            "chapter_id": chapter_id,
            "title": chapter_labels.get(chapter_id, chapter_id)
                     if chapter_id else "未映射章节"}
        if event in wrong_events:
            event["error_analysis"] = _item_error_analysis(event)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in wrong_events:
        # Only the immutable logical question id is a reliable same-question key.
        # A shared chapter/KP/family id alone never merges different questions.
        grouped[str(event.get("question_id") or event.get("paper_item_id"))].append(event)
    wrong_items: list[dict[str, Any]] = []
    for group_key, occurrences in grouped.items():
        occurrences.sort(key=lambda row: (
            str(row.get("exam_time") or ""), str(row.get("attempt_id") or "")))
        representative = dict(occurrences[-1])
        representative.update({
            "repeat_group_key": group_key,
            "repeat_group_basis": "同一不可变题目标识" if len(occurrences) > 1 else "单次错题事件",
            "error_count": len(occurrences),
            "occurrence_count": len(occurrences),
            "repeated": len(occurrences) >= 2,
            "occurrences": occurrences,
            "assessment_titles": list(dict.fromkeys(
                str(row.get("assessment_title") or "") for row in occurrences)),
        })
        wrong_items.append(representative)
    wrong_items.sort(key=lambda row: (
        not bool(row.get("repeated")), -int(row.get("error_count") or 0),
        str(row.get("exam_time") or "")))

    concept_buckets: dict[str, dict[str, Any]] = {}
    for event in wrong_events:
        kp = str(event.get("kp_id") or "")
        key = kp or f"unmapped:{event.get('question_id')}"
        row = concept_buckets.setdefault(key, {
            "kp_id": kp,
            "name": labels.get(kp, kp) if kp else "未映射知识点",
            "chapter_id": str(event.get("chapter_id") or ""),
            "chapter": (event.get("chapter") or {}).get("title") or "未映射章节",
            "error_events": 0, "assessment_ids": set(),
            "question_ids": set(), "question_refs": [],
        })
        row["error_events"] += 1
        row["assessment_ids"].add(str(event.get("assessment_id") or ""))
        row["question_ids"].add(str(event.get("question_id") or ""))
        row["question_refs"].append(
            f"{event.get('quiz_code') or event.get('assessment_title')} 第{event.get('position')}题")
    weak_concepts = []
    for row in concept_buckets.values():
        assessment_count = len({value for value in row.pop("assessment_ids") if value})
        question_count = len({value for value in row.pop("question_ids") if value})
        row["assessment_count"] = assessment_count
        row["distinct_question_count"] = question_count
        row["question_refs"] = list(dict.fromkeys(row["question_refs"]))
        row["analysis"] = (
            f"在 {assessment_count} 次考试的 {question_count} 道具体题目中出现 "
            f"{row['error_events']} 次错误事件；这是同类概念证据，"
            "不同题目没有被伪装成同一道重复错题。")
        weak_concepts.append(row)
    weak_concepts.sort(key=lambda row: (
        -int(row["error_events"]), -int(row["assessment_count"]), str(row["name"])))

    valid_attempt_rates = [float(row["score_rate"]) for row in attempt_summaries
                           if row.get("score_rate") is not None]
    total_scored = sum(int(row.get("scored_items") or 0) for row in attempt_summaries)
    total_wrong = len(wrong_events)
    weak_kp_ids = [str(row["kp_id"]) for row in weak_concepts if row.get("kp_id")]
    weak_chapter_ids = list(dict.fromkeys(
        str(row["chapter_id"]) for row in weak_concepts if row.get("chapter_id")))
    retest_ready = bool(weak_kp_ids and wrong_events)
    filters = {"date_from": date_from, "date_to": date_to,
               "assessment_version_id": assessment_version_id}
    statistics = {
        "average_score_rate": (_round(sum(valid_attempt_rates) / len(valid_attempt_rates))
                               if valid_attempt_rates else None),
        "participated_assessments": len(attempt_summaries),
        "scored_item_events": total_scored,
        "wrong_item_events": total_wrong,
        "unique_wrong_question_count": len(wrong_items),
        "ungraded_item_events": len(ungraded_events),
        "average_rule": "先计算每次考试可评分题目的得分率，再对有有效评分的考试等权平均；缺考和无有效评分考试不进入均值。",
    }
    payload = {
        "classroom_id": classroom.classroom_id,
        "student": {"student_id": student.student_id,
                    "display_code": student.display_code},
        "filters": filters,
        "statistics": statistics,
        "attempts": attempt_summaries,
        "reference_diagnosis": aggregate_reference([x["inferred_summary"] for x in attempt_summaries]),
        "comparable_knowledge_points": comparable,
        "insufficient_knowledge_points": insufficient,
        "chapters": chapter_rows,
        "comparable_chapters": [row for row in chapter_rows
                                if row["assessment_count"] >= 2],
        "single_assessment_chapters": [row for row in chapter_rows
                                       if row["assessment_count"] < 2],
        "wrong_items": wrong_items,
        "wrong_events": wrong_events,
        "ungraded_items": ungraded_events,
        "weak_concepts": weak_concepts[:8],
        "weak_kp_ids": weak_kp_ids,
        "weak_chapter_ids": weak_chapter_ids,
        "evidence_refs": all_evidence,
        "score_sources": _source_summary(all_evidence),
        "resources": _resources(db, weak_kp_ids or latest_weak),
        "ai_analysis": _history_ai_fallback({
            "attempts": attempt_summaries,
            "weak_concepts": weak_concepts,
            "statistics": statistics,
        }),
        "retest_plan": {
            "ready": retest_ready,
            "reason": (
                "将根据历次错题生成同类新题，并自动完成题目检查。"
                if retest_ready else "当前没有适合生成复测的薄弱概念。"),
            "chapter_ids": weak_chapter_ids,
            "chapters": [{"chapter_id": chapter_id,
                          "title": chapter_labels.get(chapter_id, chapter_id)}
                         for chapter_id in weak_chapter_ids],
            "kp_ids": weak_kp_ids,
            "knowledge_points": [{"kp_id": row["kp_id"], "name": row["name"]}
                                 for row in weak_concepts[:5] if row.get("kp_id")],
            "item_count": (cfg.TARGETED_RETEST_DEFAULT_QUESTION_COUNT
                           if retest_ready else 0),
            "anchor_attempt_id": (wrong_events[-1].get("attempt_id") if wrong_events else ""),
        },
        "comparison_rule": (
            "趋势使用每次考试可评分题目的得分率；原始总分不比较。"
            "不同试卷难度、题型与覆盖范围可能不同，变化只描述成绩表现，不能单独证明学习能力提高。"
        ),
        "raw_total_comparison": None,
        "uncertainties": [
            "不同试卷难度和题型仍可能不同，趋势只表示现有可比证据。",
            "只有一次测量的章节仍展示本次实际表现，但不宣称长期稳定掌握。",
        ],
    }
    payload["ai_analysis"] = _v2_fallback("student_history", payload)
    return payload


def _distribution(rates: list[float]) -> list[dict]:
    bins = [
        ("0-49", 0.0, 0.5),
        ("50-59", 0.5, 0.6),
        ("60-69", 0.6, 0.7),
        ("70-79", 0.7, 0.8),
        ("80-89", 0.8, 0.9),
        ("90-100", 0.9, 1.0000001),
    ]
    return [{"label": label,
             "count": sum(1 for value in rates if low <= value < high)}
            for label, low, high in bins]


def _class_ai_fallback(payload: dict[str, Any]) -> dict[str, Any]:
    top = list(payload.get("high_frequency_errors") or [])[:5]
    if not top:
        questions = [row for row in payload.get("question_analysis") or []
                     if int(row.get("denominator") or 0) > 0]
        participants = int((payload.get("score_overview") or {}).get(
            "comparable_student_count") or 0)
        if participants and questions and all(
                int(row.get("incorrect_count") or 0) == 0 for row in questions):
            return {
                "status": "no_wrong_items", "source": "evidence_rules",
                "summary": (
                    f"本次共有 {participants} 名学生完成有效评分，"
                    "全班已评分题目全部答对，整体表现良好。"
                    "建议用后续不同题型继续检验掌握情况。"),
                "common_errors": [], "concept_hypotheses": [],
                "recommendations": ["保留本次成绩，并在后续练习中增加不同题型检验。"],
                "limits": "",
            }
        return {
            "status": "insufficient_evidence", "source": "evidence_rules",
            "summary": "当前试卷没有足够的已评分作答，暂时无法归纳班级共性问题。",
            "common_errors": [], "concept_hypotheses": [],
            "recommendations": ["完成答卷评分后重新查看班级分析。"],
            "limits": "",
        }
    common_errors = []
    concepts: dict[str, dict[str, Any]] = {}
    for item in top:
        position = item.get("position")
        chapter = (item.get("chapter") or {}).get("title") or "未映射章节"
        kp = (item.get("knowledge_point") or {}).get("name") or "本题对应概念"
        wrong_answer = item.get("high_frequency_wrong_answer") or "作答与正确答案不一致"
        common_errors.append({
            "label": f"第{position}题：{kp}",
            "question_positions": [position],
            "evidence": (f"{item.get('incorrect_count') or 0}/{item.get('denominator') or 0} 人答错，"
                         f"高频错误作答为 {wrong_answer}。"),
        })
        row = concepts.setdefault(kp, {
            "name": kp, "chapter": chapter, "question_positions": [],
            "evidence": [], "level": "待复测确认",
        })
        row["question_positions"].append(position)
        row["evidence"].append(
            f"第{position}题正确率 {round(float(item.get('correct_rate') or 0) * 100)}%")
    positions = "、".join(f"第{row.get('position')}题" for row in top[:3])
    chapters = list(dict.fromkeys(
        (row.get("chapter") or {}).get("title") or "未映射章节" for row in top))
    return {
        "status": "evidence_grounded_fallback", "source": "evidence_rules",
        "summary": (f"本次班级失分主要集中在{positions}，涉及{'、'.join(chapters[:4])}。"
                    "建议教师优先讲解这些题目，并安排同类练习检查理解情况。"),
        "common_errors": common_errors,
        "concept_hypotheses": list(concepts.values())[:5],
        "recommendations": [
            f"课堂优先讲解{positions}，对照高频错误作答与正确答案说明关键差异。",
            f"围绕{'、'.join(chapters[:3])}安排概念对比和一组不同题族练习。",
            "生成班级复测，验证讲解后同类概念的正确率是否改善。",
        ],
        "limits": "结论用于本次试卷讲评，不代表每名学生的具体原因。",
    }


def _class_model_ai_analysis(
    db: Session, payload: dict[str, Any], run_id: str,
) -> dict[str, Any]:
    fallback = _class_ai_fallback(payload)
    top = list(payload.get("high_frequency_errors") or [])[:5]
    if not top:
        return fallback
    evidence = [{
        "position": item.get("position"),
        "question_type": item.get("question_type"),
        "chapter": (item.get("chapter") or {}).get("title"),
        "concept": (item.get("knowledge_point") or {}).get("name"),
        "stem": item.get("stem"),
        "correct_count": item.get("correct_count"),
        "incorrect_count": item.get("incorrect_count"),
        "denominator": item.get("denominator"),
        "correct_rate": item.get("correct_rate"),
        "high_frequency_wrong_answer": item.get("high_frequency_wrong_answer"),
        "correct_answer": (item.get("answer_reference") or {}).get("payload"),
    } for item in top]
    messages = [{
        "role": "system",
        "content": (
            "你是机器学习课程助教。只能依据给出的同一固定试卷班级聚合证据做中文教学诊断。"
            "不得编造学生作答、心理错因、资源链接或掌握概率。每项判断必须引用题号。"
            "严格返回 JSON：summary、common_errors、concept_hypotheses、recommendations、limits。"
            "common_errors 每项含 label、question_positions、evidence；concept_hypotheses 每项含"
            "name、chapter、question_positions、evidence、level，level 只能是高、中等、待复测确认。"),
    }, {"role": "user", "content": json.dumps({
        "assessment": payload.get("assessment"),
        "participation": payload.get("participation"),
        "top_wrong_questions": evidence,
    }, ensure_ascii=False)}]
    try:
        response = model_provider.chat(
            db, messages=messages, skill_name="diagnose_classroom",
            prompt_version="class-assessment-errors-v1", run_id=run_id,
            temperature=0.1, max_tokens=1400, timeout=60.0)
        if response.get("finish_reason") == "length":
            raise model_provider.ModelUnavailable("truncated", "模型输出被截断")
        parsed = model_provider.parse_json_content(response.get("content") or "")
        summary = _public_ai_text(parsed.get("summary")) if isinstance(parsed, dict) else ""
        if not isinstance(parsed, dict) or not summary:
            raise model_provider.ModelUnavailable("invalid_output", "模型未返回有效结构")
        common_errors = []
        for row in parsed.get("common_errors") or []:
            if not isinstance(row, dict):
                continue
            label = _public_ai_text(row.get("label") or row.get("name"))
            evidence_text = _public_ai_text(row.get("evidence") or row.get("description"))
            if not label or not evidence_text:
                continue
            common_errors.append({
                "label": label,
                "question_positions": row.get("question_positions") or [],
                "evidence": evidence_text,
            })
        concepts = []
        for row in parsed.get("concept_hypotheses") or []:
            if not isinstance(row, dict):
                continue
            name = _public_ai_text(row.get("name") or row.get("concept"))
            if not name:
                continue
            concepts.append({
                "name": name,
                "chapter": str(row.get("chapter") or "").strip(),
                "question_positions": row.get("question_positions") or [],
                "evidence": _public_ai_text(row.get("evidence")),
            })
        advice = [
            (row if isinstance(row, str) else str(
                row.get("message") or row.get("advice") or row.get("action") or ""))
            for row in (parsed.get("recommendations") or [])
            if isinstance(row, (str, dict))
        ]
        advice = [_public_ai_text(row) for row in advice if _public_ai_text(row)][:4]
        return {
            "status": "completed", "source": "deepseek",
            "summary": summary,
            "common_errors": common_errors or fallback["common_errors"],
            "concept_hypotheses": concepts or fallback["concept_hypotheses"],
            "recommendations": advice or fallback["recommendations"],
            "limits": _public_ai_text(parsed.get("limits")) or fallback["limits"],
        }
    except model_provider.ModelUnavailable as exc:
        return {**fallback, "model_status": exc.status,
                "model_message": exc.message}


CLASS_VIEW_VERSION = 2


def _class_scoring_reference(
    db: Session, item: E.PaperItem, original: dict[str, Any],
    scores: list[E.AnswerScore | E.AnswerRegrade],
) -> dict[str, Any]:
    """Pair the displayed question with the exact key version used to score it."""
    view = dict(original)
    regrades = [score for score in scores if isinstance(score, E.AnswerRegrade)]
    if not regrades:
        view["is_scoring_revision"] = False
        view["scoring_reference_status"] = (
            "available" if (view.get("answer_reference") or {}).get("available") else "missing")
        return view
    if len(regrades) != len(scores):
        view["scoring_reference_status"] = "version_conflict"
        view["answer_reference"] = {"available": False, "payload": None,
                                    "explanation": "", "reason": "评分版本不一致，参考答案待核对。"}
        return view
    versions = {score.answer_key_question_version for score in regrades}
    if len(versions) != 1:
        view["scoring_reference_status"] = "version_conflict"
        view["answer_reference"] = {"available": False, "payload": None,
                                    "explanation": "", "reason": "评分版本不一致，参考答案待核对。"}
        return view
    key_version = next(iter(versions))
    question = db.execute(select(E.Question).where(
        E.Question.question_id == item.question_id,
        E.Question.question_version == key_version,
    )).scalars().first()
    payload = question.answer_payload if question else {}
    option_keys = {str(row.get("key") or "").strip().upper()
                   for row in (question.options or []) if isinstance(row, dict)
                   and row.get("key")} if question else set()
    invalid_choice_key = bool(question and question.question_type in {
        "single_choice", "multiple_choice", "multi_choice", "matching"}
        and (not option_keys or not _answer_keys(payload)
             or not set(_answer_keys(payload)).issubset(option_keys)))
    if not payload or invalid_choice_key or any(
        (score.detail or {}).get("expected") is not None
        and set(_answer_keys((score.detail or {}).get("expected")))
        != set(_answer_keys(payload))
        for score in regrades
    ):
        view["scoring_reference_status"] = "missing"
        view["answer_reference"] = {"available": False, "payload": None,
                                    "explanation": "", "reason": "评分答案缺失或与记录不一致，待核对。"}
        return view
    assert question is not None
    original_view = dict(original)
    view.update({
        "stem": question.stem_zh or question.stem,
        "options": question.options or [],
        "question_type": question.question_type or original.get("question_type"),
        "scoring_question_version": key_version,
        "is_scoring_revision": bool(key_version != item.question_version),
        "scoring_reference_status": "available",
        "original_question": original_view if key_version != item.question_version else None,
        "answer_reference": {
            "available": True, "payload": payload,
            "explanation": question.explanation or "",
            "authority_status": regrades[0].authority_status,
            "authority_label": "AI校验参考答案" if not regrades[0].teacher_verified else "教师核验答案",
            "question_version": key_version,
        },
    })
    return view


def _class_answer_pattern(
    score: E.AnswerScore | E.AnswerRegrade, answer: E.Answer | None,
    option_keys: set[str],
) -> tuple[str, str]:
    """Return (label, evidence class), keeping uncertain mappings separate."""
    if answer is None or answer.response_status == "unanswered" or (
        isinstance(answer.response, dict) and answer.response.get("answered") is False
    ):
        return "未作答", "unanswered"
    if isinstance(score, E.AnswerRegrade):
        detail = score.detail or {}
        selected = _answer_keys(detail.get("given"))
        mappings = (detail.get("response_mapping") or {}).get("items") or []
        reliable = bool(selected and all(key in option_keys for key in selected)
                        and mappings and len(mappings) == len(selected)
                        and all(isinstance(row, dict) for row in mappings)
                        and all(
            row.get("method") == "normalized_exact" or (
                row.get("method") == "unique_fuzzy_option_text"
                and float(row.get("score") or 0) >= .70
                and float(row.get("margin") or 0) >= .15)
            for row in mappings))
        if reliable:
            return ", ".join(sorted(set(selected))), "reliable"
        return "已作答，选项对应待核对", "unmapped"
    selected, reliable = _v2_choice_keys({"options": [{"key": key} for key in option_keys],
                                           "response": answer.response})
    if reliable and selected:
        return ", ".join(selected), "reliable"
    return "已作答，选项对应待核对", "unmapped"


def class_assessment(
    db: Session,
    classroom_id: str,
    assessment_version_id: str,
    selected_student_id: str = "",
) -> dict:
    version, assessment, classroom = _version_context(db, assessment_version_id)
    if classroom.classroom_id != classroom_id:
        raise ClassroomDiagnosisError("考核版本不属于指定班级")
    members = _members(db, classroom_id)
    attempts = _attempt_map(db, assessment_version_id)
    statuses = _submission_statuses(db, assessment_version_id)
    items = _paper_items(db, version)
    student_rows = []
    all_valid: list[tuple[E.AnswerScore, E.PaperItem, E.Attempt]] = []
    selected_item_results: list[dict] = []
    for _membership, student in members:
        attempt = attempts.get(student.student_id)
        if (attempt is None and "absent" in statuses.get(student.student_id, set())) \
                or (attempt is not None and attempt.attendance_status == "absent"):
            participation = "absent"
            score_rows: dict[str, E.AnswerScore] = {}
        elif attempt is None:
            participation = "no_submission"
            score_rows = {}
        elif _attempt_is_pending(attempt) or version.scoring_status == "pending_key":
            participation = "pending_key"
            score_rows = _scores_by_item(db, attempt, items)
        else:
            score_rows = _scores_by_item(db, attempt, items)
            participation = "submitted"
        scored_items = 0
        correct_items = 0
        awarded = 0.0
        maximum = 0.0
        per_item = []
        if attempt is not None:
            for item in items:
                score = score_rows.get(item.item_id)
                if not _valid_score(version, attempt, score):
                    if selected_student_id == student.student_id:
                        per_item.append({
                            "paper_item_id": item.item_id,
                            "position": item.position,
                            "status": _excluded_reason(version, attempt, score),
                            "correct": None,
                        })
                    continue
                assert score is not None
                scored_items += 1
                correct_items += 1 if score.correct is True else 0
                awarded += float(score.awarded_score)  # zero retained
                maximum += _score_max(score, item)
                all_valid.append((score, item, attempt))
                if selected_student_id == student.student_id:
                    per_item.append({
                        "paper_item_id": item.item_id,
                        "position": item.position,
                        "status": "scored",
                        "correct": bool(score.correct),
                        "awarded_score": _round(float(score.awarded_score)),
                        "max_score": _round(_score_max(score, item)),
                    })
        if scored_items > 0:
            participation = "submitted"
        elif participation == "submitted" and scored_items == 0:
            participation = "ungraded"
        row = {
            "student_id": student.student_id,
            "display_code": student.display_code,
            "inferred_summary": _inferred_summary(version, attempt, score_rows.values(), items),
            "participation_status": participation,
            "scored_items": scored_items,
            "correct_items": correct_items,
            "awarded_score": _round(awarded) if scored_items else None,
            "max_score": _round(maximum) if scored_items else None,
            "accuracy": _rate(correct_items, scored_items),
            "score_rate": _rate(awarded, maximum),
            "fully_comparable": bool(scored_items == len(items) and len(items) > 0),
        }
        student_rows.append(row)
        if selected_student_id == student.student_id:
            selected_item_results = per_item

    scored_rows = [row for row in student_rows if row["scored_items"] > 0]
    rates = [float(row["score_rate"]) for row in scored_rows
             if row["score_rate"] is not None]
    comparable_rows = [row for row in scored_rows if row["fully_comparable"]]
    comparable_scores = [float(row["awarded_score"]) for row in comparable_rows
                         if row.get("awarded_score") is not None]
    nominal_max_score = _round(sum(float(item.score or 0) for item in items)) or 0.0
    if nominal_max_score <= 0 and comparable_rows:
        comparable_maxima = [float(row["max_score"]) for row in comparable_rows
                             if row.get("max_score") is not None]
        nominal_max_score = _round(max(comparable_maxima)) if comparable_maxima else 0.0
    item_analysis = []
    for item in items:
        q = question_analysis(db, assessment_version_id,
                              paper_item_id=item.item_id)
        original = _snapshot_view(item, include_answer_reference=True, version=version)
        item_scores = [(score, attempt) for score, scored_item, attempt in all_valid
                       if scored_item.item_id == item.item_id]
        question_view = _class_scoring_reference(
            db, item, original, [score for score, _ in item_scores])
        response_counts: dict[str, int] = defaultdict(int)
        mapping_counts: dict[str, int] = defaultdict(int)
        question_type = str(question_view.get("question_type") or "")
        valid_option_keys = {
            str(option.get("key") or "").strip().upper()
            for option in question_view.get("options") or question_view.get("source_options") or []
            if isinstance(option, dict) and option.get("key")}
        for score, attempt in item_scores:
            answer = _answers_by_item(db, attempt, [item]).get(item.item_id)
            if score.correct is True:
                continue
            if question_type == "true_false":
                # Booleans are structured public choices, not private free text.
                # In particular False is a valid answer, never a missing value.
                value = _answer_value(answer.response) if answer else None
                if answer is None or answer.response_status == "unanswered":
                    mapping_counts["unanswered"] += 1
                elif isinstance(value, bool):
                    mapping_counts["reliable"] += 1
                    response_counts["正确" if value else "错误"] += 1
                else:
                    mapping_counts["unmapped"] += 1
            elif question_type in {"single_choice", "multiple_choice", "multi_choice"} or (
                valid_option_keys and _answer_keys(
                    (question_view.get("answer_reference") or {}).get("payload"))
            ):
                label, kind = _class_answer_pattern(score, answer, valid_option_keys)
                mapping_counts[kind] += 1
                if kind == "reliable":
                    response_counts[label] += 1
            else:
                # Class-level output must not disclose individual free-text
                # answers.  Only a privacy-safe aggregate label is returned.
                mapping_counts["other"] += 1
        wrong_distribution = sorted(
            ({"answer": answer, "count": count}
             for answer, count in response_counts.items()),
            key=lambda row: (-row["count"], row["answer"]))
        max_count = wrong_distribution[0]["count"] if wrong_distribution else 0
        common = [row for row in wrong_distribution if row["count"] == max_count] if max_count >= 2 else []
        frequent_label = (
            "；".join(row["answer"] for row in common) if common else
            "错误作答分散" if wrong_distribution else
            "已作答，选项对应待核对" if mapping_counts["unmapped"] else
            "未作答" if mapping_counts["unanswered"] else
            "其他错误答案" if mapping_counts["other"] else "")
        item_analysis.append({
            **question_view,
            "knowledge_point": q["knowledge_point"],
            "chapter": q["chapter"],
            "denominator": q["denominator"],
            "correct_count": q["correct_count"],
            "incorrect_count": q["incorrect_count"],
            "zero_score_count": q["zero_score_count"],
            "correct_rate": q["correct_rate"],
            "excluded": q["excluded"],
            "wrong_answer_distribution": wrong_distribution,
            "answer_mapping_counts": dict(mapping_counts),
            "common_wrong_answers": common,
            "high_frequency_wrong_answer": frequent_label,
            "high_frequency_wrong_count": max_count if common else 0,
        })

    kp_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"items": 0, "correct": 0, "students": set(),
                 "evidence_refs": []})
    for score, item, attempt in all_valid:
        kp = _kp_id(item, score)
        if not kp:
            continue
        bucket = kp_buckets[kp]
        bucket["items"] += 1
        bucket["correct"] += 1 if score.correct is True else 0
        bucket["students"].add(attempt.student_id)
        bucket["evidence_refs"].append({
            "score_id": score.score_id,
            "attempt_id": attempt.attempt_id,
            "paper_item_id": item.item_id,
            "authority_status": score.authority_status,
        })
    labels = _kp_labels(db, kp_buckets.keys())
    kp_rows = []
    for kp, bucket in kp_buckets.items():
        kp_rows.append({
            "kp_id": kp,
            "name": labels.get(kp, kp),
            "denominator": bucket["items"],
            "correct_count": bucket["correct"],
            "correct_rate": _rate(bucket["correct"], bucket["items"]),
            "student_count": len(bucket["students"]),
            "evidence_refs": bucket["evidence_refs"],
        })
    kp_rows.sort(key=lambda row: (
        row["correct_rate"] is None,
        row["correct_rate"] if row["correct_rate"] is not None else 2,
        row["kp_id"],
    ))
    common_weak = [row for row in kp_rows
                   if row["student_count"] >= 2
                   and row["correct_rate"] is not None
                   and row["correct_rate"] < 0.6]

    chapter_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"items": 0, "correct": 0, "awarded": 0.0,
                 "maximum": 0.0, "students": set(), "evidence_refs": []})
    for score, item, attempt in all_valid:
        chapter_id = _chapter_id(item, score)
        if not chapter_id:
            continue
        bucket = chapter_buckets[chapter_id]
        bucket["items"] += 1
        bucket["correct"] += 1 if score.correct is True else 0
        bucket["awarded"] += float(score.awarded_score)
        bucket["maximum"] += _score_max(score, item)
        bucket["students"].add(attempt.student_id)
        bucket["evidence_refs"].append({
            "score_id": score.score_id, "attempt_id": attempt.attempt_id,
            "paper_item_id": item.item_id,
            "authority_status": score.authority_status,
            "chapter_id": chapter_id,
        })
    chapter_labels = _chapter_labels(db, chapter_buckets.keys())
    chapter_rows = []
    for chapter_id, bucket in chapter_buckets.items():
        score_rate = _rate(bucket["awarded"], bucket["maximum"])
        chapter_rows.append({
            "chapter_id": chapter_id,
            "title": chapter_labels.get(chapter_id, chapter_id),
            "denominator": bucket["items"],
            "correct_count": bucket["correct"],
            "correct_rate": _rate(bucket["correct"], bucket["items"]),
            "awarded_score": _round(bucket["awarded"]),
            "max_score": _round(bucket["maximum"]),
            "score_rate": score_rate,
            "student_count": len(bucket["students"]),
            "performance": ("本次整体表现较好" if score_rate is not None and score_rate >= .75
                            else "本次共性复习重点" if score_rate is not None and score_rate < .60
                            else "本次整体表现一般"),
            "evidence_refs": bucket["evidence_refs"],
        })
    chapter_rows.sort(key=lambda row: row["chapter_id"])
    common_chapter_weaknesses = [row for row in chapter_rows
                                 if row["student_count"] >= 2
                                 and row["score_rate"] is not None
                                 and row["score_rate"] < .60]

    selected = None
    if selected_student_id:
        _assert_member(db, classroom_id, selected_student_id)
        selected = next((row.copy() for row in student_rows
                         if row["student_id"] == selected_student_id), None)
        if selected is None:
            raise ClassroomDiagnosisNotFound("选定学生无课堂记录")
        selected_rate = selected.get("score_rate")
        if selected_rate is not None and rates:
            selected["class_mean_score_rate"] = _round(sum(rates) / len(rates))
            selected["delta_from_class_mean"] = _round(
                float(selected_rate) - sum(rates) / len(rates))
            selected["percentile"] = _round(
                100 * sum(1 for value in rates if value <= float(selected_rate))
                / len(rates), 2)
        else:
            selected["class_mean_score_rate"] = (
                _round(sum(rates) / len(rates)) if rates else None)
            selected["delta_from_class_mean"] = None
            selected["percentile"] = None
        selected["items"] = selected_item_results

    # Chapter is the primary diagnosis grain.  Use chapter evidence as the
    # public provenance set so questions without a fine-grained KP mapping are
    # still represented exactly once.
    evidence_refs = [ref for row in chapter_rows for ref in row["evidence_refs"]]
    if not evidence_refs:
        evidence_refs = [ref for row in kp_rows for ref in row["evidence_refs"]]
    high_frequency_errors = sorted(
        [
            row for row in item_analysis
            if row.get("denominator") and int(row.get("incorrect_count") or 0) > 0
        ],
        key=lambda row: (
            -int(row.get("incorrect_count") or 0),
            float(row.get("correct_rate") if row.get("correct_rate") is not None else 1),
            int(row.get("position") or 0)),
    )[:5]
    weak_kp_ids = list(dict.fromkeys(
        str((row.get("knowledge_point") or {}).get("kp_id") or "")
        for row in high_frequency_errors
        if float(row.get("correct_rate") if row.get("correct_rate") is not None else 1) < .75
        and (row.get("knowledge_point") or {}).get("kp_id")))
    weak_chapter_ids = list(dict.fromkeys(
        str((row.get("chapter") or {}).get("chapter_id") or "")
        for row in high_frequency_errors
        if float(row.get("correct_rate") if row.get("correct_rate") is not None else 1) < .75
        and (row.get("chapter") or {}).get("chapter_id")))
    retest_ready = bool(weak_kp_ids and high_frequency_errors)
    payload = {
        "class_view_version": CLASS_VIEW_VERSION,
        "classroom": {"classroom_id": classroom.classroom_id,
                      "name": classroom.name},
        "assessment": {"assessment_id": assessment.assessment_id,
                       "assessment_version_id": version.assessment_version_id,
                       "quiz_code": assessment.quiz_code,
                       "title": assessment.title,
                       "scheduled_at": assessment.scheduled_at,
                       "version_no": version.version_no},
        "scored_submission_dates": sorted({a.submitted_at[:10] for _, _, a in all_valid if a.submitted_at}),
        "participation": {
            "enrolled": len(members),
            "scored": len(scored_rows),
            "absent": sum(1 for row in student_rows
                          if row["participation_status"] == "absent"),
            "pending_key": sum(1 for row in student_rows
                               if row["participation_status"] == "pending_key"),
            "ungraded": sum(1 for row in student_rows
                            if row["participation_status"] == "ungraded"),
            "no_submission": sum(1 for row in student_rows
                                 if row["participation_status"] == "no_submission"),
        },
        "score_overview": {
            "average_score": (_round(sum(comparable_scores) / len(comparable_scores))
                              if comparable_scores else None),
            "highest_score": _round(max(comparable_scores)) if comparable_scores else None,
            "lowest_score": _round(min(comparable_scores)) if comparable_scores else None,
            "max_score": nominal_max_score,
            "question_count": len(items),
            "comparable_student_count": len(comparable_rows),
            "basis": "仅统计同一固定试卷版本中全部题目均有可靠评分的答卷；缺考、未完成与部分评分不混入平均分。",
        },
        "score_distribution": {
            "basis": "每名学生有效题项得分率，不比较不同满分的原始总分",
            "mean": _round(sum(rates) / len(rates)) if rates else None,
            "median": _round(median(rates)) if rates else None,
            "bins": _distribution(rates),
            "comparable_bins": _distribution([
                float(row["score_rate"]) for row in comparable_rows
                if row.get("score_rate") is not None]),
        },
        "question_analysis": item_analysis,
        "high_frequency_errors": high_frequency_errors,
        "chapters": chapter_rows,
        "common_chapter_weaknesses": common_chapter_weaknesses,
        "knowledge_points": kp_rows,
        "common_weaknesses": common_weak,
        "anonymous_comparison": student_rows,
        "reference_diagnosis": aggregate_reference([x["inferred_summary"] for x in student_rows]),
        "selected_student": selected,
        "evidence_refs": evidence_refs,
        "score_sources": _source_summary(evidence_refs),
        "resources": _resources(db, [row["kp_id"] for row in common_weak]),
        "weak_kp_ids": weak_kp_ids,
        "weak_chapter_ids": weak_chapter_ids,
        "retest_plan": {
            "ready": retest_ready,
            "reason": ("将根据本卷高频错题生成同类新题，并自动完成题目检查。"
                       if retest_ready else "当前没有适合生成班级复测的高频错题。"),
            "kp_ids": weak_kp_ids,
            "chapter_ids": weak_chapter_ids,
            "item_count": (cfg.TARGETED_RETEST_DEFAULT_QUESTION_COUNT
                           if retest_ready else 0),
            "evidence_positions": [row.get("position") for row in high_frequency_errors],
        },
        "privacy": (
            "课堂聚合仅含匿名代码、状态和评分事实；不返回任何学生原始答案、"
            "身份档案、外部身份键或导入原始内容。"
        ),
        "uncertainties": [
            "共同薄弱仅是聚合表现，不推断学生的心理错因。",
            "答案键待确认、缺考及 correct=NULL 已排除，真实零分保留。",
        ],
    }
    # Prefer resources for the actual high-frequency weak concepts.  The
    # stricter <60% aggregate remains available in common_weaknesses.
    payload["resources"] = _resources(db, weak_kp_ids or [
        row["kp_id"] for row in common_weak])
    payload["ai_analysis"] = _v2_fallback("class_assessment", payload)
    return payload


def _v2_choice_keys(item: dict[str, Any]) -> tuple[list[str], bool]:
    """Resolve imported option text to keys only when the match is unique."""
    response = item.get("response")
    if response is None or (isinstance(response, dict) and response.get("answered") is False):
        return [], False
    options = item.get("options") or item.get("source_options") or []
    valid_keys = {str(row.get("key") or "").upper() for row in options
                  if isinstance(row, dict) and row.get("key")}

    def normalized(value: Any) -> str:
        return re.sub(r"[\s。．.,，、;；:：()（）]+", "", str(value or "")).casefold()

    if isinstance(response, dict) and isinstance(response.get("texts"), list):
        selected = []
        for text in response["texts"]:
            matches = [str(row["key"]).upper() for row in options
                       if isinstance(row, dict) and row.get("key")
                       and normalized(row.get("text")) == normalized(text)]
            if len(matches) != 1:
                return [], False
            selected.append(matches[0])
        return sorted(set(selected)), bool(selected)

    selected = _answer_keys(response)
    if len(selected) == 1 and re.fullmatch(r"[A-Z](?:[,;|/、\s]+[A-Z])+", selected[0]):
        selected = re.findall(r"[A-Z]", selected[0])
    if not selected:
        return [], False
    if valid_keys and all(key in valid_keys for key in selected):
        return sorted(set(selected)), True
    if not valid_keys and all(re.fullmatch(r"[A-Z]", key) for key in selected):
        return sorted(set(selected)), True
    if len(selected) == 1 and options:
        matches = [str(row["key"]).upper() for row in options
                   if isinstance(row, dict) and row.get("key")
                   and normalized(row.get("text")) == normalized(selected[0])]
        if len(matches) == 1:
            return matches, True
    return [], False


def _v2_observation(item: dict[str, Any]) -> str:
    analysis = item.get("error_analysis") or _item_error_analysis(item)
    qtype = str(item.get("question_type"))
    if qtype in {"multiple_choice", "multi_choice", "single_choice", "matching"}:
        selected, reliable = _v2_choice_keys(item)
        if not reliable:
            return "作答选项无法与原题选项可靠对应，需核对答卷。"
        correct = set(_answer_keys((item.get("answer_reference") or {}).get("payload")))
        if not correct:
            return "参考选项缺失，需核对答案记录。"
        if qtype in {"single_choice", "matching"}:
            return f"选择{'、'.join(selected)}，参考答案{'、'.join(sorted(correct))}。"
        missed = sorted(correct - set(selected))
        extra = sorted(set(selected) - correct)
        parts = []
        if missed:
            parts.append("漏选" + "、".join(missed))
        if extra:
            parts.append("错选" + "、".join(extra))
        if parts:
            return "，".join(parts) + "。"
    if qtype in {"numeric", "calculation", "true_false"}:
        response = _answer_value(item.get("response"))
        reference = _answer_value((item.get("answer_reference") or {}).get("payload"))
        if qtype == "true_false":
            response = ("正确" if response else "错误") if isinstance(response, bool) else response
            reference = ("正确" if reference else "错误") if isinstance(reference, bool) else reference
        if all(isinstance(value, (str, int, float, bool)) and len(str(value)) <= 32
               for value in (response, reference)):
            return f"作答为{response}，参考答案为{reference}。"
    return re.sub(r"^第\s*\d+\s*题", "", str(analysis.get("evidence") or "作答与答案不一致。"))


def _v2_analysis_evidence(scope: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    if scope == "student_attempt":
        wrong = [item for item in payload.get("items") or []
                 if item.get("evidence_status") == "scored"
                 and item.get("correct") is False]
        for index, item in enumerate(wrong, 1):
            position = item.get("position")
            rows.append({
                "id": f"q{index}", "name": (item.get("knowledge_point") or {}).get("name") or "本题对应概念",
                "chapter": (item.get("chapter") or {}).get("title") or "",
                "kp_id": (item.get("knowledge_point") or {}).get("kp_id") or item.get("kp_id") or "",
                "positions": [position], "refs": [f"第{position}题"],
                "observation": _v2_observation(item),
                "interpretation_allowed": (
                    _v2_choice_keys(item)[1] if str(item.get("question_type")) in
                    {"multiple_choice", "multi_choice", "single_choice", "matching"}
                    else item.get("response") is not None),
                "stem": item.get("stem"), "question_type": item.get("question_type"),
                "options": item.get("options") or item.get("source_options") or [],
                "student_answer": item.get("response"),
                "correct_answer": (item.get("answer_reference") or {}).get("payload"),
                "explanation": (item.get("answer_reference") or {}).get("explanation"),
                "score": {"awarded": item.get("awarded_score"), "maximum": item.get("max_score")},
            })
    elif scope == "student_history":
        for index, item in enumerate(payload.get("wrong_items") or [], 1):
            occurrences = sorted(item.get("occurrences") or [],
                                 key=lambda row: str(row.get("exam_time") or ""))
            refs = [f"{row.get('assessment_title') or '考试'}第{row.get('position')}题"
                    for row in occurrences]
            latest = occurrences[-1] if occurrences else item
            mismatch = _item_error_analysis({
                **item, "position": latest.get("position") or item.get("position"),
                "response": latest.get("response"),
            })
            count = int(item.get("error_count") or len(occurrences) or 1)
            observation = _v2_observation({
                **item, "position": latest.get("position") or item.get("position"),
                "response": latest.get("response"), "error_analysis": mismatch,
            })
            if count > 1:
                observation = f"同题出现{count}次错误；最近一次{observation}"
            rows.append({
                "id": f"h{index}", "name": (item.get("knowledge_point") or {}).get("name") or "本题对应概念",
                "chapter": (item.get("chapter") or {}).get("title") or "",
                "kp_id": (item.get("knowledge_point") or {}).get("kp_id") or item.get("kp_id") or "",
                "positions": [row.get("position") for row in occurrences if row.get("position")],
                "refs": refs or [f"第{item.get('position')}题"],
                "observation": observation,
                "interpretation_allowed": (
                    _v2_choice_keys({**item, "response": latest.get("response")})[1]
                    if str(item.get("question_type")) in
                    {"multiple_choice", "multi_choice", "single_choice", "matching"}
                    else latest.get("response") is not None),
                "stem": item.get("stem"), "question_type": item.get("question_type"),
                "options": item.get("options") or item.get("source_options") or [],
                "correct_answer": (item.get("answer_reference") or {}).get("payload"),
                "explanation": (item.get("answer_reference") or {}).get("explanation"),
                "error_events": count,
                "occurrences": [{
                    "ref": f"{row.get('assessment_title') or '考试'}第{row.get('position')}题",
                    "student_answer": row.get("response"),
                    "score": {"awarded": row.get("awarded_score"),
                              "maximum": row.get("max_score")},
                } for row in (occurrences[:1] + occurrences[-1:] if len(occurrences) > 1 else occurrences)],
            })
    elif scope == "class_assessment":
        for index, item in enumerate(payload.get("high_frequency_errors") or [], 1):
            position = item.get("position")
            common = item.get("common_wrong_answers") or []
            answer_known = bool(item.get("scoring_reference_status") == "available"
                                and common and all(int(row.get("count") or 0) >= 2
                                                   for row in common))
            wrong_answer = "；".join(
                f"{row['answer']}（{row['count']}人）" for row in common) if answer_known else ""
            correct_keys = set(_answer_keys((item.get("answer_reference") or {}).get("payload")))
            choice_question = item.get("question_type") in {"single_choice", "multiple_choice", "multi_choice"}
            common_missed = (set.intersection(*(
                correct_keys - {key.strip() for key in str(row.get("answer") or "").split(",")}
                for row in common))
                if answer_known and choice_question else set())
            common_extra = (set.intersection(*(
                {key.strip() for key in str(row.get("answer") or "").split(",")} - correct_keys
                for row in common))
                if answer_known and choice_question else set())
            observation = (f"{item.get('incorrect_count')}/{item.get('denominator')}人答错"
                           + (f"，常见错误作答为{wrong_answer}。" if wrong_answer else "。")
                           + (f"共同漏选{'、'.join(sorted(common_missed))}。" if common_missed else "")
                           + (f"共同错选{'、'.join(sorted(common_extra))}。" if common_extra else ""))
            rows.append({
                "id": f"c{index}", "name": (item.get("knowledge_point") or {}).get("name") or "本题对应概念",
                "chapter": (item.get("chapter") or {}).get("title") or "",
                "kp_id": (item.get("knowledge_point") or {}).get("kp_id") or item.get("kp_id") or "",
                "positions": [position], "refs": [f"第{position}题"],
                "observation": observation, "interpretation_allowed": answer_known,
                "interpretation_limit": ("distributed_answers" if not answer_known and
                    (item.get("answer_mapping_counts") or {}).get("reliable", 0) > 0 else ""),
                "stem": item.get("stem"), "question_type": item.get("question_type"),
                "options": item.get("options") or item.get("source_options") or [],
                "high_frequency_wrong_answer": wrong_answer or None,
                "correct_answer": (item.get("answer_reference") or {}).get("payload"),
                "explanation": (item.get("answer_reference") or {}).get("explanation"),
                "correct_count": item.get("correct_count"),
                "incorrect_count": item.get("incorrect_count"),
                "denominator": item.get("denominator"),
                "high_frequency_wrong_count": item.get("high_frequency_wrong_count"),
                "mapping_counts": item.get("answer_mapping_counts") or {},
                "common_missed_keys": sorted(common_missed),
                "common_extra_keys": sorted(common_extra),
            })
    return rows


def _v2_fallback(scope: str, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.app.services.diagnosis_coaching import coaching
    return coaching(scope, _v2_analysis_evidence(scope, payload), payload)


def _model_analysis_v2(db: Session, scope: str, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
    from backend.app.services.diagnosis_coaching import coaching, context, parse_model_output
    from backend.app.services.learning_catalog import target
    evidence = _v2_analysis_evidence(scope, payload)
    fallback = coaching(scope, evidence, payload)
    interpretable = [r for r in evidence if r.get("interpretation_allowed", True)]
    if not interpretable:
        return fallback
    # Keep every scored error in deterministic coverage/priority. Bound only the
    # model prompt, selecting representative evidence in the actual priority order.
    ranked_kps = list(dict.fromkeys(c["kp_id"] for c in fallback["concept_hypotheses"] if c["kp_id"]))
    model_evidence = []
    for kp in ranked_kps:
        related = [r for r in interpretable if r.get("kp_id", "") == kp]
        model_evidence.extend(related[:2])
        if len(model_evidence) >= 12:
            break
    model_evidence = model_evidence[:12]
    if not model_evidence:
        model_evidence = interpretable[:8]
    focus = {"student_attempt":"本次答卷的具体作答差异",
             "student_history":"所选日期范围内的反复错误，不把同知识点的不同题称为同题重复",
             "class_assessment":"班级实际作答分布，不推断个人心理原因"}[scope]
    system = (
        f"你是机器学习课程教师，依据{focus}给出具体、易读的诊断。"
        "只使用提供的题干、选项、答案、评分和资料，不重复统计数字、不编造题号、日期或学习期限。"
        "不要写加强基础、多做练习、认真听讲、继续努力这类没有具体对象的占位话。"
        "输出严格JSON，只有concept_hypotheses数组，最多3项；一项真实证据也可以分析，不强凑数量。"
        "evidence_ids只填写在专用字段，analysis/action/success_check不得出现h1、q1、c1等内部证据编号。"
        "题目来源、选了什么、漏了什么、错误次数由页面按数据库展示，分析中不要复述这些作答统计，避免把最近一次差异套到历次。"
        "analysis应解释关键概念的区别与成立条件；action和success_check写具体学习产出。"
        "反向传播输出层误差依赖损失和激活函数，delta=a-y仅在相应组合下成立，不能当作通用公式。"
        "每项包含evidence_ids（原id数组）、analysis（解释作答差异及关键概念，50至130字）、"
        "action（指出要比较、计算或画出的具体内容，20至100字）、"
        "success_check（可检查的学习产出，20至100字）。不要把整段分析写成一句泛泛建议。"
        "可观察的错误与推测原因分开；推测用可能，不能断言粗心、审题失误或心理原因。"
        "interpretation_allowed=false不能归因为理解问题。班级只把各组共同的漏选/错选称为共性。"
        "学习资源由系统按知识点匹配，禁止编造URL、页码、视频时间点或已掌握结论。"
        "数学公式使用$...$或$$...$$。" + SCIENCE_GUIDANCE)
    learning = [{"kp_id":r.get("kp_id"), **target(r.get("kp_id", ""), r.get("name", ""))}
              for r in model_evidence]
    try:
        response = model_provider.chat(
            db, messages=[{"role":"system", "content":system},
                          {"role":"user", "content":json.dumps({
                              "scope":scope, "selection_context":context(scope,payload),
                                "wrong_evidence":model_evidence, "study_targets":learning}, ensure_ascii=False)}],
            skill_name="diagnose_classroom", prompt_version=f"{scope}-coaching-v3",
            run_id=run_id, temperature=.1, max_tokens=2200, timeout=90.)
        if response.get("finish_reason") == "length":
            raise model_provider.ModelUnavailable("truncated", "模型输出被截断")
        parsed, repaired = parse_model_output(response.get("content") or "")
        result = coaching(scope, evidence, payload, parsed, response)
        result["validation"]["json_math_escapes_repaired"] = repaired
        if result["source"] != "deepseek":
            return {**result, "model_status":"invalid_output"}
        return result
    except model_provider.ModelUnavailable as exc:
        return {**fallback, "model_status":exc.status, "model_message":exc.message}


def _diagnosis_target(scope_type: str, values: Iterable[str]) -> str:
    canonical = "|".join([scope_type, *[str(v or "") for v in values]])
    return f"{scope_type}:{sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def run_diagnosis(
    db: Session,
    *,
    scope_type: str,
    actor_user_id: str = "local_user",
    classroom_id: str = "",
    student_id: str = "",
    assessment_version_id: str = "",
    paper_item_id: str = "",
    attempt_id: str = "",
    include_ai: bool = False,
    date_from: str = "",
    date_to: str = "",
    trace_run_id: str = "",
) -> dict:
    """Compute and persist one explicit teacher diagnosis scope."""
    if scope_type == "student_attempt":
        if not student_id or not assessment_version_id:
            raise ClassroomDiagnosisError(
                "student_attempt 需要 student_id 与 assessment_version_id")
        payload = student_attempt(
            db, student_id, assessment_version_id, attempt_id=attempt_id)
        classroom_id = payload["classroom_id"]
        level = "paper"
        target_values = [student_id, assessment_version_id,
                         payload.get("attempt_id") or attempt_id]
    elif scope_type == "student_history":
        if not student_id or not classroom_id:
            raise ClassroomDiagnosisError(
                "student_history 需要 student_id 与 classroom_id")
        payload = student_history(
            db, student_id, classroom_id,
            date_from=date_from, date_to=date_to,
            assessment_version_id=assessment_version_id)
        level = "history"
        target_values = [classroom_id, student_id, date_from, date_to,
                         assessment_version_id]
    elif scope_type == "class_assessment":
        if not classroom_id or not assessment_version_id:
            raise ClassroomDiagnosisError(
                "class_assessment 需要 classroom_id 与 assessment_version_id")
        payload = class_assessment(db, classroom_id, assessment_version_id)
        level = "class"
        target_values = [classroom_id, assessment_version_id]
    elif scope_type == "question_analysis":
        if not assessment_version_id or not paper_item_id:
            raise ClassroomDiagnosisError(
                "question_analysis 需要 assessment_version_id 与 paper_item_id")
        payload = question_analysis(db, assessment_version_id,
                                    paper_item_id=paper_item_id)
        classroom_id = payload["classroom_id"]
        level = "question"
        target_values = [assessment_version_id, paper_item_id]
    else:
        raise ClassroomDiagnosisError(
            "scope_type 必须是 student_attempt、student_history、"
            "class_assessment 或 question_analysis")

    run_id = trace_run_id or E.new_id("run")
    if scope_type == "student_attempt" and include_ai:
        from backend.app.services import targeted_retest
        payload["ai_analysis"] = targeted_retest.enrich_analysis(
            _model_analysis_v2(db, "student_attempt", payload, run_id),
            payload.get("retest_history") or [])
    elif scope_type == "student_history" and include_ai:
        payload["ai_analysis"] = _model_analysis_v2(db, "student_history", payload, run_id)
    elif scope_type == "class_assessment" and include_ai:
        from backend.app.services.class_answer_review import review_class_answers
        payload["answer_reviews"] = review_class_answers(
            db, payload, run_id=run_id, actor_user_id=actor_user_id,
            classroom_id=classroom_id,
            assessment_version_id=assessment_version_id)
        payload["ai_analysis"] = _model_analysis_v2(db, "class_assessment", payload, run_id)

    evidence_refs = list(payload.get("evidence_refs") or [])
    uncertainties = list(payload.get("uncertainties") or [])
    if scope_type == "question_analysis":
        uncertainties.append("跨学生对错分布不能证明共同心理错因。")
    next_actions: list[str] = []
    if scope_type == "student_attempt" and payload.get("weak_chapter_ids"):
        next_actions.append("按本次薄弱章节安排不同题族复测，并用后续考试确认长期表现。")
    elif scope_type == "student_history":
        next_actions.append("对只有一次测量的章节补充独立测量，不把单次成绩当作长期掌握。")
    elif scope_type == "class_assessment" and payload.get("common_chapter_weaknesses"):
        next_actions.append("优先讲解本卷共同薄弱章节，再用等值题复测验证。")
    elif scope_type == "question_analysis":
        next_actions.append("结合题目原文与人工阅卷证据核对，不从得分编造错因。")

    diagnosis = E.Diagnosis(
        diagnosis_id=E.new_id("diag"),
        user_id=actor_user_id or "local_user",
        level=level,
        target_id=_diagnosis_target(scope_type, target_values),
        payload=payload,
        evidence_refs=evidence_refs,
        uncertainties=uncertainties,
        next_actions=next_actions,
        skill_version=SKILL_VERSION,
        run_id=run_id,
        engine=("deterministic+deepseek"
                if payload.get("ai_analysis", {}).get("source") == "deepseek"
                else "deterministic"),
        student_id=student_id,
        classroom_id=classroom_id,
        assessment_version_id=assessment_version_id,
        scope_type=scope_type,
    )
    db.add(diagnosis)
    db.commit()
    return {
        "diagnosis_id": diagnosis.diagnosis_id,
        "run_id": diagnosis.run_id,
        "scope_type": diagnosis.scope_type,
        "level": diagnosis.level,
        "target_id": diagnosis.target_id,
        "created_at": diagnosis.created_at,
        "result": payload,
        "evidence_refs": evidence_refs,
        "score_sources": _source_summary(evidence_refs),
        "uncertainties": uncertainties,
        "next_actions": next_actions,
        "engine": diagnosis.engine,
        "skill_version": diagnosis.skill_version,
    }
