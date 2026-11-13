"""资料显示名的统一规则（全站唯一口径）。

规则：`类型 · 主题（学期/年份）`
  * 类型优先取「按正文内容判别」的 resolved_type，其次取资料索引的
    source_category，最后才按文件名关键字兜底并标注「未核实」；
  * 主题去掉前导编号（`1.`、`12-`）、尾部年份与验证用后缀；
  * 学期/年份从文件名中抽取，只作为括号里的限定信息；
  * **绝不改名磁盘文件**：原文件名始终保留在 `filename / rel_path`，
    这里只生成用于页面展示的 `display_name`。
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

KIND_LABELS = {
    "exam_paper": "考试卷",
    "slides": "老师课件",
    "notes": "笔记",
    "code": "实验代码",
    "other": "其他资料",
}

UPLOAD_TYPE_TO_KIND = {
    "exam_paper": "exam_paper", "slides": "slides",
    "notes": "notes", "code": "code",
}

DOC_CATEGORY_TO_KIND = {
    "past_exam": "exam_paper",
    "course_slides": "slides", "page_screenshot": "slides",
    "classroom_photo": "slides", "class_transcript": "slides",
    "review_note": "notes", "review_note_by_chapter": "notes",
    "exam_prep_note": "notes", "structure_note": "notes",
    "uploaded_other": "other", "study_html": "other", "other": "other",
}

_EXTS = (".pdf", ".docx", ".pptx", ".xlsx", ".ipynb", ".html",
         ".markdown", ".md", ".txt", ".py", ".png", ".jpg", ".jpeg")
_TERM = re.compile(r"(20\d{2})\s*[-–]\s*(20\d{2}|\d{1,2})")
_YEAR = re.compile(r"20\d{2}(?!\d)")
_LEAD_NO = re.compile(r"^\s*\d+\s*[.、\-_]\s*")
_SEMESTER = re.compile(r"^\s*[-–]\s*\d+\s*")
_VOLUME = re.compile(r"[（(]\s*([AB])\s*[)）]", re.I)
_NOISE = re.compile(r"(?:Asever|验证用|已核验|验证|副本|copy|final|desktop)", re.I)
_SEPARATORS = re.compile(r"[\s._\-–]+")


def classify(name: str, material_type: str = "",
             doc_category: str = "") -> tuple[str, str]:
    """返回 (kind, 依据说明)。"""
    if material_type in UPLOAD_TYPE_TO_KIND:
        return UPLOAD_TYPE_TO_KIND[material_type], "按正文内容判别的资料类型"
    if doc_category in DOC_CATEGORY_TO_KIND:
        return DOC_CATEGORY_TO_KIND[doc_category], "资料索引中的来源分类"
    if any(word in name for word in ("期末", "考试", "试卷", "A卷", "B卷", "真题")):
        return "exam_paper", "按文件名关键字推断（未核实）"
    if any(word in name for word in ("笔记", "note", "复习资料")):
        return "notes", "按文件名关键字推断（未核实）"
    if any(word in name for word in ("课件", "lecture", "slide")):
        return "slides", "按文件名关键字推断（未核实）"
    return "other", "资料类型未核实"


def normalize(name: str, material_type: str = "",
              doc_category: str = "") -> dict:
    """生成统一显示名；返回完整拆解，便于前端核对规则。"""
    raw = PurePosixPath(str(name or "未命名资料")).name
    stem = raw
    for suffix in _EXTS:
        if stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
            break

    match = _TERM.search(stem)
    term = ""
    if match:
        term = match.group(0).replace(" ", "")
        stem = stem[:match.start()] + " " + stem[match.end():]
        # 「2019-2020-2」里最后的 -2 是学期序号，不作为主题的一部分。
        stem = re.sub(r"[-–]\s*\d\s*", " ", stem)
    else:
        year = _YEAR.search(stem)
        if year:
            term = year.group(0)
            stem = stem[:year.start()] + " " + stem[year.end():]

    stem = _LEAD_NO.sub("", stem)
    stem = _VOLUME.sub(r" \1卷", stem)
    stem = _NOISE.sub(" ", stem)
    topic = _SEPARATORS.sub(" ", stem).strip()[:28] or "未命名资料"

    kind, basis = classify(raw, material_type, doc_category)
    return {
        "display_name": (f"{KIND_LABELS[kind]} · {topic}"
                         + (f"（{term}）" if term else "")),
        "topic": topic,
        "term": term,
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "kind_basis": basis,
        "raw_name": raw,
    }
