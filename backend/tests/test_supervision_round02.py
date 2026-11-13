import json
import pytest
from sqlalchemy import select
from backend.tests.test_classroom_diagnosis import db
from backend.app.models import entities as E
from backend.app.services import classroom_diagnosis as d, automatic_retest as r
from backend.app.graphs.classroom_workflow import refresh_student_knowledge_states, compare_attempts


def test_inferred_not_formal_but_retained(db):
    scores = db.execute(select(E.AnswerScore).where(E.AnswerScore.attempt_id == "attempt-s1-v1")).scalars().all()
    for score in scores:
        score.authority_status = "inferred_unverified"
        score.teacher_verified = False
    db.commit()
    out = d.student_attempt(db, "stu-1", "av-1")
    assert out["score_summary"]["scored_items"] == 0
    assert out["score_summary"]["score_rate"] is None
    assert out["inferred_summary"]["scored_items"] == 2
    assert out["inferred_summary"]["awarded_score"] == 10
    assert out["weak_kp_ids"] == []
    assert d.student_history(db, "stu-1", "class-1")["attempts"][0]["scored_items"] == 0
    group = d.class_assessment(db, "class-1", "av-1")
    student = next(x for x in group["anonymous_comparison"] if x["student_id"] == "stu-1")
    assert student["scored_items"] == 0 and student["inferred_summary"]["scored_items"] == 2
    assert compare_attempts(db, "attempt-s1-v1", "attempt-s1-v1")["comparable_knowledge_points"] == []


def test_unmapped_not_weak_and_stale_state_reset(db):
    for score in db.execute(select(E.AnswerScore)).scalars():
        score.kp_id = ""
        score.authority_status = "inferred_unverified"
    for item in db.execute(select(E.PaperItem)).scalars():
        item.snapshot = {**item.snapshot, "main_kp_id": "", "kp_id": ""}
    db.add(E.StudentKnowledgeState(student_id="stu-1", kp_id="kp.loss", state="需加强", evidence_count=5))
    db.commit()
    out = d.student_attempt(db, "stu-1", "av-1")
    assert out["unmapped_item_count"] == 2 and not out["knowledge_points"]
    refresh_student_knowledge_states(db, "stu-1")
    state = db.get(E.StudentKnowledgeState, ("stu-1", "kp.loss"))
    assert state.state == "证据不足" and state.evidence_count == 0


def candidate(db):
    db.get(E.User, "teacher_local").role = "teacher"
    if db.get(E.User, "student-online") is None:
        db.add(E.User(user_id="student-online", display_name="在线学生",
                      role="student", active=True))
    db.get(E.Student, "stu-1").linked_user_id = "student-online"
    db.add(E.Document(document_id="fixture-doc", rel_path="fixture.txt", source_category="test"))
    db.add(E.DocumentVersion(document_version_id="fixture-v1", document_id="fixture-doc",
        rel_path="fixture.txt", sha256="fixture-hash", source_category="test"))
    db.add(E.Chunk(chunk_id="fixture-source", document_id="fixture-doc", document_version_id="fixture-v1",
        content_raw="平方误差是预测值与标签差的平方。", kp_ids=["kp.loss"]))
    q = E.Question(row_id="auto-new", question_id="auto-new", question_version=1,
        origin="ai", question_type="true_false", stem="平方误差目标对预测误差作平方惩罚。",
        answer_payload={"value": True}, main_kp_id="kp.loss", scope_class="in_scope",
        source_refs=[{"chunk_id": "fixture-source"}], active=False, score=2)
    db.add(q); db.commit()
    return q


def publish(db):
    return r.validate_and_publish(db, candidate_ids=["auto-new"], kp_ids=["kp.loss"],
        actor_user_id="teacher_local", student_id="stu-1", classroom_id="class-1",
        source_attempt_id="attempt-s1-v1", diagnosis_id="", thread_id="isolated-test")


def test_auto_checked_question_publishes_without_approval(db, monkeypatch):
    q = candidate(db)
    monkeypatch.setattr(r.model_provider, "chat", lambda *a, **k: {"content": json.dumps({
        "answer_payload": {"value": True}, "explanation": "平方惩罚定义", "unambiguous": True,
        "kp_matches": True}), "call_id": "stub-not-live"})
    result = publish(db)
    assert result["status"] == "published" and result["release"]["recipient_count"] == 1
    assert q.review_status == "auto_checked" and q.active
    assert q.answer_origin != "teacher_verified_ai"


def test_scalar_independent_answer_is_normalized(db, monkeypatch):
    """The model may legally return a bare bool/key despite the JSON field name."""
    candidate(db)
    monkeypatch.setattr(r.model_provider, "chat", lambda *a, **k: {
        "content": json.dumps({
            "answer_payload": True,
            "explanation": "平方惩罚定义",
            "unambiguous": True,
            "kp_matches": True,
        }),
        "call_id": "stub-scalar-answer",
    })
    assert publish(db)["status"] == "published"


def test_mismatch_never_publishes(db, monkeypatch):
    q = candidate(db)
    monkeypatch.setattr(r.model_provider, "chat", lambda *a, **k: {"content": json.dumps({
        "answer_payload": {"value": False}, "explanation": "conflict", "unambiguous": True,
        "kp_matches": True})})
    with pytest.raises(ValueError): publish(db)
    assert not q.active
    assert not db.execute(select(E.AssessmentRelease)).scalars().all()


@pytest.mark.parametrize("texts", [["", ""], ["same content", "same content"]])
def test_bad_option_text_rejected(db, monkeypatch, texts):
    q = candidate(db)
    q.question_type = "single_choice"
    q.options = [{"key": "A", "text": texts[0]}, {"key": "B", "text": texts[1]}]
    q.answer_payload = {"key": "A"}
    db.commit()
    monkeypatch.setattr(r.model_provider, "chat", lambda *a, **k: pytest.fail("must reject before model"))
    with pytest.raises(ValueError, match="正文"): publish(db)
    assert not q.active


def test_nonexistent_source_rejected(db, monkeypatch):
    q = candidate(db)
    q.source_refs = [{"chunk_id": "DOES_NOT_EXIST"}]
    db.commit()
    monkeypatch.setattr(r.model_provider, "chat", lambda *a, **k: pytest.fail("must reject before model"))
    with pytest.raises(ValueError, match="引用"): publish(db)
    assert not q.active


def test_publish_failure_rolls_back_every_business_row(db, monkeypatch):
    q = candidate(db)
    db.get(E.User, "teacher_local").role = "student"
    db.commit()
    before = {model: len(db.execute(select(model)).scalars().all())
              for model in [E.Paper, E.PaperItem, E.Assessment, E.AssessmentVersion, E.AssessmentRelease, E.ReleaseRecipient]}
    monkeypatch.setattr(r.model_provider, "chat", lambda *a, **k: {"content": json.dumps({
        "answer_payload": {"value": True}, "explanation": "平方惩罚定义", "unambiguous": True,
        "kp_matches": True})})
    with pytest.raises(PermissionError): publish(db)
    db.expire_all()
    assert not db.get(E.Question, "auto-new").active
    for model, count in before.items():
        assert len(db.execute(select(model)).scalars().all()) == count


def test_ai_snapshot_authority_is_preserved():
    from backend.app.services.attempt_service import snapshot_score_authority
    assert snapshot_score_authority({"origin": "ai", "answer_origin": "deepseek_generated",
        "answer_status": "ai_auto_checked"}) == "ai_auto_checked"
    assert snapshot_score_authority({"origin": "ai", "answer_status": "candidate"}) == "inferred_unverified"
    assert snapshot_score_authority({"origin": "ai", "answer_origin": "teacher_verified_ai",
        "answer_status": "verified"}) == "teacher_key"


@pytest.mark.parametrize("texts", [["x+1", "x-1"], ["x<1", "x>1"], ["x²", "x2"]])
def test_math_option_semantics_preserved(db, monkeypatch, texts):
    q = candidate(db)
    q.question_type = "single_choice"
    q.options = [{"key": "A", "text": texts[0]}, {"key": "B", "text": texts[1]}]
    q.answer_payload = {"key": "A"}
    db.commit()
    monkeypatch.setattr(r.model_provider, "chat", lambda *a, **k: {"content": json.dumps({
        "answer_payload": {"key": "A"}, "explanation": "独立数学解释测试桩", "unambiguous": True,
        "kp_matches": True})})
    assert publish(db)["status"] == "published"


def test_four_views_expose_ai_source_counts(db):
    for score in db.execute(select(E.AnswerScore).where(E.AnswerScore.attempt_id == "attempt-s1-v1")).scalars():
        score.authority_status = "ai_auto_checked"
        score.teacher_verified = False
    db.commit()
    views = [d.student_attempt(db, "stu-1", "av-1"),
             d.student_history(db, "stu-1", "class-1"),
             d.class_assessment(db, "class-1", "av-1"),
             d.question_analysis(db, "av-1", paper_item_id="item-1")]
    for view in views:
        assert view["score_sources"]["ai_auto_checked_count"] > 0
        assert "不是教师权威" in view["score_sources"]["note"]
