"""PDF 首页缩略图：渲染、缓存与失效（V2.3 扩展学习）。

三条边界必须分开，不能互相推定：
  * `preview_allowed` —— 允许公开展示**第一页缩略图**，仅用于识别这份资源；
  * `allows_copy`     —— 允许**再分发原卷**，才提供平台内下载；
  * 本地下载测试       —— 见 `data/external_pdfs/manifest.jsonl`，不进学生端。

因此默认 `preview_allowed=False`：没有明确授权时不生成、不返回缩略图，
卡片会退回类型化占位封面，而不是拿别的 PDF 图片冒充。

缓存策略：
  * 文件名 = `{epaper_id}_{pdf_sha256[:8]}.png`，PDF 内容一变，sha 变，
    文件名随之改变，天然失效；
  * 命中条件：`thumb_rel_path` 存在 + 文件存在 + `pdf_sha256` 与当前文件一致；
  * 渲染先写临时文件再原子替换，避免并发读到半个文件。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pymupdf
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E

THUMB_DIR = cfg.PROJECT_ROOT / "data" / "external_pdfs" / "thumbs"
# 显示宽度约 120px，按 2x 渲染保证高分屏清晰；高度随 PDF 原始比例。
TARGET_WIDTH = 240
MAX_PIXELS = 4000  # 异常大的页面尺寸保护


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def thumb_path(epaper_id: str, sha: str) -> Path:
    return THUMB_DIR / f"{epaper_id}_{sha[:8]}.png"


def _render_first_page(src: Path, out: Path, width: int = TARGET_WIDTH) -> dict:
    doc = pymupdf.open(str(src))
    try:
        if doc.page_count < 1:
            raise ValueError("PDF 没有页面")
        page = doc[0]
        rect = page.rect
        if rect.width <= 0 or rect.height <= 0:
            raise ValueError("页面尺寸异常")
        zoom = min(width / rect.width, MAX_PIXELS / max(rect.width, rect.height))
        zoom = max(zoom, 0.05)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再原子替换，避免并发读到半个图
        handle = tempfile.NamedTemporaryFile(
            suffix=".png", dir=str(out.parent), delete=False)
        tmp = Path(handle.name)
        handle.close()
        try:
            pix.save(str(tmp))
            os.replace(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)
        return {"width": pix.width, "height": pix.height,
                "bytes": out.stat().st_size}
    finally:
        doc.close()


def ensure_thumbnail(db: Session, paper: E.ExternalPaper, *,
                     force: bool = False, width: int = TARGET_WIDTH) -> dict:
    """按需生成/复用缩略图。失败时返回 reason，不抛异常给调用方。"""
    if not paper.preview_allowed:
        return {"ok": False, "reason": "preview_not_allowed",
                "message": "该资源尚未确认公开展示授权，不生成预览图"}
    if not paper.pdf_rel_path:
        return {"ok": False, "reason": "pdf_missing",
                "message": "该资源没有本地 PDF，使用占位封面"}
    src = cfg.resolve_storage_ref(paper.pdf_rel_path)
    if not src.exists():
        return {"ok": False, "reason": "pdf_missing",
                "message": "本地 PDF 文件不存在，使用占位封面"}

    sha = sha256_of(src)
    cached = (paper.thumb_rel_path
              and paper.pdf_sha256 == sha
              and cfg.resolve_storage_ref(paper.thumb_rel_path).exists())
    if cached and not force:
        thumb = cfg.resolve_storage_ref(paper.thumb_rel_path)
        width_px = height_px = 0
        try:
            pix = pymupdf.Pixmap(str(thumb))
            width_px, height_px = pix.width, pix.height
            pix = None
        except Exception:  # noqa: BLE001 - 缓存图坏了就当作未命中
            return {"ok": False, "reason": "render_failed",
                    "message": "缓存的缩略图无法读取，请带 force 重新生成"}
        return {"ok": True, "cached": True,
                "thumb_rel_path": paper.thumb_rel_path,
                "bytes": thumb.stat().st_size,
                "width": width_px, "height": height_px,
                "pdf_sha256": sha}

    out = thumb_path(paper.epaper_id, sha)
    try:
        info = _render_first_page(src, out, width=width)
    except Exception as e:  # noqa: BLE001 - 损坏 PDF 不能让接口 500
        return {"ok": False, "reason": "render_failed",
                "message": f"缩略图渲染失败：{type(e).__name__}: {e}"}

    paper.pdf_sha256 = sha
    paper.thumb_rel_path = cfg.storage_ref(out)
    paper.thumb_generated_at = E.utcnow()
    paper.updated_at = E.utcnow()
    db.commit()
    return {"ok": True, "cached": False, "thumb_rel_path": paper.thumb_rel_path,
            "pdf_sha256": sha, **info}


def thumbnail_state(paper: E.ExternalPaper) -> dict:
    """给列表/详情用的轻量状态，不做任何渲染。"""
    if not paper.preview_allowed:
        return {"available": False, "reason": "preview_not_allowed",
                "url": "", "note": "未确认公开展示授权"}
    if not paper.pdf_rel_path:
        return {"available": False, "reason": "pdf_missing",
                "url": "", "note": "没有本地 PDF"}
    thumb = cfg.resolve_storage_ref(paper.thumb_rel_path) if paper.thumb_rel_path else None
    if thumb and thumb.exists():
        return {"available": True, "reason": "ready",
                "url": f"/api/external/papers/{paper.epaper_id}/thumbnail",
                "note": "真实 PDF 第一页"}
    # 还没渲染过：给出 URL，前端懒加载时再按需生成
    return {"available": True, "reason": "on_demand",
            "url": f"/api/external/papers/{paper.epaper_id}/thumbnail",
            "note": "首次打开时生成"}
