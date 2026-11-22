"""Fixed-version student-by-knowledge-point evidence projection."""
from sqlalchemy import select

from backend.app.models import entities as E
from backend.app.services import classroom_diagnosis as diagnosis


def build(db, classroom_id, assessment_version_id):
    source = diagnosis.class_assessment(db, classroom_id, assessment_version_id)
    version, _, _ = diagnosis._version_context(db, assessment_version_id)
    items = diagnosis._paper_items(db, version)
    attempts = diagnosis._attempt_map(db, assessment_version_id)
    decisions = {(r["student_id"], r["source_attempt_id"]): r
                 for r in source["learning_loop_summary"]["rows"]}
    rows, columns = [], set()
    for student in source["anonymous_comparison"]:
        attempt = attempts.get(student["student_id"])
        scores = diagnosis._scores_by_item(db, attempt, items) if attempt else {}
        buckets = {}
        for item in items:
            score = scores.get(item.item_id)
            kp = diagnosis._kp_id(item, score)
            if not kp:
                continue
            columns.add(kp)
            bucket = buckets.setdefault(kp, {"kp_id": kp, "question_count": 0,
                "scored_count": 0, "correct_count": 0, "positions": []})
            bucket["question_count"] += 1
            bucket["positions"].append(item.position)
            if attempt and diagnosis._valid_score(version, attempt, score):
                bucket["scored_count"] += 1
                bucket["correct_count"] += score.correct is True
        summary = decisions.get((student["student_id"], attempt.attempt_id if attempt else ""), {})
        states = {r["kp_id"]: r for r in summary.get("concept_results", [])}
        for kp, bucket in buckets.items():
            wrong = bucket["scored_count"] - bucket["correct_count"]
            bucket.update(wrong_count=wrong, ungraded_count=(
                          bucket["question_count"] - bucket["scored_count"]
                          if attempt and student["participation_status"] != "absent" else 0),
                          state=states.get(kp, {}).get("state") or ("needs_review" if wrong else "insufficient"))
        account = db.scalar(select(E.User).join(E.Student, E.Student.linked_user_id == E.User.user_id)
                            .where(E.Student.student_id == student["student_id"]))
        attention = sum(cell["state"] == "needs_review" for cell in buckets.values())
        rows.append({**{key: student[key] for key in ("student_id", "display_code", "participation_status")},
            "attempt_id": attempt.attempt_id if attempt else "",
            "cells": buckets, "attention_count": attention,
            "ungraded_count": sum(cell["ungraded_count"] for cell in buckets.values()),
            "task_state": summary.get("task_state", "ready"),
            "eligible": bool(attempt and attempt.status == "submitted" and student["scored_items"] > 0
                             and account and account.active and account.role == "student")})
    labels = diagnosis._kp_labels(db, columns)
    rows.sort(key=lambda row: (-row["attention_count"], row["display_code"]))
    return {"classroom": source["classroom"], "assessment": source["assessment"],
        "columns": [{"kp_id": kp, "name": labels.get(kp) or "未命名知识点"} for kp in sorted(columns)],
        "rows": rows, "unmapped_item_count": sum(not diagnosis._kp_id(item) for item in items),
        "basis": "同一固定试卷版本，每名学生使用当前有效答卷。单元格保留原卷题量与评分；颜色结合关联复测证据。未评分、缺考和未交卷分别标记。"}
