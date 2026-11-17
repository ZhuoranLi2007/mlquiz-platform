"""Record real prompts for course evidence; never reconstruct absent history."""
import hashlib
import json
import re

from sqlalchemy import select
from backend.app import config as cfg
from backend.app.models import entities as E


def redact(value):
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if key.lower() in {"api_key", "authorization", "password"}
                      else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    if value.startswith("data:") and ";base64," in value:
        return "[image payload sha256=" + hashlib.sha256(value.encode()).hexdigest() + "]"
    for secret in (cfg.MODEL_API_KEY, cfg.EMBED_API_KEY):
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return re.sub(r"\bsk-[A-Za-z0-9_-]{16,}", "[REDACTED]", value)


def record(db, call_id, messages, response_text=""):
    clean = redact(messages)
    db.add(E.PromptRecord(call_id=call_id, messages=clean,
        response_text=redact(response_text),
        request_sha256=hashlib.sha256(json.dumps(
            clean, ensure_ascii=False, sort_keys=True).encode()).hexdigest()))


def export_history(db):
    rows = db.execute(select(E.PromptRecord, E.ModelCall).join(
        E.ModelCall, E.ModelCall.call_id == E.PromptRecord.call_id).order_by(
        E.PromptRecord.created_at, E.PromptRecord.call_id)).all()
    return {"kind": "runtime_model_prompt_history", "exported_at": E.utcnow(),
        "note": "仅包含本功能启用后的真实模型调用；开发工具中的对话历史需另行导出。图片仅留摘要，密钥已脱敏。",
        "count": len(rows), "items": [{
            "call_id": call.call_id, "run_id": call.run_id,
            "skill": call.skill_name, "model": call.model,
            "prompt_version": call.prompt_version, "created_at": record.created_at,
            "status": call.status, "messages": record.messages,
            "response": record.response_text, "request_sha256": record.request_sha256,
        } for record, call in rows]}
