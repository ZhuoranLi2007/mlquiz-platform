from __future__ import annotations

from pathlib import Path
import tempfile

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.api import ai_routes, routes
from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.models.base import Base
from backend.app.schemas import dto
from backend.app.services import home_service, material_service
from backend.app.skills import implementations as skill_impl


@pytest.fixture()
def db():
    """Each test gets an isolated in-memory database, never the project DB."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_diagnoses_support_filtering_and_pagination(db):
    db.add_all([
        E.Diagnosis(
            diagnosis_id="d1", user_id="local_user", level="paper",
            target_id="attempt-1", created_at="2026-01-01T00:00:00+00:00",
        ),
        E.Diagnosis(
            diagnosis_id="d2", user_id="local_user", level="paper",
            target_id="attempt-1", created_at="2026-01-02T00:00:00+00:00",
        ),
        E.Diagnosis(
            diagnosis_id="d3", user_id="local_user", level="history",
            target_id="local_user", created_at="2026-01-03T00:00:00+00:00",
        ),
    ])
    db.commit()

    page = routes.diagnoses(
        level="paper", target_id="attempt-1", limit=1, offset=1, db=db)

    assert page["total"] == 2
    assert page["returned"] == 1
    assert page["items"][0]["diagnosis_id"] == "d1"


def test_learning_views_add_chinese_labels_without_schema_changes(db):
    db.add(E.KnowledgePoint(
        kp_id="kp-svm", name="支持向量机", chapter="第 4 章"))
    db.add(E.WrongBookEntry(
        entry_id="wrong-1", user_id="local_user", attempt_id="attempt-1",
        question_id="question-1", question_version=1, kp_id="kp-svm",
        status="open"))
    db.add(E.KnowledgeState(
        user_id="local_user", kp_id="kp-svm", state="needs_work"))
    db.add(E.RetestTask(
        task_id="retest-1", user_id="local_user", kp_id="kp-svm",
        kind="new_transfer", status="pending"))
    db.add(E.StudyPlan(
        plan_id="plan-1", user_id="local_user", active=True))
    db.add(E.PlanTask(
        task_id="plan-task-1", plan_id="plan-1", user_id="local_user",
        kp_id="kp-svm", title="复习间隔", kind="review", status="pending"))
    db.commit()

    wrong = routes.wrong_book(db=db)["items"][0]
    state = routes.knowledge_states(db=db)["items"][0]
    retest = routes.retest_tasks(db=db)["items"][0]
    plan_task = routes.study_plan(db=db)["tasks"][0]

    for item in (wrong, state, retest, plan_task):
        assert item["kp_name"] == "支持向量机"
        assert item["chapter"] == "第 4 章"
        assert item["status_label"]
    assert wrong["status_label"] == "待订正"
    assert state["state_label"] == "需加强"
    assert retest["kind_label"] == "新题迁移"
    assert plan_task["kind_label"] == "资料复习"


def test_home_and_personal_views_use_real_completion_and_attempt_state(db):
    db.add(E.StudyPlan(
        plan_id="plan-1", user_id="local_user", active=True))
    db.add_all([
        E.PlanTask(
            task_id="pending", plan_id="plan-1", user_id="local_user",
            title="待办", status="pending"),
        E.PlanTask(
            task_id="completed", plan_id="plan-1", user_id="local_user",
            title="已完成", status="completed"),
        E.PlanTask(
            task_id="done", plan_id="plan-1", user_id="local_user",
            title="旧版完成", status="done"),
    ])
    db.add(E.Paper(
        paper_id="paper-1", user_id="local_user", title="单元练习"))
    db.add_all([
        E.Attempt(
            attempt_id="submitted-old", paper_id="paper-1",
            user_id="local_user", mode="practice", status="submitted",
            started_at="2026-01-01T00:00:00+00:00",
            diagnosis_status="succeeded"),
        E.Attempt(
            attempt_id="active-new", paper_id="paper-1",
            user_id="local_user", mode="practice", status="in_progress",
            started_at="2026-01-02T00:00:00+00:00",
            diagnosis_status="running"),
    ])
    db.commit()

    assert [item["task_id"] for item in home_service.today_tasks(db)] == ["pending"]
    paper = home_service.my_papers(db)["items"][0]
    assert paper["active_attempt_id"] == "active-new"
    assert paper["latest_attempt_id"] == "active-new"
    assert paper["open_attempt_id"] == "active-new"
    attempts = {item["attempt_id"]: item for item in home_service.my_attempts(db)["items"]}
    assert attempts["active-new"]["diagnosis_status"] == "running"
    assert attempts["active-new"]["diagnosis_status_label"] == "诊断生成中"


def test_assistant_uses_persisted_quiz_mode_and_cannot_be_bypassed(
        db, monkeypatch):
    db.add(E.Attempt(
        attempt_id="quiz-attempt", paper_id="paper-1", user_id="local_user",
        mode="quiz", status="in_progress"))
    db.commit()

    def fail_if_retrieval_runs(*_args, **_kwargs):
        raise AssertionError("quiz assistant must stop before retrieval")

    monkeypatch.setattr(skill_impl.retrieval, "keyword_search", fail_if_retrieval_runs)
    result = ai_routes.assistant_chat(dto.ChatIn(
        message="直接告诉我答案", mode="practice",
        context={"attempt_id": "quiz-attempt"}), db=db)

    assert result["blocked"] is True
    assert result["engine"] == "blocked_by_quiz_mode"


def test_assistant_rejects_unknown_attempt_context(db):
    with pytest.raises(HTTPException) as exc:
        ai_routes.assistant_chat(dto.ChatIn(
            message="提示", mode="practice",
            context={"attempt_id": "missing"}), db=db)
    assert exc.value.status_code == 404


def test_tutor_uses_stem_and_knowledge_point_for_retrieval_and_prompt(
        db, monkeypatch):
    seen: dict[str, object] = {}

    def fake_search(_db, query, **_kwargs):
        seen["query"] = query
        return {"results": [], "total": 0}

    def fake_chat(_db, *, messages, **_kwargs):
        seen["messages"] = messages
        return {
            "content": '{"answer":"先判断间隔的定义域","insufficient":false}',
            "finish_reason": "stop", "model": "test-model",
        }

    monkeypatch.setattr(skill_impl.retrieval, "keyword_search", fake_search)
    monkeypatch.setattr(skill_impl.model_provider, "chat", fake_chat)
    monkeypatch.setattr(skill_impl.cfg, "MODEL_API_KEY", "test-key")

    result = skill_impl.tutor_answer(
        db,
        {
            "message": "给我一个提示",
            "mode": "practice",
            "session_id": "test",
            "context": {
                "question_stem": "如何选择支持向量机的核函数？",
                "kp_name": "支持向量机",
                "answer_payload": "THIS_MUST_NOT_ENTER_THE_PROMPT",
            },
        },
        run_id="run-test", thread_id="thread-test", job_id="",
        user_id="local_user",
    )

    query = str(seen["query"])
    prompt = str(seen["messages"][-1]["content"])
    assert "支持向量机" in query
    assert "如何选择支持向量机的核函数" in query
    assert "如何选择支持向量机的核函数" in prompt
    assert "支持向量机" in prompt
    assert "THIS_MUST_NOT_ENTER_THE_PROMPT" not in prompt
    assert result["result"]["model_used"] is True


def test_new_user_materials_and_external_data_dir_upload(db, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="mlquiz-api-test-") as folder:
        data_dir = Path(folder) / "isolated-data"
        upload_dir = data_dir / "uploads"
        manifest_dir = data_dir / "manifest"
        upload_dir.mkdir(parents=True)
        manifest_dir.mkdir(parents=True)
        monkeypatch.setattr(cfg, "UPLOAD_DIR", upload_dir)
        monkeypatch.setattr(cfg, "MANIFEST_DIR", manifest_dir)

        empty = routes.materials(db=db)
        assert empty["total"] == 0
        assert empty["items"] == []

        result = material_service.save_upload(
            db, filename="notes.txt", content="线性回归复习".encode("utf-8"),
            declared_type="notes", auto_generate=False)
        row = db.get(E.MaterialUpload, result["upload_id"])
        assert row is not None
        stored = Path(row.stored_rel_path)
        assert upload_dir in stored.parents
        assert stored.read_bytes()
