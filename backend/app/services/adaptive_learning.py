"""Deterministic next-step policy used by the UI and actual retest selection."""
from datetime import datetime, timezone

from backend.app.services import learning_evidence as evidence
from backend.app.services.learning_catalog import target


def profile(decision, *, now=None):
    now = now or datetime.now(timezone.utc)
    state = decision.get("state", "insufficient")
    due = evidence.instant(decision.get("due_at"))
    if state == "needs_review":
        return {"stage": "foundation", "label": "基础巩固", "difficulty": "基础",
                "cognitive_level": "理解", "reason": "当前仍有可靠错答，先辨析概念与关键条件。"}
    if state == "achieved" and due and due <= now:
        return {"stage": "delayed", "label": "延后确认", "difficulty": "中等",
                "cognitive_level": "应用", "reason": "已到延后复测时间，使用独立新题确认保持情况。"}
    if state in {"achieved", "delayed_confirmed"}:
        return {"stage": "transfer", "label": "迁移巩固", "difficulty": "困难",
                "cognitive_level": "应用", "reason": "已有达标证据，继续在新的条件或情境中应用。"}
    return {"stage": "application", "label": "应用验证", "difficulty": "中等",
            "cognitive_level": "应用", "reason": "现有证据不足，补充不同题族和考查角度的独立作答。"}


def path(concept, primary_decision=None):
    decision = primary_decision or concept["learning_result"]
    next_step = profile(decision)
    guide = target(concept["kp_id"], concept["name"])
    if concept["statistics"]["ungraded_count"] and not concept["statistics"]["scored_count"]:
        next_step = {**next_step, "stage": "awaiting_score", "label": "等待评分",
                     "reason": "本卷关联题目尚无有效评分，可先阅读资料，再补充检测证据。"}
    completed = any(event["kind"] == "resource_completed" for event in concept.get("timeline", []))
    return {**next_step, "kp_id": concept["kp_id"], "due_at": decision.get("due_at", ""),
        "training_scope": "按登记知识点整体证据安排训练" if concept["id"] != concept["kp_id"] else "",
        "steps": [
            {"key": "study", "label": "阅读与梳理", "complete": completed, "detail": guide["action"]},
            {"key": "test", "label": next_step["label"], "complete": False,
             "detail": f"优先安排{next_step['difficulty']}难度、{next_step['cognitive_level']}层次的题目。"},
            {"key": "confirm", "label": "查看证据与延后确认",
             "complete": decision["state"] == "delayed_confirmed",
             "detail": "每个知识点至少覆盖2个独立题族与2个考查角度，相关题均可靠评分且答对后达标；间隔至少24小时开始的新一轮检测用于延后确认。"}]}
