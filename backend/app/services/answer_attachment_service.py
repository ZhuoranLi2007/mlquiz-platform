"""手写答案图片与 OCR 结果（V2.3）。

原则：
  * 原图是答案本体，OCR 只是辅助转写；识别失败也要能提交原图；
  * 文件只通过受控接口读取，校验 attempt 归属，不暴露目录、不允许猜 URL；
  * 图片不写日志，不把 base64 或 API Key 打进日志；
  * 已交卷的答卷锁定，不能增删附件或改 OCR 结果。
"""
from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path

import pymupdf
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import answer_ocr

MAX_BYTES = 6 * 1024 * 1024
MAX_PER_QUESTION = 6
# V3.5（RC-11）：这里原来写死 PROJECT_ROOT/data/uploads/answers，
# 于是即使设置了 MLQ_DATA_DIR（隔离实例 / 迁移部署），手写答案图片仍然写进
# 项目自带的 data 目录：隔离测试会污染真实上传目录，部署时也会分叉。
# 改为跟随统一的上传根目录 cfg.UPLOAD_DIR。
ROOT = cfg.UPLOAD_DIR / "answers"
MAGIC = {
    b"\x89PNG\r\n\x1a\n": ("image/png", "png"),
    b"\xff\xd8\xff": ("image/jpeg", "jpg"),
    b"RIFF": ("image/webp", "webp"),
}
PROMPT_VERSION = "answer-ocr-1.0"


class AttachmentError(Exception):
    """可直接展示给学生的失败原因。"""


def _decode(data_url: str) -> tuple[bytes, str, str]:
    if not data_url:
        raise AttachmentError("缺少图片数据")
    payload = data_url
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception as e:                              # noqa: BLE001
        raise AttachmentError(f"图片 base64 解析失败：{e}")
    if not raw:
        raise AttachmentError("图片内容为空")
    if len(raw) > MAX_BYTES:
        raise AttachmentError(f"图片超过 {MAX_BYTES // 1024 // 1024}MB 限制")
    for magic, (mime, ext) in MAGIC.items():
        if raw.startswith(magic):
            return raw, mime, ext
    raise AttachmentError("只支持 PNG / JPEG / WebP 图片")


def _size(path: Path) -> tuple[int, int]:
    try:
        pix = pymupdf.Pixmap(str(path))
        return pix.width, pix.height
    except Exception:                                   # noqa: BLE001
        return 0, 0


def dto(a: E.AnswerAttachment, ocr: E.OCRResult | None = None) -> dict:
    return {
        "attachment_id": a.attachment_id,
        "question_id": a.question_id,
        "mime": a.mime,
        "bytes": a.bytes,
        "width": a.width,
        "height": a.height,
        "position": a.position,
        "created_at": a.created_at,
        "url": f"/api/attachments/{a.attachment_id}/file",
        "ocr": None if ocr is None else {
            "ocr_id": ocr.ocr_id,
            "text": ocr.text,
            "status": ocr.status,
            "error": ocr.error,
            "model": ocr.model,
            "confirmed": ocr.confirmed,
            "created_at": ocr.created_at,
        },
    }


def save(db: Session, attempt: E.Attempt, question_id: str,
         data_url: str, position: int = 0) -> dict:
    if attempt.status == "submitted":
        raise AttachmentError("已交卷，不能再上传答案图片")
    count = db.execute(select(E.AnswerAttachment).where(
        E.AnswerAttachment.attempt_id == attempt.attempt_id,
        E.AnswerAttachment.question_id == question_id,
        E.AnswerAttachment.active.is_(True))).scalars().all()
    if len(count) >= MAX_PER_QUESTION:
        raise AttachmentError(f"每题最多上传 {MAX_PER_QUESTION} 张图片")

    raw, mime, ext = _decode(data_url)
    attachment_id = E.new_id("att")
    folder = ROOT / attempt.attempt_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{attachment_id}.{ext}"
    path.write_bytes(raw)
    width, height = _size(path)

    row = E.AnswerAttachment(
        attachment_id=attachment_id, attempt_id=attempt.attempt_id,
        question_id=question_id, user_id=attempt.user_id,
        rel_path=cfg.storage_ref(path), mime=mime, bytes=len(raw),
        width=width, height=height,
        sha256=hashlib.sha256(raw).hexdigest(),
        position=position or (len(count) + 1),
    )
    db.add(row)
    db.commit()
    return dto(row)


def list_for_attempt(db: Session, attempt_id: str) -> dict:
    rows = db.execute(select(E.AnswerAttachment).where(
        E.AnswerAttachment.attempt_id == attempt_id,
        E.AnswerAttachment.active.is_(True),
    ).order_by(E.AnswerAttachment.question_id,
               E.AnswerAttachment.position)).scalars().all()
    latest: dict[str, E.OCRResult] = {}
    if rows:
        ocrs = db.execute(select(E.OCRResult).where(
            E.OCRResult.attachment_id.in_([r.attachment_id for r in rows]),
        ).order_by(E.OCRResult.created_at)).scalars().all()
        for o in ocrs:
            latest[o.attachment_id] = o
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row.question_id, []).append(
            dto(row, latest.get(row.attachment_id)))
    return {"items": [dto(r, latest.get(r.attachment_id)) for r in rows],
            "by_question": grouped, "total": len(rows)}


def file_path(db: Session, attachment_id: str,
              attempt_id: str = "") -> tuple[E.AnswerAttachment, Path]:
    row = db.get(E.AnswerAttachment, attachment_id)
    if row is None or not row.active:
        raise KeyError(attachment_id)
    if attempt_id and row.attempt_id != attempt_id:
        raise PermissionError("附件不属于该答卷")
    path = cfg.resolve_storage_ref(row.rel_path)
    if not path.exists():
        raise FileNotFoundError("附件文件不存在")
    return row, path


def delete(db: Session, attachment_id: str, attempt: E.Attempt) -> dict:
    if attempt.status == "submitted":
        raise AttachmentError("已交卷，不能删除答案图片")
    row = db.get(E.AnswerAttachment, attachment_id)
    if row is None or not row.active or row.attempt_id != attempt.attempt_id:
        raise KeyError(attachment_id)
    row.active = False
    path = cfg.resolve_storage_ref(row.rel_path)
    if path.exists():
        path.unlink(missing_ok=True)
    db.commit()
    return {"attachment_id": attachment_id, "removed": True}


def run_ocr(db: Session, attachment: E.AnswerAttachment) -> dict:
    """识别一张图；失败也写一条 failed 记录，原图不受影响。"""
    path = cfg.resolve_storage_ref(attachment.rel_path)
    if not path.exists():
        raise AttachmentError("附件文件不存在")
    t0 = time.time()
    raw = path.read_bytes()
    data_url = f"data:{attachment.mime};base64," + base64.b64encode(raw).decode()
    ocr = E.OCRResult(
        ocr_id=E.new_id("ocr"), attachment_id=attachment.attachment_id,
        attempt_id=attachment.attempt_id, question_id=attachment.question_id,
        # 先记配置里的模型名；调用成功后用服务端实际返回的模型名覆盖。
        # 失败时保留配置值，避免把「没调用的模型」写成结果。
        model=cfg.MODEL_CHAT_MODEL or "",
        prompt_version=PROMPT_VERSION,
    )
    try:
        out = answer_ocr.transcribe(db, data_url)
        ocr.text = out["text"]
        ocr.model = out.get("model") or ocr.model
        ocr.status = "ok"
    except Exception as e:                              # noqa: BLE001
        # 失败不是终点：记录原因，学生仍可用原图交卷。
        ocr.status = "failed"
        ocr.error = str(e)[:400]
    ocr.duration_ms = int((time.time() - t0) * 1000)
    db.add(ocr)
    db.commit()
    return {"ocr_id": ocr.ocr_id, "status": ocr.status, "text": ocr.text,
            "error": ocr.error, "model": ocr.model,
            "attachment_id": attachment.attachment_id,
            "note": ("识别结果可修改后保存；原图始终保留"
                     if ocr.status == "ok" else
                     "识别失败，原图仍然有效，可直接用图片交卷")}


def confirm(db: Session, ocr_id: str, text: str,
            attempt: E.Attempt) -> dict:
    if attempt.status == "submitted":
        raise AttachmentError("已交卷，不能再修改识别结果")
    row = db.get(E.OCRResult, ocr_id)
    if row is None or row.attempt_id != attempt.attempt_id:
        raise KeyError(ocr_id)
    row.text = text
    row.confirmed = True
    row.confirmed_at = E.utcnow()
    db.commit()
    return {"ocr_id": ocr_id, "confirmed": True, "text": row.text}
