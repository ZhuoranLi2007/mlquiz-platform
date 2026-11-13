from backend.app.services.question_dto import student_view
from backend.app.services.scoring import score_one
from backend.app.services.subjective_scoring import _scaled_points


def test_numeric_multi_requires_every_part_with_its_tolerance():
    snapshot = {
        "question_type": "numeric",
        "answer_payload": {"parts": [
            {"label": "accuracy", "value": 0.8, "tol": 0.001},
            {"label": "precision", "value": 0.75, "tol": 0.001},
        ]},
    }
    ok = score_one(snapshot, {"parts": ["0.8004", "0.75"]}, 5)
    bad = score_one(snapshot, {"parts": ["0.8", "0.7"]}, 5)
    assert ok["correct"] is True and ok["awarded_score"] == 5
    assert bad["correct"] is False and bad["awarded_score"] == 0
    assert ok["detail"]["rule"] == "numeric_multi_tolerance"


def test_fill_blank_normalizes_synonyms_and_formula_aliases():
    text = score_one({
        "question_type": "fill_blank",
        "answer_payload": {"values": [
            {"accepted": ["Adaptive Boosting", "自适应提升"], "tol": 0},
        ]},
    }, {"text": "  adaptive，boosting  "}, 2)
    formula = score_one({
        "question_type": "fill_blank",
        "answer_payload": {"values": [{
            "accepted": ["1/2*ln((1-eps)/eps)"],
            "tol": 0,
            "formula_equivalence": True,
        }]},
    }, {"text": "0.5*ln((1-eps)/eps)"}, 2)
    assert text["correct"] is True
    assert formula["correct"] is True
    assert formula["detail"]["formula_methods"] == ["sympy_equivalent"]


def test_rubric_caps_scale_to_actual_paper_score():
    scaled = _scaled_points([
        {"id": "a", "point": "结论", "score": 3, "weight": 0.3},
        {"id": "b", "point": "推导", "score": 7, "weight": 0.7},
    ], 6)
    assert [point["score"] for point in scaled] == [1.8, 4.2]
    assert sum(point["score"] for point in scaled) == 6


def test_response_schema_exposes_shape_but_not_answers():
    view = student_view({
        "question_id": "q", "question_version": 1, "question_type": "numeric",
        "stem": "计算", "options": [], "answer_payload": {"parts": [
            {"label": "A", "value": 1.25, "tol": 0.01},
            {"label": "B", "value": 3.5, "tol": 0.01},
        ]},
    })
    assert view["response_schema"] == {
        "kind": "numeric_multi",
        "parts": [{"label": "A", "unit": ""},
                  {"label": "B", "unit": ""}],
    }
    assert "answer_payload" not in view
    assert "1.25" not in str(view)
