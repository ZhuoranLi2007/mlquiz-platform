"""Short, evidence-bound presentation of classroom diagnosis model output.

The model may suggest a hypothesis or a study action. Question references and
observable answer differences always come from scored evidence assembled by
the diagnosis service, never from model prose.
"""
from __future__ import annotations

import re
from typing import Any


ANALYSIS_VERSION = 2

SCIENCE_GUIDANCE = (
    "保持机器学习定义准确：Sigmoid 是激活函数，使用它的神经网络不因此等同于 Logistic 回归分类器；"
    "仅把 Sigmoid 换成 Tanh 不能单独解决 XOR，须考虑网络结构与非线性表达能力；"
    "反向传播按链式法则求梯度，梯度下降沿负梯度方向更新权重；"
    "SVM 的核心是间隔与核方法，不默认输出概率；"
    "PCA 寻找正交的最大方差方向，NMF 是非负分解，两者不能混同；"
    "准确率、精确率、召回率和 F1 的适用性取决于任务与类别分布。"
    "错误选项中的陈述不能作为正确理论复述。无法由作答确定的心理原因只写可能。"
)

_UNSAFE_CLAIMS = (
    re.compile(r"sigmoid.{0,30}(?:实质是|等同于|就是).{0,25}(?:logistic|逻辑回归)", re.I),
    re.compile(r"(?:tanh|双曲正切).{0,20}(?:就能|即可|足以).{0,20}(?:解决|表示).{0,10}xor", re.I),
    re.compile(r"xor.{0,55}(?:换成|使用).{0,20}(?:tanh|双曲正切)", re.I),
)
_PLAIN_FORMULA = re.compile(r"\b(?:SIGMOID|TANH|EXP)\s*\([^)]*\)|\b(?:A|Z)\(L-?\d+\)", re.I)
_MATH_SPAN = re.compile(r"\$\$[\s\S]*?\$\$|\$[^$]*\$")
_HTML_TAG = re.compile(r"<\s*/?\s*[A-Za-z!][^>]*>")
_INTERNAL_ID = re.compile(
    r"(?<![A-Za-z0-9_])(?:paper|assessv|assessment|attempt|cattempt|item|cq|kp)_[A-Za-z0-9_-]{8,}",
    re.I,
)
_CERTAIN_CAUSE = re.compile(r"概念混淆|未理解|没有理解|掌握不足|审题失误|计算错误")


def _safe_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    prose = _MATH_SPAN.sub("", text)
    if (not text or len(text) > max(180, limit * 3)
            or len(_MATH_SPAN.sub("公式", text)) > limit
            or "```" in text or _HTML_TAG.search(text)
            or _INTERNAL_ID.search(text) or _PLAIN_FORMULA.search(prose)
            or text.count("$") % 2 != 0
            or ("\\" in text and "$" not in text)
            or (_CERTAIN_CAUSE.search(text) and not re.search(r"可能|或许|待确认", text))
            or any(pattern.search(text) for pattern in _UNSAFE_CLAIMS)):
        return ""
    return text


def _unique(values: list[Any]) -> list[Any]:
    return list(dict.fromkeys(value for value in values if value))


def _reference_label(rows: list[dict]) -> str:
    refs = _unique([ref for row in rows for ref in row.get("refs") or []])
    return "、".join(refs[:2])


def _fallback_summary(scope: str, rows: list[dict]) -> str:
    if scope == "class_assessment":
        supported = [row for row in rows if row.get("interpretation_allowed", True)]
        if supported:
            rows = supported
    concepts = _unique([str(row.get("name") or "") for row in rows])[:2]
    focus = "和".join(concepts) or "相关概念"
    refs = _reference_label(rows)
    if scope == "class_assessment":
        if not any(row.get("interpretation_allowed", True) for row in rows):
            return (f"{refs}有错误记录，但常见作答缺失、无效或无法比较；"
                    "目前不能据此判断具体概念误解。请先核对提交和答案记录。")
        return f"{refs}的班级作答集中出现错误，涉及{focus}。建议讲评答案差异后用同类题复测。"
    if scope == "student_history":
        return f"{refs}等跨卷错题涉及{focus}。建议先核对反复出现的作答差异，再用新题确认。"
    return f"{refs}的作答差异主要涉及{focus}。建议先复习对应概念，再用同类新题确认。"


def _fallback_suggestions(scope: str, rows: list[dict]) -> list[str]:
    if scope == "class_assessment" and not any(
            row.get("interpretation_allowed", True) for row in rows):
        refs = _reference_label(rows)
        return [f"核对{refs}的提交与答案采集记录。",
                "取得可比较的实际选项后，再讲评答案差异并安排复测。"]
    if scope == "class_assessment":
        rows = [row for row in rows if row.get("interpretation_allowed", True)]
    result = []
    for row in rows[:2]:
        name = str(row.get("name") or "相关概念")
        ref = (row.get("refs") or ["对应错题"])[0]
        verb = "讲评" if scope == "class_assessment" else "回看"
        result.append(f"{verb}{ref}的答案差异，复习{name}的定义和适用条件。")
    if len(result) == 1:
        result.append("再用一道不同表述的新题确认理解，而非记忆原题答案。")
    return result


def evidence_analysis(
    scope: str,
    evidence: list[dict[str, Any]],
    model_output: Any = None,
    *,
    model_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate model prose and join it to server-owned evidence references."""
    rows = [row for row in evidence if row.get("id") and row.get("observation")]
    if not rows:
        summary = (
            "当前没有足够的已评分作答，暂时无法分析。"
            if scope == "insufficient" else
            "已评分题目没有错题。可用后续不同题型继续检验理解。"
        )
        return {
            "analysis_version": ANALYSIS_VERSION,
            "status": "insufficient_evidence" if scope == "insufficient" else "no_wrong_items",
            "source": "evidence_rules", "summary": summary,
            "concept_hypotheses": [], "recommendations": [],
            "evidence_details": [], "retest_summary": None,
        }

    allowed = {str(row["id"]): row for row in rows}
    parsed = model_output if isinstance(model_output, dict) else {}
    model_summary = _safe_text(parsed.get("summary"), 90)
    if re.search(r"第\s*\d+\s*题", model_summary):
        # Citations come from server-owned evidence. Remove only duplicate
        # references; preserve the surrounding complete sentence.
        model_summary = re.sub(r"第\s*\d+\s*题", "", model_summary).strip()
    model_concepts: dict[str, str] = {}
    raw_concepts = parsed.get("concept_hypotheses")
    for candidate in raw_concepts if isinstance(raw_concepts, list) else []:
        if not isinstance(candidate, dict):
            continue
        ids = candidate.get("evidence_ids") or []
        if not isinstance(ids, list) or not ids or any(str(value) not in allowed for value in ids):
            continue
        analysis = _safe_text(candidate.get("analysis"), 65)
        if not analysis:
            continue
        for evidence_id in ids:
            row = allowed[str(evidence_id)]
            if (scope == "class_assessment" and not row.get("common_missed_keys")
                    and re.search(r"漏选|未选|少选", analysis)):
                continue
            if (scope == "class_assessment" and not row.get("common_extra_keys")
                    and re.search(r"错选|误选|多选", analysis)):
                continue
            if row.get("interpretation_allowed", True):
                model_concepts[str(evidence_id)] = analysis

    suggestions = []
    raw_recommendations = parsed.get("recommendations")
    for candidate in raw_recommendations if isinstance(raw_recommendations, list) else []:
        if not isinstance(candidate, dict):
            continue
        ids = candidate.get("evidence_ids") or []
        if not isinstance(ids, list) or not ids or any(str(value) not in allowed for value in ids):
            continue
        action = _safe_text(candidate.get("action"), 45)
        if action and action not in suggestions:
            related = [allowed[str(value)] for value in ids]
            suggestions.append(f"{_reference_label(related)}：{action}")
        if len(suggestions) >= 3:
            break

    interpretable_count = sum(bool(row.get("interpretation_allowed", True)) for row in rows)
    if (scope == "class_assessment" and interpretable_count == 1
            and len(suggestions) == 1):
        supported = next(row for row in rows if row.get("interpretation_allowed", True))
        suggestions.append(
            f"{_reference_label([supported])}：用另一道{supported.get('name') or '相关概念'}题复测，"
            "核对选项依据。")
    model_valid = bool(model_summary and interpretable_count
                       and len(model_concepts) >= min(2, interpretable_count)
                       and 2 <= len(suggestions) <= 3)
    groups: dict[str, list[dict]] = {}
    concept_rows = ([row for row in rows if row.get("interpretation_allowed", True)]
                    if scope == "class_assessment" and interpretable_count else rows)
    for row in concept_rows:
        groups.setdefault(str(row.get("name") or "本题对应概念"), []).append(row)
    concepts = []
    for name, related in list(groups.items())[:3]:
        refs = _unique([ref for row in related for ref in row.get("refs") or []])
        positions = _unique([position for row in related for position in row.get("positions") or []])
        observation = "；".join(
            f"{(row.get('refs') or ['本题'])[0]}{str(row.get('observation') or '').rstrip('。')}"
            for row in related[:2]
        ) + "。"
        hypothesis = next((model_concepts.get(str(row["id"])) for row in related
                           if model_valid and model_concepts.get(str(row["id"]))), "")
        if any(not row.get("interpretation_allowed", True) for row in related):
            hypothesis = "作答缺失或无效时，暂不能判断具体概念理解问题。"
        concepts.append({
            "name": name, "chapter": related[0].get("chapter") or "",
            "question_positions": positions, "question_refs": refs,
            "observation": observation,
            "analysis": hypothesis or f"可能需要重新梳理{name}；具体原因仍需用新题确认。",
        })

    summary_refs = (concept_rows if scope == "class_assessment" else rows)
    summary = (_reference_label(summary_refs) + "：" + model_summary
               if model_valid else _fallback_summary(scope, rows))
    advice = suggestions if model_valid else _fallback_suggestions(scope, rows)
    # Drop complete lower-priority rows when the default reading panel grows
    # beyond its budget. Never cut a sentence or mathematical expression.
    def size() -> int:
        visible = lambda value: len(_MATH_SPAN.sub("公式", value))
        return (visible(summary)
                + sum(visible(x["observation"]) + visible(x["analysis"])
                      for x in concepts)
                + sum(visible(value) for value in advice))
    while size() > 350 and len(concepts) > 2:
        concepts.pop()
    while size() > 350 and len(advice) > 2:
        advice.pop()
    if size() > 350 and model_valid:
        return evidence_analysis(scope, evidence)
    if size() > 350:
        # A long source answer can expand a deterministic observation. Keep
        # complete sentences and the two strongest concepts, with one concise
        # action instead of cutting an evidence statement in the middle.
        advice = advice[:1]
        while size() > 350 and len(concepts) > 1:
            concepts.pop()

    result = {
        "analysis_version": ANALYSIS_VERSION,
        "status": "completed" if model_valid else "evidence_grounded_fallback",
        "source": "deepseek" if model_valid else "evidence_rules",
        "summary": summary,
        "concept_hypotheses": concepts,
        "recommendations": advice,
        "evidence_details": [{
            "ref": (row.get("refs") or [""])[0],
            "observation": row["observation"],
        } for row in rows[:6]],
        "retest_summary": None,
    }
    if model_meta and model_valid:
        result.update({key: model_meta.get(key) for key in ("model", "call_id")})
    return result
