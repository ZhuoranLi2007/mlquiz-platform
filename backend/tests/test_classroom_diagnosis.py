"""Teacher classroom diagnosis: four views, evidence rules and privacy."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.api import classroom_routes
from backend.app.models import entities as E
from backend.app.models.base import Base, get_session
from backend.app.services import classroom_diagnosis as service
from backend.app.services import class_answer_review


def _attempt(db: Session, aid: str, student: str, version: str, paper: str,
             score: float, maximum: float, grading: str = "graded") -> None:
    db.add(E.Attempt(
        attempt_id=aid, paper_id=paper, user_id="teacher_local",
        student_id=student, assessment_version_id=version, mode="quiz",
        status="submitted", attendance_status="attended",
        grading_status=grading, is_effective=True, score=score,
        max_score=maximum, submitted_at="2025-10-10T10:00:00+00:00"))


def _answer(db: Session, aid: str, item: str, qid: str, version: int,
            secret: str) -> None:
    db.add(E.Answer(
        answer_id=E.new_id("ans"), attempt_id=aid, paper_item_id=item,
        question_id=qid, question_version=version,
        response={"text": secret}, raw_response=f"RAW_{secret}",
        response_status="answered", grading_status="graded"))


def _score(db: Session, sid: str, aid: str, item: str, qid: str,
           version: int, kp: str, correct: bool | None, awarded: float,
           maximum: float) -> None:
    db.add(E.AnswerScore(
        score_id=sid, attempt_id=aid, paper_item_id=item,
        question_id=qid, question_version=version, kp_id=kp,
        correct=correct, awarded_score=awarded, max_score=maximum,
        scoring_status="auto", score_provenance="teacher_workbook",
        authority_status="teacher_key", teacher_verified=True,
        source_ref=f"sheet:Scores:{sid}"))


def _seed(db: Session) -> None:
    db.add(E.User(user_id="teacher_local", display_name="Teacher"))
    db.add(E.Classroom(classroom_id="class-1", course_key="ml-2025",
                       name="机器学习班", term="2025秋"))
    for index in range(1, 5):
        sid = f"stu-{index}"
        db.add(E.Student(
            student_id=sid, external_key=f"SECRET_EXT_{index}",
            display_code=f"S{index:02d}",
            profile={"real_name": f"SECRET_NAME_{index}"}))
        db.add(E.ClassroomStudent(
            membership_id=f"member-{index}", classroom_id="class-1",
            student_id=sid, status="active"))
    db.add_all([
        E.KnowledgePoint(kp_id="kp.linear", chapter="1", name="线性模型"),
        E.KnowledgePoint(kp_id="kp.loss", chapter="1", name="损失函数"),
        E.Paper(paper_id="paper-1", user_id="teacher_local",
                title="固定小测一", mode="quiz", item_count=2,
                total_score=20),
        E.Paper(paper_id="paper-2", user_id="teacher_local",
                title="固定小测二", mode="quiz", item_count=1,
                total_score=5),
        E.Assessment(
            assessment_id="assessment-1", classroom_id="class-1",
            external_key="quiz-1", quiz_code="1.1", title="线性回归",
            topic="线性模型", scheduled_at="2025-10-01"),
        E.Assessment(
            assessment_id="assessment-2", classroom_id="class-1",
            external_key="quiz-2", quiz_code="1.2", title="回归复测",
            topic="线性模型", scheduled_at="2025-10-08"),
    ])
    db.add_all([
        E.AssessmentVersion(
            assessment_version_id="av-1", assessment_id="assessment-1",
            version_no=1, version_key="quiz-1:v1", paper_id="paper-1",
            content_hash="hash-one", scoring_status="verified",
            answer_key_source="teacher", teacher_verified=True),
        E.AssessmentVersion(
            assessment_version_id="av-2", assessment_id="assessment-2",
            version_no=1, version_key="quiz-2:v1", paper_id="paper-2",
            content_hash="hash-two", scoring_status="verified",
            answer_key_source="teacher", teacher_verified=True),
        E.PaperItem(
            item_id="item-1", paper_id="paper-1", position=1,
            question_id="q-linear", question_version=3, score=10,
            snapshot={"stem": "固定版本题干 v3",
                      "question_type": "single_choice",
                      "main_kp_id": "kp.linear",
                      "answer_payload": {"key": "A"}}),
        E.PaperItem(
            item_id="item-2", paper_id="paper-1", position=2,
            question_id="q-loss", question_version=2, score=10,
            snapshot={"stem": "损失函数固定题干 v2",
                      "question_type": "numeric",
                      "main_kp_id": "kp.loss"}),
        E.PaperItem(
            item_id="item-3", paper_id="paper-2", position=1,
            question_id="q-linear-transfer", question_version=1, score=5,
            snapshot={"stem": "线性模型迁移题",
                      "question_type": "true_false",
                      "main_kp_id": "kp.linear"}),
        # A newer bank version must never replace the fixed-paper snapshot.
        E.Question(row_id="qrow-latest", question_id="q-linear",
                   question_version=99, question_type="single_choice",
                   stem="LATEST_WRONG_STEM", main_kp_id="kp.loss",
                   active=True),
    ])
    _attempt(db, "attempt-s1-v1", "stu-1", "av-1", "paper-1", 10, 20)
    _attempt(db, "attempt-s2-v1", "stu-2", "av-1", "paper-1", 10, 20)
    _attempt(db, "attempt-s4-v1", "stu-4", "av-1", "paper-1", 0, 20,
             "pending_key")
    _attempt(db, "attempt-s1-v2", "stu-1", "av-2", "paper-2", 5, 5)
    _answer(db, "attempt-s1-v1", "item-1", "q-linear", 3,
            "SECRET_RESPONSE_S1_WRONG")
    _answer(db, "attempt-s1-v1", "item-2", "q-loss", 2,
            "SECRET_RESPONSE_S1_RIGHT")
    _answer(db, "attempt-s2-v1", "item-1", "q-linear", 3,
            "SECRET_RESPONSE_S2_RIGHT")
    _answer(db, "attempt-s2-v1", "item-2", "q-loss", 2,
            "SECRET_RESPONSE_S2_UNGRADED")
    _answer(db, "attempt-s4-v1", "item-1", "q-linear", 3,
            "SECRET_RESPONSE_S4_PENDING")
    _answer(db, "attempt-s1-v2", "item-3", "q-linear-transfer", 1,
            "SECRET_RESPONSE_S1_TRANSFER")
    # Genuine zero, valid positive, NULL/ungraded, pending-key zero, history.
    _score(db, "score-s1-i1", "attempt-s1-v1", "item-1", "q-linear", 3,
           "kp.linear", False, 0, 10)
    _score(db, "score-s1-i2", "attempt-s1-v1", "item-2", "q-loss", 2,
           "kp.loss", True, 10, 10)
    _score(db, "score-s2-i1", "attempt-s2-v1", "item-1", "q-linear", 3,
           "kp.linear", True, 10, 10)
    _score(db, "score-s2-i2", "attempt-s2-v1", "item-2", "q-loss", 2,
           "kp.loss", None, 0, 10)
    _score(db, "score-s4-i1", "attempt-s4-v1", "item-1", "q-linear", 3,
           "kp.linear", False, 0, 10)
    _score(db, "score-s1-i3", "attempt-s1-v2", "item-3",
           "q-linear-transfer", 1, "kp.linear", True, 5, 5)
    db.add(E.ImportRun(run_id="import-1", source_kind="teacher_workbook",
                       source_id="workbook-1", status="completed"))
    for index, status, attempt_id in [
        (1, "canonical", "attempt-s1-v1"),
        (2, "canonical", "attempt-s2-v1"),
        (3, "absent", None),
        (4, "canonical", "attempt-s4-v1"),
    ]:
        db.add(E.ImportedSubmission(
            submission_id=f"submission-{index}", import_run_id="import-1",
            dataset_id="dataset-1", source_sheet="SECRET_SHEET",
            source_row=index + 1, source_key=f"row-{index}",
            student_id=f"stu-{index}", assessment_version_id="av-1",
            status=status, canonical_attempt_id=attempt_id,
            raw_preserved=True,
            raw_payload={"name": f"SECRET_RAW_NAME_{index}",
                         "answer": f"SECRET_RAW_ANSWER_{index}"}))
    db.add_all([
        E.Resource(resource_id="resource-verified", platform="local",
                   title="线性模型复习",
                   url="https://example.invalid/linear",
                   kp_ids=["kp.linear"], reason="复习线性模型定义",
                   verified=True, source_type="local"),
        E.Resource(resource_id="resource-unverified", platform="local",
                   title="未核验资源",
                   url="https://example.invalid/unverified",
                   kp_ids=["kp.linear"], verified=False),
    ])
    db.commit()


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True)
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    _seed(session)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db: Session) -> TestClient:
    app = FastAPI()
    app.include_router(classroom_routes.router)

    def session_override():
        yield db

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[classroom_routes.require_teacher_actor] = (
        lambda: "teacher_local")
    return TestClient(app)


def test_overview_students_and_assessments_are_anonymous(db: Session):
    result = service.overview(db, "class-1")
    assert result["counts"] == {
        "students": 4, "assessments": 2,
        "historical_assessments": 2, "online_assessments": 0,
        "assessment_versions": 2, "submitted_attempts": 4,
        "historical_submitted_attempts": 0,
        "online_submitted_attempts": 4, "online_releases": 0,
        "pending_online_tasks": 0, "pending_key_attempts": 1,
        "recorded_absences": 1}
    students = service.list_students(db, "class-1")
    assert [x["display_code"] for x in students["items"]] == [
        "S01", "S02", "S03", "S04"]
    rendered = json.dumps(students, ensure_ascii=False)
    assert "SECRET_EXT" not in rendered and "SECRET_NAME" not in rendered
    assessments = service.list_assessments(db, "class-1")
    assert assessments["total"] == 2
    assert assessments["items"][0]["versions"][0]["absent_count"] == 1


def test_student_attempt_can_pin_an_older_attempt_of_same_version(
        db: Session, client: TestClient):
    """A later retake must not replace the exact evidence selected by teacher."""
    older = service.student_attempt(
        db, "stu-1", "av-1", attempt_id="attempt-s1-v1")
    assert older["attempt_id"] == "attempt-s1-v1"
    assert older["score_summary"]["correct_items"] == 1

    with pytest.raises(service.ClassroomDiagnosisNotFound):
        service.student_attempt(
            db, "stu-2", "av-1", attempt_id="attempt-s1-v1")

    response = client.get("/api/teacher/student-attempt", params={
        "student_id": "stu-1",
        "assessment_version_id": "av-1",
        "attempt_id": "attempt-s1-v1",
    })
    assert response.status_code == 200
    assert response.json()["attempt_id"] == "attempt-s1-v1"


def test_question_view_excludes_absent_pending_null_and_keeps_zero(db: Session):
    result = service.question_analysis(db, "av-1", paper_item_id="item-1")
    assert result["question"]["stem"] == "固定版本题干 v3"
    assert result["denominator"] == 2
    assert result["correct_count"] == result["incorrect_count"] == 1
    assert result["zero_score_count"] == 1
    assert result["correct_rate"] == 0.5
    assert result["excluded"] == {"absent": 1, "pending_key": 1}
    assert result["cause_analysis"]["status"] == "not_inferred"
    second = service.question_analysis(db, "av-1", paper_item_id="item-2")
    assert second["denominator"] == 1
    assert second["excluded"]["correct_null"] == 1
    rendered = json.dumps(result, ensure_ascii=False)
    for forbidden in ("LATEST_WRONG_STEM", "SECRET_RESPONSE", "SECRET_RAW",
                      "answer_payload"):
        assert forbidden not in rendered


def test_student_attempt_evidence_no_invented_cause_and_resources(db: Session):
    result = service.student_attempt(db, "stu-1", "av-1")
    assert result["score_summary"]["score_rate"] == 0.5
    first = result["items"][0]
    assert first["stem"] == "固定版本题干 v3"
    assert first["question_version"] == 3
    assert first["awarded_score"] == 0.0 and first["correct"] is False
    assert first["cause_status"] == "not_inferred"
    assert "不足以确定具体错因" in first["diagnostic_note"]
    assert first["response"] == {"text": "SECRET_RESPONSE_S1_WRONG"}
    assert result["weak_kp_ids"] == ["kp.linear"]
    assert result["coverage"] == {
        "total_items": 2,
        "reliable_scored_items": 2,
        "reference_scored_items": 0,
        "covered_kp_count": 2,
        "weak_kp_count": 1,
        "unmapped_item_count": 0,
        "scoring_coverage_rate": 1.0,
        "reference_coverage_rate": 0.0,
        "allocation_rule": "只将得分计入题目的主知识点；辅助知识点仅展示关联，不重复计分。",
    }
    assert result["knowledge_point_overview"][0]["diagnosis_status"] == "证据不足"
    assert result["knowledge_point_overview"][0]["needs_review"] is True
    assert result["retest_plan"]["ready"] is True
    assert result["items"][0]["answer_reference"]["authority_status"] == "teacher_verified"
    assert [r["resource_id"] for r in result["resources"]] == [
        "resource-verified"]
    absent = service.student_attempt(db, "stu-3", "av-1")
    assert absent["attendance_status"] == "absent"
    assert absent["score_summary"]["awarded_score"] is None


def test_retest_eligibility_uses_verified_wrong_answer_when_legacy_points_are_zero(db: Session):
    db.add(E.Chapter(chapter_id="ch-linear", code="1", title="线性模型", sort_order=1))
    item = db.get(E.PaperItem, "item-1")
    item.chapter_id = "ch-linear"
    item.score = 0
    score = db.get(E.AnswerScore, "score-s1-i1")
    score.chapter_id = "ch-linear"
    score.max_score = 0
    db.commit()

    result = service.student_attempt(db, "stu-1", "av-1")
    assert result["items"][0]["correct"] is False
    assert result["items"][0]["answer_reference"]["available"] is True
    assert result["retest_plan"]["ready"] is True
    assert result["retest_plan"]["chapter_ids"] == ["ch-linear"]
    assert result["retest_plan"]["evidence_question_positions"] == [1]
    assert result["retest_plan"]["item_count"] == 5
    assert result["retest_plan"]["accuracy_threshold"] == 0.8


def test_student_attempt_ranking_uses_full_same_version_scores_and_ties(db: Session):
    alone = service.student_attempt(db, "stu-1", "av-1")
    assert alone["class_comparison"]["status"] == "single_participant"
    assert alone["class_comparison"]["rank"] is None
    partial = service.student_attempt(db, "stu-2", "av-1")
    assert partial["class_comparison"]["status"] == "partial_scoring"
    assert partial["class_comparison"]["rank"] is None

    second = db.get(E.AnswerScore, "score-s2-i2")
    second.correct = False
    second.scoring_status = "auto"
    db.commit()
    result = service.student_attempt(db, "stu-1", "av-1")
    comparison = result["class_comparison"]
    assert comparison["status"] == "available"
    assert comparison["rank"] == 1
    assert comparison["participant_count"] == 2
    assert comparison["attendee_count"] == 3
    assert comparison["class_size"] == 4
    assert comparison["average_score"] == 10.0
    assert result["items"][0]["class_comparison"]["correct_rate"] == 0.5
    assert result["items"][0]["error_analysis"]["status"] == "evidence_grounded"


def test_student_history_only_compares_same_kp_normalized(db: Session):
    result = service.student_history(db, "stu-1", "class-1")
    assert result["raw_total_comparison"] is None
    assert "原始总分不比较" in result["comparison_rule"]
    kp = next(x for x in result["comparable_knowledge_points"]
              if x["kp_id"] == "kp.linear")
    assert kp["assessment_count"] == 2
    assert [x["accuracy"] for x in kp["series"]] == [0.0, 1.0]
    assert all("score_rate" in x and "max_score" not in x
               for x in kp["series"])
    assert "kp.loss" in {x["kp_id"] for x in
                         result["insufficient_knowledge_points"]}


def test_student_history_statistics_wrong_events_and_exact_repeat_group(db: Session):
    before = service.student_history(db, "stu-1", "class-1")
    assert before["statistics"] == {
        "average_score_rate": 0.75,
        "participated_assessments": 2,
        "scored_item_events": 3,
        "wrong_item_events": 1,
        "unique_wrong_question_count": 1,
        "ungraded_item_events": 0,
        "average_rule": "先计算每次考试可评分题目的得分率，再对有有效评分的考试等权平均；缺考和无有效评分考试不进入均值。",
    }
    # Different questions in the same KP are not merged as one repeated error.
    assert before["wrong_items"][0]["error_count"] == 1

    db.add_all([
        E.Paper(paper_id="paper-3", user_id="teacher_local",
                title="固定小测三", mode="quiz", item_count=1, total_score=10),
        E.Assessment(assessment_id="assessment-3", classroom_id="class-1",
                     external_key="quiz-3", quiz_code="1.3", title="线性复测",
                     topic="线性模型", scheduled_at="2025-10-15"),
        E.AssessmentVersion(assessment_version_id="av-3",
                            assessment_id="assessment-3", version_no=1,
                            version_key="quiz-3:v1", paper_id="paper-3",
                            content_hash="hash-three", scoring_status="verified",
                            answer_key_source="teacher", teacher_verified=True),
        E.PaperItem(item_id="item-4", paper_id="paper-3", position=1,
                    question_id="q-linear", question_version=3, score=10,
                    snapshot={"stem": "固定版本题干 v3",
                              "question_type": "single_choice",
                              "main_kp_id": "kp.linear",
                              "answer_payload": {"key": "A"}}),
    ])
    _attempt(db, "attempt-s1-v3", "stu-1", "av-3", "paper-3", 0, 10)
    _answer(db, "attempt-s1-v3", "item-4", "q-linear", 3, "B")
    _score(db, "score-s1-i4", "attempt-s1-v3", "item-4", "q-linear", 3,
           "kp.linear", False, 0, 10)
    db.commit()

    after = service.student_history(db, "stu-1", "class-1")
    assert after["statistics"]["wrong_item_events"] == 2
    assert after["statistics"]["unique_wrong_question_count"] == 1
    assert after["wrong_items"][0]["repeated"] is True
    assert after["wrong_items"][0]["error_count"] == 2
    assert len(after["wrong_items"][0]["occurrences"]) == 2
    assert all("response" in row and "awarded_score" in row
               for row in after["wrong_items"][0]["occurrences"])


def test_student_history_supports_real_date_and_paper_filters(db: Session):
    by_date = service.student_history(
        db, "stu-1", "class-1", date_from="2025-10-08", date_to="2025-10-08")
    assert [row["assessment_version_id"] for row in by_date["attempts"]] == ["av-2"]
    assert by_date["statistics"]["participated_assessments"] == 1
    by_paper = service.student_history(
        db, "stu-1", "class-1", assessment_version_id="av-1")
    assert [row["assessment_version_id"] for row in by_paper["attempts"]] == ["av-1"]
    assert by_paper["statistics"]["average_score_rate"] == 0.5


def test_class_view_distribution_statuses_zero_and_privacy(db: Session):
    result = service.class_assessment(
        db, "class-1", "av-1", selected_student_id="stu-1")
    assert result["participation"] == {
        "enrolled": 4, "scored": 2, "absent": 1, "pending_key": 1,
        "ungraded": 0, "no_submission": 0}
    assert result["score_distribution"]["mean"] == 0.75
    assert result["question_analysis"][0]["denominator"] == 2
    assert result["question_analysis"][0]["zero_score_count"] == 1
    assert result["question_analysis"][1]["denominator"] == 1
    statuses = {x["display_code"]: x["participation_status"]
                for x in result["anonymous_comparison"]}
    assert statuses == {"S01": "submitted", "S02": "submitted",
                        "S03": "absent", "S04": "pending_key"}
    assert result["selected_student"]["items"][0]["awarded_score"] == 0.0
    assert result["common_weaknesses"][0]["kp_id"] == "kp.linear"
    assert result["score_overview"]["average_score"] == 10.0
    assert result["score_overview"]["highest_score"] == 10.0
    assert result["score_overview"]["lowest_score"] == 10.0
    assert result["score_overview"]["max_score"] == 20.0
    assert result["score_overview"]["comparable_student_count"] == 1
    assert [row["label"] for row in result["score_distribution"]["bins"]] == [
        "0-49", "50-59", "60-69", "70-79", "80-89", "90-100"]
    assert result["high_frequency_errors"][0]["position"] == 1
    assert result["high_frequency_errors"][0]["incorrect_count"] == 1
    assert result["high_frequency_errors"][0]["answer_reference"]["payload"] == {"key": "A"}
    assert result["retest_plan"]["ready"] is True
    assert result["ai_analysis"]["common_errors"][0]["question_positions"] == [1]
    rendered = json.dumps(result, ensure_ascii=False)
    for forbidden in ("SECRET_RESPONSE", "SECRET_RAW", "SECRET_NAME",
                      "SECRET_EXT", "raw_payload", "raw_response",
                      "answer_payload", '"response"'):
        assert forbidden not in rendered


def test_class_scoring_reference_keeps_question_and_key_versions_together(db: Session):
    item = db.get(E.PaperItem, "item-1")
    db.add(E.Question(row_id="qrow-revised", question_id="q-linear",
                      question_version=4, question_type="single_choice",
                      stem="修订版题干", options=[{"key": "A", "text": "修订选项"}],
                      answer_payload={"key": "A"}, explanation="修订解析"))
    db.flush()
    original = service._snapshot_view(item, include_answer_reference=True)
    score = E.AnswerRegrade(answer_key_question_version=4,
                            authority_status="ai_auto_checked", teacher_verified=False,
                            detail={"expected": ["A"]})
    view = service._class_scoring_reference(db, item, original, [score])
    assert view["stem"] == "修订版题干"
    assert view["options"][0]["text"] == "修订选项"
    assert view["answer_reference"]["payload"] == {"key": "A"}
    assert view["original_question"]["stem"] == "固定版本题干 v3"
    assert view["is_scoring_revision"] is True
    score.detail = {"expected": ["B"]}
    conflict = service._class_scoring_reference(db, item, original, [score])
    assert conflict["scoring_reference_status"] == "missing"
    assert conflict["answer_reference"]["payload"] is None
    revised = db.get(E.Question, "qrow-revised")
    revised.answer_payload = {"key": "B"}
    db.flush()
    assert service._class_scoring_reference(db, item, original, [score])[
        "scoring_reference_status"] == "missing"
    score.answer_key_question_version = 5
    conflict = service._class_scoring_reference(db, item, original, [score])
    assert conflict["scoring_reference_status"] == "missing"


def test_class_answer_mapping_requires_reliable_evidence():
    answer = E.Answer(response={"answered": True}, response_status="answered")
    score = E.AnswerRegrade(detail={
        "given": ["A", "C"],
        "response_mapping": {"items": [
            {"method": "normalized_exact"},
            {"method": "unique_fuzzy_option_text", "score": .70, "margin": .15},
        ]},
    })
    assert service._class_answer_pattern(score, answer, {"A", "B", "C"}) == (
        "A, C", "reliable")
    score.detail["response_mapping"]["items"][1]["score"] = .69
    assert service._class_answer_pattern(score, answer, {"A", "B", "C"})[1] == "unmapped"
    score.detail["response_mapping"]["items"][1]["score"] = .70
    score.detail["response_mapping"]["items"][1]["margin"] = .14
    assert service._class_answer_pattern(score, answer, {"A", "B", "C"})[1] == "unmapped"
    answer.response = {"answered": False}
    assert service._class_answer_pattern(score, answer, {"A", "B", "C"}) == (
        "未作答", "unanswered")


def test_class_answer_review_agreement_disagreement_and_anonymity(
    db: Session, monkeypatch: pytest.MonkeyPatch,
):
    items = [{"position": position, "question_id": f"q{position}",
              "scoring_question_version": 2, "scoring_reference_status": "available",
              "stem": f"第{position}题", "options": [{"key": "A", "text": "甲"},
                                                   {"key": "B", "text": "乙"}],
              "answer_reference": {"payload": {"key": "A"},
                                   "explanation": "原有解析"}}
             for position in (1, 2)]
    payload = {"question_analysis": items,
               "high_frequency_errors": [dict(row) for row in items]}
    seen = []
    def fake_chat(_db, **kwargs):
        seen.append(kwargs)
        return {"content": json.dumps({"items": [
            {"position": 1, "answer_payload": {"key": "A"},
             "explanation": "甲符合题意。"},
            {"position": 2, "answer_payload": {"key": "B"},
             "explanation": "乙符合题意。"},
        ]}, ensure_ascii=False), "finish_reason": "stop", "call_id": "call-test"}
    monkeypatch.setattr(class_answer_review.model_provider, "chat", fake_chat)
    reviews = class_answer_review.review_class_answers(
        db, payload, run_id="run-test", actor_user_id="teacher_local",
        classroom_id="class-1", assessment_version_id="av-1")
    assert len(seen) == 1 and len(reviews) == 2
    assert [row["status"] for row in reviews] == ["agreed", "disagreed"]
    assert payload["question_analysis"][0]["answer_reference"]["explanation"] == "甲符合题意。"
    assert payload["question_analysis"][1]["answer_reference"]["explanation"] == "原有解析"
    sent = json.dumps(seen[0]["messages"], ensure_ascii=False)
    assert "student" not in sent and "answer_key" not in sent
    assert "SECRET" not in sent
    db.add(E.Diagnosis(diagnosis_id="diag-reviewed", user_id="teacher_local",
                       level="class", target_id="class-1", scope_type="class_assessment",
                       classroom_id="class-1", assessment_version_id="av-1",
                       payload={"answer_reviews": reviews}))
    db.commit()
    monkeypatch.setattr(class_answer_review.model_provider, "chat",
                        lambda *_args, **_kwargs: pytest.fail("有效复核应从诊断记录复用"))
    cached = class_answer_review.review_class_answers(
        db, payload, run_id="run-cached", actor_user_id="teacher_local",
        classroom_id="class-1", assessment_version_id="av-1")
    assert [row["status"] for row in cached] == ["agreed", "disagreed"]


def test_class_answer_review_failure_preserves_existing_explanation(
    db: Session, monkeypatch: pytest.MonkeyPatch,
):
    item = {"position": 1, "question_id": "q1", "scoring_question_version": 2,
            "scoring_reference_status": "available", "stem": "题干",
            "options": [{"key": "A", "text": "甲"}],
            "answer_reference": {"payload": {"key": "A"},
                                 "explanation": "原有解析"}}
    def unavailable(*_args, **_kwargs):
        raise class_answer_review.model_provider.ModelUnavailable("network_error", "连接失败")
    monkeypatch.setattr(class_answer_review.model_provider, "chat", unavailable)
    payload = {"question_analysis": [item], "high_frequency_errors": [dict(item)]}
    result = class_answer_review.review_class_answers(
        db, payload, run_id="run-failed", actor_user_id="teacher_local",
        classroom_id="class-1", assessment_version_id="av-1")
    assert result[0]["status"] == "unavailable"
    assert payload["question_analysis"][0]["answer_reference"]["explanation"] == "原有解析"
    monkeypatch.setattr(class_answer_review.model_provider, "chat",
                        lambda *_args, **_kwargs: {
                            "content": json.dumps({"items": [
                                {"position": [], "answer_payload": {"key": "A"},
                                 "explanation": "不能使用的解析"}]}),
                            "finish_reason": "stop"})
    malformed = class_answer_review.review_class_answers(
        db, payload, run_id="run-malformed", actor_user_id="teacher_local",
        classroom_id="class-1", assessment_version_id="av-1")
    assert malformed[0]["status"] == "unavailable"
    assert payload["question_analysis"][0]["answer_reference"]["explanation"] == "原有解析"


def test_class_ai_rejects_empty_output_and_never_returns_blank_lists(
    db: Session, monkeypatch: pytest.MonkeyPatch,
):
    payload = service.class_assessment(db, "class-1", "av-1")

    monkeypatch.setattr(service.model_provider, "chat", lambda *_args, **_kwargs: {
        "content": json.dumps({"summary": "   ", "recommendations": ["", {}]}),
        "finish_reason": "stop",
    })
    fallback = service._class_model_ai_analysis(db, payload, "run-empty")
    assert fallback["source"] == "evidence_rules"
    assert fallback["model_status"] == "invalid_output"
    assert fallback["summary"].strip()
    assert all(str(row).strip() for row in fallback["recommendations"])

    monkeypatch.setattr(service.model_provider, "chat", lambda *_args, **_kwargs: {
        "content": json.dumps({
            "summary": "第1题错误较集中，建议课堂重点讲解。",
            "common_errors": [{"label": "", "evidence": ""}],
            "concept_hypotheses": [{"name": ""}],
            "recommendations": ["", {}, {"advice": "讲解第1题后安排同类练习。"}],
        }, ensure_ascii=False),
        "finish_reason": "stop",
    })
    cleaned = service._class_model_ai_analysis(db, payload, "run-clean")
    assert cleaned["source"] == "deepseek"
    assert cleaned["recommendations"] == ["讲解第1题后安排同类练习。"]
    assert cleaned["common_errors"]
    assert cleaned["concept_hypotheses"]


def test_class_ai_has_a_useful_all_correct_summary():
    payload = {
        "score_overview": {"comparable_student_count": 3},
        "question_analysis": [
            {"position": 1, "denominator": 3, "incorrect_count": 0},
            {"position": 2, "denominator": 3, "incorrect_count": 0},
        ],
        "high_frequency_errors": [],
    }
    analysis = service._class_ai_fallback(payload)
    assert analysis["status"] == "no_wrong_items"
    assert "全部答对" in analysis["summary"]
    assert analysis["recommendations"]


def test_model_prose_hides_internal_vocabulary_and_ids():
    value = service._public_ai_text(
        "同一question_id有2次occurrences，采用all_or_nothing；"
        "来源paper_deadbeef12345678。")
    assert value == "同一题目有2次作答记录，采用整题计分；来源对应记录。"
    assert "question_id" not in value and "paper_" not in value


@pytest.mark.parametrize("body", [
    {"scope_type": "question_analysis", "assessment_version_id": "av-1",
     "paper_item_id": "item-1"},
    {"scope_type": "student_attempt", "student_id": "stu-1",
     "assessment_version_id": "av-1"},
    {"scope_type": "student_history", "student_id": "stu-1",
     "classroom_id": "class-1"},
    {"scope_type": "class_assessment", "classroom_id": "class-1",
     "assessment_version_id": "av-1"},
])
def test_api_persists_four_explicit_scopes(client: TestClient, db: Session,
                                           body: dict):
    response = client.post("/api/teacher/diagnoses/run", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    row = db.get(E.Diagnosis, data["diagnosis_id"])
    assert row is not None and row.scope_type == body["scope_type"]
    assert row.user_id == "teacher_local" and row.engine == "deterministic"


def test_latest_diagnosis_is_exact_to_student_and_version(
    client: TestClient, db: Session,
):
    saved = client.post("/api/teacher/diagnoses/run", json={
        "scope_type": "student_attempt",
        "student_id": "stu-1",
        "assessment_version_id": "av-1",
    })
    assert saved.status_code == 200
    latest = client.get("/api/teacher/diagnoses/latest", params={
        "scope_type": "student_attempt",
        "student_id": "stu-1",
        "assessment_version_id": "av-1",
    })
    assert latest.status_code == 200
    assert latest.json()["diagnosis_id"] == saved.json()["diagnosis_id"]
    missing = client.get("/api/teacher/diagnoses/latest", params={
        "scope_type": "student_attempt",
        "student_id": "stu-2",
        "assessment_version_id": "av-2",
    })
    assert missing.status_code == 404


def test_all_teacher_get_endpoints_are_mounted(client: TestClient):
    checks = [
        ("/api/teacher/overview", {"classroom_id": "class-1"}),
        ("/api/teacher/students", {"classroom_id": "class-1"}),
        ("/api/teacher/assessments", {"classroom_id": "class-1"}),
        ("/api/teacher/question-analysis",
         {"assessment_version_id": "av-1", "paper_item_id": "item-1"}),
        ("/api/teacher/student-attempt",
         {"student_id": "stu-1", "assessment_version_id": "av-1"}),
        ("/api/teacher/student-history",
         {"student_id": "stu-1", "classroom_id": "class-1"}),
        ("/api/teacher/class-assessment",
         {"classroom_id": "class-1", "assessment_version_id": "av-1",
          "selected_student_id": "stu-1"}),
    ]
    for path, params in checks:
        response = client.get(path, params=params)
        assert response.status_code == 200, (path, response.text)


def test_class_endpoint_never_returns_other_students_answers(client: TestClient):
    response = client.get("/api/teacher/class-assessment", params={
        "classroom_id": "class-1", "assessment_version_id": "av-1",
        "selected_student_id": "stu-1"})
    assert response.status_code == 200
    assert "SECRET_RESPONSE_S2" not in response.text
    assert "SECRET_RESPONSE_S4" not in response.text
    assert "SECRET_RAW" not in response.text


def test_class_true_false_preserves_false_as_reliable_response(db):
    item = db.get(E.PaperItem, "item-1")
    item.snapshot = {**item.snapshot, "question_type": "true_false",
                     "answer_payload": {"value": True}}
    for sid in (1, 2):
        answer = db.execute(select(E.Answer).where(
            E.Answer.attempt_id == f"attempt-s{sid}-v1",
            E.Answer.paper_item_id == "item-1")).scalars().one()
        answer.response = {"value": False}
        answer.response_status = "answered"
        score = db.get(E.AnswerScore, f"score-s{sid}-i1")
        score.correct = False
        score.awarded_score = 0
    db.commit()
    result = service.class_assessment(db, "class-1", "av-1")
    row = next(r for r in result["question_analysis"] if r["position"] == 1)
    assert row["common_wrong_answers"] == [{"answer": "错误", "count": 2}]
    assert row["answer_mapping_counts"]["reliable"] == 2
    evidence = service._v2_analysis_evidence("class_assessment", result)
    observed = next(r for r in evidence if r["positions"] == [1])
    assert observed["interpretation_allowed"] is True
    assert observed["common_missed_keys"] == []
    assert observed["common_extra_keys"] == []
