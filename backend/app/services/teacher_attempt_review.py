"""Read-only teacher review paired to the answer version actually scored."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services import bank_service
from backend.app.services.classroom_content import asset_path
from backend.app.services.attempt_service import attempt_elapsed, grading_summary
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


def _review_options(question: E.Question) -> list[dict]:
    """Attach verified media URLs to revised options without mixing versions."""
    source_sha = next((str(ref.get("file_sha256") or "")
                       for ref in (question.source_refs or [])
                       if isinstance(ref, dict) and ref.get("kind") == "teacher_docx"), "")
    result = []
    for source in question.options or []:
        if not isinstance(source, dict):
            result.append(source)
            continue
        option = dict(source)
        media = []
        for image in source.get("media") or []:
            asset_id = str(image.get("selected_asset_id") or "")
            if not source_sha or not asset_id:
                continue
            try:
                asset_path(source_sha, asset_id)
            except ValueError:
                continue
            media.append({"url": f"/api/classroom-media/{source_sha}/{asset_id}",
                          "role": image.get("role") or "image",
                          "alt_text": image.get("alt_text") or "题目公式或配图"})
        option["review_media"] = media
        result.append(option)
    return result


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
        if view.get("is_scoring_revision") and view.get("scoring_reference_status") == "available":
            revised = db.execute(select(E.Question).where(
                E.Question.question_id == item.question_id,
                E.Question.question_version == view.get("scoring_question_version"),
            )).scalars().first()
            if revised is not None:
                view["options"] = _review_options(revised)
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
    # The student result and the teacher review must use the same effective
    # marks, including the validity check for historical regrades.
    from backend.app.services.classroom_diagnosis import _valid_score
    version = assessment_version
    for item, row in zip(items, rows):
        if not _valid_score(version, attempt, scores.get(item.item_id)):
            row["score"] = None
    grading = grading_summary(db, attempt)
    elapsed_ms, elapsed_source = attempt_elapsed(attempt, len(items))
    return {
        "attempt_id": attempt.attempt_id,
        "status": attempt.status,
        "mode": attempt.mode,
        "retest_of_attempt_id": attempt.retest_of_attempt_id,
        "started_at": attempt.started_at,
        "submitted_at": attempt.submitted_at,
        "duration_ms": attempt.duration_ms,
        "display_elapsed_ms": elapsed_ms,
        "elapsed_source": elapsed_source,
        "score": grading["score"],
        "max_score": grading["max_score"],
        "scored_item_count": grading["scored_item_count"],
        "ungraded_item_count": grading["ungraded_item_count"],
        "grading_status": grading["grading_status"],
        "score_source": "current_scoring",
        "items": rows,
    }
