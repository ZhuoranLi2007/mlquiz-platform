"""AI reference-answer queue + '答案有疑问' workflow endpoints.

Student-facing safety: NONE of these endpoints return `AnswerGeneration.result`
(that contains the answer payload / rubric). Only provenance metadata and
status are exposed. Answers themselves reach the browser exclusively through
`student_view(reveal=True)` after submission.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException

from backend.app import config as cfg
from backend.app.api.routes import USER_ID
from backend.app.models import entities as E
from backend.app.models.base import get_session
from backend.app.schemas import dto
from backend.app.services import (answer_pipeline, attempt_service,
                                  bank_service, model_provider)
from backend.app.skills import prompts
from backend.app.workers.worker import create_job

router = APIRouter(prefix="/api")


def _gen_public(g: E.AnswerGeneration) -> dict:
    """Metadata only -- never the generated answer content."""
    ac = (g.checks or {}).get("auto_check")
    checks_passed = bool(ac.get("passed")) if isinstance(ac, dict) else False
    return {
        "generation_id": g.generation_id,
        "question_id": g.question_id,
        "question_version": g.question_version,
        "answer_version": g.answer_version,
        "trigger": g.trigger, "status": g.status,
        "source": g.source, "label": answer_pipeline.answer_label(g.source),
        "model_provider": g.model_provider, "model": g.model_name,
        "prompt_version": g.prompt_version, "generated_at": g.generated_at,
        "request_fingerprint": g.request_fingerprint,
        "student_answer_included": g.student_answer_included,
        "exception_code": g.exception_code,
        "exception_detail": g.exception_detail,
        "checks_passed": checks_passed,
        "retry_count": g.retry_count,
        "created_at": g.created_at, "updated_at": g.updated_at,
    }


@router.get("/answer-generations")
def list_generations(status: str = "", limit: int = 200,
                     db: Session = Depends(get_session)) -> dict:
    stmt = select(E.AnswerGeneration)
    if status:
        stmt = stmt.where(E.AnswerGeneration.status == status)
    rows = db.execute(stmt.order_by(E.AnswerGeneration.created_at.desc())
                      ).scalars().all()[:limit]
    return {"total": len(rows), "items": [_gen_public(g) for g in rows],
            "summary": answer_pipeline.generation_summary(db),
            "note": "本接口只返回来源/状态元数据，不返回参考答案内容"}


@router.get("/answers/queue-summary")
def queue_summary(db: Session = Depends(get_session)) -> dict:
    s = answer_pipeline.generation_summary(db)
    s["model"] = model_provider.chat_status()
    s["label_policy"] = answer_pipeline.ANSWER_DISCLAIMER
    s["human_confirmation_required"] = False
    return s


@router.post("/answers/generate-batch")
def generate_batch(body: dto.AnswerBatchIn,
                   db: Session = Depends(get_session)) -> dict:
    """Import-batch path: enqueue every question that has no usable answer."""
    qs = answer_pipeline.iter_answerless_questions(db)
    total = len(qs)
    if body.limit > 0:
        qs = qs[:body.limit]
    created, reused, jobs, processed = [], [], [], []
    for q in qs:
        info = answer_pipeline.enqueue(db, q, body.trigger, force=body.force)
        (reused if info.get("reused") else created).append(info["generation_id"])
        if body.process_now and model_provider.chat_status()["configured"]:
            r = answer_pipeline.generate(db, info["generation_id"])
            processed.append({"generation_id": info["generation_id"],
                              "status": r.get("status")})
        else:
            j = create_job(db, "answer_generation",
                           {"generation_id": info["generation_id"],
                            "trigger": body.trigger}, USER_ID,
                           idempotency_key=f"ansgen:{info['generation_id']}")
            if not j.get("reused"):
                jobs.append(j["job_id"])
    return {"answerless_questions": total, "enqueued": len(qs),
            "held_back_questions": answer_pipeline.count_held_back(db),
            "created": len(created), "reused": len(reused),
            "jobs_created": len(jobs), "processed_now": processed,
            "model_configured": model_provider.chat_status()["configured"],
            "human_confirmation_required": False,
            "note": ("模型未配置时任务如实标记 pending_model_configuration，"
                     "不生成答案、不写题库、无 mock；配置后检查通过即自动入库，"
                     "不要求逐题人工确认")}


@router.post("/answers/generate")
def generate_one(body: dto.AnswerGenerateIn,
                 db: Session = Depends(get_session)) -> dict:
    """On-demand path: used when a question is first selected into a paper."""
    gid = body.generation_id
    if not gid:
        if not body.question_id:
            raise HTTPException(400, "需要 question_id 或 generation_id")
        q = answer_pipeline.latest_question(db, body.question_id)
        if not q:
            raise HTTPException(404, "题目不存在")
        r = answer_pipeline.ensure_answer_ready(db, q, trigger=body.trigger)
        return {"ready": r.get("ready"), **r}
    r = answer_pipeline.generate(db, gid)
    return r


@router.get("/questions/{question_id}/answer-provenance")
def answer_provenance(question_id: str,
                      db: Session = Depends(get_session)) -> dict:
    q = answer_pipeline.latest_question(db, question_id)
    if not q:
        raise HTTPException(404, "题目不存在")
    meta = answer_pipeline.answer_meta_for(
        db, q.question_id, q.question_version)
    return {"question_id": q.question_id,
            "question_version": q.question_version,
            "answer_status": q.answer_status,
            "answer_origin": q.answer_origin,
            "label": answer_pipeline.answer_label(q.answer_origin),
            "answer_meta": meta,
            "human_confirmation_required": False,
            "disclaimer": answer_pipeline.ANSWER_DISCLAIMER}


# ------------------------------------------------------------------ disputes
@router.post("/questions/{question_id}/answer-dispute")
def open_dispute(question_id: str, body: dto.AnswerDisputeIn,
                 db: Session = Depends(get_session)) -> dict:
    q = answer_pipeline.latest_question(db, question_id)
    if not q:
        raise HTTPException(404, "题目不存在")
    gen = db.execute(
        select(E.AnswerGeneration).where(
            E.AnswerGeneration.question_id == question_id)
        .order_by(E.AnswerGeneration.answer_version.desc())
    ).scalars().first()
    d = E.AnswerDispute(
        dispute_id=E.new_id("dispute"), question_id=question_id,
        question_version=q.question_version, user_id=USER_ID,
        reason=body.reason.strip(), evidence=body.evidence.strip(),
        suggested_answer=body.suggested_answer, status="open",
        from_generation_id=gen.generation_id if gen else "",
    )
    db.add(d)
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=USER_ID,
        event_type="answer_disputed", target_id=question_id,
        payload={"dispute_id": d.dispute_id, "reason": d.reason,
                 "question_version": q.question_version}))
    db.commit()
    return {"dispute_id": d.dispute_id, "status": d.status,
            "question_id": question_id,
            "next_actions": ["调用 /api/answer-disputes/{id}/resolve 进行复核、"
                             "答案版本修正、相关作答重评与诊断更新"]}


@router.get("/answer-disputes")
def list_disputes(db: Session = Depends(get_session)) -> dict:
    rows = db.execute(select(E.AnswerDispute).order_by(
        E.AnswerDispute.created_at.desc())).scalars().all()
    return {"total": len(rows), "items": [{
        "dispute_id": d.dispute_id, "question_id": d.question_id,
        "question_version": d.question_version, "reason": d.reason,
        "evidence": d.evidence, "status": d.status,
        "resolution": d.resolution, "resolution_note": d.resolution_note,
        "new_question_version": d.new_question_version,
        "rescored_rows": d.rescored_rows,
        "diagnosis_jobs_queued": d.diagnoses_invalidated,
        "created_at": d.created_at, "resolved_at": d.resolved_at,
    } for d in rows]}


@router.post("/answer-disputes/{dispute_id}/resolve")
def resolve_dispute(dispute_id: str, body: dto.AnswerDisputeResolveIn,
                    db: Session = Depends(get_session)) -> dict:
    """Full closed loop: review -> answer version correction -> regrade all
    affected answers -> queue diagnosis/knowledge-state recomputation."""
    d = db.get(E.AnswerDispute, dispute_id)
    if not d:
        raise HTTPException(404, "疑问记录不存在")
    if d.status in ("resolved", "rejected"):
        return {"dispute_id": dispute_id, "status": d.status,
                "already_closed": True,
                "note": "该疑问已关闭，未重复修正版本或重评"}
    res = body.resolution
    if res not in ("corrected", "regenerated", "confirmed", "rejected"):
        raise HTTPException(400, "resolution 必须是 corrected/regenerated/confirmed/rejected")

    q = answer_pipeline.latest_question(db, d.question_id)
    if not q:
        raise HTTPException(404, "题目最新版本不存在")

    if res in ("confirmed", "rejected"):
        d.status = "resolved" if res == "confirmed" else "rejected"
        d.resolution = res
        d.resolution_note = body.resolution_note
        d.resolved_at = E.utcnow()
        db.commit()
        return {"dispute_id": dispute_id, "status": d.status,
                "resolution": res,
                "note": "复核后保留原答案，未改动题目版本与历史成绩"}

    if res == "corrected" and not body.answer_payload:
        raise HTTPException(400, "corrected 需要提供修正后的 answer_payload")

    # 1. New immutable question version (published papers keep their snapshot).
    patch: dict = {
        "answer_status": ("ai_auto_checked" if res == "regenerated" else "verified"),
        "review_status": ("queued_for_regeneration" if res == "regenerated"
                          else "verified"),
        "answer_origin": ("deepseek_generated" if res == "regenerated"
                          else "human_correction"),
        "notes": f"答案疑问 {dispute_id} 的复核修正：{body.resolution_note}",
    }
    if res == "corrected":
        patch.update(answer_payload=body.answer_payload,
                     rubric=body.rubric or q.rubric,
                     explanation=body.explanation or q.explanation)
    out = bank_service.revise_question(db, d.question_id, patch)
    new_ver = out["new_version"]

    # 2. A new answer generation record = new answer version.
    prev = db.execute(select(E.AnswerGeneration).where(
        E.AnswerGeneration.question_id == d.question_id)
        .order_by(E.AnswerGeneration.answer_version.desc())).scalars().first()
    gen = E.AnswerGeneration(
        generation_id=E.new_id("gen"), question_id=d.question_id,
        question_version=new_ver,
        answer_version=(prev.answer_version + 1) if prev else 1,
        trigger="dispute_regen", status=("queued" if res == "regenerated"
                                         else "succeeded"),
        source=("deepseek" if res == "regenerated" else "human_correction"),
        model_provider=("" if res == "corrected" else cfg.MODEL_PROVIDER),
        model_name=("human_correction" if res == "corrected"
                    else cfg.MODEL_CHAT_MODEL),
        prompt_version=("" if res == "corrected"
                        else prompts.ANSWER_PROMPT_VERSION),
        generated_at=E.utcnow() if res == "corrected" else "",
        student_answer_included=False,
        result=({} if res == "regenerated" else {
            "answer_payload": body.answer_payload,
            "rubric": body.rubric or q.rubric,
            "explanation": body.explanation or q.explanation}),
        checks={"resolution": res, "dispute_id": dispute_id,
                "note": body.resolution_note},
    )
    db.add(gen)
    d.to_generation_id = gen.generation_id
    d.new_question_version = new_ver

    if res == "regenerated":
        j = create_job(db, "answer_generation",
                       {"generation_id": gen.generation_id,
                        "trigger": "dispute_regen"}, USER_ID,
                       idempotency_key=f"ansgen:{gen.generation_id}")
        d.status = "investigating"
        db.commit()
        return {"dispute_id": dispute_id, "status": d.status,
                "new_question_version": new_ver,
                "generation_id": gen.generation_id, "job": j,
                "note": "已建新题目版本并重新入队生成；模型未配置时保持待配置，"
                        "生成成功后需再次调用本接口以重评与更新诊断"}

    # 3. Regrade all affected answers with the corrected answer + rubric,
    #    which also queues diagnosis recomputation.
    rg = attempt_service.regrade_question(
        db, d.question_id, body.answer_payload,
        f"答案疑问复核修正（{dispute_id}）", new_rubric=body.rubric)
    d.status = "resolved"
    d.resolution = res
    d.resolution_note = body.resolution_note
    d.resolved_at = E.utcnow()
    d.rescored_rows = rg["rescored_rows"]
    d.diagnoses_invalidated = rg["diagnosis_jobs_queued"]
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=USER_ID,
        event_type="answer_dispute_resolved", target_id=d.question_id,
        payload={"dispute_id": dispute_id, "resolution": res,
                 "new_question_version": new_ver,
                 "rescored_rows": rg["rescored_rows"],
                 "diagnosis_jobs_queued": rg["diagnosis_jobs_queued"]}))
    db.commit()
    return {"dispute_id": dispute_id, "status": d.status, "resolution": res,
            "new_question_version": new_ver,
            "answer_version": gen.answer_version,
            "generation_id": gen.generation_id,
            "rescored_rows": rg["rescored_rows"],
            "diagnosis_jobs_queued": rg["diagnosis_jobs_queued"],
            "note": "已生成答案新版本；相关作答已重评，诊断与知识状态已排队重算"}
