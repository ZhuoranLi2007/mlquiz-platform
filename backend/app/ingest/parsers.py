"""Step 2-4: format-aware parsing, per-page quality judgement, cross-page
merging and semantic chunking.

Evidence-driven rules calibrated on the three REAL samples (see
docs/MATERIALS_AUDIT.md). Important: a non-empty text layer and zero embedded
images do NOT imply completeness -- formulas are often vector drawings.
Nothing here invents mathematical content: pages that need visual/formula
review are flagged with their page image retained.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pymupdf as fitz  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.app import config as cfg  # noqa: E402
from backend.app.models import entities as E  # noqa: E402
from backend.app.models.base import SessionLocal  # noqa: E402

BOILERPLATE = [
    re.compile(r"^第\s*\d+\s*页\s*共\s*\d+\s*页$"),
    re.compile(r"^草\s*稿\s*区$"),
    re.compile(r"^得\s*分$"),
    re.compile(r"^\d{1,3}$"),
    re.compile(r"^[-—–\s]*$"),
]
UI_NOISE = ("此题未设置答案", "请点击右侧设置按钮", "设置", "提交", "多选题", "单选题")
CONNECTOR_ONLY = {"由", "和", "可知", "得", "即", "为", "或者", "其中", "因此",
                  "所以", "则", "有", "满足", "可得", "即：", "得：", "由："}
QUESTION_MARKERS = re.compile(
    r"(?:^|\n)\s*(?:题\s*(\d+)|(\d+)\s*[\.、）\)]|（\s*(\d+)\s*）)\s*")
ANSWER_MARKERS = re.compile(r"(?:^|\n)\s*(答案|参考答案|标准答案|解析|解答)\s*[:：]\s*(.+)")
OPTION_LINE = re.compile(r"^\s*[（(]?([A-Ea-e])[\)）\.、:：]\s*(.*)$")
SECTION_TITLE = re.compile(
    r"^\s*(?:[一二三四五六七八九十]+\s*[、.]|第\s*\d+\s*章|\d+(?:\.\d+){1,3}\s+\S)")


def normalize_text(text: str) -> str:
    """NFKC folds CJK compatibility ideographs (⽋->欠, ⾼->高 ...).
    Stored SEPARATELY; raw text is never overwritten."""
    return unicodedata.normalize("NFKC", text)


def clean_lines(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s or any(p.match(s) for p in BOILERPLATE):
            continue
        out.append(s)
    return out


def page_quality(page: fitz.Page, text: str) -> dict:
    """Heuristic signals, explicitly NOT a completeness proof."""
    lines = clean_lines(text)
    images = page.get_images(full=True)
    drawings = page.get_drawings()
    n_img, n_draw = len(images), len(drawings)
    blank_opts, thin_opts = [], []
    math_chars = 0
    for l in lines:
        m = OPTION_LINE.match(l)
        if m:
            payload = m.group(2).strip()
            if not payload:
                blank_opts.append(l)
            elif len(payload) <= 3:
                thin_opts.append(l)
        math_chars += sum(l.count(c) for c in "∑∏∫∂∇≤≥≠≈∈∀∃→←αβγδθλμσφω")
    latex_hint = bool(re.search(r"\\[a-zA-Z]{2,}|\$|_[{]|\^[{]", text))
    connector_lines = [l for l in lines
                       if l in CONNECTOR_ONLY or re.fullmatch(r"[\u4e00-\u9fff]{1,3}\s*[:：]", l)]
    ui = [u for u in UI_NOISE if u in text]
    is_draft = bool(re.search(r"草\s*稿\s*区", text)) and len(lines) <= 3

    reasons: list[str] = []
    text_chars = len("".join(lines))
    if is_draft:
        text_status = "boilerplate_only"
        reasons.append("仅页眉页码与草稿区，非知识内容页；不得据此生成题目")
    elif text_chars == 0:
        text_status = "empty"
        reasons.append("整页无文本层，疑似扫描件，需要 OCR")
    elif text_chars < 120:
        text_status = "sparse"
        reasons.append("有效文本过少，公式/图形可能未进入文本层")
    else:
        text_status = "ok"

    formula_status = "no_signal"
    if blank_opts or thin_opts:
        formula_status = "suspected_missing_formula"
        reasons.append(f"检测到 {len(blank_opts)} 个空选项、{len(thin_opts)} 个极短选项；"
                       "公式选项可能未进入文本层，不静默丢弃")
    elif n_draw > 60 and (text_chars < 400 or math_chars > 0 or latex_hint):
        formula_status = "suspected_missing_formula"
        reasons.append("大量矢量绘图且说明文字孤立，公式很可能为绘制图形")
    elif len(connector_lines) >= 2 and (n_img > 0 or n_draw > 20):
        formula_status = "suspected_missing_formula"
        reasons.append("文本只剩『由/和/得/即』等连接词且页内有图，公式位置空缺")
    elif math_chars > 5 or latex_hint:
        formula_status = "text_math_present"

    figure_status = ("embedded_image+vector_drawings" if n_img and n_draw
                     else "embedded_image" if n_img
                     else "vector_drawings" if n_draw > 20 else "none")

    if is_draft:
        review = "non_content_page"
    elif formula_status == "suspected_missing_formula":
        review = "needs_formula_review"
    elif text_status in ("empty", "sparse") or n_img > 0:
        review = "needs_visual_review"
    else:
        review = "auto_ok"

    return {
        "text_status": text_status,
        "formula_status": formula_status,
        "figure_status": figure_status,
        "review_status": review,
        "reasons": reasons,
        "signals": {
            "raw_text_chars": len(text.strip()),
            "effective_text_chars": text_chars,
            "line_count": len(lines),
            "image_count": n_img,
            "drawing_count": n_draw,
            "math_char_count": math_chars,
            "blank_option_lines": blank_opts,
            "thin_option_lines": thin_opts,
            "connector_lines": connector_lines[:12],
            "ui_noise": ui,
            "classroom_question": "多选题" in ui or "单选题" in ui,
            "answer_not_set_in_source": "此题未设置答案" in text,
            "is_draft_area": is_draft,
        },
        "lines": lines,
    }


def render_page(doc: fitz.Document, page_no: int, doc_id: str,
                scale: float = 1.2) -> str:
    out = cfg.ASSET_DIR / "pages" / doc_id / f"p{page_no:04d}.png"
    if out.exists():
        return cfg.asset_ref(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pix = doc.load_page(page_no - 1).get_pixmap(
        matrix=fitz.Matrix(scale, scale), alpha=False)
    pix.save(str(out))
    return cfg.asset_ref(out)


def resolve_source(rel_path: str) -> Path:
    """Resolve a stored relative path to a real file.

    Read-only course materials live under MATERIAL_ROOT; user uploads live
    inside the project (data/uploads/...). Both are stored as a project- or
    material-relative path, so try the material root first and fall back to the
    project root. Never writes to either location here.
    """
    p = Path(rel_path)
    if p.is_absolute():
        return p
    cand = cfg.MATERIAL_ROOT / rel_path
    if cand.exists():
        return cand
    return cfg.PROJECT_ROOT / rel_path


def ensure_page_image(doc_id: str, rel_path: str, page_no: int) -> str | None:
    """Lazy render + cache for the browser's original-page preview."""
    out = cfg.ASSET_DIR / "pages" / doc_id / f"p{page_no:04d}.png"
    if out.exists():
        return cfg.asset_ref(out)
    src = resolve_source(rel_path)
    if not src.exists():
        return None
    doc = fitz.open(str(src))
    try:
        if page_no < 1 or page_no > doc.page_count:
            return None
        return render_page(doc, page_no, doc_id)
    finally:
        doc.close()


def _page_blocks(page: fitz.Page) -> list[dict]:
    """Layout blocks keep reading order + bbox for provenance."""
    blocks = []
    for b in page.get_text("blocks"):
        if len(b) < 5:
            continue
        x0, y0, x1, y1, txt = b[0], b[1], b[2], b[3], b[4]
        if not (txt or "").strip():
            continue
        blocks.append({"bbox": [round(v, 1) for v in (x0, y0, x1, y1)],
                       "text": txt.strip()})
    return blocks


def _parse_pdf(path: Path, doc_id: str, manifest_rec: dict,
               render_limit: int = 24) -> dict:
    doc = fitz.open(str(path))
    try:
        toc = doc.get_toc()
        pages = []
        for pn in range(1, doc.page_count + 1):
            page = doc.load_page(pn - 1)
            text = page.get_text("text") or ""
            q = page_quality(page, text)
            pages.append({
                "page_no": pn,
                "raw_text": text,
                "normalized_text": normalize_text(text),
                "blocks": _page_blocks(page),
                "quality": {k: v for k, v in q.items() if k != "lines"},
                "page_image": "",
                "source_ref": {
                    "document_id": doc_id,
                    "document_version_id": manifest_rec["document_version_id"],
                    "rel_path": manifest_rec["relative_path"],
                    "page": pn,
                    "unit_label": "page",
                },
            })

        def priority(p: dict) -> int:
            q, sig = p["quality"], p["quality"]["signals"]
            if q["review_status"] == "non_content_page":
                return 99
            # Highest: pages whose option/formula payload is missing from the
            # text layer -- the page image is the only remaining evidence.
            if sig.get("blank_option_lines") or sig.get("thin_option_lines"):
                return 0
            if q["review_status"] == "needs_formula_review":
                return 1
            if q["review_status"] == "needs_visual_review":
                return 2
            return 3

        order = sorted(range(len(pages)),
                       key=lambda i: (priority(pages[i]), pages[i]["page_no"]))
        critical = [i for i in order if priority(pages[i]) == 0][:60]
        chosen: list[int] = []
        for i in critical + order[:render_limit]:
            if i not in chosen and priority(pages[i]) != 99:
                chosen.append(i)
        for i in chosen:
            pages[i]["page_image"] = render_page(doc, pages[i]["page_no"], doc_id)
        return {
            "unit_label": "page", "unit_count": doc.page_count,
            "toc": toc, "pages": pages,
            "rendered_pages": [pages[i]["page_no"] for i in chosen],
        }
    finally:
        doc.close()


def _parse_docx(path: Path) -> dict:
    import docx
    d = docx.Document(str(path))
    units = []
    for i, p in enumerate(d.paragraphs):
        if p.text.strip():
            units.append({"unit_no": i + 1, "unit_label": "paragraph",
                          "raw_text": p.text,
                          "normalized_text": normalize_text(p.text),
                          "style": p.style.name if p.style else ""})
    for ti, t in enumerate(d.tables):
        rows = [[c.text for c in r.cells] for r in t.rows]
        units.append({"unit_no": f"table{ti + 1}", "unit_label": "table",
                      "raw_text": "\n".join(" | ".join(r) for r in rows),
                      "normalized_text": normalize_text(
                          "\n".join(" | ".join(r) for r in rows)),
                      "rows": rows})
    return {"unit_label": "paragraph", "unit_count": len(units), "units": units,
            "toc": []}


def _parse_pptx(path: Path) -> dict:
    import pptx
    pr = pptx.Presentation(str(path))
    units = []
    for i, slide in enumerate(pr.slides, start=1):
        texts = []
        for sh in slide.shapes:
            if sh.has_text_frame:
                texts.append(sh.text_frame.text)
            if getattr(sh, "has_table", False):
                texts.append("\n".join(" | ".join(c.text for c in r.cells)
                                       for r in sh.table.rows))
        raw = "\n".join(texts)
        units.append({"unit_no": i, "unit_label": "slide", "raw_text": raw,
                      "normalized_text": normalize_text(raw)})
    return {"unit_label": "slide", "unit_count": len(units), "units": units,
            "toc": []}


def _parse_text(path: Path, ext: str) -> dict:
    raw = ""
    for enc in ("utf-8", "utf-8-sig", "gb18030", "cp1252"):
        try:
            raw = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if ext in (".html", ".htm"):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw, "lxml")
        for t in soup(["script", "style"]):
            t.decompose()
        raw = soup.get_text("\n")
    units = [{"unit_no": i + 1, "unit_label": "line_block",
              "raw_text": blk, "normalized_text": normalize_text(blk)}
             for i, blk in enumerate(re.split(r"\n\s*\n", raw)) if blk.strip()]
    return {"unit_label": "block", "unit_count": len(units), "units": units,
            "toc": []}


def _parse_image(path: Path) -> dict:
    return {"unit_label": "image", "unit_count": 1,
            "units": [{"unit_no": 1, "unit_label": "image", "raw_text": "",
                       "normalized_text": ""}],
            "toc": [], "needs_visual_review": True}


def _split_qa_chunks(pages: list[dict]) -> list[dict]:
    """Review notes: separate 题干 / 选项 / 答案 / 解析 across page boundaries.

    Page 203 of the real 212-page note opens with the *previous* question's
    options and answer, so splitting per page would break a question. Here the
    question boundary is the '题N' marker, and every chunk keeps ALL pages it
    touches. Empty formula options are preserved as explicit placeholders.
    """
    items: list[dict] = []
    cur: dict | None = None
    for pg in pages:
        pn = pg["page_no"]
        for ln in clean_lines(pg["raw_text"]):
            qm = re.match(r"^题\s*(\d+)\s*$", ln)
            am = re.match(r"^(答案|参考答案|标准答案|解析|解答)\s*[:：]\s*(.*)$", ln)
            om = OPTION_LINE.match(ln)
            if qm:
                if cur:
                    items.append(cur)
                cur = {"kind": "question", "no": f"题{qm.group(1)}",
                       "stem": "", "options": [], "answer": "",
                       "explanation": "", "page": pn, "pages": [pn],
                       "flags": []}
                continue
            if cur is None:
                continue
            if pn not in cur["pages"]:
                cur["pages"].append(pn)
            if am:
                if am.group(1) in ("答案", "参考答案", "标准答案"):
                    cur["answer"] += (" " if cur["answer"] else "") + am.group(2).strip()
                    cur["flags"].append("answer_is_source_statement")
                else:
                    cur["explanation"] += (" " if cur["explanation"] else "") + am.group(2).strip()
                continue
            if om:
                payload = om.group(2).strip()
                missing = not payload or len(payload) <= 3
                cur["options"].append({"key": om.group(1).upper(),
                                       "text": payload,
                                       "missing_formula": missing})
                if missing:
                    cur["flags"].append("formula_option_missing_in_text_layer")
                continue
            if not cur["answer"] and not cur["explanation"] and not cur["options"]:
                cur["stem"] += (" " if cur["stem"] else "") + ln
            elif cur["explanation"]:
                cur["explanation"] += " " + ln
            elif cur["answer"]:
                cur["answer"] += " " + ln
    if cur:
        items.append(cur)
    for c in items:
        c["cross_page"] = len(c["pages"]) > 1
        if c["cross_page"]:
            c["flags"].append("question_spans_pages")
        c["flags"] = sorted(set(c["flags"]))
    return items


def build_chunks(parsed: dict, manifest_rec: dict, doc_id: str) -> tuple[list[dict], dict]:
    """Return (chunks, quality_summary)."""
    units = parsed.get("pages") or parsed.get("units") or []
    chunks: list[dict] = []
    status_counter: Counter = Counter()
    is_note = manifest_rec["source_category"] in (
        "review_note", "exam_prep_note", "review_note_by_chapter")

    qa_items: list[dict] = []
    if is_note and parsed.get("pages"):
        qa_items = _split_qa_chunks(units)
        page_images = {pg["page_no"]: pg.get("page_image", "") for pg in units}
        for i, q in enumerate(qa_items):
            flags = q["flags"]
            quality = {"review_status": "needs_formula_review"
                       if "formula_option_missing_in_text_layer" in flags
                       else "auto_ok",
                       "flags": flags,
                       "answer_separated": True,
                       "answer_status_hint": "source_statement_ai_answer_candidate",
                       "note": "笔记中『答案/标准答案/老师重点』是来源陈述，"
                               "未经核查不得升级为已核验答案",
                       "reasons": (["公式选项在文本层为空，已保留占位并指向原页图，"
                                    "不得按普通文字题发布"] if
                                   "formula_option_missing_in_text_layer" in flags else [])}
            chunks.append({
                "chunk_id": f"{doc_id}_q{i + 1:04d}",
                "document_id": doc_id,
                "document_version_id": manifest_rec["document_version_id"],
                "unit_refs": [{"page": p} for p in q["pages"]],
                "printed_page": "-".join(str(p) for p in q["pages"]),
                "kind": "question",
                "title": q["no"],
                "content_raw": json.dumps(q, ensure_ascii=False),
                "content_normalized": normalize_text(
                    q["stem"] + " " + " ".join(o["text"] for o in q["options"])),
                "assets": [page_images.get(p, "") for p in q["pages"]
                           if page_images.get(p)],
                "quality": quality,
                "source_refs": [{
                    "document_id": doc_id,
                    "document_version_id": manifest_rec["document_version_id"],
                    "rel_path": manifest_rec["relative_path"],
                    "page": p, "unit_label": "page", "chunk_label": q["no"]}
                    for p in q["pages"]],
                "chapter": "", "kp_ids": [], "review_status": quality["review_status"],
            })

    # Page/slide level chunks
    for u in units:
        if "page_no" in u:
            no, label = u["page_no"], "page"
        else:
            no, label = u["unit_no"], u["unit_label"]
        q = u.get("quality") or {}
        review = q.get("review_status", "auto_ok" if parsed["unit_label"] != "image"
                       else "needs_visual_review")
        status_counter[review] += 1
        if review == "non_content_page":
            continue  # 草稿区不生成知识 chunk
        raw = u.get("raw_text", "")
        if not raw.strip():
            continue
        chunks.append({
            "chunk_id": f"{doc_id}_u{str(no).zfill(4)}",
            "document_id": doc_id,
            "document_version_id": manifest_rec["document_version_id"],
            "unit_refs": [{"page": no} if label == "page" else {label: no}],
            "printed_page": str(no),
            "kind": "prose",
            "title": f"{label} {no}",
            "content_raw": raw,
            "content_normalized": u.get("normalized_text", normalize_text(raw)),
            "assets": [u["page_image"]] if u.get("page_image") else [],
            "quality": {k: v for k, v in q.items() if k != "lines"},
            "source_refs": [{
                "document_id": doc_id,
                "document_version_id": manifest_rec["document_version_id"],
                "rel_path": manifest_rec["relative_path"],
                "page": no, "unit_label": label}],
            "chapter": "", "kp_ids": [],
            "review_status": review,
        })

    # --- Cross-page question merge (real case: exam p3 题干 -> p4 图/选项) ---
    if parsed.get("pages"):
        for i in range(len(units) - 1):
            a, b = units[i], units[i + 1]
            ta = a["raw_text"]
            tb = b["raw_text"]
            a_lines = clean_lines(ta)
            b_lines = clean_lines(tb)
            last = a_lines[-1] if a_lines else ""
            first_opts = [l for l in b_lines[:8] if OPTION_LINE.match(l)]
            # Locate the LAST question stem on page A. In the real exam, p3
            # ends with 题5 (PCA) whose figure+options are at the top of p4.
            last_q_idx = -1
            for idx, l in enumerate(a_lines):
                if re.match(r"^\s*(?:\d+\s*[\.、）\)]|题\s*\d+)", l):
                    last_q_idx = idx
            tail = a_lines[last_q_idx:] if last_q_idx >= 0 else a_lines[-3:]
            options_after_stem = [l for l in tail if OPTION_LINE.match(l)]
            has_qtail = bool(re.search(
                r"请问|多少|下列哪项|最少选择|哪个选项|正确的是|合适|？|\?", last))
            if has_qtail and first_opts and not options_after_stem:
                merged_text = "\n".join(a_lines[-3:] + ["[跨页续]"] + b_lines[:6])
                chunks.append({
                    "chunk_id": f"{doc_id}_merge_p{a['page_no']}_p{b['page_no']}",
                    "document_id": doc_id,
                    "document_version_id": manifest_rec["document_version_id"],
                    "unit_refs": [{"page": a["page_no"]}, {"page": b["page_no"]}],
                    "printed_page": f"{a['page_no']}-{b['page_no']}",
                    "kind": "cross_page_question",
                    "title": f"跨页题目 p{a['page_no']}→p{b['page_no']}",
                    "content_raw": merged_text,
                    "content_normalized": normalize_text(merged_text),
                    "assets": [x for x in (a.get("page_image"), b.get("page_image")) if x],
                    "quality": {
                        "review_status": "needs_visual_review",
                        "flags": ["cross_page_merged"],
                        "reasons": ["题干与图/选项分布在不同页，已合并并保留全部来源页；"
                                    "纯文本不足以重建图形，需看原页图"],
                        "merge_evidence": {"prev_page_last_line": last,
                                           "next_page_first_options": first_opts[:4]},
                    },
                    "source_refs": [
                        {"document_id": doc_id, "rel_path": manifest_rec["relative_path"],
                         "page": a["page_no"], "unit_label": "page"},
                        {"document_id": doc_id, "rel_path": manifest_rec["relative_path"],
                         "page": b["page_no"], "unit_label": "page"}],
                    "chapter": "", "kp_ids": [],
                    "review_status": "needs_visual_review",
                })
                status_counter["needs_merge_check"] += 1

    return chunks, {"by_review_status": dict(status_counter),
                    "qa_items_found": len(qa_items),
                    "chunks": len(chunks)}


def parse_one(db: Session, rec: dict, *, render_limit: int = 24,
              force: bool = False) -> dict:
    path = resolve_source(rec["relative_path"])
    doc_id = "doc_" + rec["sha256"][:14]
    version_id = "ver_" + rec["sha256"][:14] + "_" + cfg.PARSER_CONFIG_VERSION.replace(".", "")
    mrec = dict(rec, document_version_id=version_id)

    existing = db.execute(
        select(E.DocumentVersion).where(
            E.DocumentVersion.sha256 == rec["sha256"],
            E.DocumentVersion.parser_config_version == cfg.PARSER_CONFIG_VERSION)
    ).scalars().first()
    if existing and not force and existing.parse_status in ("succeeded", "partial"):
        return {"document_id": doc_id, "status": "skipped_cached",
                "parse_status": existing.parse_status,
                "rel_path": rec["relative_path"], "notes": [],
                "chunks": 0}

    result: dict[str, Any] = {"document_id": doc_id,
                              "document_version_id": version_id,
                              "rel_path": rec["relative_path"],
                              "source_category": rec["source_category"],
                              "status": "", "parse_status": "", "notes": []}
    ext = rec["extension"]
    try:
        if ext == ".pdf" or rec["sniffed_type"] == "pdf":
            parsed = _parse_pdf(path, doc_id, mrec, render_limit=render_limit)
        elif ext == ".docx":
            parsed = _parse_docx(path)
        elif ext == ".pptx":
            parsed = _parse_pptx(path)
        elif ext in (".txt", ".md", ".markdown", ".html", ".htm", ".py", ".csv", ".json", ".ipynb"):
            parsed = _parse_text(path, ext)
        elif rec["sniffed_type"] in ("jpeg", "png", "image"):
            parsed = _parse_image(path)
        elif ext in (".doc", ".ppt", ".xls") or rec["readability"] == "unsupported_legacy":
            raise ValueError("旧版 OLE2 Office 格式：需另存为 docx/pptx 后处理")
        elif ext == ".zip":
            raise ValueError("归档文件：已登记成员清单，不自动展开为知识内容")
        else:
            raise ValueError("不支持的格式")
        chunks, qsummary = build_chunks(parsed, mrec, doc_id)

        out_dir = cfg.PARSED_DIR / doc_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "meta.json").write_text(
            json.dumps({"doc_id": doc_id, "version_id": version_id,
                        "rel_path": rec["relative_path"],
                        "sha256": rec["sha256"],
                        "source_category": rec["source_category"],
                        "unit_label": parsed["unit_label"],
                        "unit_count": parsed["unit_count"],
                        "toc_entries": len(parsed.get("toc") or []),
                        "quality_summary": qsummary,
                        "parsed_utc": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"),
                        "parser_config_version": cfg.PARSER_CONFIG_VERSION,
                        "read_only_source": True},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "units.json").write_text(
            json.dumps(parsed, ensure_ascii=False), encoding="utf-8")
        (out_dir / "chunks.json").write_text(
            json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")
        raw_all = "\n\n".join(
            f"--- {parsed['unit_label']} {u.get('page_no', u.get('unit_no'))} ---\n"
            + u.get("raw_text", "") for u in (parsed.get("pages") or parsed.get("units") or []))
        (out_dir / "raw_text.txt").write_text(raw_all, encoding="utf-8")
        (out_dir / "normalized_text.txt").write_text(
            normalize_text(raw_all), encoding="utf-8")

        dv = existing or E.DocumentVersion(
            document_version_id=version_id, document_id=doc_id,
            sha256=rec["sha256"], rel_path=rec["relative_path"],
            size_bytes=rec["size_bytes"], unit_label=parsed["unit_label"],
            unit_count=parsed["unit_count"],
            source_category=rec["source_category"],
            readability=rec["readability"],
            parser_config_version=cfg.PARSER_CONFIG_VERSION,
        )
        dv.parse_status = "partial" if any(
            v for k, v in qsummary["by_review_status"].items()
            if k.startswith("needs_")) else "succeeded"
        dv.quality_summary = qsummary
        db.add(dv)
        d = db.get(E.Document, doc_id) or E.Document(
            document_id=doc_id, rel_path=rec["relative_path"],
            source_category=rec["source_category"])
        d.current_version_id = version_id
        db.add(d)
        # Replace this version's chunks (idempotent re-run)
        old = db.execute(select(E.Chunk).where(
            E.Chunk.document_version_id == version_id)).scalars().all()
        for c in old:
            db.delete(c)
        for c in chunks:
            db.add(E.Chunk(**c))
        db.commit()
        result.update(status="ok", parse_status=dv.parse_status,
                      unit_count=parsed["unit_count"],
                      toc_entries=len(parsed.get("toc") or []),
                      chunks=len(chunks), quality_summary=qsummary,
                      parsed_dir=cfg.storage_ref(out_dir))
    except Exception as e:
        db.rollback()
        dv = existing or E.DocumentVersion(
            document_version_id=version_id, document_id=doc_id,
            sha256=rec["sha256"], rel_path=rec["relative_path"],
            size_bytes=rec["size_bytes"], source_category=rec["source_category"],
            readability=rec["readability"],
            parser_config_version=cfg.PARSER_CONFIG_VERSION)
        dv.parse_status = "failed"
        dv.quality_summary = {"error": f"{type(e).__name__}: {e}"}
        db.add(dv)
        db.commit()
        result.update(status="failed", parse_status="failed",
                      notes=[f"{type(e).__name__}: {e}"])
    return result


def run_parse(*, limit: int | None = None, only: list[str] | None = None,
              render_limit: int = 24, force: bool = False) -> dict:
    manifest = json.loads(
        (cfg.MANIFEST_DIR / "materials_manifest.json").read_text(encoding="utf-8"))
    recs = [r for r in manifest["records"]
            if r["source_category"] != "workspace_metadata"]
    if only:
        recs = [r for r in recs if any(o in r["relative_path"] for o in only)]
    if limit:
        recs = recs[:limit]
    results = []
    db = SessionLocal()
    try:
        for r in recs:
            results.append(parse_one(db, r, render_limit=render_limit,
                                     force=force))
    finally:
        db.close()
    summary = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "parser_config_version": cfg.PARSER_CONFIG_VERSION,
        "attempted": len(results),
        "succeeded": sum(1 for x in results if x["status"] == "ok" and
                         x["parse_status"] == "succeeded"),
        "partial": sum(1 for x in results if x["parse_status"] == "partial"),
        "failed": sum(1 for x in results if x["status"] == "failed"),
        "skipped_cached": sum(1 for x in results if x["status"] == "skipped_cached"),
        "total_chunks": sum(x.get("chunks", 0) for x in results),
        "failures": [{"path": x["rel_path"], "notes": x["notes"]}
                     for x in results if x["status"] == "failed"],
    }
    (cfg.MANIFEST_DIR / "processing_state.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary, "results": results}
