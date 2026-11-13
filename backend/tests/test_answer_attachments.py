"""手写答案附件与 OCR 的确定性回归测试（不联网）。

重点锁定两条业务底线：
  1. **识别失败时原图仍然有效**，且失败原因被如实写入 ocr_results；
  2. 非图片 / 超限 / 交卷后 一律拒绝，且不产生脏数据。

模型调用被 monkeypatch 成抛错 —— 这是"失败路径"的确定性模拟，
不代表真实 API 成功；真实视觉调用见 docs/evidence/e2e_ocr.json。
"""
from __future__ import annotations

import base64
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import entities as E
from backend.app.models.base import Base
from backend.app.services import (answer_attachment_service as svc,
                                  answer_ocr)

# 系统临时目录在本机有 ACL 限制（pytest 的 tmp_path 会 PermissionError），
# 因此测试自己用工作区内的临时目录，结束后清理。
SCRATCH = Path(__file__).resolve().parents[2] / "data" / "tmp" / "pytest-attachments"


@pytest.fixture()
def workdir():
    path = SCRATCH / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def storage(workdir, monkeypatch):
    # 测试绝不能写进真实 data/uploads
    monkeypatch.setattr(svc, "ROOT", workdir / "answers")
    return workdir


def make_attempt(db, *, status="in_progress") -> E.Attempt:
    att = E.Attempt(attempt_id=E.new_id("attempt"), paper_id="paper-1",
                    user_id="local_user", mode="practice", status=status,
                    started_at=E.utcnow())
    db.add(att)
    db.commit()
    return att


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def png_data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()


def test_upload_saves_file_and_row(db, storage):
    att = make_attempt(db)
    out = svc.save(db, att, "q1", png_data_url())
    assert out["mime"] == "image/png"
    assert out["bytes"] == len(PNG_1PX)
    row = db.get(E.AnswerAttachment, out["attachment_id"])
    assert row is not None and row.active is True
    path = (storage / "answers" / att.attempt_id / f"{row.attachment_id}.png")
    assert path.exists() and path.read_bytes() == PNG_1PX


def test_non_image_is_rejected(db, storage):
    att = make_attempt(db)
    bad = "data:image/png;base64," + base64.b64encode(b"not an image").decode()
    with pytest.raises(svc.AttachmentError):
        svc.save(db, att, "q1", bad)
    assert db.query(E.AnswerAttachment).count() == 0


def test_submitted_attempt_rejects_upload(db, storage):
    att = make_attempt(db, status="submitted")
    with pytest.raises(svc.AttachmentError):
        svc.save(db, att, "q1", png_data_url())


def test_ocr_failure_keeps_original_image(db, storage, monkeypatch):
    """模型挂了：写一条 failed 记录，原图与附件都还在。"""
    att = make_attempt(db)
    up = svc.save(db, att, "q1", png_data_url())
    row = db.get(E.AnswerAttachment, up["attachment_id"])

    def boom(_db, _data_url):
        raise RuntimeError("model_unavailable: 未配置模型")

    monkeypatch.setattr(answer_ocr, "transcribe", boom)
    out = svc.run_ocr(db, row)

    assert out["status"] == "failed"
    assert "model_unavailable" in (out["error"] or "")
    ocr = db.get(E.OCRResult, out["ocr_id"])
    assert ocr is not None and ocr.status == "failed"
    assert ocr.duration_ms >= 0
    # 原图仍然有效
    assert db.get(E.AnswerAttachment, row.attachment_id).active is True
    path = storage / "answers" / att.attempt_id / f"{row.attachment_id}.png"
    assert path.exists()


def test_ocr_success_marks_ok_and_confirm_saves_text(db, storage, monkeypatch):
    att = make_attempt(db)
    up = svc.save(db, att, "q1", png_data_url())
    row = db.get(E.AnswerAttachment, up["attachment_id"])

    monkeypatch.setattr(answer_ocr, "transcribe",
                        lambda _db, _u: {"text": "$J(\\theta)=\\frac{1}{2m}$",
                                         "model": "test-model", "call_id": "c1"})
    out = svc.run_ocr(db, row)
    assert out["status"] == "ok" and out["model"] == "test-model"

    conf = svc.confirm(db, out["ocr_id"], "学生修正后的文本", att)
    assert conf["confirmed"] is True
    assert db.get(E.OCRResult, out["ocr_id"]).text == "学生修正后的文本"


def test_confirm_rejected_after_submit(db, storage, monkeypatch):
    att = make_attempt(db)
    up = svc.save(db, att, "q1", png_data_url())
    row = db.get(E.AnswerAttachment, up["attachment_id"])
    monkeypatch.setattr(answer_ocr, "transcribe",
                        lambda _db, _u: {"text": "x", "model": "m", "call_id": ""})
    out = svc.run_ocr(db, row)

    att.status = "submitted"
    db.add(att)
    db.commit()
    with pytest.raises(svc.AttachmentError):
        svc.confirm(db, out["ocr_id"], "改", att)


def test_file_path_refuses_wrong_attempt(db, storage):
    att = make_attempt(db)
    up = svc.save(db, att, "q1", png_data_url())
    with pytest.raises(PermissionError):
        svc.file_path(db, up["attachment_id"], attempt_id="other-attempt")
