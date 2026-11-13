"""V3.5 统一资料导入流水线的 API。

只暴露"看状态"和"触发处理"两件事；具体阶段全在 services/unified_import.py。
两个入口（初始化批量 / 手动上传）都通过这里或 worker 触发同一套流程。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.models.base import get_session
from backend.app.services import unified_import

router = APIRouter(prefix="/api")
USER_ID = "local_user"

VALID_KINDS = ("external_paper", "material_upload")


@router.get("/import/stages")
def import_stages() -> dict:
    return {"stages": unified_import.STAGE_ORDER,
            "labels": unified_import.STAGE_LABELS,
            "note": "初始化批量导入与手动上传共用这一套阶段"}


@router.get("/import/runs")
def list_runs(limit: int = Query(50, ge=1, le=200),
              db: Session = Depends(get_session)) -> dict:
    return unified_import.list_runs(db, limit=limit)


@router.get("/import/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_session)) -> dict:
    row = db.get(E.ImportRun, run_id)
    if row is None:
        raise HTTPException(404, "导入记录不存在")
    return {"run_id": row.run_id, "source_kind": row.source_kind,
            "source_id": row.source_id, "status": row.status,
            "stage": row.stage,
            "stage_label": unified_import.STAGE_LABELS.get(row.stage, row.stage),
            "language": row.language, "steps": row.steps or [],
            "result": row.result or {}, "error": row.error,
            "attempts": row.attempts, "job_id": row.job_id,
            "created_at": row.created_at, "updated_at": row.updated_at}


@router.get("/import/status")
def import_status(source_kind: str, source_id: str,
                  db: Session = Depends(get_session)) -> dict:
    if source_kind not in VALID_KINDS:
        raise HTTPException(400, f"source_kind 必须是 {VALID_KINDS}")
    return unified_import.status_for(db, source_kind=source_kind,
                                     source_id=source_id)


@router.post("/import/start")
def start_import(body: dict, db: Session = Depends(get_session)) -> dict:
    kind = str((body or {}).get("source_kind") or "")
    sid = str((body or {}).get("source_id") or "")
    if kind not in VALID_KINDS:
        raise HTTPException(400, f"source_kind 必须是 {VALID_KINDS}")
    if not sid:
        raise HTTPException(400, "缺少 source_id")
    if kind == "external_paper" and db.get(E.ExternalPaper, sid) is None:
        raise HTTPException(404, "外部试卷不存在")
    if kind == "material_upload" and db.get(E.MaterialUpload, sid) is None:
        raise HTTPException(404, "上传资料不存在")
    return unified_import.enqueue(db, source_kind=kind, source_id=sid,
                                  user_id=USER_ID,
                                  force=bool((body or {}).get("force")))


@router.get("/external/papers/{epaper_id}/zh-thumbnail")
def zh_thumbnail(epaper_id: str, db: Session = Depends(get_session)):
    """中文版 PDF 的首页预览图。

    ExternalPaper 表没有独立的"中文版缩略图"列（不新增列以避免迁移风险），
    因此按本次统一流水线的执行记录反查：只认 zh_status 已完成的记录，
    且授权边界沿用 `preview_allowed`。
    """
    paper = db.get(E.ExternalPaper, epaper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    if not paper.preview_allowed:
        raise HTTPException(403, "该资源未获得公开预览授权")
    row = db.execute(select(E.ImportRun).where(
        E.ImportRun.source_kind == "external_paper",
        E.ImportRun.source_id == epaper_id).order_by(
        E.ImportRun.created_at.desc())).scalars().first()
    rel = ((row.result or {}).get("zh_thumbnail") if row else "") or ""
    if not rel:
        raise HTTPException(404, "尚未生成中文版首页预览")
    path = cfg.resolve_storage_ref(rel)
    if not path.exists():
        raise HTTPException(404, "中文版首页预览文件不存在")
    return FileResponse(str(path), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=600"})
