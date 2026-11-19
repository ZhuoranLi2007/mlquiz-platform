"""Background worker: atomically claims jobs, runs real graphs/skills and
records stage progress. Restart-safe: stale running jobs are requeued.

Run with:  .venv/Scripts/python.exe -m backend.app.workers.worker
"""
from __future__ import annotations

import json
import sys
import time
import traceback
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.app import config as cfg  # noqa: E402
from backend.app.models import entities as E  # noqa: E402
from backend.app.models.base import SessionLocal, init_db  # noqa: E402

MAX_ATTEMPTS = 3
STALE_SECONDS = 900
JOB_TYPES = {"diagnosis", "generate_questions", "retest", "study_plan",
             "tutor", "verify_learning", "answer_generation", "paper_ingest",
             "answer_booklet", "slides_questions", "external_paper_localize",
             "unified_import", "teacher_intervention", "teacher_compose", "teacher_diagnosis", "learning_retest"}


def create_job(db: Session, job_type: str, payload: dict,
               user_id: str = "local_user",
               idempotency_key: str | None = None) -> dict:
    if job_type not in JOB_TYPES:
        raise ValueError(f"未知任务类型: {job_type}")
    if idempotency_key:
        old = db.execute(select(E.Job).where(
            E.Job.job_type == job_type,
            E.Job.idempotency_key == idempotency_key)).scalars().first()
        if old:
            return {"job_id": old.job_id, "status": old.status, "reused": True,
                    "note": "幂等键已存在，未重复创建任务"}
    job = E.Job(
        job_id=E.new_id("job"), job_type=job_type, user_id=user_id,
        status="queued", stage="queued", progress={}, payload=payload,
        idempotency_key=idempotency_key,
    )
    db.add(job)
    db.commit()
    return {"job_id": job.job_id, "status": "queued", "reused": False}


def recover_stale(db: Session) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_SECONDS)) \
        .isoformat(timespec="seconds")
    rows = db.execute(select(E.Job).where(
        E.Job.status == "running")).scalars().all()
    n = 0
    for j in rows:
        if max(j.claimed_at or "", j.updated_at or "") < cutoff:
            # Publishing work must not be replayed blindly after a crash.
            # A saved release is the authoritative commit marker.
            release = db.execute(select(E.AssessmentRelease).where(
                E.AssessmentRelease.workflow_run_id == f"teacher-job:{j.job_id}"
            )).scalars().first() if j.job_type == "teacher_intervention" else None
            if release:
                j.status, j.stage = "succeeded", "recovered_published_release"
                j.result = {"status": "succeeded", "outputs": {"retest": {
                    "status": "published", "release": {"release_id": release.release_id}}}}
            elif j.job_type in {"teacher_intervention", "teacher_compose", "teacher_diagnosis", "learning_retest"} or j.attempts >= MAX_ATTEMPTS:
                j.status, j.stage = "failed", "interrupted"
                j.error = ("诊断任务中断；请先查看已保存结果，再决定是否重新运行。未自动重复调用模型。"
                           if j.job_type == "teacher_diagnosis" else
                           "任务中断；请检查已发布任务后重试。未自动重复出题或发布。")
            else:
                j.status = "queued"
                j.stage = "requeued_after_restart"
            j.progress = {"note": ("已重新排队" if j.status == "queued" else
                                    "已恢复发布结果" if j.status == "succeeded" else "中断任务等待检查后重试"),
                          "previous_attempts": j.attempts}
            n += 1
    if n:
        db.commit()
    return n


def claim_job(db: Session) -> E.Job | None:
    job = db.execute(select(E.Job).where(E.Job.status == "queued")
                     .order_by(E.Job.created_at)).scalars().first()
    if not job:
        return None
    res = db.execute(update(E.Job).where(
        E.Job.job_id == job.job_id, E.Job.status == "queued").values(
        status="running", claimed_at=E.utcnow(), stage="claimed",
        attempts=E.Job.attempts + 1, updated_at=E.utcnow()))
    db.commit()
    if res.rowcount != 1:
        return None
    db.expire(job)
    return job


def _set_stage(db: Session, job: E.Job, stage: str, detail: dict | None = None) -> None:
    job.stage = stage
    job.progress = {"stage": stage, "updated_at": E.utcnow(),
                    "detail": detail or {}}
    job.updated_at = E.utcnow()
    db.commit()


def process_job(db: Session, job: E.Job) -> None:
    from backend.app.graphs import workflows
    from backend.app.services.skill_runtime import run_skill

    p = job.payload or {}
    _set_stage(db, job, "running")
    try:
        if job.job_type in {"teacher_intervention", "learning_retest"}:
            from backend.app.graphs import classroom_workflow
            _set_stage(db, job, "diagnose_generate_verify_publish")
            result = classroom_workflow.run_teacher_intervention(
                db, actor_user_id=job.user_id, thread_id=f"teacher-job:{job.job_id}", **p)
            ok = result.get("status") == "succeeded"
            if job.job_type == 'learning_retest' and (result.get('outputs') or {}).get('retest',{}).get('status') == 'published':
                ok = True  # partial class success remains reviewable, with per-student errors
        elif job.job_type == "teacher_diagnosis":
            _set_stage(db, job, "analyze_answers_and_match_resources")
            out = run_skill(db, "diagnose_classroom", p,
                            thread_id=f"teacher-job:{job.job_id}", job_id=job.job_id,
                            user_id=job.user_id)
            diagnosis_id = (out.get("extra") or {}).get("diagnosis_id")
            diagnosis = db.get(E.Diagnosis, diagnosis_id) if diagnosis_id else None
            if diagnosis is None:
                raise ValueError("诊断未保存，任务不能标为完成")
            kp_ids = [r["kp_id"] for r in (out.get("result") or {}).get("ai_analysis", {}).get("learning_plan", []) if r.get("kp_id")]
            if kp_ids:
                rec = run_skill(db, "recommend_resources", {"kp_ids":kp_ids},
                                thread_id=f"teacher-job:{job.job_id}", job_id=job.job_id,
                                user_id=job.user_id)
                if rec.get("status") != "succeeded":
                    raise ValueError("学习资源匹配未完成")
            result = {"diagnosis_id":diagnosis.diagnosis_id, "run_id":out["run_id"],
                      "created_at":diagnosis.created_at, "engine":diagnosis.engine,
                      "result":diagnosis.payload, "skill_version":diagnosis.skill_version}
            ok = True
        elif job.job_type == "teacher_compose":
            from backend.app.services import paper_builder_service
            _set_stage(db, job, "compose_paper")
            result = paper_builder_service.compose_draft(
                db, teacher_user_id=job.user_id, **p)
            ok = True
        elif job.job_type == "diagnosis":
            _set_stage(db, job, "run_diagnosis_graph",
                       {"attempt_id": p.get("attempt_id")})
            result = workflows.run_diagnosis(
                db, p["attempt_id"], job.user_id, job.job_id)
            ok = result.get("status") != "failed"
        elif job.job_type == "generate_questions":
            _set_stage(db, job, "run_generation_graph")
            result = workflows.run_generation(
                db, p.get("blueprint", {}), job.user_id, job.job_id)
            ok = result.get("status") in ("succeeded", "model_not_configured")
        elif job.job_type == "retest":
            _set_stage(db, job, "run_retest_graph")
            result = workflows.run_retest(
                db, p.get("retest_task_id", ""), job.user_id, job.job_id)
            ok = result.get("status") != "failed"
        elif job.job_type == "study_plan":
            r = run_skill(db, "build_study_plan", p,
                          thread_id=f"job:{job.job_id}", job_id=job.job_id,
                          user_id=job.user_id)
            result, ok = r, r.get("status") == "succeeded"
        elif job.job_type == "verify_learning":
            r = run_skill(db, "verify_learning", p,
                          thread_id=f"job:{job.job_id}", job_id=job.job_id,
                          user_id=job.user_id)
            result, ok = r, r.get("status") == "succeeded"
        elif job.job_type == "answer_generation":
            _set_stage(db, job, "solve_answer",
                       {"generation_id": p.get("generation_id")})
            r = run_skill(db, "solve_answer", p,
                          thread_id=f"job:{job.job_id}", job_id=job.job_id,
                          user_id=job.user_id)
            result = r
            ok = r.get("status") in ("succeeded", "model_not_configured",
                                     "needs_attention", "retrying", "skipped")
        elif job.job_type == "paper_ingest":
            # Upload -> original saved -> questions -> AI answers -> booklet ->
            # PDF. The service writes its own stage/progress to MaterialUpload,
            # so the UI keeps a truthful status even across a worker restart.
            from backend.app.services import material_service
            _set_stage(db, job, "ingest_upload",
                       {"upload_id": p.get("upload_id"),
                        "retry_only": bool(p.get("retry_only"))})
            result = material_service.process_upload(
                db, p["upload_id"],
                auto_generate=bool(p.get("auto_generate", True)))
            # "parsed" means every question is waiting for a model: honest
            # completion of the pipeline, not a failure to hide.
            ok = result.get("status") in ("completed", "partial", "parsed")
        elif job.job_type == "answer_booklet":
            # Answers requested AFTER parsing (V2 §5.4 rule 9): same code path,
            # same truthfulness, just entered from the user's click.
            from backend.app.services import material_service
            _set_stage(db, job, "answer_upload",
                       {"upload_id": p.get("upload_id"),
                        "regenerate": bool(p.get("regenerate"))})
            result = material_service.run_answer_job(
                db, p["upload_id"], regenerate=bool(p.get("regenerate")))
            result = {**result, "kind": "answer_booklet"}
            ok = result.get("status") in ("completed", "partial", "parsed")
        elif job.job_type == "slides_questions":
            # V2.2：老师课件导入后由后台静默识别课件原题 + 按考点补题并入库。
            # 页面上不提示、不需要用户点击；失败不伪装成成功，交给重试机制。
            from backend.app.services import slides_question_service
            _set_stage(db, job, "slides_questions",
                       {"upload_id": p.get("upload_id")})
            result = slides_question_service.generate_for_upload(
                db, p["upload_id"])
            result = {**result, "kind": "slides_questions"}
            ok = result.get("status") in ("succeeded", "skipped", "deferred")
        elif job.job_type == "unified_import":
            # V3.5：初始化批量导入与日常手动上传共用同一条流水线。
            # 两个入口都只负责建 ImportRun + 排这个任务，真正做什么由
            # services/unified_import.py 的阶段机决定。
            from backend.app.services import unified_import
            _set_stage(db, job, "unified_import",
                       {"run_id": p.get("run_id")})
            result = unified_import.run(
                db, p["run_id"], force=bool(p.get("force")))
            result = {**result, "kind": "unified_import"}
            ok = result.get("status") in ("completed", "partial",
                                          "needs_review")
        elif job.job_type == "external_paper_localize":
            # V2.3：扩展试卷「提取 → 翻译 → 中文 PDF」在后台一次做完并缓存。
            from backend.app.services import paper_localization
            _set_stage(db, job, "external_paper_localize",
                       {"epaper_id": p.get("epaper_id")})
            paper = db.get(E.ExternalPaper, p.get("epaper_id", ""))
            if paper is None:
                result, ok = {"status": "failed", "message": "试卷不存在"}, False
            else:
                try:
                    result = paper_localization.localize(
                        db, paper, force=bool(p.get("force")))
                    result = {**result, "kind": "external_paper_localize"}
                    ok = bool(result.get("ok"))
                except paper_localization.LocalizeError as e:
                    paper_localization.mark_failed(db, paper, str(e))
                    result, ok = {"status": "failed", "message": str(e)}, False
                except Exception as e:                       # noqa: BLE001
                    paper_localization.mark_failed(
                        db, paper, f"{type(e).__name__}: {e}")
                    result, ok = ({"status": "failed",
                                   "message": f"{type(e).__name__}: {e}"}, False)
        else:  # tutor
            r = run_skill(db, "tutor_answer", p,
                          thread_id=f"job:{job.job_id}", job_id=job.job_id,
                          user_id=job.user_id)
            result, ok = r, r.get("status") == "succeeded"
        # A graph that ran honestly but could not call a missing model must NOT
        # be reported as a plain success: record the pending-configuration state.
        # For an uploaded paper the same is true when every question was
        # recognised but zero answers could be produced (`parsed`) — UNLESS the
        # reason is that the user simply hasn't clicked 生成 yet (V2 §5.4 rule 9),
        # which is a waiting state, not a missing configuration.
        awaiting = bool(result.get("awaiting_answer_request"))
        pending_config = (result.get("status") == "model_not_configured" or
                          (job.job_type in ("paper_ingest", "answer_booklet") and
                           result.get("status") == "parsed" and not awaiting))
        if pending_config:
            ok = False
        job.result = result
        job.status = "succeeded" if ok else "failed"
        job.stage = ("awaiting_answer_request" if awaiting else
                     "pending_model_configuration" if pending_config else
                     "succeeded" if ok else "failed_with_error")
        if awaiting:
            job.progress = {"stage": "awaiting_answer_request",
                            "updated_at": E.utcnow(),
                            "detail": {"upload_id": p.get("upload_id"),
                                       "question_count": result.get("question_count"),
                                       "note": "解析与题目识别已完成；按规则不自动调用模型，"
                                               "答案解析等待用户点击「AI 生成答案解析 PDF」"}}
        elif pending_config:
            job.progress = {"stage": "pending_model_configuration",
                            "updated_at": E.utcnow(),
                            "detail": {"generated": 0,
                                       "upload_id": p.get("upload_id"),
                                       "question_count": result.get("question_count"),
                                       "note": "流程已真实执行；模型未配置，未生成结果，"
                                               "未使用模拟结果冒充真实调用"}}
        if not ok:
            job.error = json.dumps(result.get("errors") or result.get("error") or
                                   result.get("status"), ensure_ascii=False)[:800]
    except Exception as e:
        # A failed flush leaves the session in "pending rollback": touching any
        # ORM attribute afterwards raises PendingRollbackError *inside* the
        # handler, which used to kill the whole worker daemon (one bad question
        # id took the queue down with it). Roll back first, then re-read the job.
        from backend.app.services.prompt_history import redact
        err = redact(f"{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")
        db.rollback()
        job = db.get(E.Job, job.job_id) or job
        job.status = "failed"
        job.stage = "exception"
        job.error = err
        job.result = {}
    job.updated_at = E.utcnow()
    if (job.status == "failed" and job.attempts < MAX_ATTEMPTS
            and job.stage != "pending_model_configuration"
            and job.job_type not in {"teacher_intervention", "teacher_compose", "teacher_diagnosis"}):
        job.status = "queued"
        job.stage = "retry_queued"
    db.commit()


def _heartbeat(job_id, stopped):
    """Keep long model calls distinguishable from a dead worker."""
    while not stopped.wait(30):
        try:
            with SessionLocal() as heartbeat_db:
                heartbeat_db.execute(update(E.Job).where(
                    E.Job.job_id == job_id, E.Job.status == "running").values(updated_at=E.utcnow()))
                heartbeat_db.commit()
        except Exception:
            # A short SQLite write lock is harmless; the next heartbeat retries.
            pass


def run_forever(interval: float = 2.0, max_jobs: int | None = None) -> None:
    init_db()
    db = SessionLocal()
    n = recover_stale(db)
    print(f"[worker] 启动；重启恢复重新排队 {n} 个任务；job_types={sorted(JOB_TYPES)}",
          flush=True)
    done = 0
    try:
        while True:
            if max_jobs is not None and done >= max_jobs:
                break
            job = claim_job(db)
            if job is None:
                # Bounded mode means "drain the current queue then exit",
                # which is what start.ps1 / CI verification needs.
                if max_jobs is not None:
                    print("[worker] 队列已清空，按 --max-jobs 模式退出", flush=True)
                    break
                recover_stale(db)
                time.sleep(interval)
                continue
            print(f"[worker] 领取 {job.job_id} ({job.job_type})", flush=True)
            # One job must never take the daemon down: a worker restart also
            # un-claims the job, so crashing here silently stalls the queue.
            stopped = threading.Event()
            heartbeat = threading.Thread(target=_heartbeat, args=(job.job_id, stopped), daemon=True)
            heartbeat.start()
            try:
                process_job(db, job)
            except Exception as e:                    # noqa: BLE001
                db.rollback()
                print(f"[worker] 任务 {job.job_id} 异常，进程继续："
                      f"{type(e).__name__}: {e}", flush=True)
                try:
                    j = db.get(E.Job, job.job_id)
                    if j is not None:
                        j.status = "failed"
                        j.stage = "worker_exception"
                        j.error = f"{type(e).__name__}: {e}"[:600]
                        db.commit()
                except Exception:                     # noqa: BLE001
                    db.rollback()
            finally:
                stopped.set()
                heartbeat.join(timeout=1)
            db.expire_all()
            print(f"[worker] 完成 {job.job_id} -> {job.status}/{job.stage}", flush=True)
            done += 1
    except KeyboardInterrupt:
        print("[worker] 已停止", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="学情诊断后台任务 worker")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="无任务时的轮询间隔（秒）")
    ap.add_argument("--max-jobs", type=int, default=None,
                    help="最多处理多少个任务后退出；不传则常驻")
    args = ap.parse_args()
    run_forever(interval=args.interval, max_jobs=args.max_jobs)
