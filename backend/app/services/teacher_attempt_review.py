"""Read-only teacher review paired to the answer version actually scored."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services import bank_service
from backend.app.services.attempt_service import attempt_elapsed
from backend.app.services.classroom_diagnosis import (
    _answer_keys, _answers_by_item, _class_answer_pattern,
    _class_scoring_reference, _scores_by_item, _snapshot_view,
)


def _boolean(value: Any) -> bool | None:
    if isinstance(value, dict):
        for key in ("value", "answer", "text", "key"):
            if key in value:
                return _boolean(value[key])
        return None
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in {"true", "t", "1", "对", "正确", "是", "√"}:
        return True
    if text in {"false", "f", "0", "错", "错误", "否", "×"}:
        return False
    return None


def teacher_attempt_review(db: Session, attempt: E.Attempt) -> dict:
    if attempt.status != "submitted":
        raise ValueError("答卷尚未提交")
    items = bank_service.get_paper_items(db, attempt.paper_id)
    answers = _answers_by_item(db, attempt, items)
    scores = _scores_by_item(db, attempt, items)
    assessment_version = (db.get(E.AssessmentVersion, attempt.assessment_version_id)
                          if attempt.assessment_version_id else None)
    rows = []
    for item in items:
        answer = answers.get(item.item_id)
        score = scores.get(item.item_id)
        original = _snapshot_view(item, include_answer_reference=True,
                                  version=assessment_version)
        view = _class_scoring_reference(db, item, original, [score] if score else [])
        reference = view.get("answer_reference") or {}
        if (score and not isinstance(score, E.AnswerRegrade)
                and score.question_version != item.question_version):
            view["scoring_reference_status"] = "version_conflict"
        choice = str(view.get("question_type") or "") in {
            "single_choice", "multi_choice", "multiple_choice"}
        option_keys = {str(row.get("key") or "").upper()
                       for row in view.get("options") or [] if isinstance(row, dict)}
        reference_keys = _answer_keys(reference.get("payload")) if choice else []
        if choice and (not reference_keys or not set(reference_keys).issubset(option_keys)):
            view["scoring_reference_status"] = "missing"
        reference_available = view.get("scoring_reference_status") == "available"
        selected_keys: list[str] = []
        selection_status = "unanswered" if answer is None else "not_applicable"
        if choice and answer is not None:
            if score is not None:
                pattern, selection_status = _class_answer_pattern(score, answer, option_keys)
                if selection_status == "reliable":
                    selected_keys = [key.strip() for key in pattern.split(",")]
            elif answer.response_status == "answered":
                selected_keys = [key for key in _answer_keys(answer.response)
                                 if key in option_keys]
                selection_status = "reliable" if selected_keys else "unmapped"
            else:
                selection_status = "unanswered"
        student_boolean = _boolean(answer.response) if answer else None
        reference_boolean = (_boolean(reference.get("payload"))
                             if reference_available else None)
        rows.append({
            **view,
            "question_id": item.question_id,
            "paper_item_id": item.item_id,
            "reference_available": reference_available,
            "selected_keys": selected_keys,
            "reference_keys": reference_keys if reference_available else [],
            "selection_status": selection_status,
            "student_boolean": student_boolean,
            "reference_boolean": reference_boolean,
            "student_response": answer.response if answer else None,
            "score": ({"correct": score.correct,
                       "awarded_score": score.awarded_score,
                       "max_score": score.max_score}
                      if score else None),
        })
    scored = [row["score"] for row in rows if row["score"] is not None]
    complete = len(scored) == len(items) and all(row["correct"] is not None for row in scored)
    elapsed_ms, elapsed_source = attempt_elapsed(attempt, len(items))
    return {
        "attempt_id": attempt.attempt_id,
        "status": attempt.status,
        "mode": attempt.mode,
        "started_at": attempt.started_at,
        "submitted_at": attempt.submitted_at,
        "duration_ms": attempt.duration_ms,
        "display_elapsed_ms": elapsed_ms,
        "elapsed_source": elapsed_source,
        "score": (round(sum(float(row["awarded_score"] or 0) for row in scored), 4)
                  if complete else attempt.score),
        "max_score": (round(sum(float(row["max_score"] or 0) for row in scored), 4)
                      if complete else attempt.max_score),
        "score_source": "current_scoring" if complete else "source_record",
        "items": rows,
    }
