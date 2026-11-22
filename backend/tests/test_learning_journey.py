from sqlalchemy import select

from backend.app.models import entities as E
from backend.app.services import learning_journey
from backend.app.services.feedback_workbench import build
from backend.app.api.role_routes import _student_question_view
from backend.tests.test_teacher_paper_builder import builder


def payload():
    return {"items": [{
        "position": 1, "paper_item_id": "item-source", "stem": "支持向量机的分类间隔。",
        "main_kp_id": "kp-svm", "knowledge_point": {"kp_id": "kp-svm", "name": "分类间隔"},
        "evidence_status": "scored", "correct": True,
        "chapter": {"title": "支持向量机"}, "answer_reference": {},
    }]}


def test_activity_is_owned_validated_and_idempotent(builder, monkeypatch):
    client, local = builder
    with local() as db:
        db.add(E.Attempt(attempt_id="source", paper_id="paper", user_id="student",
                         student_id="stu-demo", status="submitted", assessment_version_id="av-source"))
        db.add(E.Attempt(attempt_id="other-source", paper_id="other", user_id="other",
                         student_id="other-student", status="submitted"))
        db.add(E.Resource(resource_id="lesson", title="间隔课程", kp_ids=["kp-svm"]))
        db.commit()
    monkeypatch.setattr("backend.app.api.role_routes.classroom_diagnosis.student_attempt",
                        lambda *args, **kwargs: payload())
    endpoint = "/api/student/learning-activity"
    body = dict(attempt_id="source", kp_id="kp-svm", resource_id="lesson", action="resource_completed")
    assert client.post(endpoint, json=body).status_code == 401
    client.post("/api/auth/login", json={"user_id": "teacher", "password": "Teacher!2026"})
    assert client.post(endpoint, json=body).status_code == 403
    client.post("/api/auth/login", json={"user_id": "student", "password": "Student!2026"})
    assert client.post(endpoint, json={**body, "attempt_id": "other-source"}).status_code == 403
    assert client.post(endpoint, json={**body, "kp_id": "missing"}).status_code == 422
    assert client.post(endpoint, json={**body, "resource_id": "missing"}).status_code == 422
    assert client.post(endpoint, json={**body, "action": "mastered"}).status_code == 422
    for _ in range(2):
        assert client.post(endpoint, json=body).status_code == 200
    with local() as db:
        events = list(db.scalars(select(E.LearningEvent).where(E.LearningEvent.event_type == "resource_completed")))
        assert len(events) == 1
        result = learning_journey.attach(db, build(payload(), _student_question_view),
            user_id="student", attempt_id="source", submitted_at="2026-11-20T00:00:00+00:00")
        concept = result["concepts"][0]
        assert concept["learning_result"]["state"] == "insufficient"
        assert [event["kind"] for event in concept["timeline"]] == ["exam", "resource_completed"]
        assert concept["timeline"][0]["state"] == "insufficient"
        other = learning_journey.attach(db, build(payload(), _student_question_view),
            user_id="other", attempt_id="source", submitted_at="")
        assert len(other["concepts"][0]["timeline"]) == 1


def test_timeline_retains_partial_scoring_and_uncovered_evidence(builder):
    _, local = builder
    data = payload()
    data["retest_history"] = [{
        "round": 1, "kp_ids": ["kp-svm"], "items": [], "status": "passed",
        "published_at": "2026-11-20T00:00:00+00:00",
        "submitted_at": "2026-11-21T00:00:00+00:00",
    }]
    with local() as db:
        concept = learning_journey.attach(db, build(data, _student_question_view),
            user_id="student", attempt_id="source", submitted_at="")["concepts"][0]
        retest = next(event for event in concept["timeline"] if event["kind"] == "retest")
        assert retest["state"] == "not_covered"
        assert not retest["independent"]
        assert concept["learning_result"]["state"] == "insufficient"
