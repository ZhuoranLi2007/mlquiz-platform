from sqlalchemy import select
from backend.tests.test_classroom_diagnosis import db
from backend.app.models import entities as E
from backend.app.services import classroom_diagnosis as d
from backend.app.services.evidence_strength import summarize_independence


def ev(i, family="", content="", correct=False, version="v1"):
    return {"score_id": str(i), "family_id": family, "content_fingerprint": content,
            "correct": correct, "assessment_id": version, "assessment_version_id": version, "computed_at": str(i)}


def test_one_error_is_observation_not_persistent_weakness():
    result = summarize_independence([ev(1, family="f1")])
    assert result["state"] == "证据不足" and result["independent_family_count"] == 1


def test_duplicate_content_or_family_does_not_inflate_independence():
    result = summarize_independence([ev(1, family="f1", content="same"), ev(2, family="f2", content="same", version="v2"), ev(3, family="f2", content="variant", version="v3")])
    assert result["independent_family_count"] == 1 and result["state"] == "证据不足"


def test_distinct_families_across_tests_support_only_heuristic():
    result = summarize_independence([ev(1, family="f1"), ev(2, family="f2"), ev(3, family="f3", version="v2")])
    assert result["state"] == "需加强" and result["independent_family_count"] == 3
    assert "不是统计证明" in result["rule"]


def test_inferred_reference_works_with_no_formal_scores(db):
    for score in db.execute(select(E.AnswerScore)).scalars():
        score.authority_status="inferred_unverified"
        score.teacher_verified=False
    db.commit()
    one = d.student_attempt(db,"stu-1","av-1")
    assert one["score_summary"]["scored_items"] == 0
    reference = one["inferred_summary"]["reference_diagnosis"]
    assert reference["evidence_count"] == 2 and reference["knowledge_points"]
    assert reference["formal_mastery"] is False
    history = d.student_history(db,"stu-1","class-1")
    assert not history["comparable_knowledge_points"]
    assert history["reference_diagnosis"]["evidence_count"] > 0
    group = d.class_assessment(db,"class-1","av-1")
    assert not group["common_weaknesses"]
    assert group["reference_diagnosis"]["knowledge_points"]


def test_persisted_state_counts_real_families_and_avoids_single_error_label(db):
    from backend.app.graphs.classroom_workflow import refresh_student_knowledge_states
    refresh_student_knowledge_states(db, "stu-1")
    linear = db.get(E.StudentKnowledgeState, ("stu-1", "kp.linear"))
    loss = db.get(E.StudentKnowledgeState, ("stu-1", "kp.loss"))
    assert linear.independent_family_count == 2
    assert loss.independent_family_count == 1
    assert linear.state == "证据不足" and loss.state == "证据不足"


def test_revised_versions_of_one_assessment_are_not_separate_exams():
    refs = [ev(1, family="a", version="v1"), ev(2, family="b", version="v2"), ev(3, family="c", version="v2")]
    for ref in refs: ref["assessment_id"] = "one-exam"
    result = summarize_independence(refs)
    assert result["independent_family_count"] == 3
    assert result["assessment_count"] == 1 and result["state"] == "证据不足"
    for ref in refs: ref.pop("assessment_id")
    assert summarize_independence(refs)["assessment_count"] == 0


def test_history_uses_actual_assessment_not_version_count(db):
    revision = db.get(E.AssessmentVersion, "av-2")
    revision.assessment_id = "assessment-1"
    revision.version_no = 2
    db.commit()
    history = d.student_history(db, "stu-1", "class-1")
    assert not history["comparable_knowledge_points"]
    linear = next(k for k in history["insufficient_knowledge_points"] if k["kp_id"] == "kp.linear")
    assert linear["assessment_count"] == linear["evidence_strength"]["assessment_count"] == 1
