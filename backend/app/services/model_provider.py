"""Model provider adapter. Chat and embeddings are configured separately.

No mock: if configuration is missing, callers get a typed unavailable error
and must report that status. Every real call is recorded in model_calls with
provider/model/prompt_version/duration/usage; missing usage is recorded as
missing rather than invented.
"""
from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E


class ModelUnavailable(RuntimeError):
    def __init__(self, status: str, message: str, call_id: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        # Present only when the request actually reached the provider and was
        # recorded in model_calls; lets callers point at the failed row.
        self.call_id = call_id


def chat_status() -> dict:
    if not (cfg.MODEL_PROVIDER and cfg.MODEL_CHAT_MODEL and cfg.MODEL_API_KEY):
        return {"configured": False, "status": "not_configured",
                "provider": cfg.MODEL_PROVIDER or None,
                "model": cfg.MODEL_CHAT_MODEL or None,
                "message": "未配置模型 API：provider / model / api_key 需在 .env 中填写"}
    return {"configured": True, "status": "ready_for_validation",
            "provider": cfg.MODEL_PROVIDER, "model": cfg.MODEL_CHAT_MODEL,
            "base_url": cfg.MODEL_BASE_URL or "(provider default)",
            "message": "配置已存在；实际连通性与输出质量需通过连接诊断验证"}


def embedding_status() -> dict:
    if not (cfg.EMBED_PROVIDER and cfg.EMBED_MODEL and cfg.EMBED_API_KEY):
        return {"configured": False, "status": "not_configured",
                "message": "未单独配置嵌入模型；当前仅提供真实关键词检索"}
    return {"configured": True, "status": "pending_index_build",
            "model": cfg.EMBED_MODEL, "dim": cfg.EMBED_DIM or None,
            "message": "已配置但索引尚未构建；不宣称语义检索可用"}


def _classify_error(e: Exception) -> tuple[str, str]:
    """Map an SDK/HTTP failure to a stable, diagnosable code.

    A single "call_failed" for every problem (bad key, wrong model name,
    rate limit, network down, timeout) makes real integration failures
    impossible to tell apart, so each class gets its own code.
    """
    name = type(e).__name__
    status_code = getattr(e, "status_code", None)
    text = f"{name}: {e}".lower()
    try:                                       # prefer the SDK's own classes
        import openai
        if isinstance(e, openai.APITimeoutError):
            return "timeout", "请求超时（模型未在限定时间内返回）"
        if isinstance(e, openai.APIConnectionError):
            return "network_error", "无法连接到模型服务（网络/DNS/代理）"
        if isinstance(e, openai.AuthenticationError):
            return "auth_failed", "认证失败：api_key 无效或已过期"
        if isinstance(e, openai.PermissionDeniedError):
            return "auth_failed", "认证通过但无权访问该模型"
        if isinstance(e, openai.RateLimitError):
            return "rate_limited", "触发限流或余额不足"
        if isinstance(e, openai.NotFoundError):
            return "model_not_found", "模型名或接口路径不存在"
        if isinstance(e, openai.BadRequestError):
            return "bad_request", "请求参数被拒绝（如 max_tokens 超限）"
        if isinstance(e, openai.InternalServerError):
            return "provider_error", "模型服务端错误"
    except Exception:                          # noqa: BLE001 - SDK optional here
        pass
    if "timeout" in name.lower() or "timed out" in text:
        return "timeout", "请求超时（模型未在限定时间内返回）"
    if status_code in (401, 403):
        return "auth_failed", f"认证失败（HTTP {status_code}）"
    if status_code == 429:
        return "rate_limited", "触发限流或余额不足"
    if status_code == 404:
        return "model_not_found", "模型名或接口路径不存在"
    if status_code and int(status_code) >= 500:
        return "provider_error", f"模型服务端错误（HTTP {status_code}）"
    if "connect" in text or "resolve" in text or "ssl" in text:
        return "network_error", "无法连接到模型服务（网络/DNS/代理）"
    return "call_failed", "模型调用失败"


def chat(db: Session, *, messages: list[dict], skill_name: str = "",
         prompt_version: str = "", run_id: str = "",
         temperature: float = 0.2, max_tokens: int = 1200,
         timeout: float = 60.0) -> dict:
    """Call a real OpenAI-compatible chat endpoint. Raises ModelUnavailable.

    Returns {"content", "call_id", "usage", "usage_missing", "model",
    "finish_reason"}. `model` is what the provider actually reports (not the
    configured alias) and `finish_reason == "length"` means the output was cut
    off — callers must NOT treat a truncated JSON as a valid answer.
    """
    st = chat_status()
    if not st["configured"]:
        raise ModelUnavailable("model_not_configured", st["message"])
    try:
        from openai import OpenAI
    except ImportError as e:
        raise ModelUnavailable("sdk_missing", f"openai SDK 不可用: {e}")

    kwargs: dict[str, Any] = {"api_key": cfg.MODEL_API_KEY, "timeout": timeout,
                              "max_retries": 1}
    if cfg.MODEL_BASE_URL:
        kwargs["base_url"] = cfg.MODEL_BASE_URL
    call_id = E.new_id("call")
    t0 = time.time()
    try:
        client = OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=cfg.MODEL_CHAT_MODEL, messages=messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        choice = resp.choices[0]
        content = choice.message.content or ""
        finish_reason = str(getattr(choice, "finish_reason", "") or "")
        actual_model = str(getattr(resp, "model", "") or cfg.MODEL_CHAT_MODEL)
        usage: dict[str, Any] = {}
        usage_missing = True
        if getattr(resp, "usage", None):
            u = resp.usage
            usage = {k: getattr(u, k, None) for k in
                     ("prompt_tokens", "completion_tokens", "total_tokens")}
            usage = {k: v for k, v in usage.items() if v is not None}
            usage_missing = not usage
        db.add(E.ModelCall(
            call_id=call_id, run_id=run_id, skill_name=skill_name,
            provider=cfg.MODEL_PROVIDER, model=actual_model,
            prompt_version=prompt_version, usage=usage,
            usage_missing=usage_missing, finish_reason=finish_reason,
            duration_ms=int((time.time() - t0) * 1000), status="ok",
        ))
        db.commit()
        return {"content": content, "call_id": call_id,
                "usage": usage, "usage_missing": usage_missing,
                "model": actual_model, "finish_reason": finish_reason,
                "duration_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        code, hint = _classify_error(e)
        db.add(E.ModelCall(
            call_id=call_id, run_id=run_id, skill_name=skill_name,
            provider=cfg.MODEL_PROVIDER, model=cfg.MODEL_CHAT_MODEL,
            prompt_version=prompt_version, usage={}, usage_missing=True,
            duration_ms=int((time.time() - t0) * 1000),
            status="failed", error=f"[{code}] {hint} | {type(e).__name__}: {e}",
        ))
        db.commit()
        raise ModelUnavailable(code, f"{hint}（{type(e).__name__}: {e}）", call_id)


def parse_json_content(content: str) -> dict | list | None:
    """Tolerant JSON extraction; never pretend malformed output is valid."""
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1] if "```" in content[3:] else content
        if content.lstrip().startswith("json"):
            content = content.lstrip()[4:]
        content = content.strip().rstrip("`").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = min([i for i in (content.find("{"), content.find("[")) if i >= 0],
                    default=-1)
        if start < 0:
            return None
        for end in range(len(content), start, -1):
            try:
                return json.loads(content[start:end])
            except json.JSONDecodeError:
                continue
        return None
