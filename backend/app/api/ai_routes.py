"""Stage-2 API: LangGraph orchestration, skills, retest, plan, assistant."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.graphs import workflows
from backend.app.models import entities as E
from backend.app.models.base import get_session
from backend.app.schemas import dto
from backend.app.services import (bank_service, home_service, model_provider,
                                  retest_service)
from backend.app.services.skill_runtime import run_skill
from backend.app.workers.worker import create_job

router = APIRouter(prefix="/api")
USER_ID = "local_user"


# ---------------------------------------------------------------- graphs / skills
@router.get("/graphs")
def graphs() -> dict:
    try:
        return workflows.graph_status()
    except Exception as e:
        return {"langgraph_available": False,
                "error": f"{type(e).__name__}: {e}",
                "note": "LangGraph 图编译失败，未宣称已接入"}


@router.post("/papers/generate")
def generate_papers(body: dict, db: Session = Depends(get_session)) -> dict:
    blueprint = body.get("blueprint", body)
    r = workflows.run_generation(db, blueprint, USER_ID)
    result = r.get("result") or {}
    return {
        "status": r.get("status"),
        "message": result.get("message", ""),
        "candidates": result.get("candidates", []),
        "pipeline": result.get("pipeline", []),
        "rejected": result.get("rejected", []),
        "run_id": r.get("run_id"), "thread_id": r.get("thread_id"),
        "engine": r.get("engine"), "errors": r.get("errors", []),
        "note": ("模型未配置时本接口不生成任何题目，也不返回模拟结果。"
                 "可使用 /api/papers/assemble 从已核验题库组卷。"),
    }


@router.post("/diagnoses/run")
def run_diagnoses(body: dict | None = None,
                  db: Session = Depends(get_session)) -> dict:
    payload = body or {}
    limit = int(payload.get("limit", 3))
    attempt_id = payload.get("attempt_id")
    if attempt_id:
        attempts = [db.get(E.Attempt, attempt_id)]
        attempts = [a for a in attempts if a]
    else:
        attempts = db.execute(select(E.Attempt).where(
            E.Attempt.user_id == USER_ID,
            E.Attempt.status == "submitted").order_by(
            desc(E.Attempt.started_at)).limit(limit)).scalars().all()
    if not attempts:
        return {"status": "no_submitted_attempt", "ran": 0,
                "message": "尚无已交卷测验；先完成一次答题并交卷"}
    runs = []
    for a in attempts:
        runs.append(workflows.run_diagnosis(db, a.attempt_id, USER_ID))
    return {
        "status": "succeeded" if all(r["status"] != "failed" for r in runs)
                  else "partial_failure",
        "ran": len(runs), "runs": [
            {"thread_id": r["thread_id"], "status": r["status"],
             "stages": [s["node"] for s in r["stages"]],
             "errors": r["errors"],
             "checkpoint_available": r["checkpoint_available"]} for r in runs],
        "message": "三层诊断、推荐与学习计划已由 LangGraph 节点真实执行；"
                   "客观成绩在交卷时已保存，不依赖本次执行结果",
    }


# ---------------------------------------------------------------- study plan
@router.post("/study-plan/generate")
def generate_plan(body: dict, db: Session = Depends(get_session)) -> dict:
    # 个人设置里的学习偏好优先于全局默认值：用户改过「每日学习分钟」后，
    # 重新生成的计划必须按新值分配时间（V2 §11.9-6）。
    prefs = home_service.get_settings(db)
    r = run_skill(db, "build_study_plan", {
        "exam_date": body.get("exam_date") or prefs.get("exam_date") or cfg.EXAM_DATE,
        "daily_minutes": int(body.get("daily_minutes")
                             or prefs.get("daily_minutes")
                             or cfg.DEFAULT_DAILY_MINUTES),
    }, user_id=USER_ID)
    out = r.get("result") or {}
    return {"status": r["status"], "message": out.get("message", ""),
            "plan_id": out.get("plan_id"), "total_minutes": out.get("total_minutes"),
            "tasks": out.get("tasks", []), "run_id": r["run_id"],
            "uncertainties": r["uncertainties"]}


@router.patch("/plan-tasks/{task_id}")
def patch_plan_task(task_id: str, body: dto.PlanTaskPatchIn,
                    db: Session = Depends(get_session)) -> dict:
    t = db.get(E.PlanTask, task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    if body.status is not None:
        t.status = body.status
        t.completed_at = E.utcnow() if body.status == "completed" else ""
    if body.minutes is not None:
        t.minutes = max(1, int(body.minutes))
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=USER_ID,
        event_type="plan_task_updated", target_id=task_id,
        payload=body.model_dump(exclude_none=True)))
    db.commit()
    return {"task_id": task_id, "status": t.status, "minutes": t.minutes}


# ---------------------------------------------------------------- retest
@router.post("/retest-tasks/generate")
def generate_retest_tasks(db: Session = Depends(get_session)) -> dict:
    return retest_service.generate_retest_tasks(db, USER_ID)


@router.post("/retest-tasks/{task_id}/start")
def start_retest(task_id: str, db: Session = Depends(get_session)) -> dict:
    try:
        return retest_service.start_retest(db, task_id, USER_ID)
    except KeyError:
        raise HTTPException(404, "复测任务不存在")


@router.post("/retest-tasks/{task_id}/verify")
def verify_retest(task_id: str, body: dict | None = None,
                  db: Session = Depends(get_session)) -> dict:
    r = run_skill(db, "verify_learning",
                  {"retest_task_id": task_id,
                   "attempt_id": (body or {}).get("attempt_id", "")},
                  user_id=USER_ID)
    return {"status": r["status"], "run_id": r["run_id"],
            "result": r["result"], "uncertainties": r["uncertainties"],
            "next_actions": r["next_actions"]}


@router.post("/retest/run-graph")
def run_retest_graph(body: dict | None = None,
                     db: Session = Depends(get_session)) -> dict:
    return workflows.run_retest(db, (body or {}).get("retest_task_id", ""),
                                USER_ID)


# ---------------------------------------------------------------- assistant
@router.post("/assistant/chat")
def assistant_chat(body: dto.ChatIn, db: Session = Depends(get_session)) -> dict:
    payload = body.model_dump()
    context = dict(payload.get("context") or {})
    attempt_id = str(context.get("attempt_id") or "").strip()
    if attempt_id:
        attempt = db.get(E.Attempt, attempt_id)
        if not attempt or attempt.user_id != USER_ID:
            raise HTTPException(404, "作答记录不存在")
        # Security boundary: the client may render a stale/tampered mode.  The
        # persisted attempt is authoritative, so an active quiz cannot obtain
        # hints by posting mode="practice".
        payload["mode"] = attempt.mode
        context["attempt_status"] = attempt.status
        payload["context"] = context
    r = run_skill(db, "tutor_answer", payload,
                  thread_id=f"chat:{body.session_id}", user_id=USER_ID)
    out = r.get("result") or {}
    return {
        "answer": out.get("answer", ""),
        "source_refs": out.get("source_refs", []),
        "retrieval_mode": out.get("retrieval_mode", "keyword"),
        "model_used": out.get("model_used", False),
        "engine": r["engine"], "status": r["status"],
        "run_id": r["run_id"], "note": out.get("note", ""),
        "blocked": out.get("blocked", False),
        "uncertainties": r["uncertainties"],
    }


@router.get("/attempts/{attempt_id}/solution/{question_id}")
def solution(attempt_id: str, question_id: str,
             db: Session = Depends(get_session)) -> dict:
    """Learning mode only. Records solution_viewed so the attempt cannot be
    counted as independent evidence. Quiz mode is hard-disabled."""
    a = db.get(E.Attempt, attempt_id)
    if not a:
        raise HTTPException(404, "作答记录不存在")
    if a.status == "submitted":
        # Score page (attempt_state with reveal=True) is the only post-submit
        # channel; this learning-mode endpoint must not become a second one.
        raise HTTPException(409, "已交卷，请在成绩页查看解析")
    if a.mode == "quiz":
        raise HTTPException(403, "测验模式提交前禁用解析；请交卷后查看")
    item = next((it for it in bank_service.get_paper_items(db, a.paper_id)
                 if it.question_id == question_id), None)
    if not item:
        raise HTTPException(404, "题目不在本卷中")
    ans = db.execute(select(E.Answer).where(
        E.Answer.attempt_id == attempt_id,
        E.Answer.question_id == question_id)).scalars().first()
    if ans:
        ans.solution_viewed = True
        ans.is_independent_evidence = False
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=USER_ID,
        event_type="solution_viewed", target_id=attempt_id,
        payload={"question_id": question_id,
                 "note": "查看解析后该次作答不再是独立证据"}))
    db.commit()
    snap = item.snapshot
    return {
        "question_id": question_id,
        "explanation": snap.get("explanation", ""),
        "rubric": snap.get("rubric", {}),
        "answer_payload": snap.get("answer_payload", {}),
        "answer_status": snap.get("answer_status", "unknown"),
        "note": ("学习模式解析；答案来源见 answer_meta（AI参考答案 / 原资料 / "
                 "人工复核），AI 参考答案不是教师或原试卷答案。"
                 "本次作答已标记为非独立证据"),
        "answer_label": (snap.get("answer_meta") or {}).get("label")
                        or "暂无参考答案标注",
    }


# ---------------------------------------------------------------- jobs / model config
@router.post("/jobs")
def post_job(body: dto.JobIn, db: Session = Depends(get_session)) -> dict:
    try:
        return create_job(db, body.job_type, body.payload, USER_ID,
                          body.idempotency_key)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/model/connection-test")
def connection_test() -> dict:
    st = model_provider.chat_status()
    if not st["configured"]:
        return {"status": "not_configured", "model": st.get("model"),
                "message": st["message"],
                "embedding": model_provider.embedding_status(),
                "note": "未发起任何网络请求；不把 mock 通过写成真实调用成功"}
    from backend.app.models.base import SessionLocal
    s = SessionLocal()
    import time as _time
    t0 = _time.time()
    try:
        r = model_provider.chat(s, messages=[
            {"role": "user", "content": "只回复 ok 两个字符"}],
            skill_name="connection_test", prompt_version="prompt-1.0",
            max_tokens=16, timeout=20)
        # `model` = provider 实际返回的模型名；`configured_model` = .env 里写的别名。
        # 两者可能不同（如别名指向具体版本），都回传便于核对。
        return {"status": "ok",
                "model": r.get("model", ""),
                "configured_model": cfg.MODEL_CHAT_MODEL,
                "provider": cfg.MODEL_PROVIDER,
                "base_url": cfg.MODEL_BASE_URL,
                "content_preview": r["content"][:80],
                "finish_reason": r.get("finish_reason", ""),
                "duration_ms": r.get("duration_ms", 0),
                "usage": r["usage"], "usage_missing": r["usage_missing"],
                "call_id": r["call_id"]}
    except model_provider.ModelUnavailable as e:
        return {"status": e.status, "message": e.message,
                "model": cfg.MODEL_CHAT_MODEL,
                "provider": cfg.MODEL_PROVIDER,
                "base_url": cfg.MODEL_BASE_URL,
                "duration_ms": int((_time.time() - t0) * 1000),
                "call_id": getattr(e, "call_id", "")}
    finally:
        s.close()
