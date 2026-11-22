from datetime import datetime, timezone

import pytest

from backend.app.models import entities as E
from backend.app.services import adaptive_learning, class_matrix, learning_loop
from backend.app.services import classroom_diagnosis
from backend.tests.test_classroom_diagnosis import db
from backend.tests.test_course_acceptance import prepare
from backend.tests.test_teacher_student_flow import flow, _login


def test_matrix_preserves_zero_ungraded_absence_and_version(db):
    matrix = class_matrix.build(db, "class-1", "av-1")
    students = {row["student_id"]: row for row in matrix["rows"]}
    wrong = students["stu-1"]["cells"]["kp.linear"]
    assert wrong["correct_count"] == 0 and wrong["scored_count"] == 1
    assert wrong["state"] == "needs_review"
    assert students["stu-2"]["cells"]["kp.loss"]["scored_count"] == 0
    assert students["stu-2"]["cells"]["kp.linear"]["state"] == "insufficient"
    assert students["stu-3"]["participation_status"] == "absent"
    assert students["stu-3"]["ungraded_count"] == 0
    assert students["stu-4"]["cells"]["kp.linear"]["scored_count"] == 0
    assert students["stu-1"]["attempt_id"] == "attempt-s1-v1"
    assert not any(row["eligible"] for row in matrix["rows"])  # accounts are unbound in this fixture
    with pytest.raises(classroom_diagnosis.ClassroomDiagnosisError):
        class_matrix.build(db, "other-class", "av-1")
    assert "SECRET_RESPONSE" not in str(matrix) and "SECRET_NAME" not in str(matrix)


def test_bulk_validates_entire_selection_before_queueing_and_enforces_role(flow, monkeypatch):
    client, _ = flow
    endpoint = "/api/teacher/class-matrix/practice"
    body = {"classroom_id": "class-1", "assessment_version_id": "av-1",
            "student_ids": ["stu-1", "outside"], "kp_id": "kp.linear"}
    assert client.post(endpoint, json=body).status_code == 401
    _login(client, "student1", "Student1!2026")
    assert client.post(endpoint, json=body).status_code == 403
    _login(client, "teacher", "Teacher!2026")
    monkeypatch.setattr(class_matrix, "build", lambda *args: {"rows": [{
        "student_id": "stu-1", "attempt_id": "source", "eligible": True,
        "cells": {"kp.linear": {"scored_count": 1}},
    }]})
    calls = []
    def queue(db, principal, request):
        calls.append(request)
        return {"job_id": "job-test", "status": "queued"}
    monkeypatch.setattr(learning_loop, "queue", queue)
    assert client.post(endpoint, json=body).status_code == 422
    assert not calls
    body["student_ids"] = ["stu-1", "stu-1"]
    assert client.post(endpoint, json=body).json()["items"][0]["job_id"] == "job-test"
    assert len(calls) == 1 and calls[0]["prefer_bank"] is True
    assert calls[0]["kp_ids"] == ["kp.linear"]


def test_adaptive_stages_use_evidence_and_due_time():
    now = datetime(2026, 11, 22, tzinfo=timezone.utc)
    assert adaptive_learning.profile({"state": "needs_review"}, now=now)["difficulty"] == "基础"
    assert adaptive_learning.profile({"state": "insufficient"}, now=now)["stage"] == "application"
    assert adaptive_learning.profile({"state": "achieved", "due_at": "2026-11-23T00:00:00Z"}, now=now)["stage"] == "transfer"
    assert adaptive_learning.profile({"state": "achieved", "due_at": "2026-11-21T00:00:00Z"}, now=now)["stage"] == "delayed"
    concept = {"id": "kp.linear:detail", "kp_id": "kp.linear", "name": "局部概念",
               "statistics": {"ungraded_count": 0, "scored_count": 1},
               "learning_result": {"state": "insufficient"}, "timeline": []}
    planned = adaptive_learning.path(concept, {"state": "needs_review"})
    assert planned["stage"] == "foundation" and planned["training_scope"]


def test_actual_bank_selection_prefers_stage_without_reusing_evidence(db, monkeypatch):
    prepare(db, monkeypatch)
    sentences = [
        "构造两个不同的预测误差，比较绝对损失对异常观测的敏感程度。",
        "分析正规方程系数矩阵的秩，判断线性参数是否能唯一确定。",
        "根据小批量样本计算梯度估计，说明更新方向为何存在随机性。",
        "比较两组验证集残差随惩罚强度变化的趋势，选择合理参数。",
        "在输入变量尺度不同的场景中，解释标准化对优化步长的影响。",
    ]
    for band in ["基础", "困难"]:
        for i, stem in enumerate(sentences):
            qid = f"{band}-{i}"
            db.add(E.Question(row_id=qid, question_id=qid, question_version=1,
                stem=stem + band, question_type="true_false", answer_payload={"value": True},
                main_kp_id="kp.linear", chapter_id="ch1", question_family_id=qid,
                cognitive_level="理解" if i % 2 else "应用", difficulty=band,
                origin="human", review_status="verified", answer_status="verified", answer_origin="teacher"))
    db.commit()
    scope = learning_loop.resolve_scopes(db, {"student_id": "stu-1", "attempt_id": "attempt-s1-v1"})[0]
    plan = learning_loop.plan_scope(db, scope, ["kp.linear"])
    assert plan["adaptive_profiles"]["kp.linear"]["stage"] == "foundation"
    questions, manifest, _, _ = learning_loop.prepare_questions(db, plan, [], "test")
    assert len(questions) == 5
    assert all(q.difficulty == "基础" for q in questions)
    assert all(value["new_to_student"] for value in manifest.values())
    assert {value["target_difficulty"] for value in manifest.values()} == {"基础"}
    plan["adaptive_profiles"]["kp.linear"] = adaptive_learning.profile({"state": "delayed_confirmed"})
    questions, _, _, _ = learning_loop.prepare_questions(db, plan, [], "test")
    assert all(q.difficulty == "困难" for q in questions)
    with pytest.raises(ValueError):
        learning_loop.plan_scope(db, scope, ["kp.missing"])
