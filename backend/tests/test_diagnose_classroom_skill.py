"""Reusable classroom Skill and LangGraph routing regression tests."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.graphs import classroom_workflow
from backend.app.models import entities as E
from backend.app.models.base import Base
from backend.app.services.skill_runtime import run_skill
from backend.app.skills.registry import REGISTRY, assert_registry


def _attempt(db: Session, attempt_id: str, student_id: str, *,
             attendance: str = "attended", grading: str = "graded") -> None:
    db.add(E.Attempt(
        attempt_id=attempt_id, paper_id="paper-fixed", user_id="teacher",
        student_id=student_id, assessment_version_id="version-fixed",
        mode="quiz", status="submitted", attendance_status=attendance,
        grading_status=grading, is_effective=True, score=0, max_score=10,
        submitted_at="2026-11-01T10:00:00+00:00"))


def _score(db: Session, score_id: str, attempt_id: str,
           correct: bool, *, pending: bool = False) -> None:
    db.add(E.AnswerScore(
        score_id=score_id, attempt_id=attempt_id, paper_item_id="item-fixed",
        question_id="question-fixed", question_version=3, kp_id="kp.linear",
        correct=correct, awarded_score=10 if correct else 0, max_score=10,
        scoring_status="pending_key" if pending else "auto",
        score_provenance="teacher_workbook",
        authority_status="pending_key" if pending else "teacher_key",
        teacher_verified=not pending, source_ref=f"scores:{score_id}"))


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    session.add_all([
        E.User(user_id="teacher", display_name="教师"),
        E.Classroom(classroom_id="classroom", course_key="ml",
                    name="机器学习班", term="2026秋"),
        E.KnowledgePoint(kp_id="kp.linear", chapter="1", name="线性模型"),
        E.Paper(paper_id="paper-fixed", user_id="teacher", title="固定卷",
                mode="quiz", item_count=1, total_score=10),
        E.Assessment(assessment_id="assessment", classroom_id="classroom",
                     external_key="quiz", quiz_code="Q1", title="固定小测"),
        E.AssessmentVersion(
            assessment_version_id="version-fixed",
            assessment_id="assessment", version_no=3,
            version_key="quiz:v3", paper_id="paper-fixed",
            content_hash="fixed-content", scoring_status="verified",
            answer_key_source="teacher", teacher_verified=True),
        E.PaperItem(
            item_id="item-fixed", paper_id="paper-fixed", position=1,
            question_id="question-fixed", question_version=3, score=10,
            snapshot={"stem": "固定版本题干 v3",
                      "question_type": "true_false",
                      "main_kp_id": "kp.linear",
                      "answer_payload": {"value": True}}),
        # A later bank row must never replace the fixed PaperItem snapshot.
        E.Question(row_id="latest-row", question_id="question-fixed",
                   question_version=99, question_type="true_false",
                   stem="LATEST_WRONG_STEM", main_kp_id="kp.linear",
                   active=True),
    ])
    for index in range(1, 5):
        student_id = f"student-{index}"
        session.add(E.Student(student_id=student_id,
                              external_key=f"private-{index}",
                              display_code=f"S{index:02d}"))
        session.add(E.ClassroomStudent(
            membership_id=f"member-{index}", classroom_id="classroom",
            student_id=student_id, status="active"))
    _attempt(session, "attempt-valid-zero", "student-1")
    _attempt(session, "attempt-valid-correct", "student-2")
    _attempt(session, "attempt-absent", "student-3", attendance="absent")
    _attempt(session, "attempt-pending", "student-4", grading="pending_key")
    _score(session, "score-zero", "attempt-valid-zero", False)
    _score(session, "score-correct", "attempt-valid-correct", True)
    _score(session, "score-absent", "attempt-absent", False)
    _score(session, "score-pending", "attempt-pending", False, pending=True)
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_registry_and_skill_preserve_classroom_evidence_rules(db: Session):
    assert_registry()
    spec = REGISTRY["diagnose_classroom"]
    assert spec.requires_model is False
    assert spec.input_schema["properties"]["scope_type"]["enum"] == [
        "question_analysis", "student_attempt", "student_history",
        "class_assessment"]

    out = run_skill(db, "diagnose_classroom", {
        "scope_type": "question_analysis",
        "assessment_version_id": "version-fixed",
        "paper_item_id": "item-fixed",
    }, thread_id="test:classroom-skill", user_id="teacher")

    assert out["status"] == "succeeded"
    assert out["skill"] == "diagnose_classroom"
    assert out["skill_version"].endswith("/diagnose_classroom-1.0")
    result = out["result"]
    assert result["question"]["stem"] == "固定版本题干 v3"
    assert result["question"]["question_version"] == 3
    assert result["denominator"] == 2
    assert result["correct_count"] == result["incorrect_count"] == 1
    assert result["zero_score_count"] == 1
    assert result["excluded"] == {"absent": 1, "pending_key": 1}
    assert result["cause_analysis"]["status"] == "not_inferred"
    assert len(out["evidence_refs"]) == 2
    assert out["extra"]["diagnosis_id"]

    skill_run = db.get(E.SkillRun, out["run_id"])
    diagnosis = db.get(E.Diagnosis, out["extra"]["diagnosis_id"])
    assert skill_run is not None and skill_run.status == "succeeded"
    assert diagnosis is not None and diagnosis.scope_type == "question_analysis"
    assert diagnosis.run_id == out["run_id"]
    assert diagnosis.skill_version == out["skill_version"]
    rendered = json.dumps(out, ensure_ascii=False)
    assert "LATEST_WRONG_STEM" not in rendered
    assert "answer_payload" not in rendered


def test_classroom_workflows_call_diagnosis_skill(db: Session, monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_run_skill(_db, name, payload, **_context):
        calls.append((name, dict(payload)))
        if name == "diagnose_classroom":
            scope = payload["scope_type"]
            result = ({"attempt_id": "attempt-valid-zero",
                       "weak_kp_ids": []}
                      if scope == "student_attempt" else {})
            return {"skill": name, "status": "succeeded", "result": result,
                    "evidence_refs": [], "uncertainties": [],
                    "next_actions": [],
                    "extra": {"diagnosis_id": f"diag-{scope}"}}
        assert name == "recommend_resources"
        return {"skill": name, "status": "succeeded",
                "result": {"recommendations": []}, "evidence_refs": [],
                "uncertainties": [], "next_actions": [], "extra": {}}

    monkeypatch.setattr(classroom_workflow, "run_skill", fake_run_skill)

    teacher = classroom_workflow.run_teacher_intervention(
        db, actor_user_id="teacher", student_id="student-1",
        assessment_version_id="version-fixed")
    assert teacher["status"] == "succeeded"
    assert [name for name, _payload in calls] == [
        "diagnose_classroom", "recommend_resources"]
    assert calls[0][1]["scope_type"] == "student_attempt"

    calls.clear()
    submitted = classroom_workflow.run_post_submission(
        db, actor_user_id="teacher", attempt_id="attempt-valid-zero")
    assert submitted["status"] == "succeeded"
    assert [payload["scope_type"] for name, payload in calls
            if name == "diagnose_classroom"] == [
                "student_attempt", "student_history"]
    assert [name for name, _payload in calls][-1] == "recommend_resources"
