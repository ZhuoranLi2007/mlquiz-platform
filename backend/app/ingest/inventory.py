"""Step 1 of material onboarding: full READ-ONLY inventory.

Enumerates every file under the explicitly supplied root(s), records real
type (magic bytes, not just extension), size, mtime, SHA256, structural unit
count (pages / slides / cells / images), readability, source category,
duplicate group and processing status.

Nothing under the material root is ever written or modified. Results go to
data/manifest/. This is a discovery manifest, NOT a claim that content has
been semantically verified.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running as a script: python backend/app/ingest/inventory.py
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from backend.app import config as cfg
else:
    from backend.app import config as cfg

CHUNK = 1 << 20

KNOWN_EXT = {
    ".pdf", ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".md",
    ".markdown", ".ipynb", ".py", ".html", ".htm", ".csv", ".json", ".tex",
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".zip",
    ".epub", ".mobi", ".png",
}

# Directories that are project/tooling output, not course material.
NON_COURSE_DIRS = {".workbuddy", ".venv", "node_modules", ".git",
                   "__pycache__", "dist", "build", ".pytest_cache"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sniff(path: Path) -> str:
    """Real type from magic bytes. Returns a short label."""
    with path.open("rb") as f:
        head = f.read(16)
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        # Distinguish OOXML / ODF / generic zip by reading names
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
            if any(n.startswith("word/") for n in names):
                return "docx(ooxml)"
            if any(n.startswith("ppt/") for n in names):
                return "pptx(ooxml)"
            if any(n.startswith("xl/") for n in names):
                return "xlsx(ooxml)"
            return "zip"
        except Exception:
            return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole2(legacy office)"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] in (b"GIF8", b"RIFF", b"II*\x00", b"MM\x00*"):
        return "image"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "binary"


def category_of(rel: str, ext: str) -> str:
    """Heuristic source category. Rule-based, recorded as such, not verified."""
    p = rel.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    low = name.lower()
    first = p.split("/", 1)[0]
    if first in NON_COURSE_DIRS:
        return "workspace_metadata"
    if first == "_shots":
        return "page_screenshot"
    if first == "课上":
        return "classroom_photo"
    if first == "关键结构解释":
        return "structure_note"
    if first == "机器学习期末复习_按章节拆分":
        return "review_note_by_chapter"
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"):
        return "image_asset"
    if ext == ".zip":
        return "archive"
    if "期末考试" in name or "真题" in name or "考试（" in name or "考试(" in name:
        return "past_exam"
    if "复习" in name or "整合版" in name or "梳理" in name:
        return "review_note"
    if "考题" in name or "精讲" in name or "实战" in name or "预测分析" in name:
        return "exam_prep_note"
    if re.match(r"^\d+\.", name) and ext == ".pdf":
        return "course_slides"
    if ext in (".html", ".htm"):
        return "study_html"
    if re.match(r"^第[一二三]次课", name):
        return "class_transcript"
    if ext == ".doc":
        return "legacy_doc_uncertain"
    return "other"


def _pdf_probe(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"unit_label": "page", "unit_count": None,
                           "readability": "unknown", "notes": []}
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        try:
            if doc.is_encrypted and not doc.authenticate(""):
                out.update(readability="unreadable", unit_count=doc.page_count,
                           notes=["PDF 已加密，无法无密码读取"])
                return out
            out["unit_count"] = doc.page_count
            out["readability"] = "readable"
            try:
                out["toc_entries"] = len(doc.get_toc())
            except Exception:
                out["toc_entries"] = 0
            # Sample first pages for text layer availability
            sample = min(doc.page_count, 3)
            txt_pages = 0
            for i in range(sample):
                t = doc.load_page(i).get_text("text") or ""
                if t.strip():
                    txt_pages += 1
            out["text_layer_sample"] = f"{txt_pages}/{sample}"
            if txt_pages == 0:
                out["notes"].append("抽样页无文本层，疑似扫描件，需要 OCR 才能提取文字")
                out["readability"] = "partially_readable"
        finally:
            doc.close()
    except ImportError:
        # Fallback: count /Type /Page objects from raw bytes (estimate only)
        try:
            raw = path.read_bytes()
            n = len(re.findall(rb"/Type\s*/Page\b", raw))
            out.update(unit_count=n, readability="partially_readable",
                       unit_label="page(estimated)",
                       notes=["PyMuPDF 未安装，页数为字节正则估计值，需复核"])
        except Exception as e:
            out.update(readability="unreadable", notes=[f"读取失败: {e}"])
    except Exception as e:
        out.update(readability="unreadable", notes=[f"PDF 打开失败: {type(e).__name__}: {e}"])
    return out


def _ooxml_probe(path: Path, kind: str) -> dict[str, Any]:
    out: dict[str, Any] = {"unit_count": None, "readability": "unknown", "notes": []}
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if kind == "docx":
                out["unit_label"] = "paragraph"
                try:
                    import docx
                    d = docx.Document(str(path))
                    out["unit_count"] = len(d.paragraphs)
                    out["table_count"] = len(d.tables)
                    out["readability"] = "readable"
                except Exception:
                    xml = z.read("word/document.xml")
                    out["unit_count"] = xml.count(b"<w:p ") + xml.count(b"<w:p>")
                    out["unit_label"] = "paragraph(estimated)"
                    out["readability"] = "partially_readable"
                    out["notes"].append("仅按 XML 计数段落，未做语义读取")
                out["embedded_media"] = sum(1 for n in names if n.startswith("word/media/"))
            elif kind == "pptx":
                out["unit_label"] = "slide"
                try:
                    import pptx
                    pr = pptx.Presentation(str(path))
                    out["unit_count"] = len(pr.slides)
                    out["readability"] = "readable"
                except Exception:
                    out["unit_count"] = sum(
                        1 for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n))
                    out["unit_label"] = "slide(estimated)"
                    out["readability"] = "partially_readable"
                out["embedded_media"] = sum(1 for n in names if n.startswith("ppt/media/"))
            else:
                out["unit_label"] = "sheet"
                out["unit_count"] = sum(
                    1 for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
                out["readability"] = "readable"
    except Exception as e:
        out.update(readability="unreadable", notes=[f"ZIP/OOXML 读取失败: {e}"])
    return out


def _text_probe(path: Path, ext: str) -> dict[str, Any]:
    out: dict[str, Any] = {"unit_label": "line", "readability": "unknown", "notes": []}
    for enc in ("utf-8", "utf-8-sig", "gb18030", "cp1252"):
        try:
            text = path.read_text(encoding=enc)
            out["encoding"] = enc
            out["readability"] = "readable"
            out["unit_count"] = text.count("\n") + 1
            out["char_count"] = len(text)
            if ext == ".py":
                try:
                    tree = ast.parse(text)
                    out["unit_label"] = "def_or_class"
                    out["unit_count"] = sum(
                        1 for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
                except SyntaxError as e:
                    out["notes"].append(f"Python 语法解析失败（仅登记，不执行）: {e}")
            if ext == ".ipynb":
                try:
                    nb = json.loads(text)
                    cells = nb.get("cells", [])
                    out["unit_label"] = "cell"
                    out["unit_count"] = len(cells)
                    out["cell_types"] = dict(
                        (t, sum(1 for c in cells if c.get("cell_type") == t))
                        for t in ("markdown", "code", "raw"))
                    out["has_outputs"] = any(c.get("outputs") for c in cells)
                except Exception as e:
                    out["notes"].append(f"Notebook JSON 解析失败: {e}")
            if ext in (".html", ".htm"):
                out["unit_label"] = "html_tag"
                out["unit_count"] = len(re.findall(r"<[a-zA-Z]", text))
            if ext in (".md", ".markdown"):
                out["math_blocks"] = text.count("$$") // 2 + len(re.findall(r"\\\(|\\\[", text))
                out["code_fences"] = text.count("```") // 2
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if out["readability"] == "unknown":
        out.update(readability="unreadable", notes=["无法用 utf-8/gb18030/cp1252 解码"])
    return out


def _image_probe(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"unit_label": "image", "unit_count": 1,
                           "readability": "unknown", "notes": []}
    try:
        from PIL import Image
        with Image.open(path) as im:
            out["width"], out["height"] = im.size
            out["mode"] = im.mode
        out["readability"] = "readable"
        out["notes"].append("图片内容需 OCR/VLM 才能成为可检索知识；本阶段仅登记")
    except Exception as e:
        out.update(readability="partially_readable", notes=[f"图片尺寸读取失败: {e}"])
    return out


def _zip_probe(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"unit_label": "zip_entry", "readability": "unknown", "notes": []}
    try:
        with zipfile.ZipFile(path) as z:
            members = [i for i in z.infolist() if not i.is_dir()]
            out["unit_count"] = len(members)
            out["members"] = [m.filename for m in members][:100]
            out["readability"] = "readable"
            out["notes"].append("归档内容未在本阶段展开；如与同名目录对应，按重复关系登记")
    except Exception as e:
        out.update(readability="unreadable", notes=[f"ZIP 读取失败: {e}"])
    return out


def probe(path: Path, ext: str, real: str) -> dict[str, Any]:
    if real == "pdf" or ext == ".pdf":
        return _pdf_probe(path)
    if real == "docx(ooxml)" or (ext == ".docx" and real != "ole2(legacy office)"):
        return _ooxml_probe(path, "docx")
    if real == "pptx(ooxml)" or (ext == ".pptx" and real != "ole2(legacy office)"):
        return _ooxml_probe(path, "pptx")
    if real == "xlsx(ooxml)" or ext in (".xlsx", ".xls"):
        return _ooxml_probe(path, "xlsx")
    if real == "ole2(legacy office)" or ext in (".doc", ".ppt", ".xls"):
        return {"unit_label": "n/a", "unit_count": None,
                "readability": "unsupported_legacy",
                "notes": ["旧版 OLE2 二进制 Office 格式，当前解析器不支持；"
                          "需用户另存为 .docx/.pptx 后再处理，不静默跳过"]}
    if ext == ".zip" or real == "zip":
        return _zip_probe(path)
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff") or \
            real in ("jpeg", "png", "image"):
        return _image_probe(path)
    if ext in (".txt", ".md", ".markdown", ".ipynb", ".py", ".html", ".htm",
               ".csv", ".json", ".tex"):
        return _text_probe(path, ext)
    return {"unit_label": "n/a", "unit_count": None,
            "readability": "unsupported", "notes": ["未登记扩展名，保留记录待人工确认"]}


def initial_processing_status(rec: dict[str, Any]) -> str:
    """Discovery status. NOT semantic verification."""
    if rec["source_category"] == "workspace_metadata":
        return "excluded_non_course"
    if rec["readability"] in ("unreadable", "unsupported", "unsupported_legacy"):
        return "failed_" + rec["readability"]
    if rec["readability"] == "partially_readable":
        return "partially_readable_pending"
    if rec["source_category"] in ("page_screenshot", "classroom_photo", "image_asset"):
        return "needs_visual_review"
    return "discovered_pending_parse"


def build_inventory(roots: list[Path]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"资料根目录不存在: {root}")
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            ext = path.suffix.lower()
            stat = path.stat()
            try:
                digest = sha256_file(path)
                hash_err = ""
            except Exception as e:
                digest, hash_err = "", f"{type(e).__name__}: {e}"
            try:
                real = sniff(path)
                sniff_err = ""
            except Exception as e:
                real, sniff_err = "unknown", f"{type(e).__name__}: {e}"
            try:
                pr = probe(path, ext, real)
            except Exception as e:
                pr = {"unit_label": "n/a", "unit_count": None,
                      "readability": "unreadable", "notes": [f"探测异常: {e}"]}
            rec: dict[str, Any] = {
                "relative_path": rel,
                "material_root": str(root),
                "filename": path.name,
                "extension": ext,
                "sniffed_type": real,
                "size_bytes": stat.st_size,
                "modified_utc": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
                "sha256": digest,
                "unit_label": pr.get("unit_label", "n/a"),
                "unit_count": pr.get("unit_count"),
                "readability": pr.get("readability", "unknown"),
                "source_category": category_of(rel, ext),
                "extension_known": ext in KNOWN_EXT,
                "type_mismatch": bool(
                    real in ("pdf", "docx(ooxml)", "pptx(ooxml)", "ole2(legacy office)",
                             "jpeg", "png", "zip") and ext not in KNOWN_EXT),
                "notes": "; ".join(filter(None, pr.get("notes", []) +
                                          ([sniff_err] if sniff_err else []) +
                                          ([f"sha256失败:{hash_err}"] if hash_err else []))),
                "extra": {k: v for k, v in pr.items()
                          if k not in ("unit_label", "unit_count", "readability", "notes")},
            }
            rec["processing_status"] = initial_processing_status(rec)
            records.append(rec)

    # Content-hash based duplicate detection (same bytes may appear at several paths)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if r["sha256"]:
            groups[r["sha256"]].append(r)
    dup_id = 0
    for digest, members in groups.items():
        if len(members) < 2:
            continue
        dup_id += 1
        canonical = sorted(members, key=lambda m: (len(m["relative_path"]),
                                                   m["relative_path"]))[0]
        for m in members:
            m["duplicate_group"] = f"D{dup_id:03d}"
            m["duplicate_of"] = "" if m is canonical else canonical["relative_path"]
            m["is_duplicate_copy"] = m is not canonical
    for r in records:
        r.setdefault("duplicate_group", "")
        r.setdefault("duplicate_of", "")
        r.setdefault("is_duplicate_copy", False)

    counts: dict[str, int] = defaultdict(int)
    for r in records:
        counts[r["readability"]] += 1
    by_cat: dict[str, int] = defaultdict(int)
    for r in records:
        by_cat[r["source_category"]] += 1
    by_status: dict[str, int] = defaultdict(int)
    for r in records:
        by_status[r["processing_status"]] += 1
    by_ext: dict[str, int] = defaultdict(int)
    for r in records:
        by_ext[r["extension"] or "(无扩展名)"] += 1

    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "roots": [str(r.resolve()) for r in roots],
        "roots_read_only": True,
        "parser_config_version": cfg.PARSER_CONFIG_VERSION,
        "total_files": len(records),
        "total_bytes": sum(r["size_bytes"] for r in records),
        "distinct_content_hashes": len({r["sha256"] for r in records if r["sha256"]}),
        "duplicate_groups": dup_id,
        "duplicate_extra_copies": sum(1 for r in records if r["is_duplicate_copy"]),
        "by_readability": dict(sorted(counts.items())),
        "by_source_category": dict(sorted(by_cat.items())),
        "by_processing_status": dict(sorted(by_status.items())),
        "by_extension": dict(sorted(by_ext.items())),
        "unsupported_or_unreadable": [
            {"path": r["relative_path"], "readability": r["readability"],
             "notes": r["notes"]}
            for r in records
            if r["readability"] in ("unreadable", "unsupported",
                                    "unsupported_legacy", "partially_readable")
        ],
        "excluded_paths": [r["relative_path"] for r in records
                           if r["source_category"] == "workspace_metadata"],
        "disclaimer": ("本清单是文件发现与可读取性检查，不等于内容已完整核验、"
                       "不等于题目已可用。处理状态需由解析/人工复核流程另行更新。"),
    }
    return {"summary": summary, "records": records}


CSV_FIELDS = [
    "relative_path", "extension", "sniffed_type", "size_bytes", "modified_utc",
    "sha256", "unit_label", "unit_count", "readability", "source_category",
    "processing_status", "duplicate_group", "duplicate_of", "is_duplicate_copy",
    "extension_known", "type_mismatch", "notes",
]


def write_outputs(inv: dict[str, Any], out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or cfg.MANIFEST_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "materials_manifest.json"
    csv_path = out_dir / "materials_manifest.csv"
    report_path = out_dir / "inventory_report.md"
    json_path.write_text(json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in inv["records"]:
            w.writerow(r)
    s = inv["summary"]
    lines = [
        "# 资料全目录只读盘点报告", "",
        f"- 生成时间(UTC): {s['generated_utc']}",
        f"- 指定根目录: {', '.join(s['roots'])}（只读，未修改任何原文件）",
        f"- 解析配置版本: {s['parser_config_version']}",
        f"- 文件总数: **{s['total_files']}**，总字节: {s['total_bytes']:,}",
        f"- 不同内容哈希数: {s['distinct_content_hashes']}；"
        f"重复组: {s['duplicate_groups']}；重复副本: {s['duplicate_extra_copies']}", "",
        "## 可读取性分布", "",
        "| 状态 | 文件数 |", "| --- | --- |",
    ]
    lines += [f"| {k} | {v} |" for k, v in s["by_readability"].items()]
    lines += ["", "## 来源类别（规则启发式，未经内容核验）", "",
              "| 类别 | 文件数 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in s["by_source_category"].items()]
    lines += ["", "## 处理状态（发现阶段）", "",
              "| 状态 | 文件数 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in s["by_processing_status"].items()]
    lines += ["", "## 不支持 / 部分可读 / 不可读（不静默跳过）", ""]
    if s["unsupported_or_unreadable"]:
        for u in s["unsupported_or_unreadable"]:
            lines.append(f"- `{u['path']}` — {u['readability']}: {u['notes']}")
    else:
        lines.append("- 无")
    lines += ["", "## 排除项（非课程资料，保留记录但不导入）", ""]
    lines += [f"- `{p}`" for p in s["excluded_paths"]] or ["- 无"]
    lines += ["", "## 重复内容组", ""]
    dups: dict[str, list[str]] = defaultdict(list)
    for r in inv["records"]:
        if r["duplicate_group"]:
            dups[r["duplicate_group"]].append(r["relative_path"])
    if dups:
        for g, ps in sorted(dups.items()):
            lines.append(f"- {g}: " + " ↔ ".join(f"`{p}`" for p in ps))
    else:
        lines.append("- 未发现内容完全相同的文件（SHA256 级）。"
                     "注意：语义相近但字节不同的文件不在此列。")
    lines += ["", "## 声明", "", s["disclaimer"], "",
              "> 「文件已导入」「文本可提取」「内容已完整核验」是三件不同的事；",
              "> 本清单只完成第一件的发现与第二件的可读取性判断。", ""]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return [json_path, csv_path, report_path]


def main() -> None:
    roots = [Path(p) for p in sys.argv[1:]] or [cfg.MATERIAL_ROOT]
    inv = build_inventory(roots)
    paths = write_outputs(inv)
    s = inv["summary"]
    print(json.dumps(s, ensure_ascii=False, indent=2))
    print("\n输出:")
    for p in paths:
        print(" -", p)


if __name__ == "__main__":
    main()
