"""Evidence ownership and safe, concise analysis for the four diagnosis panels."""
from __future__ import annotations

from backend.app.api.role_routes import _student_analysis_view
from backend.app.services import classroom_diagnosis
from backend.app.services.classroom_diagnosis import _v2_analysis_evidence
from backend.app.services.diagnosis_analysis_v2 import evidence_analysis
from backend.app.services.targeted_retest import enrich_analysis


def _item(position: int, response: dict, key: list[str], name: str) -> dict:
    return {
        "position": position, "question_type": "multiple_choice", "correct": False,
        "evidence_status": "scored", "stem": "选择成立的命题", "options": [
            {"key": letter, "text": f"选项{letter}"} for letter in "ABCD"],
        "response": response, "answer_reference": {
            "payload": {"keys": key}, "explanation": "根据定义逐项判断。"},
        "knowledge_point": {"name": name}, "chapter": {"title": "神经网络"},
        "awarded_score": 0, "max_score": 5,
    }


def _model() -> dict:
    return {
        "summary": "主要问题涉及非线性表达与梯度计算，宜对照两题的选项逐项核验。",
        "concept_hypotheses": [
            {"evidence_ids": ["q1"], "analysis": "可能没有区分激活函数与整个网络的表达能力。"},
            {"evidence_ids": ["q2"], "analysis": "可能需要结合链式法则再检查 $\\frac{\\partial L}{\\partial W}$。"},
        ],
        "recommendations": [
            {"evidence_ids": ["q1"], "action": "比较 $\\tanh(x)$ 与 Sigmoid 的作用条件。"},
            {"evidence_ids": ["q2"], "action": "手算一次链式法则中的权重梯度。"},
        ],
    }


def test_real_multi_select_mismatch_owns_observation_and_references():
    evidence = _v2_analysis_evidence("student_attempt", {"items": [
        _item(1, {"keys": ["B", "C"]}, ["A", "B", "D"], "激活函数"),
        _item(4, {"keys": ["B"]}, ["A", "B"], "反向传播"),
    ]})
    assert evidence[0]["observation"] == "漏选A、D，错选C。"
    assert evidence[0]["student_answer"] == {"keys": ["B", "C"]}
    result = evidence_analysis("student_attempt", evidence, _model())
    assert result["source"] == "deepseek"
    assert result["concept_hypotheses"][0]["question_refs"] == ["第1题"]
    assert result["concept_hypotheses"][0]["observation"] == "第1题漏选A、D，错选C。"
    assert result["recommendations"][0].startswith("第1题：")
    assert "$\\tanh(x)$" in result["recommendations"][0]


def test_imported_option_text_is_mapped_to_original_keys_without_guessing():
    item = _item(2, {"answered": True, "texts": ["最大化间隔", "允许松弛因子"]},
                 ["A"], "支持向量机")
    item["options"] = []
    item["source_options"] = [
        {"key": "A", "text": "最大化间隔。"},
        {"key": "D", "text": "允许松弛因子。"},
    ]
    evidence = _v2_analysis_evidence("student_attempt", {"items": [item]})
    assert evidence[0]["observation"] == "错选D。"
    assert evidence[0]["interpretation_allowed"] is True
    assert len(evidence[0]["options"]) == 2
    item["response"] = {"answered": True, "texts": ["无法对应的表述"]}
    unresolved = _v2_analysis_evidence("student_attempt", {"items": [item]})[0]
    assert unresolved["interpretation_allowed"] is False
    assert "无法与原题选项可靠对应" in unresolved["observation"]


def test_invented_reference_or_unsafe_claim_falls_back_to_evidence():
    evidence = _v2_analysis_evidence("student_attempt", {"items": [
        _item(1, {"keys": ["B"]}, ["A", "B"], "激活函数"),
        _item(4, {"keys": ["A"]}, ["B"], "反向传播"),
    ]})
    invented = _model()
    invented["concept_hypotheses"][0]["evidence_ids"] = ["q99"]
    assert evidence_analysis("student_attempt", evidence, invented)["source"] == "evidence_rules"
    false_theory = _model()
    false_theory["summary"] = "Sigmoid神经元实质是Logistic回归分类器。"
    assert evidence_analysis("student_attempt", evidence, false_theory)["source"] == "evidence_rules"
    certain_cause = _model()
    certain_cause["summary"] = "两题说明学生概念混淆。"
    assert evidence_analysis("student_attempt", evidence, certain_cause)["source"] == "evidence_rules"
    markup = _model()
    markup["summary"] = "<img src=x onerror=alert(1)>"
    assert evidence_analysis("student_attempt", evidence, markup)["source"] == "evidence_rules"
    malformed = _model()
    malformed["concept_hypotheses"] = "not a list"
    assert evidence_analysis("student_attempt", evidence, malformed)["source"] == "evidence_rules"


def test_latex_source_length_does_not_reject_short_visible_advice():
    evidence = _v2_analysis_evidence("student_attempt", {"items": [
        _item(1, {"keys": ["B"]}, ["A", "B"], "激活函数"),
        _item(4, {"keys": ["A"]}, ["B"], "反向传播"),
    ]})
    model = _model()
    model["recommendations"][1]["action"] = (
        "结合链式法则核对 $\\delta^{(l)}$ 与 "
        "$\\frac{\\partial J}{\\partial W^{(l)}}$ 的维度与转置。")
    assert evidence_analysis("student_attempt", evidence, model)["source"] == "deepseek"


def test_class_unanswered_aggregate_does_not_become_concept_cause(monkeypatch):
    aggregate = {
        "high_frequency_errors": [{
            **_item(2, {}, ["A"], "支持向量机"),
            "incorrect_count": 8, "denominator": 10,
            "high_frequency_wrong_answer": "未作答/无效选项",
            "high_frequency_wrong_count": 5,
            "student_id": "SECRET_STUDENT", "response": {"text": "SECRET_ANSWER"},
        }],
    }
    evidence = _v2_analysis_evidence("class_assessment", aggregate)
    assert "student_id" not in evidence[0] and "response" not in evidence[0]
    assert evidence[0]["interpretation_allowed"] is False
    result = evidence_analysis("class_assessment", evidence)
    assert "暂不能判断" in result["concept_hypotheses"][0]["analysis"]
    assert "不能据此判断" in result["summary"]
    assert "SECRET" not in str(result)
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Uninterpretable aggregate should not call the model")
    monkeypatch.setattr(classroom_diagnosis.model_provider, "chat", unexpected_call)
    fallback = classroom_diagnosis._model_analysis_v2(None, "class_assessment", aggregate, "run-test")
    assert fallback["source"] == "evidence_rules"
    assert "model_status" not in fallback


def test_class_tied_answers_only_support_shared_mismatch():
    aggregate = {"high_frequency_errors": [{
        **_item(7, {"keys": ["A", "B"]}, ["A", "B"], "交叉验证"),
        "scoring_reference_status": "available",
        "incorrect_count": 6, "denominator": 12,
        "common_wrong_answers": [
            {"answer": "A, C", "count": 3},
            {"answer": "B, C", "count": 3}],
    }]}
    evidence = _v2_analysis_evidence("class_assessment", aggregate)
    assert evidence[0]["interpretation_allowed"] is True
    assert evidence[0]["common_missed_keys"] == []
    assert evidence[0]["common_extra_keys"] == ["C"]
    model = {"summary": "班级常见作答错选C。",
             "concept_hypotheses": [{"evidence_ids": ["c1"],
                                     "analysis": "可能把错误选项C误当成正确解释。"}],
             "recommendations": [{"evidence_ids": ["c1"],
                                  "action": "讲评选项C与正确选项的差异。"}]}
    assert evidence_analysis("class_assessment", evidence, model)["source"] == "deepseek"
    model["concept_hypotheses"][0]["analysis"] = "班级共同漏选A，可能混淆定义。"
    assert evidence_analysis("class_assessment", evidence, model)["source"] == "evidence_rules"


def test_no_wrong_and_retest_result_preserve_original_conclusion():
    empty = evidence_analysis("student_attempt", [])
    assert empty["concept_hypotheses"] == []
    assert empty["status"] == "no_wrong_items"
    base = {"analysis_version": 2, "summary": "先核对激活函数的选项差异。",
            "concept_hypotheses": [], "recommendations": []}
    passed = enrich_analysis(base, [{"round": 1, "status": "passed", "accuracy": .8,
                                     "correct_count": 4, "question_count": 5,
                                     "wrong_items": []}])
    assert passed["summary"] == base["summary"]
    assert "80%" in passed["retest_summary"]["text"]
    assert "已达标" in passed["retest_summary"]["text"]
    failed = enrich_analysis(base, [{"round": 1, "status": "not_passed", "accuracy": .6,
                                     "correct_count": 3, "question_count": 5,
                                     "threshold": .8,
                                     "wrong_items": [{"position": 2}]}])
    assert "第2题" in failed["retest_summary"]["text"]
    assert "尚未达到80%" in failed["retest_summary"]["text"]
    assert _student_analysis_view({"ai_analysis": failed})["retest_summary"] == failed["retest_summary"]
