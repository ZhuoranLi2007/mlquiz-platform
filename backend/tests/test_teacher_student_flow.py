from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.api import auth_routes, role_routes, routes
from backend.app.graphs import classroom_workflow
from backend.app.models import entities as E
from backend.app.models.base import Base, get_session
from backend.app.services import auth_service, bank_service, release_service


@pytest.fixture()
def flow(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'flow.db').as_posix()}",
                           connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = local()
    classroom = E.Classroom(
        classroom_id="class-1", course_key="course-1",
        name="测试班", term="2026秋", source="teacher_workbook",
        details={"official": True, "single_class_mode": True})
    demo_classroom = E.Classroom(
        classroom_id="class-demo", course_key="course-demo",
        name="隔离演示班", term="演示", source="demo_account",
        details={"test_fixture": True, "archived_from_official_stats": True})
    db.add_all([classroom, demo_classroom])
    users = [
        E.User(user_id="teacher", display_name="教师", role="teacher",
               password_hash=auth_service.hash_password("Teacher!2026")),
        E.User(user_id="student1", display_name="学生一", role="student",
               password_hash=auth_service.hash_password("Student1!2026")),
        E.User(user_id="student2", display_name="学生二", role="student",
               password_hash=auth_service.hash_password("Student2!2026")),
        E.User(user_id="student_demo1", display_name="旧演示学生", role="student",
               password_hash=auth_service.hash_password("StudentDemo1!2026")),
    ]
    students = [
        E.Student(student_id="stu-1", external_key="demo:1",
                  display_code="STU001", linked_user_id="student1",
                  source="classroom_import"),
        E.Student(student_id="stu-2", external_key="demo:2",
                  display_code="STU002", linked_user_id="student2",
                  source="classroom_import"),
        E.Student(student_id="stu-history", external_key="history:1",
                  display_code="STU003", linked_user_id=None,
                  source="classroom_import"),
        E.Student(student_id="stu-demo", external_key="demo:legacy:1",
                  display_code="DEMO001", linked_user_id="student_demo1",
                  source="demo_account"),
    ]
    db.add_all(users + students)
    for student in students[:3]:
        db.add(E.ClassroomStudent(membership_id=E.new_id("member"),
            classroom_id=classroom.classroom_id, student_id=student.student_id))
    db.add(E.ClassroomStudent(
        membership_id=E.new_id("member"), classroom_id=demo_classroom.classroom_id,
        student_id="stu-demo"))
    question = E.Question(
        row_id="row-1", question_id="q-1", question_version=1,
        origin="human", question_type="true_false", stem="监督学习使用标签。",
        options=[], score=5, main_kp_id="kp-supervised",
        question_family_id="fam-supervised", answer_payload={"value": True},
        answer_status="verified", answer_origin="teacher",
        review_status="verified", active=True)
    db.add(question)
    db.flush()
    paper = E.Paper(paper_id="paper-1", user_id="teacher",
                    title="监督学习小测", mode="quiz", total_score=5,
                    item_count=1, source="teacher")
    db.add(paper)
    db.add(E.PaperItem(item_id="item-1", paper_id=paper.paper_id,
        position=1, question_id=question.question_id, question_version=1,
        score=5, snapshot=bank_service.question_snapshot(question)))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(auth_routes.router)
    app.include_router(role_routes.router)
    app.include_router(routes.router)

    def override_session():
        session = local()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app), local
    finally:
        engine.dispose()


def _login(client: TestClient, user_id: str, password: str):
    response = client.post("/api/auth/login", json={
        "user_id": user_id, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["principal"]


def test_task_estimate_uses_fixed_item_types_and_hides_incomplete_papers(flow):
    _, local = flow
    with local() as db:
        assert release_service._task_paper_facts(db, "paper-1") == (1, 2)
        db.add(E.PaperItem(
            item_id="item-2", paper_id="paper-1", position=2,
            question_id="q-2", question_version=1, score=5,
            snapshot={"question_type": "multiple_choice", "stem": "选择适用方法",
                      "options": [{"key": "A", "text": "方法 A"}]}))
        db.commit()
        assert release_service._task_paper_facts(db, "paper-1") == (2, 4)
        item = db.get(E.PaperItem, "item-2")
        item.snapshot = {"question_type": "multiple_choice", "stem": "选择适用方法"}
        db.commit()
        assert release_service._task_paper_facts(db, "paper-1") == (2, None)


def test_auth_identity_scope_and_formal_assignment_isolation(flow):
    client, _local = flow
    _login(client, "teacher", "Teacher!2026")
    published = client.post("/api/teacher/releases", json={
        "title": "正式班专属任务", "paper_id": "paper-1",
        "student_ids": ["stu-1"], "classroom_id": "class-1",
        "idempotency_key": "identity-scope-formal-1",
    })
    assert published.status_code == 200, published.text
    recipient_id = published.json()["recipients"][0]["recipient_id"]
    client.post("/api/auth/logout")

    official = _login(client, "student1", "Student1!2026")
    assert official["student_code"] == "STU001"
    assert official["classroom_id"] == "class-1"
    assert official["classroom_name"] == "测试班"
    assert official["identity_scope"] == "official"
    assert official["official_classroom"] is True
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["identity_scope"] == "official"
    assignments = client.get("/api/student/assignments").json()
    assert assignments["total"] == 1
    assert assignments["items"][0]["item_count"] == 1
    assert assignments["items"][0]["estimated_minutes"] == 2
    assert assignments["items"][0]["recipient_id"] == recipient_id
    client.post("/api/auth/logout")

    demo = _login(client, "student_demo1", "StudentDemo1!2026")
    assert demo["student_code"] == "DEMO001"
    assert demo["classroom_id"] == "class-demo"
    assert demo["classroom_name"] == "隔离演示班"
    assert demo["identity_scope"] == "test"
    assert demo["official_classroom"] is False
    assert client.get("/api/student/assignments").json()["total"] == 0
    assert client.post(
        f"/api/student/assignments/{recipient_id}/start").status_code == 403
    client.post("/api/auth/logout")

    _login(client, "student2", "Student2!2026")
    assert client.get("/api/student/assignments").json()["total"] == 0
    assert client.post(
        f"/api/student/assignments/{recipient_id}/start").status_code == 403


def test_direct_paper_accepts_auto_checked_without_faking_teacher_approval(flow):
    client, local = flow
    with local() as db:
        db.add(E.Question(
            row_id="row-auto", question_id="q-auto", question_version=1,
            origin="imported", question_type="single_choice",
            stem="线性回归常用哪种损失？",
            options=[{"key": "A", "text": "平方误差"},
                     {"key": "B", "text": "0-1损失"}],
            score=3, answer_payload={"key": "A"},
            answer_status="ai_auto_checked",
            answer_origin="deepseek_v2_source_ai_validated",
            review_status="auto_checked", active=True,
            scope_class="in_scope"))
        db.commit()

    _login(client, "teacher", "Teacher!2026")
    response = client.post("/api/teacher/papers/from-questions", json={
        "title": "自动校验题组卷", "question_ids": ["q-auto"]})
    assert response.status_code == 200, response.text
    with local() as db:
        question = db.execute(select(E.Question).where(
            E.Question.question_id == "q-auto")).scalar_one()
        assert question.review_status == "auto_checked"
        assert question.answer_status == "ai_auto_checked"
        assert question.answer_origin == "deepseek_v2_source_ai_validated"


def test_release_answer_feedback_and_cross_student_isolation(flow, monkeypatch):
    client, local = flow
    monkeypatch.setattr(classroom_workflow, "run_post_submission",
                        lambda *_args, **_kwargs: {
                            "graph": "post_submission_graph",
                            "status": "succeeded", "stages": []})

    _login(client, "teacher", "Teacher!2026")
    published = client.post("/api/teacher/releases", json={
        "title": "第一次课堂发布", "paper_id": "paper-1",
        "student_ids": ["stu-1"], "classroom_id": "class-1"})
    assert published.status_code == 200, published.text
    release = published.json()
    recipient_id = release["recipients"][0]["recipient_id"]
    client.post("/api/auth/logout")

    _login(client, "student1", "Student1!2026")
    assignments = client.get("/api/student/assignments").json()
    assert assignments["total"] == 1
    started = client.post(
        f"/api/student/assignments/{recipient_id}/start")
    assert started.status_code == 200, started.text
    attempt_id = started.json()["attempt_id"]
    saved = client.put(f"/api/student/attempts/{attempt_id}/answers/q-1",
                       json={"response": {"value": True}})
    assert saved.status_code == 200, saved.text
    assert client.get(f"/api/student/attempts/{attempt_id}/review").status_code == 409
    client.post("/api/auth/logout")

    _login(client, "student2", "Student2!2026")
    assert client.get(f"/api/student/attempts/{attempt_id}").status_code == 403
    assert client.get(f"/api/student/feedback/{attempt_id}").status_code == 403
    # The legacy URL is protected too; changing the path parameter cannot
    # bypass the role-specific student endpoint.
    assert client.get(f"/api/attempts/{attempt_id}").status_code == 403
    client.post("/api/auth/logout")

    _login(client, "student1", "Student1!2026")
    submitted = client.post(
        f"/api/student/assignments/{recipient_id}/submit",
        json={"request_key": "submit-flow-1"})
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["score"] == 5
    review = client.get(f"/api/student/attempts/{attempt_id}/review")
    assert review.status_code == 200, review.text
    assert review.json()["score"] == body["score"]
    assert review.json()["items"][0]["score"]["awarded_score"] == 5
    client.post("/api/auth/logout")
    _login(client, "student2", "Student2!2026")
    assert client.get(f"/api/student/attempts/{attempt_id}/review").status_code == 403
    client.post("/api/auth/logout")
    _login(client, "student1", "Student1!2026")
    assert body["classroom_workflow"]["graph"] == "post_submission_graph"
    feedback = client.get("/api/student/feedback")
    assert feedback.status_code == 200
    assert feedback.json()["attempts"][0]["attempt_id"] == attempt_id
    detail = client.get(f"/api/student/feedback/{attempt_id}")
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["attempt_id"] == attempt_id
    assert detail_body["score_summary"]["correct_items"] == 1
    assert detail_body["items"][0]["response"] == {"value": True}
    assert detail_body["items"][0]["answer_reference"]["payload"] == {"value": True}
    assert "student_id" not in detail_body
    client.post("/api/auth/logout")

    _login(client, "teacher", "Teacher!2026")
    detail = client.get(f"/api/teacher/releases/{release['release_id']}")
    assert detail.status_code == 200
    assert detail.json()["submitted_count"] == 1
    assert detail.json()["recipients"][0]["score"] == 5

    with local() as db:
        attempt = db.get(E.Attempt, attempt_id)
        assert attempt.student_id == "stu-1"
        assert attempt.assessment_version_id
        answer = db.query(E.Answer).filter_by(attempt_id=attempt_id).one()
        score = db.query(E.AnswerScore).filter_by(attempt_id=attempt_id).one()
        assert answer.paper_item_id == "item-1"
        assert score.paper_item_id == "item-1"
        assert score.correct is True


def test_online_recipient_validation_and_idempotent_multi_student_release(
        flow, monkeypatch):
    client, local = flow
    monkeypatch.setattr(classroom_workflow, "run_post_submission",
                        lambda *_args, **_kwargs: {
                            "graph": "post_submission_graph",
                            "status": "succeeded", "stages": []})
    _login(client, "teacher", "Teacher!2026")

    offline = client.post("/api/teacher/releases", json={
        "title": "不能投递给历史档案", "paper_id": "paper-1",
        "student_ids": ["stu-history"], "classroom_id": "class-1",
        "idempotency_key": "offline-1"})
    assert offline.status_code == 409
    assert "没有可登录的在线账号" in offline.json()["detail"]

    body = {
        "title": "双人课堂任务", "paper_id": "paper-1",
        "student_ids": ["stu-1", "stu-2"], "classroom_id": "class-1",
        "idempotency_key": "multi-release-1",
    }
    first = client.post("/api/teacher/releases", json=body)
    assert first.status_code == 200, first.text
    retry = client.post("/api/teacher/releases", json=body)
    assert retry.status_code == 200, retry.text
    assert retry.json()["release_id"] == first.json()["release_id"]
    assert retry.json()["recipient_count"] == 2
    conflict = client.post("/api/teacher/releases", json={
        **body, "title": "同一个键却换了标题"})
    assert conflict.status_code == 409

    release = first.json()
    by_student = {item["student_id"]: item
                  for item in release["recipients"]}
    client.post("/api/auth/logout")

    attempt_ids = []
    for user_id, password, student_id, answer in (
        ("student1", "Student1!2026", "stu-1", True),
        ("student2", "Student2!2026", "stu-2", False),
    ):
        _login(client, user_id, password)
        assignments = client.get("/api/student/assignments").json()
        own = [item for item in assignments["items"]
               if item["release_id"] == release["release_id"]]
        assert len(own) == 1
        assert own[0]["student_id"] == student_id
        recipient_id = by_student[student_id]["recipient_id"]
        started = client.post(
            f"/api/student/assignments/{recipient_id}/start")
        assert started.status_code == 200, started.text
        attempt_id = started.json()["attempt_id"]
        attempt_ids.append(attempt_id)
        saved = client.put(
            f"/api/student/attempts/{attempt_id}/answers/q-1",
            json={"response": {"value": answer}})
        assert saved.status_code == 200, saved.text
        submitted = client.post(
            f"/api/student/assignments/{recipient_id}/submit",
            json={"request_key": f"submit-{student_id}"})
        assert submitted.status_code == 200, submitted.text
        if student_id == "stu-1":
            assert submitted.json()["newly_submitted"] is True
            repeated = client.post(
                f"/api/student/assignments/{recipient_id}/submit",
                json={"request_key": f"submit-{student_id}"})
            assert repeated.status_code == 200
            assert repeated.json()["newly_submitted"] is False
        client.post("/api/auth/logout")

        _login(client, "teacher", "Teacher!2026")
        detail = client.get(
            f"/api/teacher/releases/{release['release_id']}").json()
        expected = 1 if student_id == "stu-1" else 2
        assert detail["submitted_count"] == expected
        client.post("/api/auth/logout")

    assert len(set(attempt_ids)) == 2
    _login(client, "student1", "Student1!2026")
    assert client.get(
        f"/api/student/attempts/{attempt_ids[1]}").status_code == 403


def test_expired_closed_and_retest_assignment_visibility(flow):
    client, local = flow
    _login(client, "teacher", "Teacher!2026")
    expired_release = client.post("/api/teacher/releases", json={
        "title": "会过期的任务", "paper_id": "paper-1",
        "student_ids": ["stu-1"], "classroom_id": "class-1",
        "due_at": "2099-01-01T00:00:00",
        "idempotency_key": "expiry-test-1"}).json()
    closed_release = client.post("/api/teacher/releases", json={
        "title": "未发布任务", "paper_id": "paper-1",
        "student_ids": ["stu-1"], "classroom_id": "class-1",
        "idempotency_key": "closed-test-1"}).json()
    retest_release = client.post("/api/teacher/releases", json={
        "title": "章节针对性复测", "paper_id": "paper-1",
        "student_ids": ["stu-1"], "classroom_id": "class-1",
        "release_type": "retest", "idempotency_key": "retest-test-1"}).json()
    with local() as db:
        db.get(E.AssessmentRelease, expired_release["release_id"]).due_at = (
            "2020-01-01T00:00:00+00:00")
        db.get(E.AssessmentRelease, closed_release["release_id"]).status = "draft"
        db.commit()
    client.post("/api/auth/logout")

    _login(client, "student1", "Student1!2026")
    assignments = client.get("/api/student/assignments")
    assert assignments.status_code == 200
    by_release = {item["release_id"]: item
                  for item in assignments.json()["items"]}
    assert by_release[expired_release["release_id"]]["status"] == "expired"
    assert closed_release["release_id"] not in by_release
    assert by_release[retest_release["release_id"]]["release_type"] == "retest"
    expired_recipient = expired_release["recipients"][0]["recipient_id"]
    cannot_start = client.post(
        f"/api/student/assignments/{expired_recipient}/start")
    assert cannot_start.status_code == 409
    assert "超过截止时间" in cannot_start.json()["detail"]


def test_classroom_graphs_are_compiled(flow):
    _, local = flow
    with local() as db:
        status = classroom_workflow.graph_status(db)
    assert status["langgraph_available"] is True
    assert set(status["graphs"]) == {
        "teacher_intervention_graph", "post_submission_graph"}
    assert "generate_retest_candidates" in status["graphs"][
        "teacher_intervention_graph"]["nodes"]
    assert "compare_retest" in status["graphs"][
        "post_submission_graph"]["nodes"]


def test_official_classroom_and_student_history_records_include_absence(flow):
    client, local = flow
    with local() as db:
        run = E.ImportRun(
            run_id="import-history", source_kind="material_upload",
            source_id="history", status="completed", stage="completed")
        db.add(run)
        assessment = E.Assessment(
            assessment_id="assessment-history", classroom_id="class-1",
            external_key="course-1:history:1", quiz_code="1.1",
            title="线性回归历史小测", source="teacher_workbook",
            scheduled_at="2025-09-22", chapter_id="ch01")
        version = E.AssessmentVersion(
            assessment_version_id="av-history", assessment_id=assessment.assessment_id,
            version_no=1, version_key="course-1:history:1:v1",
            paper_id="paper-1", scoring_status="graded",
            answer_key_source="teacher_workbook")
        absent_paper = E.Paper(
            paper_id="paper-absent", user_id="teacher", title="缺考小测",
            mode="quiz", total_score=5, item_count=0, source="teacher_workbook")
        absent_assessment = E.Assessment(
            assessment_id="assessment-absent", classroom_id="class-1",
            external_key="course-1:history:2", quiz_code="1.2",
            title="逻辑回归历史小测", source="teacher_workbook",
            scheduled_at="2025-09-29", chapter_id="ch01")
        absent_version = E.AssessmentVersion(
            assessment_version_id="av-absent", assessment_id="assessment-absent",
            version_no=1, version_key="course-1:history:2:v1",
            paper_id="paper-absent", scoring_status="graded",
            answer_key_source="teacher_workbook")
        attempt = E.Attempt(
            attempt_id="attempt-history", paper_id="paper-1",
            user_id="historical_import", student_id="stu-1",
            assessment_version_id="av-history", status="submitted",
            submitted_at="2025-09-22T00:00:00+00:00", score=4,
            max_score=5, source_reported_total=4,
            source_import_run_id="import-history", grading_status="graded")
        db.add_all([assessment, absent_paper, absent_assessment,
                    version, absent_version, attempt])
        db.flush()
        db.add_all([
            E.ImportedSubmission(
                submission_id="submission-history", import_run_id=run.run_id,
                dataset_id="history-dataset", source_sheet="sheet1", source_row=1,
                source_key="stu1:1.1", student_id="stu-1",
                assessment_version_id="av-history", status="canonical",
                canonical_attempt_id="attempt-history", raw_preserved=True),
            E.ImportedSubmission(
                submission_id="submission-absent", import_run_id=run.run_id,
                dataset_id="history-dataset", source_sheet="sheet1", source_row=2,
                source_key="stu1:1.2", student_id="stu-1",
                assessment_version_id="av-absent", status="absent",
                raw_preserved=True),
        ])
        db.commit()

    _login(client, "teacher", "Teacher!2026")
    official = client.get("/api/teacher/official-classroom")
    assert official.status_code == 200
    assert official.json()["classroom_id"] == "class-1"
    client.post("/api/auth/logout")

    _login(client, "student1", "Student1!2026")
    response = client.get("/api/student/feedback")
    assert response.status_code == 200, response.text
    records = response.json()["exam_records"]
    assert len(records) == 2
    attended = next(row for row in records if row["quiz_code"] == "1.1")
    absent = next(row for row in records if row["quiz_code"] == "1.2")
    assert attended["source_type"] == "historical_import"
    assert attended["reported_score"] == 4
    assert attended["attempt_id"] == "attempt-history"
    assert absent["absent"] is True
    assert absent["attempt_id"] == ""
