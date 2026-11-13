"""Skill framework: static registry + real execution with a persisted run.

Every invocation produces the unified envelope required by the task book:
result / evidence_refs / uncertainties / next_actions / skill_version /
run_id (+ engine, status, prompt_version, thread_id, job_id).
A failed model call never deletes saved answers or deterministic scores.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import model_provider


@dataclass(frozen=True)
class SkillSpec:
    name: str
    version: str
    description: str
    requires_model: bool
    input_schema: dict
    output_schema: dict
    prompt_version: str
    execute: Callable[..., dict]
    examples: list[dict] = field(default_factory=list)


def envelope(spec: SkillSpec, out: dict, *, run_id: str, thread_id: str,
             job_id: str, status: str = "succeeded", error: str = "") -> dict:
    return {
        "skill": spec.name,
        "result": out.get("result"),
        "evidence_refs": out.get("evidence_refs", []),
        "uncertainties": out.get("uncertainties", []),
        "next_actions": out.get("next_actions", []),
        "skill_version": spec.version,
        "run_id": run_id,
        "status": status,
        "engine": out.get("engine", "deterministic"),
        "model": out.get("model", ""),
        "prompt_version": spec.prompt_version,
        "thread_id": thread_id,
        "job_id": job_id,
        "error": error,
        "extra": out.get("extra", {}),
    }


def upsert_diagnosis(db: Session, *, user_id: str, level: str, target_id: str,
                     payload: dict | None, evidence_refs: list | None = None,
                     uncertainties: list | None = None,
                     next_actions: list | None = None,
                     skill_version: str, run_id: str = "",
                     engine: str = "deterministic",
                     merge_payload: bool = False) -> str:
    """Upsert the current report for (level, target, skill version).

    Public on purpose: the LangGraph orchestration layer must persist the very
    same rows `run_skill()` writes. Before this was private, every node in
    ``skills/graphs/learning_diagnosis_graph.py`` called an implementation
    directly, so a graph run produced a success status and *no* Diagnosis row —
    the three-layer report then rendered empty.

    ``merge_payload=True`` patches the existing payload instead of replacing it,
    which is how the cognitive-layer node adds ``three_layers`` to the paper
    diagnosis written moments earlier by the same graph run.
    """
    from sqlalchemy import select
    row = db.execute(select(E.Diagnosis).where(
        E.Diagnosis.user_id == user_id, E.Diagnosis.level == level,
        E.Diagnosis.target_id == target_id,
        E.Diagnosis.skill_version == skill_version)).scalars().first()
    if row is None:
        row = E.Diagnosis(diagnosis_id=E.new_id("diag"), user_id=user_id,
                          level=level, target_id=target_id)
        db.add(row)
    if merge_payload and isinstance(row.payload, dict) and isinstance(payload, dict):
        merged = dict(row.payload)
        merged.update(payload)
        row.payload = merged
    else:
        row.payload = payload
    if evidence_refs is not None:
        existing = list(row.evidence_refs or [])
        seen = {str(e) for e in existing}
        row.evidence_refs = existing + [e for e in evidence_refs
                                        if str(e) not in seen]
    if uncertainties is not None:
        row.uncertainties = uncertainties
    if next_actions is not None:
        row.next_actions = next_actions
    row.skill_version = skill_version
    row.run_id = run_id or row.run_id or ""
    row.engine = engine
    row.created_at = E.utcnow()
    db.commit()
    return row.diagnosis_id


def _save_diagnosis(db: Session, *, user_id: str, level: str, target_id: str,
                    envelope: dict, spec: SkillSpec) -> None:
    upsert_diagnosis(
        db, user_id=user_id, level=level, target_id=target_id,
        payload=envelope.get("result"),
        evidence_refs=envelope.get("evidence_refs", []),
        uncertainties=envelope.get("uncertainties", []),
        next_actions=envelope.get("next_actions", []),
        skill_version=spec.version, run_id=envelope.get("run_id", ""),
        engine=envelope.get("engine", "deterministic"))


def run_skill(db: Session, name: str, payload: dict, *,
              thread_id: str = "", job_id: str = "",
              user_id: str = "local_user") -> dict:
    from backend.app.skills.registry import REGISTRY  # avoid circular import
    if name not in REGISTRY:
        raise KeyError(f"未注册的 skill: {name}")
    spec = REGISTRY[name]
    run_id = E.new_id("run")
    # Every invocation must be traceable, including direct API calls that are
    # not part of a LangGraph thread or a background job.
    thread_id = thread_id or f"direct:{run_id}"
    t0 = time.time()
    sr = E.SkillRun(
        run_id=run_id, skill_name=name, skill_version=spec.version,
        thread_id=thread_id, job_id=job_id, user_id=user_id,
        status="running", input_payload=payload,
        prompt_version=spec.prompt_version,
        model=(cfg.MODEL_CHAT_MODEL if spec.requires_model else ""),
        # Write the full envelope skeleton at insert time: a run that is still
        # executing must already carry the same envelope shape, otherwise any
        # audit of "every call saved a uniform envelope" fails on in-flight runs.
        output_payload=envelope(spec, {"status": "running"}, run_id=run_id,
                                thread_id=thread_id, job_id=job_id,
                                status="running",
                                error="skill 正在执行，尚未产生结果"),
    )
    db.add(sr)
    db.commit()
    try:
        out = spec.execute(db, payload, run_id=run_id,
                           thread_id=thread_id, job_id=job_id,
                           user_id=user_id) or {}
        status = out.get("status", "succeeded")
        result = envelope(spec, out, run_id=run_id, thread_id=thread_id,
                          job_id=job_id, status=status,
                          error=out.get("error", ""))
        if out.get("diagnosis_level"):
            _save_diagnosis(db, user_id=user_id,
                            level=out["diagnosis_level"],
                            target_id=str(out.get("diagnosis_target", "")),
                            envelope=result, spec=spec)
        sr.status = status
        sr.engine = result["engine"]
        sr.output_payload = result
        sr.error = result["error"]
    except model_provider.ModelUnavailable as e:
        result = envelope(spec, {
            "result": {"message": e.message},
            "uncertainties": ["模型未配置或调用失败，本次未产生模型结论"],
            "next_actions": ["配置 .env 后重新运行", "已有的确定性结果仍然有效"],
            "engine": "model_unavailable",
        }, run_id=run_id, thread_id=thread_id, job_id=job_id,
            status=e.status, error=e.message)
        sr.status = e.status
        sr.engine = "model_unavailable"
        sr.output_payload = result
        sr.error = e.message
    except Exception as e:
        result = envelope(spec, {
            "result": None,
            "uncertainties": ["skill 执行异常，未产生可信结论"],
            "next_actions": ["检查输入与日志后重试"],
            "engine": "error",
        }, run_id=run_id, thread_id=thread_id, job_id=job_id,
            status="failed", error=f"{type(e).__name__}: {e}")
        sr.status = "failed"
        sr.engine = "error"
        sr.output_payload = result
        sr.error = result["error"]
    sr.finished_at = E.utcnow()
    sr.duration_ms = int((time.time() - t0) * 1000)
    db.add(sr)
    db.commit()
    return result
