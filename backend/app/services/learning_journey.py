"""Read-only learning timelines and explicitly recorded resource interactions."""
from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services.learning_evidence import instant
from backend.app.services.learning_resources import material_catalog
from backend.app.services.adaptive_learning import path

ACTIVITIES = {"resource_opened": "首次查看学习资料", "resource_completed": "标记阅读完成"}


def record_activity(db: Session, *, user_id: str, attempt_id: str,
                    kp_id: str, resource_id: str, action: str) -> dict:
    if action not in ACTIVITIES:
        raise ValueError("学习活动类型无效")
    resource = db.get(E.Resource, resource_id)
    local = material_catalog().get(resource_id.removeprefix("course.")) if resource_id.startswith("course.") else None
    if resource:
        valid = kp_id in (resource.kp_ids or [])
        title = resource.title
    else:
        valid = bool(local) and resource_id == f"course.{kp_id}"
        title = (local or {}).get("title") or "课程原页"
    if not valid:
        raise ValueError("资料与当前知识点不匹配")
    identity = "\n".join([user_id, attempt_id, kp_id, resource_id, action])
    event_id = "study_" + hashlib.sha256(identity.encode()).hexdigest()[:40]
    db.execute(insert(E.LearningEvent).values(
        event_id=event_id, user_id=user_id, target_id=attempt_id, event_type=action,
        payload={"kp_id": kp_id, "resource_id": resource_id, "title": title},
    ).on_conflict_do_nothing(index_elements=["event_id"]))
    db.commit()
    return {"recorded": True, "action": action}


def attach(db: Session, workbench: dict, *, user_id: str,
           attempt_id: str, submitted_at: str, learning_summary: dict | None = None) -> dict:
    decisions = {row["kp_id"]: row for row in (learning_summary or {}).get("concept_results", [])}
    activities = list(db.scalars(select(E.LearningEvent).where(
        E.LearningEvent.user_id == user_id,
        E.LearningEvent.target_id == attempt_id,
        E.LearningEvent.event_type.in_(ACTIVITIES),
    ).order_by(E.LearningEvent.occurred_at, E.LearningEvent.event_id)))
    for concept in workbench["concepts"]:
        kp = db.get(E.KnowledgePoint, concept["kp_id"])
        concept["map_chapter"] = (concept["chapter"] if concept["chapter"] and
            concept["chapter"] != "未映射章节" else kp.chapter if kp and kp.chapter else "章节待确认")
        stats = concept["statistics"]
        events = []
        if stats["question_count"]:
            events.append({
                "id": "baseline", "kind": "exam", "title": "原卷作答",
                "at": submitted_at, "correct_count": stats["correct_count"],
                "scored_count": stats["scored_count"], "ungraded_count": stats["ungraded_count"],
                "state": "needs_review" if stats["wrong_count"] else "insufficient",
                "description": "按本卷关联题目统计，掌握结论结合独立复测证据。",
                "action": "answers",
            })
        events.extend({
            "id": row.event_id, "kind": row.event_type, "title": ACTIVITIES[row.event_type],
            "at": row.occurred_at, "description": (row.payload or {}).get("title") or "学习资料",
            "resource_id": (row.payload or {}).get("resource_id"),
            "state": "recorded",
        } for row in activities if (row.payload or {}).get("kp_id") == concept["kp_id"])
        evidence = {row["round"]: row for row in concept["learning_result"].get("history", [])}
        for round_row in concept["retests"]:
            proof = evidence.get(round_row["round"], {})
            events.append({
                "id": f"round-{round_row['round']}", "kind": "retest",
                "title": f"第 {round_row['round']} 轮复测",
                "at": round_row.get("submitted_at") or round_row.get("published_at") or "",
                "state": proof.get("state") or round_row["state"],
                "description": round_row["label"],
                "correct_count": round_row["correct_count"], "scored_count": round_row["scored_count"],
                "ungraded_count": round_row["ungraded_count"],
                "independent": proof.get("independent", False),
                "distinct_families": proof.get("distinct_families"),
                "distinct_angles": proof.get("distinct_angles"),
                "action": "retest", "round": round_row["round"],
            })
        events.sort(key=lambda event: (instant(event["at"]).timestamp()
                    if instant(event["at"]) else float("inf"), event["id"]))
        concept["timeline"] = events
        concept["learning_path"] = path(concept, decisions.get(concept["kp_id"]))
    return workbench
