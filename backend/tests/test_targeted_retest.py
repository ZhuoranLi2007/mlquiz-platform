from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.models import entities as E
from backend.app.models.base import Base
from backend.app.graphs import classroom_workflow
from backend.app.services import targeted_retest


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite+pysqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    session.add_all([
        E.User(user_id="teacher", display_name="教师", role="teacher"),
        E.User(user_id="student-user", display_name="学生", role="student"),
        E.Student(student_id="stu-1", external_key="s1", display_code="STU001",
                  linked_user_id="student-user"),
        E.Classroom(classroom_id="class-1", course_key="c1", name="测试班"),
        E.ClassroomStudent(membership_id="member-1", classroom_id="class-1",
                           student_id="stu-1"),
        E.Assessment(assessment_id="assessment-source", classroom_id="class-1",
                     external_key="source", quiz_code="11", title="矩阵分解"),
        E.Paper(paper_id="paper-source", user_id="teacher", title="原卷",
                item_count=1, total_score=2),
        E.AssessmentVersion(assessment_version_id="av-source",
                            assessment_id="assessment-source", version_key="source-v1",
                            paper_id="paper-source", scoring_status="graded"),
        E.Attempt(attempt_id="attempt-source", paper_id="paper-source",
                  user_id="student-user", student_id="stu-1",
                  assessment_version_id="av-source", status="submitted",
                  grading_status="graded"),
    ])
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _round(db: Session, number: int, correct: int, *, pending: bool = False):
    paper_id = f"paper-r{number}"
    assessment_id = f"assessment-r{number}"
    version_id = f"av-r{number}"
    release_id = f"release-r{number}"
    attempt_id = f"attempt-r{number}"
    db.add_all([
        E.Paper(paper_id=paper_id, user_id="teacher", title=f"第{number}轮",
                mode="retest", item_count=5, total_score=10),
        E.Assessment(assessment_id=assessment_id, classroom_id="class-1",
                     external_key=f"r{number}", quiz_code=f"R{number}", title=f"第{number}轮"),
        E.AssessmentVersion(assessment_version_id=version_id,
                            assessment_id=assessment_id, version_key=f"r{number}-v1",
                            paper_id=paper_id, scoring_status="graded"),
        E.AssessmentRelease(release_id=release_id, assessment_version_id=version_id,
                            teacher_user_id="teacher", title=f"第{number}轮",
                            release_type="retest", status="published",
                            source_attempt_id="attempt-source"),
        E.ReleaseRecipient(recipient_id=f"recipient-r{number}", release_id=release_id,
                           student_id="stu-1", status="submitted", attempt_id=attempt_id),
        E.Attempt(attempt_id=attempt_id, paper_id=paper_id, user_id="student-user",
                  student_id="stu-1", assessment_version_id=version_id,
                  mode="retest", status="submitted", grading_status="graded",
                  score=correct * 2, max_score=10, submitted_at=E.utcnow(),
                  retest_of_attempt_id="attempt-source"),
    ])
    question_ids = []
    for index in range(5):
        qid = f"q-r{number}-{index}"
        item_id = f"item-r{number}-{index}"
        question_ids.append(qid)
        db.add(E.PaperItem(item_id=item_id, paper_id=paper_id, position=index + 1,
                           question_id=qid, question_version=1, score=2,
                           chapter_id="ch11", snapshot={"question_type": "true_false",
                               "stem": f"第{index + 1}题", "answer_payload": {"value": True},
                               "main_kp_id": "kp.pca" if index < 3 else "kp.nmf"}))
        db.add(E.AnswerScore(score_id=f"score-r{number}-{index}",
                             attempt_id=attempt_id, paper_item_id=item_id,
                             question_id=qid, question_version=1, chapter_id="ch11",
                             kp_id="kp.pca" if index < 3 else "kp.nmf",
                             correct=(None if pending and index == 4 else index < correct),
                             awarded_score=2 if index < correct else 0, max_score=2,
                             scoring_status="needs_manual" if pending and index == 4 else "auto",
                             authority_status="teacher_key", teacher_verified=True))
    cycle = targeted_retest.create_cycle(
        db, student_id="stu-1", source_attempt_id="attempt-source",
        source_assessment_version_id="av-source", diagnosis_id="diag-source",
        release_id=release_id, workflow_run_id=f"workflow-{number}",
        round_no=number, question_ids=question_ids, chapter_ids=["ch11"],
        kp_ids=["kp.pca", "kp.nmf"], concept_names=["PCA", "NMF"])
    cycle.retest_attempt_id = attempt_id
    db.commit()
    return cycle


def test_threshold_keeps_full_five_question_denominator_and_all_rounds(db: Session):
    first = _round(db, 1, 3)
    targeted_retest.refresh_cycle(db, first, commit=True)
    assert first.status == "not_passed"
    assert first.correct_count == 3 and first.accuracy == 0.6
    assert targeted_retest.next_round(
        db, source_attempt_id="attempt-source", student_id="stu-1") == 2

    second = _round(db, 2, 4)
    targeted_retest.refresh_cycle(db, second, commit=True)
    assert second.status == "passed"
    assert second.correct_count == 4 and second.accuracy == 0.8
    history = targeted_retest.list_cycles(
        db, source_attempt_id="attempt-source", student_id="stu-1")
    assert [(row["round"], row["status"]) for row in history] == [
        (1, "not_passed"), (2, "passed")]


def test_ungraded_item_never_reduces_denominator_or_marks_passed(db: Session):
    cycle = _round(db, 1, 4, pending=True)
    targeted_retest.refresh_cycle(db, cycle, commit=True)
    assert cycle.status == "scoring_pending"
    assert cycle.correct_count is None and cycle.accuracy is None
    with pytest.raises(ValueError, match="待学生完成或评分"):
        targeted_retest.next_round(
            db, source_attempt_id="attempt-source", student_id="stu-1")


def test_teacher_intervention_keeps_fixed_five_question_round(
        db: Session, monkeypatch):
    captured: dict = {}

    class _Graph:
        def invoke(self, state, config):
            captured.update(state)
            return {**state, "status": "succeeded"}

    monkeypatch.setattr(
        classroom_workflow, "build_teacher_intervention_graph",
        lambda _db: _Graph())
    classroom_workflow.run_teacher_intervention(
        db, actor_user_id="teacher", student_id="stu-1",
        assessment_version_id="av-source", count=2)
    assert captured["count"] == 5
