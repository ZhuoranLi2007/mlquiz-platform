"""Append-only, anonymous DeepSeek review of classroom scoring references."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services import model_provider
from backend.app.services.diagnosis_analysis_v2 import _safe_text


PROMPT_VERSION = "class-answer-review-v1"


def _keys(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    values = payload.get("keys") if isinstance(payload.get("keys"), list) else [payload.get("key")]
    return sorted({str(value).strip().upper() for value in values if str(value or "").strip()})


def _fingerprint(item: dict) -> str:
    reference = item.get("answer_reference") or {}
    serial = json.dumps({
        "prompt_version": PROMPT_VERSION, "question_id": item.get("question_id"),
        "version": item.get("scoring_question_version"), "stem": item.get("stem"),
        "options": item.get("options"), "answer": reference.get("payload"),
    }, ensure_ascii=False, sort_keys=True)
    return sha256(serial.encode("utf-8")).hexdigest()


def _original_explanation(item: dict) -> str:
    return str((item.get("answer_reference") or {}).get("explanation") or "")


def _apply(payload: dict, reviews: list[dict]) -> None:
    by_position = {row["position"]: row for row in reviews}
    for collection in (payload.get("question_analysis") or [],
                       payload.get("high_frequency_errors") or []):
        for item in collection:
            review = by_position.get(item.get("position"))
            if not review:
                continue
            item["answer_review_status"] = review["status"]
            if review["status"] == "agreed":
                item["answer_reference"] = {
                    **(item.get("answer_reference") or {}),
                    "explanation": review["explanation"],
                }


def review_class_answers(
    db: Session, payload: dict, *, run_id: str, actor_user_id: str,
    classroom_id: str, assessment_version_id: str,
) -> list[dict]:
    eligible = [item for item in payload.get("question_analysis") or []
                if item.get("scoring_reference_status") == "available"
                and item.get("options") and _keys((item.get("answer_reference") or {}).get("payload"))]
    if not eligible:
        return []
    previous = db.execute(select(E.Diagnosis).where(
        E.Diagnosis.user_id == actor_user_id,
        E.Diagnosis.scope_type == "class_assessment",
        E.Diagnosis.classroom_id == classroom_id,
        E.Diagnosis.assessment_version_id == assessment_version_id,
    ).order_by(E.Diagnosis.created_at.desc())).scalars().all()
    cached: dict[str, dict] = {}
    for diagnosis in previous:
        for row in (diagnosis.payload or {}).get("answer_reviews") or []:
            if (isinstance(row, dict) and row.get("fingerprint")
                    and row.get("status") in {"agreed", "disagreed"}):
                cached.setdefault(row["fingerprint"], row)
    results: list[dict] = []
    pending: list[dict] = []
    for item in eligible:
        fingerprint = _fingerprint(item)
        if fingerprint in cached:
            results.append(dict(cached[fingerprint]))
        else:
            pending.append(item)
    if pending:
        questions = [{
            "position": item["position"], "stem": item["stem"],
            "options": [{"key": row.get("key"), "text": row.get("text")}
                        for row in item["options"] if isinstance(row, dict)],
            "answer_shape": ("keys" if len(_keys((item.get("answer_reference") or {}).get("payload"))) > 1
                             else "key"),
        } for item in pending]
        system = (
            "你是机器学习课程的独立审题教师。只依据给出的修订版题干和选项解题；"
            "不得假设学生如何作答。返回严格 JSON：items 数组，每题有 position、"
            "answer_payload（单选 {key}，多选 {keys}）和 explanation。"
            "解析用1至3句说明关键理由，不提评分流程或学生，不输出 HTML。"
            "公式用有效的 $...$ 或 $$...$$ LaTeX。不能确定时返回空答案及原因。"
        )
        try:
            response = model_provider.chat(
                db, messages=[{"role": "system", "content": system},
                              {"role": "user", "content": json.dumps(
                                  {"questions": questions}, ensure_ascii=False)}],
                skill_name="class_answer_review", prompt_version=PROMPT_VERSION,
                run_id=run_id, temperature=0.1, max_tokens=2400, timeout=60.0)
            if response.get("finish_reason") == "length":
                raise model_provider.ModelUnavailable("truncated", "解析输出被截断")
            parsed = model_provider.parse_json_content(response.get("content") or "")
            raw_rows = parsed.get("items") if isinstance(parsed, dict) else None
            candidates: dict[int, dict | None] = {}
            for row in raw_rows if isinstance(raw_rows, list) else []:
                if not isinstance(row, dict):
                    continue
                try:
                    position = int(row.get("position"))
                except (TypeError, ValueError):
                    continue
                candidates[position] = row if position not in candidates else None
            for item in pending:
                candidate = candidates.get(int(item["position"])) or {}
                expected = _keys((item.get("answer_reference") or {}).get("payload"))
                given = _keys(candidate.get("answer_payload"))
                explanation = _safe_text(candidate.get("explanation"), 220)
                options = {str(row.get("key") or "").upper() for row in item["options"]
                           if isinstance(row, dict)}
                agreed = bool(given and given == expected and explanation
                              and all(key in options for key in given))
                status = "agreed" if agreed else "disagreed" if given else "unavailable"
                results.append({
                    "position": item["position"], "fingerprint": _fingerprint(item),
                    "status": status,
                    "explanation": explanation if agreed else _original_explanation(item),
                    "model_call_id": response.get("call_id") or "",
                    "prompt_version": PROMPT_VERSION,
                })
        except model_provider.ModelUnavailable as exc:
            for item in pending:
                results.append({
                    "position": item["position"], "fingerprint": _fingerprint(item),
                    "status": "unavailable", "explanation": _original_explanation(item),
                    "reason": exc.status, "prompt_version": PROMPT_VERSION,
                })
    results.sort(key=lambda row: int(row["position"]))
    _apply(payload, results)
    return results
