"""错题复测的持久化回归测试（RC-12 / RC-13）。

两条底线：
  * AI 相似题必须真的写进 questions 表（而不是只存在于内存快照里）；
  * 相似题不得进入正式题库抽题池（active=False + 不可发布标记）。

模型调用被 monkeypatch 成返回固定候选 —— 这是对「写库逻辑」的确定性测试，
不代表真实 API 成功；真实生成见 docs/evidence/e2e_retest.json。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import entities as E
from backend.app.models.base import Base
from backend.app.services import retest_service, skill_runtime


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def seed_wrong_entry(db) -> str:
    q = E.Question(
        row_id=E.new_id("qrow"), question_id="q_origin", question_version=1,
        question_type="single_choice", stem="原题题干：关于过拟合",
        options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
        answer_payload={"key": "B"}, answer_status="verified",
        review_status="verified", active=True, score=2.0,
        main_kp_id="kp.regularization.l1l2", question_family_id="fam-1",
    )
    db.add(q)
    att = E.Attempt(attempt_id="attempt-1", paper_id="paper-1",
                    user_id="local_user", mode="quiz", status="submitted",
                    started_at=E.utcnow())
    db.add(att)
    w = E.WrongBookEntry(
        entry_id="wrong-1", user_id="local_user", attempt_id="attempt-1",
        question_id="q_origin", question_version=1,
        kp_id="kp.regularization.l1l2", family_id="fam-1",
        status="open", original_response={"key": "A"},
    )
    db.add(w)
    db.commit()
    return w.entry_id


CANDIDATE = {
    "stem": "相似题题干：下列哪种做法最能缓解过拟合？",
    "question_type": "single_choice",
    "options": [{"key": "A", "text": "增加层数"},
                {"key": "B", "text": "加入 L2 正则"}],
    "answer_payload": {"key": "B"},
    "explanation": "L2 正则限制参数幅度。",
    "difficulty": "中等",
    "cognitive_level": "理解",
    "scope_class": "in_scope",
}


def test_similar_question_row_is_persisted(db, monkeypatch):
    eid = seed_wrong_entry(db)
    before = db.query(E.Question).count()

    monkeypatch.setattr(skill_runtime, "run_skill", lambda *a, **k: {
        "status": "succeeded", "model": "fake-model",
        "result": {"candidates": [dict(CANDIDATE)]},
    })
    out = retest_service.generate_similar_questions(db, eid, count=1)

    assert out["ok"] is True
    ids = out["question_ids"]
    assert len(ids) == 1

    # 最关键的一条：题目必须真的在库里
    assert db.query(E.Question).count() == before + 1
    row = db.query(E.Question).filter(E.Question.question_id == ids[0]).one()
    assert row.stem == CANDIDATE["stem"]
    assert row.active is False, "AI 相似题不能进入正式题库抽题池"
    assert "不可发布" in (row.notes or "")
    assert row.created_by == "wrong_book_similar"


def test_similar_paper_items_point_to_existing_questions(db, monkeypatch):
    eid = seed_wrong_entry(db)
    monkeypatch.setattr(skill_runtime, "run_skill", lambda *a, **k: {
        "status": "succeeded", "model": "fake-model",
        "result": {"candidates": [dict(CANDIDATE)]},
    })
    out = retest_service.generate_similar_questions(db, eid, count=1)
    items = db.query(E.PaperItem).filter(
        E.PaperItem.paper_id == out["paper_id"]).all()
    assert len(items) == 1
    # 试卷项指向的题目在 questions 表里必须能查到（RC-13 的实质）
    q = db.query(E.Question).filter(
        E.Question.question_id == items[0].question_id).one_or_none()
    assert q is not None


def test_original_retest_keeps_wrong_entry_untouched(db):
    eid = seed_wrong_entry(db)
    before = db.query(E.WrongBookEntry).filter(
        E.WrongBookEntry.entry_id == eid).one()
    snapshot = (before.attempt_id, before.status, before.question_id)

    out = retest_service.retest_original(db, eid)
    assert out["ok"] is True and out["item_count"] == 1

    after = db.query(E.WrongBookEntry).filter(
        E.WrongBookEntry.entry_id == eid).one()
    assert (after.attempt_id, after.status, after.question_id) == snapshot
    # 新建了独立任务与试卷，但原错题记录行数不变
    assert db.query(E.WrongBookEntry).count() == 1
    assert db.query(E.RetestTask).count() == 1
