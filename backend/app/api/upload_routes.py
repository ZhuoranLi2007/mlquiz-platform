"""资料库「添加资料」上传链路 + 原卷旁答案 + 原卷在线答题。

Everything an uploaded paper needs is reachable from ONE material item:

    POST /api/materials/upload              -> 保存原卷 + 进入后台处理（不等首次出卷）
    GET  /api/uploads                       -> 列表（真实状态，刷新不丢）
    GET  /api/uploads/{id}                  -> 详情（状态、阶段、题量、四入口可用性）
    GET  /api/uploads/{id}/original         -> 查看原卷（不算已见答案）
    GET  /api/uploads/{id}/questions        -> 原卷题目结构（题号/分值/分区，不含答案）
    GET  /api/uploads/{id}/booklet          -> 查看 AI 答案（此处才记录已见）
    GET  /api/uploads/{id}/booklet.pdf      -> 下载答案 PDF（此处才记录已见）
    POST /api/uploads/{id}/generate-answers -> 点击「AI 生成答案解析 PDF」（默认不自动生成）
    POST /api/uploads/{id}/retry            -> 只重试未完成的题目
    POST /api/uploads/{id}/start-quiz       -> 用原卷题目/顺序/分值直接在线答题

Two rules the endpoints must never break:
  * the web answers, the downloaded PDF and online scoring all read the SAME
    saved booklet, so no endpoint regenerates content on its own;
  * generating an answer is not the same as the user having seen it, so only
    `/booklet` and `/booklet.pdf` write `AnswerReveal`.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.api.routes import USER_ID
from backend.app.models import entities as E
from backend.app.models.base import get_session
from backend.app.services import (answer_pipeline, attempt_service,
                                  material_service)

router = APIRouter(prefix="/api")


def _get_upload(db: Session, upload_id: str) -> E.MaterialUpload:
    up = db.get(E.MaterialUpload, upload_id)
    if not up:
        raise HTTPException(404, f"资料不存在: {upload_id}")
    return up


def _booklet(db: Session, upload_id: str) -> E.AnswerBooklet | None:
    return db.execute(select(E.AnswerBooklet).where(
        E.AnswerBooklet.upload_id == upload_id)
        .order_by(E.AnswerBooklet.version.desc())).scalars().first()


# --------------------------------------------------------------------- upload
@router.post("/materials/upload")
async def upload_material(
    file: UploadFile = File(...),
    material_type: str = Form("auto"),
    auto_generate: str = Form(""),
    db: Session = Depends(get_session),
) -> dict:
    """接收文件字节并立刻落库排队；不在请求里等答案生成。

    `auto_generate` is the explicit opt-in from V2 §5.4 rule 9. Empty means
    "use my saved preference", which defaults to OFF: an upload alone must not
    trigger a paid model call.
    """
    content = await file.read()
    flag: bool | None = None
    if auto_generate in ("1", "true", "yes", "on"):
        flag = True
    elif auto_generate in ("0", "false", "no", "off"):
        flag = False
    try:
        out = material_service.save_upload(
            db, filename=file.filename or "upload",
            content=content, declared_type=material_type,
            auto_generate=flag, user_id=USER_ID)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(500, f"写入失败：{e}")
    out["material_types"] = [
        {"value": k, "label": v} for k, v in
        material_service.TYPE_LABELS.items()]
    return out


@router.get("/uploads")
def list_uploads(limit: int = Query(100, ge=1, le=500),
                 db: Session = Depends(get_session)) -> dict:
    out = material_service.list_uploads(db, limit=limit)
    out["material_types"] = [
        {"value": k, "label": v} for k, v in
        material_service.TYPE_LABELS.items()]
    out["accept"] = sorted(material_service.ALLOWED_EXT)
    # Category tab counts are derived from the rows just returned, so the tab
    # badges can never disagree with the table underneath them.
    from collections import Counter
    counts = Counter(x["material_type"] for x in out["items"])
    has_booklet = sum(1 for x in out["items"] if x["booklet_version"] > 0)
    out["category_counts"] = {
        "all": len(out["items"]),
        "exam_paper": counts.get("exam_paper", 0),
        "slides": counts.get("slides", 0),
        "notes": counts.get("notes", 0),
        "code": counts.get("code", 0),
        "other": counts.get("other", 0),
        "ai_answer": has_booklet,
    }
    return out


@router.get("/uploads/{upload_id}")
def upload_detail(upload_id: str, db: Session = Depends(get_session)) -> dict:
    up = _get_upload(db, upload_id)
    out = material_service.upload_public(db, up)
    out["job"] = _job_view(db, up.job_id)
    return out


def _job_view(db: Session, job_id: str) -> dict:
    if not job_id:
        return {}
    j = db.get(E.Job, job_id)
    if not j:
        return {}
    return {"job_id": j.job_id, "status": j.status, "stage": j.stage,
            "attempts": j.attempts, "error": j.error,
            "progress": j.progress}


# --------------------------------------------------------------- original file
@router.get("/uploads/{upload_id}/original")
def upload_original(upload_id: str, download: int = 0,
                    db: Session = Depends(get_session)):
    """Serve the untouched original. Viewing the paper is NOT seeing answers."""
    up = _get_upload(db, upload_id)
    path = cfg.resolve_storage_ref(up.stored_rel_path)
    if not path.exists():
        path = cfg.UPLOAD_DIR / up.sha256[:14] / up.filename
    if not path.exists():
        raise HTTPException(404, "原卷文件不存在")
    if not download:
        db.add(E.LearningEvent(
            event_id=E.new_id("evt"), user_id=up.user_id,
            event_type="material_original_viewed", target_id=up.upload_id,
            payload={"filename": up.filename,
                     "note": "查看原卷不计为已见答案"}))
        db.commit()
    # 预览尽量内联；显式下载一律 attachment
    inline = (not download) and up.mime in (
        "application/pdf", "text/plain", "text/markdown")
    name = Path(up.filename).name
    # HTTP headers are latin-1: a Chinese filename must go through RFC 5987
    # (filename*) with an ASCII fallback, never into the raw header.
    ascii_name = (name.encode("ascii", "ignore").decode().strip(" ._")
                  or f"original{Path(name).suffix}")
    headers = {"Content-Disposition":
               f'{"inline" if inline else "attachment"}; '
               f'filename="{ascii_name}"; '
               f"filename*=UTF-8''{quote(name)}"}
    return FileResponse(str(path), media_type=up.mime or "application/octet-stream",
                        headers=headers)


# ------------------------------------------------------------------ questions
@router.get("/uploads/{upload_id}/questions")
def upload_questions(upload_id: str, db: Session = Depends(get_session)) -> dict:
    """题库中的结构化记录 + 原始题号/分值。不返回答案、解析与评分要点。

    V2.2：导入的考试题不是只生成一份 PDF 答案册，而是逐题作为结构化题目
    写入题库（题干/选项/题型/考点/来源页码/答案 payload/解析/评分要点/范围档），
    因此可以直接参与组卷、每日 3 题与错题本。这里把「哪些字段已经结构化」
    如实列出来供核对；答案正文仍只在 `/booklet` 中打开，遵守答案隔离。
    """
    up = _get_upload(db, upload_id)
    kp_names = {k.kp_id: k.name for k in db.execute(
        select(E.KnowledgePoint)).scalars().all()}
    items = []
    for i, qid in enumerate(up.question_order or [], start=1):
        q = answer_pipeline.latest_question(db, qid)
        if not q:
            continue
        ref = (q.source_refs or [{}])[0]
        ready = answer_pipeline.is_answer_ready(q)
        meta = answer_pipeline.answer_meta_for(
            db, q.question_id, q.question_version) if ready else {}
        pages = ref.get("pages") or ([ref.get("page")] if ref.get("page") else [])
        items.append({
            "position": i, "question_id": qid,
            "question_version": q.question_version,
            "original_no": q.original_question_no,
            "section": ref.get("section"),
            "section_index": ref.get("section_index"),
            "question_type": q.question_type,
            "type_label": material_service.TYPE_LABEL_OF_QUESTION.get(
                q.question_type, q.question_type),
            "stem": q.stem, "options": q.options or [], "score": q.score,
            "answer_status": "ready" if ready else "pending",
            # 结构化字段是否齐备（只报有无，不泄露内容）
            "kp_id": q.main_kp_id or "",
            "kp_name": kp_names.get(q.main_kp_id or "", ""),
            "source_pages": pages,
            "answer_origin": q.answer_origin or "",
            "answer_label": meta.get("label") or "",
            "answer_version": meta.get("answer_version") or 0,
            "has_answer_payload": bool(q.answer_payload),
            "has_explanation": bool((q.explanation or "").strip()),
            "rubric_point_count": len(material_service._rubric_points(q.rubric or {})),
            "scope_class": q.scope_class or "in_scope",
            "in_bank": True,
            "bank_usable": bool(ready and q.active
                                and answer_pipeline.is_publishable(q)),
        })
    return {"upload_id": upload_id, "filename": up.filename,
            "total": len(items), "items": items,
            "answer_ready_count": up.answer_ready_count,
            "structured": {
                "in_bank": len(items),
                "bank_usable": sum(1 for x in items if x["bank_usable"]),
                "with_kp": sum(1 for x in items if x["kp_id"]),
                "with_source_pages": sum(1 for x in items if x["source_pages"]),
                "with_answer": sum(1 for x in items if x["has_answer_payload"]),
                "with_explanation": sum(1 for x in items if x["has_explanation"]),
                "with_rubric": sum(1 for x in items if x["rubric_point_count"]),
                "storage": "questions 表逐题一行；答案/解析/评分要点/来源页均为结构化字段",
            },
            "note": "题目已结构化写入题库，可用于组卷与每日 3 题；"
                    "本接口只返回题干、选项、题号、分值与字段齐备情况，"
                    "答案正文需在「查看 AI 答案」中打开"}


@router.post("/uploads/{upload_id}/reclassify")
def reclassify_upload(upload_id: str, db: Session = Depends(get_session)) -> dict:
    """V2.2：按解析后的【正文内容】重新识别资料类型（不使用文件名/后缀）。

    识别为老师课件时会顺带登记后台静默出题任务；结果写回资料库并留痕。
    """
    try:
        return material_service.reclassify_upload(db, upload_id)
    except KeyError:
        raise HTTPException(404, f"资料不存在: {upload_id}")


# ------------------------------------------------------------------- answers
@router.get("/uploads/{upload_id}/booklet")
def upload_booklet(upload_id: str, reveal: bool = True,
                   db: Session = Depends(get_session)) -> dict:
    """The single source of truth for answers.

    The saved booklet is returned as-is; it is never regenerated here. Opening
    this endpoint IS the act of viewing the answers, so `reveal=True` writes the
    per-question reveal records (generation alone never does).
    """
    up = _get_upload(db, upload_id)
    bk = _booklet(db, upload_id)
    if not bk:
        raise HTTPException(404, "该资料还没有答案册（可能仍在生成中）")
    answered = [q["question_id"] for sec in (bk.sections or [])
                for q in sec.get("questions", []) if q.get("answered")]
    reveal_info = {}
    if reveal and answered:
        reveal_info = material_service.record_reveal(db, up, answered, "web")
    return {
        "upload_id": upload_id, "booklet_id": bk.booklet_id,
        "title": bk.title, "status": bk.status,
        "version": bk.version or 1,
        "generated_at": bk.generated_at,
        "status_label": material_service.STATUS_LABELS.get(up.status, up.status),
        "total_count": bk.total_count, "answered_count": bk.answered_count,
        "content_hash": bk.content_hash,
        "answer_version_map": bk.answer_version_map,
        "sections": bk.sections or [],
        "pdf_ready": bool(bk.pdf_rel_path),
        "pdf_url": (f"/api/uploads/{upload_id}/booklet.pdf"
                    if bk.pdf_rel_path else ""),
        "reveal": reveal_info,
        "label_policy": answer_pipeline.ANSWER_DISCLAIMER,
        "note": "网页答案、下载的答案 PDF 与在线评分读取同一份已保存答案；"
                "本接口只读取，不重新生成",
    }


@router.get("/uploads/{upload_id}/booklet.pdf")
def upload_booklet_pdf(upload_id: str, db: Session = Depends(get_session)):
    """Download the answer PDF rendered from the saved booklet."""
    up = _get_upload(db, upload_id)
    bk = _booklet(db, upload_id)
    if not bk:
        raise HTTPException(404, "该资料还没有答案册")
    # Re-render only when the saved content changed (answer corrected) or the
    # file is missing -- never produce a different answer set than the web view.
    stale = not bk.pdf_rel_path or not (cfg.PROJECT_ROOT / bk.pdf_rel_path).exists()
    if stale:
        info = material_service.render_booklet_pdf(db, up)
        if not info.get("ok"):
            raise HTTPException(409, f"答案 PDF 暂不可用：{info.get('reason')}")
        db.refresh(bk)
    answered = [q["question_id"] for sec in (bk.sections or [])
                for q in sec.get("questions", []) if q.get("answered")]
    if answered:
        material_service.record_reveal(db, up, answered, "pdf_download")
    path = cfg.PROJECT_ROOT / bk.pdf_rel_path
    name = f"{Path(up.filename).stem}_AI参考答案.pdf"
    return FileResponse(str(path), media_type="application/pdf",
                        filename=name)


# -------------------------------------------------------------------- actions
@router.post("/uploads/{upload_id}/generate-answers")
def generate_answers(upload_id: str, body: dict | None = None,
                     db: Session = Depends(get_session)) -> dict:
    """The click behind 「AI 生成答案解析 PDF」 / 「重新生成」.

    Returns immediately with a job id; the page polls `GET /uploads/{id}` so the
    progress it shows is the database, not an animation. `regenerate=true`
    produces a NEW booklet version — old attempts keep the score they were
    graded with, and the change is recorded as a learning event.
    """
    regenerate = bool((body or {}).get("regenerate", False))
    try:
        return material_service.request_answers(db, upload_id,
                                                regenerate=regenerate)
    except KeyError:
        raise HTTPException(404, f"资料不存在: {upload_id}")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/uploads/{upload_id}/retry")
def retry_upload(upload_id: str, db: Session = Depends(get_session)) -> dict:
    try:
        return material_service.retry_upload(db, upload_id)
    except KeyError:
        raise HTTPException(404, f"资料不存在: {upload_id}")


@router.post("/uploads/{upload_id}/start-quiz")
def start_quiz(upload_id: str, db: Session = Depends(get_session)) -> dict:
    """Answer the ORIGINAL paper: same questions, order and scores."""
    up = _get_upload(db, upload_id)
    if not up.question_order:
        raise HTTPException(400, "该资料尚未识别出题目，无法开始答题")
    try:
        paper = material_service.assemble_original_paper(db, up, USER_ID)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        out = attempt_service.create_attempt(db, paper["paper_id"], USER_ID)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    out.update(attempt_service.attempt_state(db, out["attempt_id"]))
    out["upload_id"] = upload_id
    out["original"] = {
        "filename": up.filename,
        "keep_original_order": True,
        "total_score": paper["total_score"],
        "item_count": paper["item_count"],
        "answer_pending": (paper.get("coverage_report") or {}).get(
            "answer_pending", []),
        "note": "题目、顺序、分值取自原卷；评分依据复用已生成的答案，"
                "未就绪的题目本次不自动评分",
    }
    return out
