"""Read-only, hash-pinned teacher source presentation. No answer reconstruction."""
from pathlib import Path
from hashlib import sha256
from functools import lru_cache
from io import BytesIO
import json
import re
from urllib.parse import urlparse
from PIL import Image
from backend.app import config as cfg


def is_search_entry(url, title=""):
    parsed = urlparse(url or "")
    return ("检索入口" in (title or "") or "搜索入口" in (title or "")
            or parsed.hostname in {"search.bilibili.com", "so.csdn.net", "www.google.com", "www.bing.com", "www.baidu.com"}
            or "/search" in parsed.path.lower())


def manifests():
    root = cfg.DATA_DIR / "classroom_source"
    for path in root.glob("*/teacher_docx.json"):
        try:
            yield path, json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue


@lru_cache(maxsize=32)
def _emf_png(path: str, mtime_ns: int, size: int) -> bytes:
    """Render an original vector formula for browsers without changing its source."""
    del mtime_ns, size
    with Image.open(path) as image:
        image.load()
        output = BytesIO()
        image.convert("RGBA").save(output, format="PNG")
        return output.getvalue()


def render_emf_png(path: Path) -> bytes:
    stat = path.stat()
    return _emf_png(str(path), stat.st_mtime_ns, stat.st_size)


def asset_path(source_sha, asset_id):
    if not re.fullmatch(r"[a-f0-9]{64}", source_sha or "") or not re.fullmatch(r"media-[a-f0-9]+", asset_id or ""):
        raise ValueError("无效媒体标识")
    for manifest, doc in manifests():
        if doc.get("source", {}).get("sha256") != source_sha:
            continue
        for asset in doc.get("media_assets", []):
            if asset.get("asset_id") != asset_id:
                continue
            path = (manifest.parent / asset.get("safe_path", "")).resolve()
            root = (manifest.parent / "media").resolve()
            if not path.is_relative_to(root) or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".emf"}:
                raise ValueError("媒体格式暂无法显示")
            if not path.is_file() or sha256(path.read_bytes()).hexdigest() != asset.get("sha256"):
                raise ValueError("媒体文件缺失或哈希不符")
            if path.suffix.lower() == ".emf":
                try:
                    render_emf_png(path)
                except (OSError, ValueError) as exc:
                    raise ValueError("公式图片暂无法显示") from exc
            return path
    raise ValueError("媒体不存在")


def source_content(snapshot):
    qid = snapshot.get("docx_question_id")
    if not qid:
        return {}
    expected = snapshot.get("docx_content_hash")
    source_sha = next((r.get("file_sha256") for r in snapshot.get("source_refs", [])
                       if isinstance(r, dict) and r.get("kind") == "teacher_docx"), "")
    for manifest, doc in manifests():
        if doc.get("source", {}).get("sha256") != source_sha:
            continue
        for quiz in doc.get("quizzes", []):
            for q in quiz.get("questions", []):
                if q.get("question_id") != qid:
                    continue
                if not expected or q.get("content_hash") != expected:
                    return {"media_incomplete": True, "content_warning": "原题内容哈希不匹配，未擅自替换快照"}
                unavailable = []
                def media(m):
                    aid = m.get("selected_asset_id", "")
                    out = {"type": "media", "asset_id": aid,
                           "role": m.get("role") or "image",
                           "alt_text": m.get("alt_text") or "题目配图或公式"}
                    try:
                        asset_path(source_sha, aid)
                        out.update(url=f"/api/classroom-media/{source_sha}/{aid}", available=True)
                    except ValueError as exc:
                        out.update(available=False, reason=str(exc))
                        unavailable.append(aid)
                    return out
                def block(b):
                    fragments = []
                    for f in b.get("fragments", []):
                        if f.get("type") == "text": fragments.append({"type": "text", "text": str(f.get("text", ""))})
                        elif f.get("type") == "media": fragments.append(media(f))
                    if not fragments and b.get("text"):
                        fragments.append({"type": "text", "text": str(b["text"])})
                    return {"paragraph_index": b.get("paragraph_index"), "fragments": fragments}
                blocks = [block(b) for b in [q.get("stem_block") or {}, *q.get("body_blocks", [])]]
                options = [{"key": o.get("key"), "text": o.get("text", ""),
                            "blocks": [block(o)]} for o in q.get("options", [])]
                ids = q.get("shared_material_ids", [])
                shared = [{"shared_material_id": x["shared_material_id"], "blocks": [block(b) for b in x.get("blocks", [])]}
                          for x in quiz.get("shared_materials", []) if x.get("shared_material_id") in ids]
                if len(shared) != len(ids): unavailable.append("missing_shared_material")
                return {"source_question_id": qid, "source_stem": q.get("source_stem", q.get("stem", "")),
                        "source_blocks": blocks, "source_options": options, "shared_material_ids": ids,
                        "shared_materials": shared, "media": [media(m) for m in q.get("media", [])],
                        "media_incomplete": bool(unavailable),
                        "content_warning": "部分原题媒体尚不可显示，不能按完整题目判分" if unavailable else "",
                        "content_source": {"sha256": source_sha, "question_content_hash": expected,
                                           "note": "原Word有序内容；与答卷快照文本分开保留，不覆盖评分事实"}}
    return {"media_incomplete": True, "content_warning": "原题资料暂不可用，未伪造共享材料"}
