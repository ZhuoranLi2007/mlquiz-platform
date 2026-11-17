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
from backend.app.services import retest_service, skill_runtime, answer_pipeline


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
    "stem": "某研究组训练模型时发现训练集误差持续下降，但是独立留出的验证集误差逐步上升。研究者希望在保持输入特征和训练样本不变的情况下控制模型复杂度，同时仍允许模型学习有效信号。在其余条件相同时，下列哪种做法最可能缓解这一现象？",
    "question_type": "single_choice",
    "options": [{"key": "A", "text": "增加层数"},
                {"key": "B", "text": "加入 L2 正则"}],
    "answer_payload": {"key": "B"},
    "explanation": "L2 正则限制参数幅度。",
    "difficulty": "中等",
    "cognitive_level": "理解",
    "scope_class": "in_scope",
}


def mock_candidate_pipeline(db, monkeypatch, *, verified=True):
    db.add(E.Chunk(chunk_id="source", document_id="doc", document_version_id="v1",
        content_raw="正则化约束模型复杂度，以缓解过拟合。"))
    db.commit()
    def generate(*args, **kwargs):
        q = E.Question(row_id="ai-full", question_id="ai-full", question_version=1,
            origin="ai", main_kp_id="kp.regularization.l1l2", active=False,
            review_status="pending", answer_status="candidate", score=2,
            source_refs=[{"chunk_id":"source"}], **CANDIDATE)
        db.add(q); db.commit()
        # Match the real skill contract: ID + short preview, NO options.
        return {"status":"succeeded", "model":"TEST_DOUBLE_NOT_LIVE",
            "result":{"candidates":[{"question_id":q.question_id,"stem":q.stem[:80]}]}}
    def solve(_db, q, **kwargs):
        if not verified:
            return {"ready":False,"status":"needs_attention"}
        q.answer_status="ai_auto_checked";q.review_status="auto_checked"
        q.rubric={"points":[{"id":"p1","text":"L2正则化","score":q.score,"accept":["B"]}]}
        q.explanation="L2正则化约束权重幅度。"
        return {"ready":True,"generation_id":"test-only"}
    monkeypatch.setattr(skill_runtime,"run_skill",generate)
    monkeypatch.setattr(answer_pipeline,"ensure_answer_ready",solve)


def test_similar_question_row_is_persisted(db, monkeypatch):
    eid = seed_wrong_entry(db)
    before = db.query(E.Question).count()

    mock_candidate_pipeline(db, monkeypatch)
    out = retest_service.generate_similar_questions(db, eid, count=1)

    assert out["ok"] is True
    ids = out["question_ids"]
    assert len(ids) == 1

    # 最关键的一条：题目必须真的在库里
    assert db.query(E.Question).count() == before + 1
    row = db.query(E.Question).filter(E.Question.question_id == ids[0]).one()
    assert row.stem == CANDIDATE["stem"]
    assert len(row.stem)>80 and row.options == CANDIDATE["options"]
    assert row.source_refs == [{"chunk_id":"source"}]
    assert row.answer_status == "ai_auto_checked"
    assert row.active is False, "AI 相似题不能进入正式题库抽题池"
    assert "不可发布" in (row.notes or "")
    assert row.created_by == "wrong_book_similar"


def test_similar_paper_items_point_to_existing_questions(db, monkeypatch):
    eid = seed_wrong_entry(db)
    mock_candidate_pipeline(db, monkeypatch)
    out = retest_service.generate_similar_questions(db, eid, count=1)
    items = db.query(E.PaperItem).filter(
        E.PaperItem.paper_id == out["paper_id"]).all()
    assert len(items) == 1
    # 试卷项指向的题目在 questions 表里必须能查到（RC-13 的实质）
    q = db.query(E.Question).filter(
        E.Question.question_id == items[0].question_id).one_or_none()
    assert q is not None
    assert items[0].snapshot["stem"] == CANDIDATE["stem"]
    assert items[0].snapshot["options"] == CANDIDATE["options"]


def test_failed_independent_answer_never_creates_similar_attempt(db, monkeypatch):
    eid=seed_wrong_entry(db)
    mock_candidate_pipeline(db,monkeypatch,verified=False)
    result=retest_service.generate_similar_questions(db,eid)
    assert result['ok'] is False
    assert db.query(E.Paper).count()==0 and db.query(E.Attempt).count()==1
    assert db.query(E.Question).filter(E.Question.question_id=='ai-full').one().active is False


def test_ai_only_composition_requires_independent_validation(db, monkeypatch):
    eid=seed_wrong_entry(db)
    mock_candidate_pipeline(db,monkeypatch,verified=False)
    result=retest_service.build_retest_paper(db,entry_ids=[eid],scope='selected',count=1,use_original=False)
    assert result['ok'] is False and result['ai_count']==0
    assert db.query(E.Paper).count()==0


def test_mixed_composition_reports_only_actual_sources(db, monkeypatch):
    eid=seed_wrong_entry(db)
    mock_candidate_pipeline(db,monkeypatch)
    result=retest_service.build_retest_paper(db,entry_ids=[eid],scope='selected',count=2)
    assert result['original_count']==1 and result['ai_count']==1
    paper=db.get(E.Paper,result['paper_id'])
    assert paper.blueprint['source_counts']=={'original_reused':1,'ai_generated':1,'bank_selected':0}
    assert db.query(E.Question).filter(E.Question.question_id=='ai-full').one().active is False


def test_bank_retest_does_not_fill_with_unrelated_knowledge(db):
    seed_wrong_entry(db)
    task=E.RetestTask(task_id='task',kp_id='kp.different',family_id='other-family')
    questions,report=retest_service.choose_new_questions(db,task,'local_user',count=2)
    assert questions==[] and report['shortfall']==2
    assert report['kp_constraint_relaxed'] is False


def test_requested_transfer_does_not_invent_independent_family(db, monkeypatch):
    eid=seed_wrong_entry(db)
    mock_candidate_pipeline(db,monkeypatch)
    result=retest_service.generate_similar_questions(db,eid,evidence_type='transfer')
    q=db.query(E.Question).filter(E.Question.question_id==result['question_ids'][0]).one()
    assert q.question_family_id=='fam-1'


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
