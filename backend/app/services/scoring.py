"""Deterministic scoring. No model is involved here.

Rules (project rules, see docs/DATA_MODEL.md):
* single_choice  : exact key match -> full score, else 0.
* multi_choice   : default 'proportional_no_wrong': any wrong option -> 0;
                   otherwise score * (#selected_correct / #all_correct).
                   Alternative 'all_or_nothing' requires the exact key set.
* true_false     : boolean / T-F / 对-错 normalised to bool.
* numeric        : absolute OR relative tolerance (whichever is configured),
                   optional unit check, optional rounding of the response.
* text types     : 'needs_model_scoring' in this pure deterministic layer;
                   the real rubric-point evaluation (accepting equivalent
                   expressions and alternative correct solutions) is done by
                   services/subjective_scoring.py via score_one_with_model().
* unknown type   : must not be silently coerced into single_choice.

`detail` may contain the expected key. It is stored server-side and MUST NOT
be returned before submission (see services/question_dto.py and api/).
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

OBJECTIVE_TYPES = {
    "single_choice", "multi_choice", "true_false", "numeric", "matching",
    "fill_blank",
}
TEXT_TYPES = {"term", "short_answer", "derivation", "comprehensive"}


def _normalize_text_value(value: Any) -> str:
    """Package-v2 fill-blank normalization, shared by aliases and formulas.

    NFKC handles full-width characters; whitespace and Chinese/ASCII
    punctuation are ignored.  Mathematical operators are deliberately kept.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[\s,，。；;、:：'\"`]+", "", text)


def _formula_token(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().strip("$")
    replacements = {
        "\\left": "", "\\right": "", "\\dfrac": "\\frac",
        "\\epsilon": "eps", "ε": "eps", "ϵ": "eps",
        "\\alpha": "alpha", "α": "alpha", "\\ln": "ln",
        "\\sum": "sum", "Σ": "sum", "−": "-", "×": "*",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"[\s{}]", "", text).casefold()


def _ascii_formula(value: Any) -> str:
    """Return a tightly restricted SymPy expression or an empty string.

    This path is intentionally limited to scalar algebra.  Indexed sums are
    handled by the token/alias path instead of executing a permissive parser.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).strip().strip("$")
    text = text.replace("ε", "eps").replace("ϵ", "eps").replace("^", "**")
    text = text.replace("ln", "log")
    if (not text or "sum" in text.casefold() or "Σ" in text
            or not re.fullmatch(r"[A-Za-z0-9_+\-*/().\s*]+", text)):
        return ""
    return text


def _formula_equivalent(given: Any, accepted: list[str]) -> tuple[bool, str]:
    token = _formula_token(given)
    if token and token in {_formula_token(value) for value in accepted}:
        return True, "formula_normalized_alias"
    lhs = _ascii_formula(given)
    if not lhs:
        return False, "formula_not_safely_parseable"
    try:
        import sympy as sp
        names = set(re.findall(r"[A-Za-z_]\w*", lhs)) - {"log", "exp", "sqrt"}
        local = {name: sp.Symbol(name) for name in names}
        local.update({"log": sp.log, "exp": sp.exp, "sqrt": sp.sqrt})
        left = sp.sympify(lhs, locals=local, evaluate=True)
        for candidate in accepted:
            rhs = _ascii_formula(candidate)
            if not rhs:
                continue
            rhs_names = set(re.findall(r"[A-Za-z_]\w*", rhs)) - {"log", "exp", "sqrt"}
            rhs_local = {name: local.get(name, sp.Symbol(name)) for name in rhs_names}
            rhs_local.update({"log": sp.log, "exp": sp.exp, "sqrt": sp.sqrt})
            if sp.simplify(left - sp.sympify(rhs, locals=rhs_local,
                                             evaluate=True)) == 0:
                return True, "sympy_equivalent"
    except Exception:  # unsafe/unsupported syntax is not forced into a score
        return False, "formula_equivalence_failed"
    return False, "formula_not_equivalent"


def _as_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "t", "对", "正确", "yes", "y", "1", "√"):
            return True
        if s in ("false", "f", "错", "错误", "no", "n", "0", "×"):
            return False
    return None


def effective_type(snapshot: dict) -> str:
    """题型标签缺失时，按已保存答案的形态判定。

    `unknown` 若一律当主观题，一道 `{"key": "B"}` 的单选题会被送去做要点评分：
    多一次模型调用，而且分数不如精确比对可靠。生成阶段的 structure_check 与
    答案册渲染已使用同一条证据规则，这里让评分也跟上，避免三条路径各判一套。
    """
    qtype = snapshot.get("question_type") or "unknown"
    if qtype not in ("", "unknown", "other"):
        return qtype
    ans = snapshot.get("answer_payload") or {}
    if isinstance(ans.get("keys"), list) and any(str(k).strip()
                                                 for k in ans["keys"]):
        return "multi_choice"
    if isinstance(ans.get("value"), bool):
        return "true_false"
    if str(ans.get("key") or "").strip():
        return "single_choice"
    return qtype


def score_one(snapshot: dict, response: dict, max_score: float) -> dict:
    """Return {awarded_score, max_score, correct, scoring_status, detail}.
    `snapshot` is the immutable paper-item snapshot (server-side fields included).
    """
    qtype = effective_type(snapshot)
    detail_type = snapshot.get("question_type", "unknown")
    ans = snapshot.get("answer_payload") or {}
    detail: dict[str, Any] = {"question_type": qtype, "rule": "", "notes": []}
    base = {"max_score": max_score, "awarded_score": 0.0, "correct": None,
            "scoring_status": "auto", "detail": detail}
    if detail_type != qtype:
        detail["stored_question_type"] = detail_type
        detail["notes"].append(
            f"题目未标注题型，按已保存答案的形态判定为 {qtype}")

    if qtype in TEXT_TYPES or qtype == "unknown":
        # A saved AI reference answer makes text items scorable by rubric
        # points; see subjective_scoring.score_one_with_model(). This pure
        # function only reports the pending state when called without a
        # model-capable session.
        base["scoring_status"] = "needs_model_scoring"
        detail["rule"] = "rubric_points_model_pending"
        detail["notes"].append(
            "主观题：按评分要点评价，接受等价表达与其他正确解法；"
            "确定性层不猜测分数"
            if qtype != "unknown" else
            "题型未知，按主观题评分要点处理，未擅自转换为客观题")
        return base

    if qtype not in OBJECTIVE_TYPES:
        base["scoring_status"] = "needs_manual"
        detail["rule"] = "unsupported_type"
        detail["notes"].append("未登记的题型，待人工评分")
        return base

    if not ans:
        base["scoring_status"] = "needs_manual"
        detail["rule"] = "no_reference_answer"
        detail["notes"].append("该题尚无参考答案（可进入 AI 解答队列自动补齐），暂不评分")
        return base

    if qtype in ("single_choice", "matching"):
        given = str(response.get("key", response.get("text", ""))).strip().upper()
        expected = str(ans.get("key", "")).strip().upper()
        detail.update(rule=("matching_exact_key" if qtype == "matching" else "exact_key"),
                      given=given, expected=expected)
        ok = bool(given) and given == expected
        base.update(correct=ok, awarded_score=max_score if ok else 0.0)
        return base

    if qtype == "multi_choice":
        rule = ans.get("partial_rule", "proportional_no_wrong")
        expected = {str(k).strip().upper() for k in ans.get("keys", [])}
        raw = response.get("keys", response.get("key", []))
        if isinstance(raw, str):
            raw = [c for c in raw if c.isalpha()] or [raw]
        given = {str(k).strip().upper() for k in raw}
        detail.update(rule=rule, given=sorted(given), expected=sorted(expected))
        wrong = given - expected
        if not expected:
            base["scoring_status"] = "needs_manual"
            detail["notes"].append("答案为空，待人工核对")
            return base
        if rule == "all_or_nothing":
            ok = given == expected
            base.update(correct=ok, awarded_score=max_score if ok else 0.0)
            return base
        if wrong:
            base.update(correct=False, awarded_score=0.0)
            detail["notes"].append("含错误选项，按规则记 0 分")
            return base
        ratio = len(given & expected) / len(expected)
        base.update(correct=(ratio >= 1.0 - 1e-9),
                    awarded_score=round(max_score * ratio, 4))
        detail["partial_ratio"] = round(ratio, 4)
        return base

    if qtype == "true_false":
        expected = _as_bool(ans.get("value", ans.get("key")))
        given = _as_bool(response.get("value", response.get("key")))
        detail.update(rule="boolean_match", given=given, expected=expected)
        if expected is None:
            base["scoring_status"] = "needs_manual"
            detail["notes"].append("答案布尔值无法解析")
            return base
        ok = given is not None and given == expected
        base.update(correct=ok, awarded_score=max_score if ok else 0.0)
        return base

    if qtype == "fill_blank":
        expected_values = ans.get("values") or ans.get("accepted_values") or []
        # Canonical shape: values=[{"accepted":["10025"],"tol":0}, ...].
        normalized_expected = []
        for cell in expected_values:
            if isinstance(cell, dict):
                accepted = cell.get("accepted") or cell.get("values") or []
                tol = float(cell.get("tol", 0.0) or 0.0)
                formula = bool(cell.get("formula_equivalence"))
            else:
                accepted = cell if isinstance(cell, list) else [cell]
                tol = 0.0
                formula = False
            normalized_expected.append(([str(v).strip() for v in accepted], tol,
                                        formula))
        raw_values = response.get("values", response.get("parts"))
        if not isinstance(raw_values, list):
            raw_text = str(response.get("text", response.get("value", "")) or "")
            raw_values = [v.strip() for v in raw_text.replace("；", ";").split(";")]
        given_values = [str(v).strip() for v in raw_values if str(v).strip()]
        detail.update(rule="multi_blank_equivalence", given=given_values,
                      expected_count=len(normalized_expected))
        if not normalized_expected:
            base["scoring_status"] = "needs_manual"
            detail["notes"].append("填空题缺少等价答案集合")
            return base
        if len(given_values) != len(normalized_expected):
            base.update(correct=False, awarded_score=0.0)
            detail["notes"].append("作答空位数量与题目要求不一致")
            return base
        matches = []
        formula_methods = []
        for given, (accepted, tol, formula) in zip(given_values,
                                                   normalized_expected):
            direct = _normalize_text_value(given) in {
                _normalize_text_value(v) for v in accepted}
            numeric_ok = False
            if tol > 0:
                try:
                    numeric_ok = any(abs(float(given) - float(v)) <= tol + 1e-12
                                     for v in accepted)
                except (TypeError, ValueError):
                    numeric_ok = False
            formula_ok, method = ((False, "") if not formula else
                                  _formula_equivalent(given, accepted))
            matches.append(direct or numeric_ok or formula_ok)
            formula_methods.append(method if formula else "")
        ok = all(matches)
        base.update(correct=ok, awarded_score=max_score if ok else 0.0)
        detail["blank_matches"] = matches
        if any(formula_methods):
            detail["formula_methods"] = formula_methods
        return base

    # numeric
    parts = ans.get("parts") if isinstance(ans.get("parts"), list) else []
    if parts:
        raw_parts = response.get("parts", response.get("values"))
        if not isinstance(raw_parts, list):
            raw = str(response.get("value", response.get("text", "")) or "")
            raw_parts = [item.strip() for item in re.split(r"[;；,，]", raw)]
        given_parts = []
        for item in raw_parts:
            if isinstance(item, dict):
                given_parts.append(item.get("value", item.get("numeric", "")))
            else:
                given_parts.append(item)
        detail.update(rule="numeric_multi_tolerance",
                      expected_count=len(parts), given_count=len(given_parts))
        if len(given_parts) != len(parts):
            base.update(correct=False, awarded_score=0.0)
            detail["notes"].append("多值作答数量与题目要求不一致")
            return base
        matches = []
        checked = []
        for given, part in zip(given_parts, parts):
            try:
                expected = float(part.get("value"))
                actual = float(given)
                tol = float(part.get("tol", part.get("tolerance", 0.0)) or 0.0)
                ok = abs(actual - expected) <= tol + 1e-12
            except (TypeError, ValueError):
                expected, actual, tol, ok = part.get("value"), given, 0.0, False
            matches.append(ok)
            checked.append({"label": part.get("label", ""), "given": actual,
                            "expected": expected, "tol": tol, "match": ok})
        ok = all(matches)
        detail["parts"] = checked
        base.update(correct=ok, awarded_score=max_score if ok else 0.0)
        return base

    try:
        expected_v = float(ans.get("value"))
    except (TypeError, ValueError):
        base["scoring_status"] = "needs_manual"
        detail["rule"] = "numeric"
        detail["notes"].append("答案数值无法解析")
        return base
    raw_given = response.get("value", response.get("numeric", ""))
    try:
        given_v = float(raw_given)
    except (TypeError, ValueError):
        base.update(correct=False, awarded_score=0.0)
        detail.update(rule="numeric", expected=expected_v, given=raw_given,
                      notes=["作答不是有效数值，记 0 分"])
        return base
    rounding = ans.get("rounding")
    if rounding is not None:
        given_v = round(given_v, int(rounding))
    tol = float(ans.get("tol", 0.0))
    rel_tol = float(ans.get("rel_tol", 0.0))
    unit = str(ans.get("unit", "") or "")
    given_unit = str(response.get("unit", "") or "")
    abs_ok = abs(given_v - expected_v) <= tol + 1e-12
    rel_ok = rel_tol > 0 and abs(given_v - expected_v) <= rel_tol * max(1.0, abs(expected_v))
    unit_ok = (not unit) or (given_unit.strip() == unit.strip())
    ok = (abs_ok or rel_ok) and unit_ok
    detail.update(rule="numeric_tolerance", expected=expected_v, given=given_v,
                  tol=tol, rel_tol=rel_tol, unit=unit, given_unit=given_unit,
                  unit_ok=unit_ok, rounding=rounding)
    if not unit_ok:
        detail["notes"].append("单位不匹配，记 0 分")
    base.update(correct=ok, awarded_score=max_score if ok else 0.0)
    return base


def independent_evidence(answer_row: dict) -> bool:
    """Hints, viewed solutions, or previously seen answers disqualify an
    attempt from being counted as independent evidence in state updates."""
    return not (answer_row.get("hints_used", 0) > 0
                or answer_row.get("solution_viewed", False)
                or answer_row.get("seen_before", False)
                or answer_row.get("is_independent_evidence") is False)
