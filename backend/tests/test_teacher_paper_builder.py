from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api import auth_routes, role_routes
from backend.app.models import entities as E
from backend.app.models.base import Base, get_session
from backend.app.services import auth_service, bank_service, paper_builder_service


@pytest.fixture()
def builder(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'builder.db').as_posix()}",
        connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    local = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with local() as db:
        db.add(E.User(user_id="teacher", display_name="教师", role="teacher",
                      password_hash=auth_service.hash_password("Teacher!2026")))
        db.add(E.User(user_id="student", display_name="演示学生", role="student",
                      password_hash=auth_service.hash_password("Student!2026")))
        db.add(E.KnowledgePoint(kp_id="kp-svm", chapter="第5章 支持向量机",
                                name="硬间隔", status="active"))
        db.add(E.Chunk(chunk_id="chunk-svm", document_id="doc-svm",
                       document_version_id="docv-svm",
                       content_raw="支持向量机通过最大化间隔获得分类边界。",
                       content_normalized="支持向量机通过最大化间隔获得分类边界。",
                       chapter="第5章 支持向量机", kp_ids=["kp-svm"]))
        classroom = E.Classroom(classroom_id="class-demo", course_key="demo",
                                name="演示班", term="2026秋", source="teacher_created")
        student = E.Student(student_id="stu-demo", external_key="demo:student",
                            display_code="DEMO001", linked_user_id="student",
                            source="demo_account")
        db.add_all([classroom, student])
        db.add(E.ClassroomStudent(membership_id="member-demo",
                                  classroom_id=classroom.classroom_id,
                                  student_id=student.student_id))
        questions = [
            E.Question(row_id="row-1", question_id="q-1", question_version=1,
                       origin="human", question_type="single_choice",
                       stem="硬间隔 SVM 的目标是什么？",
                       options=[{"key": "A", "text": "最大化间隔"},
                                {"key": "B", "text": "最小化样本数"}],
                       score=4, main_kp_id="kp-svm", difficulty="基础",
                       question_family_id="fam-1", answer_payload={"key": "A"},
                       answer_status="verified", answer_origin="teacher",
                       review_status="verified", scope_class="in_scope", active=True),
            E.Question(row_id="row-2", question_id="q-2", question_version=1,
                       origin="human", question_type="true_false",
                       stem="硬间隔 SVM 要求训练数据线性可分。", options=[],
                       score=2, main_kp_id="kp-svm", difficulty="中等",
                       question_family_id="fam-2", answer_payload={"value": True},
                       answer_status="verified", answer_origin="teacher",
                       review_status="verified", scope_class="in_scope", active=True),
            E.Question(row_id="row-3", question_id="q-bad", question_version=1,
                       origin="human", question_type="single_choice",
                       stem="缺少答案的题", options=[{"key": "A", "text": "是"},
                                                   {"key": "B", "text": "否"}],
                       score=2, main_kp_id="kp-svm", difficulty="困难",
                       question_family_id="fam-3", answer_payload={},
                       answer_status="unknown", answer_origin="none",
                       review_status="pending", scope_class="in_scope", active=True),
        ]
        db.add_all(questions)
        db.commit()

    app = FastAPI()
    app.include_router(auth_routes.router)
    app.include_router(role_routes.router)

    def override_session():
        with local() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app), local
    finally:
        engine.dispose()


def _login(client: TestClient):
    result = client.post("/api/auth/login", json={
        "user_id": "teacher", "password": "Teacher!2026"})
    assert result.status_code == 200, result.text


def test_teacher_bank_filters_and_exposes_answers_only_to_teacher(builder):
    client, _ = builder
    assert client.get("/api/teacher/question-bank").status_code == 401
    _login(client)
    response = client.get("/api/teacher/question-bank", params={
        "chapter": "第5章 支持向量机", "kp_id": "kp-svm",
        "qtype": "single_choice", "difficulty": "easy",
        "source": "teacher", "availability": "usable", "scope": "course",
        "keyword": "间隔",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["returned"] == 1
    assert body["items"][0]["question_id"] == "q-1"
    assert body["items"][0]["answer_payload"] == {"key": "A"}
    assert body["items"][0]["usable_for_quiz"] is True


def test_teacher_bank_uses_independent_chapter_without_fake_kp(builder):
    client, local = builder
    with local() as db:
        db.add(E.Chapter(chapter_id="ch04", code="04",
                         title="第4章 深度学习", sort_order=4))
        db.add(E.Question(
            row_id="row-chapter-only", question_id="q-chapter-only",
            question_version=1, origin="imported",
            question_type="true_false", stem="卷积层可以学习局部特征。",
            score=2, chapter_id="ch04", main_kp_id="",
            answer_payload={"value": True},
            answer_status="ai_auto_checked", review_status="auto_checked",
            answer_origin="deepseek_v2_source_ai_validated",
            scope_class="in_scope", active=True))
        db.commit()
    _login(client)
    response = client.get("/api/teacher/question-bank", params={
        "chapter": "第4章 深度学习", "keyword": "卷积层"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["returned"] == 1
    assert body["items"][0]["chapter_id"] == "ch04"
    assert body["items"][0]["chapter_title"] == "第4章 深度学习"
    assert body["items"][0]["main_kp_id"] == ""
    chapter_meta = next(item for item in body["metadata"]["chapters"]
                        if item["value"] == "第4章 深度学习")
    assert chapter_meta == {"value": "第4章 深度学习",
                            "chapter_id": "ch04", "count": 1}


def test_manual_draft_edit_publish_and_immutable_boundary(builder):
    client, local = builder
    _login(client)
    created = client.post("/api/teacher/paper-drafts/compose", json={
        "title": "SVM 手动组卷", "instructions": "独立完成", "mode": "manual",
        "selected_items": [
            {"question_id": "q-1", "score": 4},
            {"question_id": "q-2", "score": 2},
        ], "requirements": {"total_score": 100},
    })
    assert created.status_code == 200, created.text
    paper = created.json()
    assert paper["editable"] is True
    assert paper["total_score"] == 100
    assert [q["question_id"] for q in paper["questions"]] == ["q-1", "q-2"]

    updated = client.put(f"/api/teacher/paper-drafts/{paper['paper_id']}", json={
        "title": "SVM 手动组卷（调整后）", "instructions": "闭卷",
        "items": [
            {"question_id": "q-2", "question_version": 1, "score": 3},
            {"question_id": "q-1", "question_version": 1, "score": 7},
        ],
    })
    assert updated.status_code == 200, updated.text
    assert updated.json()["total_score"] == 10
    assert [q["question_id"] for q in updated.json()["questions"]] == ["q-2", "q-1"]

    release = client.post("/api/teacher/releases", json={
        "title": "SVM 发布", "paper_id": paper["paper_id"],
        "classroom_id": "class-demo", "student_ids": ["stu-demo"],
    })
    assert release.status_code == 200, release.text
    frozen = client.get(f"/api/teacher/paper-drafts/{paper['paper_id']}").json()
    assert frozen["editable"] is False
    assert frozen["assessment_version_id"]
    rejected = client.put(f"/api/teacher/paper-drafts/{paper['paper_id']}", json={
        "title": "不应成功", "instructions": "", "items": [
            {"question_id": "q-1", "question_version": 1, "score": 1}],
    })
    assert rejected.status_code == 409

    with local() as db:
        version = db.query(E.AssessmentVersion).filter_by(
            paper_id=paper["paper_id"]).one()
        assert version.content_hash
        assert db.query(E.PaperItem).filter_by(
            paper_id=paper["paper_id"]).count() == 2


def test_existing_ai_plan_mixed_lock_and_shortage_are_strict(builder, monkeypatch):
    client, _ = builder
    _login(client)
    automatic = client.post("/api/teacher/paper-drafts/compose", json={
        "title": "SVM 自动组卷", "mode": "ai", "selected_items": [],
        "requirements": {
            "kp_ids": ["kp-svm"], "type_targets": [
                {"question_type": "single_choice", "count": 1},
                {"question_type": "true_false", "count": 1}],
            "total_count": 2, "total_score": 20,
            "source_policy": "existing", "allow_ai_fill": False,
        },
    })
    assert automatic.status_code == 200, automatic.text
    assert automatic.json()["item_count"] == 2
    assert automatic.json()["total_score"] == 20

    chapter_only = client.post("/api/teacher/paper-drafts/compose", json={
        "title": "按章节自动分配题型", "mode": "ai", "selected_items": [],
        "requirements": {
            "chapters": ["第5章 支持向量机"], "type_targets": [],
            "auto_type_distribution": True, "total_count": 2,
            "source_policy": "existing", "allow_ai_fill": False,
        },
    })
    assert chapter_only.status_code == 200, chapter_only.text
    assert chapter_only.json()["item_count"] == 2

    mixed = client.post("/api/teacher/paper-drafts/compose", json={
        "title": "SVM 混合组卷", "mode": "mixed",
        "selected_items": [{"question_id": "q-1", "score": 4}],
        "requirements": {
            "kp_ids": ["kp-svm"], "type_targets": [
                {"question_type": "true_false", "count": 1}],
            "total_count": 1, "total_score": 12,
            "source_policy": "existing", "allow_ai_fill": False,
        },
    })
    assert mixed.status_code == 200, mixed.text
    assert mixed.json()["item_count"] == 2
    assert mixed.json()["questions"][0]["question_id"] == "q-1"

    shortage = client.post("/api/teacher/paper-drafts/compose", json={
        "title": "不能悄悄减题", "mode": "ai", "selected_items": [],
        "requirements": {
            "kp_ids": ["kp-svm"], "type_targets": [
                {"question_type": "multi_choice", "count": 2}],
            "total_count": 2, "source_policy": "existing",
            "allow_ai_fill": False,
        },
    })
    assert shortage.status_code == 409
    assert "缺" in shortage.json()["detail"]

    monkeypatch.setattr(bank_service, "ai_fill_questions",
                        lambda *_args, **_kwargs: [])
    ai_failure = client.post("/api/teacher/paper-drafts/compose", json={
        "title": "AI补题失败必须明示", "mode": "ai", "selected_items": [],
        "requirements": {
            "kp_ids": ["kp-svm"], "type_targets": [
                {"question_type": "multi_choice", "count": 2}],
            "total_count": 2, "source_policy": "mixed",
            "allow_ai_fill": True,
        },
    })
    assert ai_failure.status_code == 409
    assert "AI 未返回通过自动校验的新题" in ai_failure.json()["detail"]


def test_ai_candidate_requires_independent_answer_solver(builder, monkeypatch):
    _, local = builder
    with local() as db:
        candidate = E.Question(
            row_id="row-ai-1", question_id="q-ai-1", question_version=1,
            origin="ai", question_type="single_choice",
            stem="关于支持向量机最大间隔，下列说法正确的是？",
            options=[{"key": "A", "text": "间隔越大通常越有利于泛化"},
                     {"key": "B", "text": "间隔必须为零"}],
            score=2, main_kp_id="kp-svm", difficulty="中等",
            source_refs=[{"chunk_id": "chunk-svm"}],
            question_family_id="fam-ai-1", answer_payload={"key": "A"},
            answer_status="candidate", answer_origin="ai_candidate",
            review_status="pending", explanation="AI 候选，需核验",
            scope_class="in_scope", active=False)
        db.add(candidate)
        db.commit()

        monkeypatch.setattr(
            paper_builder_service.answer_pipeline, "ensure_answer_ready",
            lambda *_args, **_kwargs: {
                "ready": False, "status": "needs_attention",
                "generation_id": "gen-rejected"})
        rejected = paper_builder_service._auto_validate_candidates(
            db, [candidate], kp_ids=["kp-svm"],
            requested_types={"single_choice"}, already_selected=[])
        assert rejected == []
        assert candidate.active is False

        def solved(_db, question, **_kwargs):
            question.answer_payload = {"key": "A"}
            question.rubric = {"points": [{"id": "p1", "text": "选 A",
                                             "score": 2, "accept": ["A"]}]}
            question.explanation = "最大间隔的分类边界通常具有更好的泛化性。"
            question.answer_status = "ai_auto_checked"
            question.answer_origin = "deepseek_generated"
            question.review_status = "auto_checked"
            question.active = True
            return {"ready": True, "status": "succeeded",
                    "generation_id": "gen-solved"}

        monkeypatch.setattr(
            paper_builder_service.answer_pipeline, "ensure_answer_ready", solved)
        accepted = paper_builder_service._auto_validate_candidates(
            db, [candidate], kp_ids=["kp-svm"],
            requested_types={"single_choice"}, already_selected=[])
        assert accepted == [candidate]
        assert candidate.answer_status == "ai_auto_checked"
        assert "automatic_builder_v2" in candidate.notes
