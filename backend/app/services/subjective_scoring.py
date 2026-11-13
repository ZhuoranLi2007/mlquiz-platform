"""Rubric-based scoring for short-answer / derivation / comprehensive items.

Unlike ANSWER GENERATION (which must never see a student response), scoring
deliberately receives the response. The two request paths are separate modules
and separate prompt versions so the audit trail stays unambiguous.

Equivalent expressions and alternative correct solutions are explicitly
accepted by the prompt AND enforced by the deterministic post-check: a point
cannot be marked hit without a reason, and the awarded score must equal the sum
of hit point scores and stay within [0, max_score].

When the model is not configured this returns `needs_model_scoring` -- it does
NOT fabricate a score and does NOT fall back to a keyword guess that would
silently punish a differently worded correct answer.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import model_provider
from backend.app.skills import prompts


def _result(snapshot: dict, max_score: float) -> dict:
    return {
        "max_score": max_score,
        "awarded_score": 0.0,
        "correct": None,
        "scoring_status": "needs_model_scoring",
        "detail": {"question_type": snapshot.get("question_type", "unknown"),
                   "rule": "rubric_points", "notes": []},
    }


def _scaled_points(points: list[dict], max_score: float) -> list[dict]:
    """Scale rubric caps to the paper item's actual score.

    The source package may use 10/12/15/16-point rubrics while a teacher can
    assign any paper score.  Weights are authoritative when present; otherwise
    original point scores are converted to weights.  The final point absorbs
    the rounding residue so caps always sum exactly to ``max_score``.
    """
    valid = [dict(point) for point in points if isinstance(point, dict)]
    if not valid:
        return []
    raw_weights = []
    for point in valid:
        try:
            raw_weights.append(max(0.0, float(point.get("weight", 0) or 0)))
        except (TypeError, ValueError):
            raw_weights.append(0.0)
    if sum(raw_weights) <= 0:
        raw_scores = []
        for point in valid:
            try:
                raw_scores.append(max(0.0, float(point.get("score", 0) or 0)))
            except (TypeError, ValueError):
                raw_scores.append(0.0)
        raw_weights = raw_scores
    total = sum(raw_weights)
    if total <= 0:
        raw_weights = [1.0] * len(valid)
        total = float(len(valid))
    scaled = []
    running = 0.0
    for index, (point, weight) in enumerate(zip(valid, raw_weights)):
        cap = (round(max_score - running, 4) if index == len(valid) - 1
               else round(max_score * weight / total, 4))
        running += cap
        point["source_score"] = point.get("score", 0)
        point["weight"] = round(weight / total, 8)
        point["score"] = cap
        point.setdefault("id", f"p{index + 1}")
        point.setdefault("text", point.get("point", ""))
        scaled.append(point)
    return scaled


def score_subjective(db: Session, snapshot: dict, response: dict,
                     max_score: float, *, run_id: str = "") -> dict:
    """Return the same shape as scoring.score_one()."""
    base = _result(snapshot, max_score)
    detail = base["detail"]
    rub = snapshot.get("rubric") or {}
    ans = snapshot.get("answer_payload") or {}
    source_points = rub.get("points") or []
    points = _scaled_points(source_points, max_score)
    reference = ans.get("reference_answer") or ""
    student_text = str(response.get("text") or response.get("content") or "")

    if not student_text.strip():
        detail["notes"].append("未作答，记 0 分")
        base.update(correct=False, awarded_score=0.0, scoring_status="auto")
        detail["rule"] = "empty_response"
        return base
    if not points and not reference:
        detail["notes"].append("该题没有评分要点或参考答案，无法自动评分")
        detail["rule"] = "no_rubric"
        base["scoring_status"] = "needs_manual"
        return base

    st = model_provider.chat_status()
    if not st["configured"]:
        detail["notes"].append(
            "模型未配置：主观题保留作答，等待按评分要点评分；不使用关键词猜测冒充评分")
        detail["rule"] = "rubric_points_model_pending"
        return base

    messages = [
        {"role": "system", "content": prompts.RUBRIC_SCORING_SYSTEM},
        {"role": "user", "content": prompts.RUBRIC_SCORING_USER.format(
            stem=snapshot.get("stem", ""), reference_answer=reference,
            points=json.dumps(points or [{"id": "p1", "text": "结论与推导正确",
                                          "score": max_score,
                                          "accept": ["等价表达", "其他正确解法"]}],
                              ensure_ascii=False),
            max_score=max_score, response=student_text[:4000])},
    ]
    try:
        res = model_provider.chat(
            db, messages=messages, skill_name="rubric_scoring",
            prompt_version=prompts.RUBRIC_PROMPT_VERSION, run_id=run_id,
            temperature=0.0, max_tokens=2000, timeout=120.0)
    except model_provider.ModelUnavailable as e:
        detail["notes"].append(f"模型评分失败，保留待评分：{e.message}")
        detail["rule"] = "rubric_points_model_failed"
        return base

    if res.get("finish_reason") == "length":
        # Truncated JSON can still "parse" into a partial object; a partial
        # rubric evaluation would silently under-score a correct answer.
        detail["notes"].append("模型评分输出被 max_tokens 截断，丢弃本次结果，保留待评分")
        detail["rule"] = "rubric_points_truncated"
        detail["model"] = res.get("model", "")
        detail["model_call_id"] = res.get("call_id", "")
        base["scoring_status"] = "needs_manual"
        return base

    out = model_provider.parse_json_content(res.get("content", ""))
    if not isinstance(out, dict):
        detail["notes"].append("模型评分输出不是 JSON 对象，保留待评分")
        detail["rule"] = "rubric_points_malformed"
        base["scoring_status"] = "needs_manual"
        return base

    # ---- deterministic guardrails on the model's judgement ----
    evals = out.get("points") if isinstance(out.get("points"), list) else []
    allowed = {str(p.get("id", i)): float(p.get("score", 0) or 0)
               for i, p in enumerate(points) if isinstance(p, dict)}
    total = 0.0
    clean: list[dict] = []
    for ev in evals:
        if not isinstance(ev, dict):
            continue
        pid = str(ev.get("id", ""))
        cap = allowed.get(pid, max_score if not allowed else 0.0)
        try:
            sc = float(ev.get("score", 0) or 0)
        except (TypeError, ValueError):
            sc = 0.0
        hit = bool(ev.get("hit"))
        # A hit without a reason is not auditable; clamp any over-award.
        reason = str(ev.get("reason", "") or "")
        sc = max(0.0, min(sc, cap, max_score))
        if not hit or not reason.strip():
            sc = 0.0
        total += sc
        clean.append({"id": pid, "hit": hit, "score": sc, "reason": reason})
    total = round(min(total, max_score), 4)
    detail.update(
        rule="rubric_points_model",
        scoring_engine="model",
        deterministic_guardrails=True,
        rubric_scaling={"source_total": round(sum(float(p.get("source_score", 0) or 0)
                                                    for p in points), 4),
                        "paper_item_max_score": max_score,
                        "scaled_caps_total": round(sum(float(p.get("score", 0) or 0)
                                                        for p in points), 4)},
        rubric_evaluation=clean,
        equivalent_expression_accepted=bool(
            out.get("equivalent_expression_accepted")),
        alternative_solution_accepted=bool(
            out.get("alternative_solution_accepted")),
        model_notes=str(out.get("notes", "") or "")[:600],
        model=res.get("model", "") or cfg.MODEL_CHAT_MODEL,
        model_call_id=res.get("call_id", ""),
        finish_reason=res.get("finish_reason", ""),
        duration_ms=res.get("duration_ms", 0),
    )
    base.update(awarded_score=total, scoring_status="auto",
                correct=(abs(total - max_score) <= 1e-6))
    if not clean:
        detail["notes"].append("模型未返回可核对的要点结果，给 0 分并保留原始输出")
    return base


def score_one_with_model(db: Session, snapshot: dict, response: dict,
                         max_score: float, *, run_id: str = "") -> dict:
    """Objective items stay deterministic; text items use rubric scoring."""
    from backend.app.services.scoring import OBJECTIVE_TYPES, effective_type, score_one
    if effective_type(snapshot) in OBJECTIVE_TYPES:
        return score_one(snapshot, response, max_score)
    if not (snapshot.get("answer_payload") or {}):
        base = _result(snapshot, max_score)
        base["scoring_status"] = "needs_manual"
        base["detail"].update(rule="no_reference_answer",
                              notes=["该题尚无参考答案，不能评分"])
        return base
    return score_subjective(db, snapshot, response, max_score, run_id=run_id)
