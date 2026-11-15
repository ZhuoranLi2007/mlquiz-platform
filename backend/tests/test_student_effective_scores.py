"""Student result totals and question details use the same scored item facts."""
from backend.tests.test_classroom_diagnosis import db
from backend.app.models import entities as E
from backend.app.services import attempt_service


def _regrade(db):
    db.add(E.Question(
        row_id="student-review-v4", question_id="q-linear",
        question_version=4, question_type="single_choice",
        stem="修订后的评分题干", options=[
            {"key": "A", "text": "旧选项"},
            {"key": "B", "text": "评分答案"}],
        answer_payload={"key": "B"}, explanation="评分版本解析"))
    db.add(E.ScoringRun(
        scoring_run_id="student-run", scope="classroom:class-1",
        status="completed", completed_at="2026-11-13T05:12:11+00:00"))
    db.add(E.AnswerRegrade(
        regrade_id="student-regrade", scoring_run_id="student-run",
        original_score_id="score-s1-i1", attempt_id="attempt-s1-v1",
        paper_item_id="item-1", question_id="q-linear",
        original_question_version=3, answer_key_question_version=4,
        correct=False, awarded_score=5, max_score=10,
        authority_status="ai_auto_checked", scoring_status="auto",
        detail={"given": ["A"], "expected": ["B"]}))
    db.get(E.AnswerScore, "score-s1-i1").authority_status = "inferred_unverified"
    db.commit()


def test_student_result_uses_regrade_and_its_question_version(db):
    _regrade(db)
    original = db.get(E.Attempt, "attempt-s1-v1")
    source_score = original.score
    source_answer = db.query(E.Answer).filter_by(
        attempt_id=original.attempt_id, paper_item_id="item-1").one().response
    state = attempt_service.attempt_state(db, original.attempt_id)
    assert (state["score"], state["max_score"], state["scored_item_count"]) == (15, 20, 2)
    assert sum(row["awarded_score"] for row in state["scores_by_item"].values()) == state["score"]
    assert state["scores_by_item"]["item-1"]["awarded_score"] == 5
    assert state["questions"][0]["stem"] == "修订后的评分题干"
    assert state["questions"][0]["answer_payload"] == {"key": "B"}
    assert state["questions"][0]["original_question"]["stem"] == "固定版本题干 v3"
    assert state["answers_by_item"]["item-1"]["response"] == source_answer
    assert original.score == source_score
    assert db.get(E.AnswerScore, "score-s1-i1").awarded_score == 0


def test_partial_result_excludes_unscored_item_without_inventing_zero(db):
    _regrade(db)
    missing = db.get(E.AnswerScore, "score-s1-i2")
    missing.correct = None
    missing.scoring_status = "needs_manual"
    db.commit()
    state = attempt_service.attempt_state(db, "attempt-s1-v1")
    assert (state["score"], state["max_score"]) == (5, 10)
    assert (state["scored_item_count"], state["ungraded_item_count"]) == (1, 1)
    assert "item-2" not in state["scores_by_item"]
    assert state["answers_by_item"]["item-2"]["response"]


def test_newer_unrelated_run_does_not_hide_attempt_regrade(db):
    _regrade(db)
    db.add(E.ScoringRun(
        scoring_run_id="other-run", scope="classroom:other",
        status="completed", completed_at="2026-11-14T00:00:00+00:00"))
    db.add(E.AnswerRegrade(
        regrade_id="other-regrade", scoring_run_id="other-run",
        attempt_id="attempt-s2-v1", paper_item_id="item-1",
        question_id="q-linear", answer_key_question_version=4,
        correct=True, awarded_score=10, max_score=10,
        authority_status="ai_auto_checked", scoring_status="auto"))
    db.commit()
    state = attempt_service.attempt_state(db, "attempt-s1-v1")
    assert state["scores_by_item"]["item-1"]["awarded_score"] == 5
