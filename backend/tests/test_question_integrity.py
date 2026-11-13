"""Regression tests for the data-integrity fixes (V3.4).

These are pure/deterministic and need no network. Each test pins down a defect
that was reproduced on the real database, so the bug cannot silently come back.

Covered:
  RC-1/RC-4  答案与选项结构自洽性（{"key": "Z"} 不能再被当成有效答案）
  RC-4       question_type='unknown' 的有效题型推断
  RC-5       选项正文缺失时保留占位而不是静默丢弃
  RC-7       bank_stats 只统计 active 题目
  RC-2       Diagnosis 的 upsert / merge（LangGraph 落库依赖它）
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import entities as E
from backend.app.models.base import Base
from backend.app.services import answer_pipeline, bank_service, skill_runtime
from backend.app.services.question_dto import normalize_options, student_view
from backend.app.services.scoring import effective_type


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


def make_question(**kw) -> E.Question:
    base = dict(row_id=E.new_id("qrow"), question_id="q1", question_version=1,
                question_type="single_choice",
                options=[{"key": "A", "text": "选项甲"}, {"key": "B", "text": "选项乙"}],
                answer_payload={"key": "B"}, answer_status="ai_auto_checked",
                review_status="auto_checked", active=True, score=2.0)
    base.update(kw)
    return E.Question(**base)


# --------------------------------------------------------------- RC-1 / RC-4
def test_answer_key_outside_options_is_rejected():
    q = make_question(answer_payload={"key": "Z"})
    ok, reason = answer_pipeline.answer_structurally_valid(q)
    assert ok is False
    assert "Z" in reason
    # 即使带着"已通过自动检查"的状态，也不能算可用答案
    assert answer_pipeline.is_answer_ready(q) is False


def test_answer_matching_options_is_accepted():
    q = make_question(answer_payload={"key": "B"})
    ok, reason = answer_pipeline.answer_structurally_valid(q)
    assert ok is True and reason == ""
    assert answer_pipeline.is_answer_ready(q) is True


def test_multi_choice_keys_subset_of_options():
    opts = [{"key": k, "text": f"t{k}"} for k in "ABCD"]
    good = make_question(question_type="multi_choice", options=opts,
                         answer_payload={"keys": ["A", "C"]})
    bad = make_question(question_type="multi_choice", options=opts,
                        answer_payload={"keys": ["A", "F"]})
    assert answer_pipeline.answer_structurally_valid(good)[0] is True
    assert answer_pipeline.answer_structurally_valid(bad)[0] is False


def test_true_false_and_numeric_shapes():
    tf_ok = make_question(question_type="true_false", options=[],
                          answer_payload={"value": True})
    tf_bad = make_question(question_type="true_false", options=[],
                           answer_payload={"key": "A"})
    num_ok = make_question(question_type="numeric", options=[],
                           answer_payload={"value": 3.75, "tol": 0.01})
    num_bad = make_question(question_type="numeric", options=[],
                            answer_payload={"value": "不是数字"})
    assert answer_pipeline.answer_structurally_valid(tf_ok)[0] is True
    assert answer_pipeline.answer_structurally_valid(tf_bad)[0] is False
    assert answer_pipeline.answer_structurally_valid(num_ok)[0] is True
    assert answer_pipeline.answer_structurally_valid(num_bad)[0] is False


def test_unknown_type_is_inferred_from_answer_shape():
    q = make_question(question_type="unknown", answer_payload={"key": "B"})
    assert effective_type({"question_type": "unknown",
                           "answer_payload": {"key": "B"}}) == "single_choice"
    view = student_view(q)
    assert view["question_type_effective"] == "single_choice"
    assert view["question_type_inferred"] is True


# ------------------------------------------------------------------- RC-5
def test_blank_option_text_is_kept_as_placeholder():
    raw = [{"key": "A", "text": "有正文"}, {"key": "B", "text": ""},
           {"key": "C"}, {"key": "D", "text": "也有正文"}]
    opts, incomplete, hint = normalize_options(raw, "single_choice")
    # 四项都要保留，否则前端会以为"只有 3 个选项"
    assert [o["key"] for o in opts] == ["A", "B", "C", "D"]
    assert [o["key"] for o in opts if o["missing_text"]] == ["B", "C"]
    assert incomplete is True
    assert "B" in hint and "C" in hint


def test_complete_options_are_not_marked_incomplete():
    raw = [{"key": "A", "text": "x"}, {"key": "B", "text": "y"}]
    opts, incomplete, hint = normalize_options(raw, "single_choice")
    assert len(opts) == 2 and incomplete is False and hint == ""


def test_judge_question_without_options_is_not_incomplete():
    _, incomplete, _ = normalize_options([], "true_false")
    assert incomplete is False


def test_string_option_list_is_supported():
    opts, incomplete, _ = normalize_options(["甲", "乙", "丙"], "single_choice")
    assert [o["key"] for o in opts] == ["A", "B", "C"]
    assert incomplete is False


# ------------------------------------------------------------------- RC-7
def test_bank_stats_counts_active_only(db):
    db.add_all([
        make_question(row_id="r1", question_id="q1", question_version=1,
                      active=False),
        make_question(row_id="r2", question_id="q1", question_version=2,
                      active=True),
        make_question(row_id="r3", question_id="q2", question_version=1,
                      active=True),
    ])
    db.commit()
    stats = bank_service.bank_stats(db)
    assert stats["total_versions"] == 3
    assert stats["active"] == 2
    assert sum(stats["by_type"].values()) == 2
    assert sum(stats["all_versions_by_type"].values()) == 3


# ------------------------------------------------------------------- RC-2
def test_upsert_diagnosis_persists_and_merges(db):
    did = skill_runtime.upsert_diagnosis(
        db, user_id="local_user", level="paper", target_id="attempt-1",
        payload={"weak_points": ["kp.x"]},
        evidence_refs=[{"score_id": "s1"}], uncertainties=["u"],
        next_actions=["a"], skill_version="skills-1.0/diagnose_paper-1.0",
        run_id="run_1", engine="deterministic")
    assert did

    # 同一 (level, target, skill_version) 再写一次：更新而不是新增
    skill_runtime.upsert_diagnosis(
        db, user_id="local_user", level="paper", target_id="attempt-1",
        payload={"three_layers": [{"key": "L1", "accuracy": 0.5}]},
        skill_version="skills-1.0/diagnose_paper-1.0",
        run_id="run_2", engine="langgraph", merge_payload=True)

    rows = db.query(E.Diagnosis).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.payload["weak_points"] == ["kp.x"]          # merge 不丢旧字段
    assert row.payload["three_layers"][0]["key"] == "L1"   # 新字段写进去了
    assert row.engine == "langgraph"
    # evidence_refs 去重累加
    assert [e["score_id"] for e in row.evidence_refs] == ["s1"]
