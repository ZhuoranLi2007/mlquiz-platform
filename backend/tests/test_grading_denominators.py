from sqlalchemy import select
from backend.tests.test_classroom_diagnosis import db
from backend.app.models import entities as E
from backend.app.services import attempt_service as service, classroom_content


def test_missing_media_keeps_submission_but_excludes_denominator(db, monkeypatch):
    monkeypatch.setattr(classroom_content, "source_content", lambda snap: {"media_incomplete": snap.get("main_kp_id") == "kp.loss"})
    monkeypatch.setattr("backend.app.workers.worker.create_job", lambda *a, **k: None)
    created = service.create_attempt(db, "paper-1", user_id="teacher_local", student_id="stu-1", assessment_version_id="av-1")
    aid = created["attempt_id"]
    service.save_answer(db, aid, "q-linear", {"key": "A"})
    result, first = service.submit_attempt(db, aid, request_key="partial-test")
    assert first and result["status"] == "submitted"
    assert result["grading_status"] == "partial"
    assert result["max_score"] == 10 and result["nominal_max_score"] == 20
    assert result["ungraded_item_count"] == 1 and result["ungraded_max_score"] == 10
    assert db.get(E.Attempt, aid).max_score == 10
    assert result["scope_breakdown"]["by_scope"]["course"]["max"] == 10
    assert result["scope_breakdown"]["by_scope"]["course"]["nominal_max"] == 20
    pending = db.execute(select(E.AnswerScore).where(E.AnswerScore.attempt_id == aid, E.AnswerScore.question_id == "q-loss")).scalar_one()
    assert pending.correct is None


def test_all_ungraded_is_not_zero_percent(db, monkeypatch):
    monkeypatch.setattr(classroom_content, "source_content", lambda snap: {"media_incomplete": True})
    monkeypatch.setattr("backend.app.workers.worker.create_job", lambda *a, **k: None)
    aid = service.create_attempt(db, "paper-1", user_id="teacher_local")["attempt_id"]
    result, _ = service.submit_attempt(db, aid, request_key="pending-test")
    assert result["status"] == "submitted" and result["grading_status"] == "awaiting_grading"
    assert result["score"] is None and result["max_score"] is None
    assert result["nominal_max_score"] == 20 and result["ungraded_item_count"] == 2


import pytest

@pytest.mark.parametrize("pending_target", ["version", "attempt"])
def test_pending_key_is_excluded_by_every_summary(db, pending_target):
    from backend.app.services import classroom_diagnosis as diagnosis, scope_service
    attempt = db.get(E.Attempt, "attempt-s1-v1")
    if pending_target == "version":
        db.get(E.AssessmentVersion, "av-1").scoring_status = "pending_key"
    else:
        attempt.grading_status = "pending_key"
    db.commit()
    summary = service.grading_summary(db, attempt)
    formal = diagnosis.student_attempt(db, "stu-1", "av-1")
    scope = scope_service.attempt_scope_breakdown(db, "paper-1", attempt.attempt_id)
    assert summary["scored_item_count"] == formal["score_summary"]["scored_items"] == 0
    assert summary["score"] is None and summary["max_score"] is None
    assert scope["by_scope"]["course"]["scored"] == 0
    assert scope["by_scope"]["course"]["max"] == 0
