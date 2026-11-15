"""Frozen server timing and read-only teacher answer markings."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.tests.test_classroom_diagnosis import db
from backend.app.models import entities as E
from backend.app.services import attempt_service, teacher_attempt_review


def test_elapsed_sources_keep_historical_demo_out_of_facts():
    attempt = E.Attempt(attempt_id="demo-one", status="submitted",
                        source_import_run_id="import-old", server_started_ms=0,
                        started_at="", submitted_at="", duration_ms=None)
    first = attempt_service.attempt_elapsed(attempt, 5)
    assert first == attempt_service.attempt_elapsed(attempt, 5)
    assert first[1] == "historical_display"
    assert 15 * 60_000 <= first[0] <= 40 * 60_000
    assert attempt.duration_ms is None
    assert 15 * 60_000 <= attempt_service.attempt_elapsed(attempt, 1)[0] <= 40 * 60_000
    assert 15 * 60_000 <= attempt_service.attempt_elapsed(attempt, 25)[0] <= 40 * 60_000
    attempt.source_import_run_id = ""
    assert attempt_service.attempt_elapsed(attempt, 5) == (None, "unavailable")
    attempt.started_at = "2026-11-14T10:27:16+00:00"
    attempt.submitted_at = "2026-11-14T10:27:29+00:00"
    assert attempt_service.attempt_elapsed(attempt, 5) == (13_000, "derived")
    attempt.duration_ms = 12_500
    assert attempt_service.attempt_elapsed(attempt, 5) == (12_500, "recorded")
    attempt.status = "in_progress"
    attempt.server_started_ms = 1_000
    assert attempt_service.attempt_elapsed(attempt, 5, now_ms=15_000) == (14_000, "live")


def test_first_submission_freezes_duration_and_replay_keeps_it(db, monkeypatch):
    monkeypatch.setattr("backend.app.workers.worker.create_job", lambda *a, **k: None)
    aid = attempt_service.create_attempt(db, "paper-1", user_id="teacher_local")["attempt_id"]
    attempt = db.get(E.Attempt, aid)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    attempt.server_started_ms = now_ms - 13_250
    db.commit()
    monkeypatch.setattr(attempt_service, "_now_ms", lambda: now_ms)
    first, created = attempt_service.submit_attempt(db, aid, request_key="duration-test")
    assert created is True and first["duration_ms"] == 13_250
    assert first["server_elapsed_ms"] == 13_250
    assert first["elapsed_source"] == "recorded"
    monkeypatch.setattr(attempt_service, "_now_ms", lambda: now_ms + 90_000)
    replay, created = attempt_service.submit_attempt(db, aid, request_key="duration-test")
    assert created is False and replay["duration_ms"] == 13_250
    assert replay["server_elapsed_ms"] == 13_250
    assert db.get(E.Attempt, aid).duration_ms == 13_250


def test_teacher_review_marks_selection_and_reference_on_same_options(db):
    item = db.get(E.PaperItem, "item-1")
    item.snapshot = {**item.snapshot,
                     "options": [{"key": "A", "text": "选项A"},
                                 {"key": "B", "text": "选项B"},
                                 {"key": "C", "text": "选项C"}],
                     "answer_payload": {"keys": ["A", "B"]},
                     "question_type": "multi_choice", "explanation": "逐项核对。"}
    answer = next(row for row in db.query(E.Answer).filter_by(
        attempt_id="attempt-s1-v1", paper_item_id="item-1"))
    answer.response = {"keys": ["A", "C"]}
    db.commit()
    attempt = db.get(E.Attempt, "attempt-s1-v1")
    review = teacher_attempt_review.teacher_attempt_review(db, attempt)
    row = review["items"][0]
    assert row["selected_keys"] == ["A", "C"]
    assert row["reference_keys"] == ["A", "B"]
    assert row["selection_status"] == "reliable"
    assert row["reference_available"] is True
    assert row["answer_reference"]["explanation"] == "逐项核对。"


def test_historical_review_uses_scoring_revision_and_rejects_uncertain_mapping(db):
    db.add(E.Question(row_id="review-revision", question_id="q-linear",
                      question_version=4, question_type="single_choice",
                      stem="评分修订题干", options=[
                          {"key": "A", "text": "修订A"},
                          {"key": "B", "text": "修订B"}],
                      answer_payload={"key": "B"}, explanation="修订解析"))
    db.add(E.ScoringRun(scoring_run_id="review-run", scope="classroom",
                        status="completed", completed_at="2026-11-15T00:00:00+00:00"))
    regrade = E.AnswerRegrade(
        regrade_id="review-regrade", scoring_run_id="review-run",
        original_score_id="score-s1-i1", attempt_id="attempt-s1-v1",
        paper_item_id="item-1", question_id="q-linear",
        original_question_version=3, answer_key_question_version=4,
        correct=False, awarded_score=0, max_score=10,
        authority_status="ai_auto_checked", detail={
            "given": ["A"], "expected": ["B"],
            "response_mapping": {"items": [{
                "method": "unique_fuzzy_option_text", "score": .69,
                "margin": .20}]},
        })
    db.add(regrade)
    db.commit()
    attempt = db.get(E.Attempt, "attempt-s1-v1")
    row = teacher_attempt_review.teacher_attempt_review(db, attempt)["items"][0]
    assert row["stem"] == "评分修订题干"
    assert row["original_question"]["stem"] == "固定版本题干 v3"
    assert row["reference_keys"] == ["B"]
    assert row["selection_status"] == "unmapped" and row["selected_keys"] == []
    regrade.detail = {**regrade.detail,
                      "response_mapping": {"items": [{
                          "method": "unique_fuzzy_option_text", "score": .70,
                          "margin": .15}]}}
    db.commit()
    row = teacher_attempt_review.teacher_attempt_review(db, attempt)["items"][0]
    assert row["selection_status"] == "reliable" and row["selected_keys"] == ["A"]
    regrade.detail = {**regrade.detail, "expected": ["A"]}
    db.commit()
    row = teacher_attempt_review.teacher_attempt_review(db, attempt)["items"][0]
    assert row["reference_available"] is False
    assert row["reference_keys"] == []
