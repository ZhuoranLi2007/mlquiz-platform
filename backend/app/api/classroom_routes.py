"""Teacher classroom-diagnosis API.

This router is intentionally independent so the application entry point can
mount it behind the project's final teacher-role dependency.  Until that
integration happens, ``require_teacher_actor`` is the one centralized seam to
replace; it keeps the current local-only owner compatible with ``local_user``.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.models.base import get_session
from backend.app.services import auth_service
from backend.app.services import classroom_diagnosis as service


router = APIRouter(prefix="/api/teacher", tags=["teacher-diagnosis"])


def require_teacher_actor(
    principal: auth_service.Principal = Depends(auth_service.require_teacher),
) -> str:
    """Return the authenticated teacher id used by persisted diagnoses."""
    return principal.user_id


class DiagnosisRunIn(BaseModel):
    scope_type: Literal[
        "student_attempt", "student_history", "class_assessment",
        "question_analysis",
    ]
    classroom_id: str = Field(default="", max_length=64)
    student_id: str = Field(default="", max_length=64)
    assessment_version_id: str = Field(default="", max_length=64)
    paper_item_id: str = Field(default="", max_length=64)
    attempt_id: str = Field(default="", max_length=64)
    include_ai: bool = False
    date_from: str = Field(default="", max_length=10)
    date_to: str = Field(default="", max_length=10)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except service.ClassroomDiagnosisNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.ClassroomDiagnosisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/overview")
def classroom_overview(
    classroom_id: str = Query(..., min_length=1, max_length=64),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_teacher_actor),
) -> dict:
    return _call(service.overview, db, classroom_id)


@router.get("/students")
def classroom_students(
    classroom_id: str = Query(..., min_length=1, max_length=64),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_teacher_actor),
) -> dict:
    return _call(service.list_students, db, classroom_id)


@router.get("/assessments")
def classroom_assessments(
    classroom_id: str = Query(..., min_length=1, max_length=64),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_teacher_actor),
) -> dict:
    return _call(service.list_assessments, db, classroom_id)


@router.get("/question-analysis")
def assessment_question_analysis(
    assessment_version_id: str = Query(..., min_length=1, max_length=64),
    paper_item_id: str = Query(default="", max_length=64),
    question_id: str = Query(default="", max_length=64),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_teacher_actor),
) -> dict:
    if not paper_item_id and not question_id:
        raise HTTPException(400, "paper_item_id 与 question_id 至少提供一个")
    return _call(
        service.question_analysis, db, assessment_version_id,
        paper_item_id=paper_item_id, question_id=question_id,
    )


@router.get("/student-attempt")
def one_student_one_assessment(
    student_id: str = Query(..., min_length=1, max_length=64),
    assessment_version_id: str = Query(..., min_length=1, max_length=64),
    attempt_id: str = Query(default="", max_length=64),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_teacher_actor),
) -> dict:
    return _call(service.student_attempt, db, student_id,
                 assessment_version_id, attempt_id=attempt_id)


@router.get("/student-history")
def one_student_many_assessments(
    student_id: str = Query(..., min_length=1, max_length=64),
    classroom_id: str = Query(..., min_length=1, max_length=64),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    assessment_version_id: str = Query(default="", max_length=64),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_teacher_actor),
) -> dict:
    return _call(
        service.student_history, db, student_id, classroom_id,
        date_from=date_from, date_to=date_to,
        assessment_version_id=assessment_version_id,
    )


@router.get("/class-assessment")
def many_students_one_assessment(
    classroom_id: str = Query(..., min_length=1, max_length=64),
    assessment_version_id: str = Query(..., min_length=1, max_length=64),
    selected_student_id: str = Query(default="", max_length=64),
    db: Session = Depends(get_session),
    _actor: str = Depends(require_teacher_actor),
) -> dict:
    return _call(
        service.class_assessment, db, classroom_id, assessment_version_id,
        selected_student_id=selected_student_id,
    )


@router.get("/diagnoses/latest")
def latest_teacher_diagnosis(
    scope_type: Literal[
        "student_attempt", "student_history", "class_assessment",
        "question_analysis",
    ] = Query(...),
    classroom_id: str = Query(default="", max_length=64),
    student_id: str = Query(default="", max_length=64),
    assessment_version_id: str = Query(default="", max_length=64),
    paper_item_id: str = Query(default="", max_length=64),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    db: Session = Depends(get_session),
    actor: str = Depends(require_teacher_actor),
) -> dict:
    return _call(
        service.latest_diagnosis,
        db,
        actor_user_id=actor,
        scope_type=scope_type,
        classroom_id=classroom_id,
        student_id=student_id,
        assessment_version_id=assessment_version_id,
        paper_item_id=paper_item_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.post("/diagnoses/run")
def persist_teacher_diagnosis(
    body: DiagnosisRunIn,
    db: Session = Depends(get_session),
    actor: str = Depends(require_teacher_actor),
) -> dict:
    return _call(
        service.run_diagnosis,
        db,
        scope_type=body.scope_type,
        actor_user_id=actor,
        classroom_id=body.classroom_id,
        student_id=body.student_id,
        assessment_version_id=body.assessment_version_id,
        paper_item_id=body.paper_item_id,
        attempt_id=body.attempt_id,
        include_ai=body.include_ai,
        date_from=body.date_from,
        date_to=body.date_to,
    )
