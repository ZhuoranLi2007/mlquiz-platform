"""Material type detection + exam-paper question extraction.

Used by the upload pipeline. Everything here is deterministic text analysis of
the parsed page text: no model call, so it works with no API key configured and
its output can be re-derived at any time.

The real target paper is the 2019-2020(A) exam, whose text layer looks like:

    一 、名词解释（本题共15 分，每小题3 分）
    1. SVM
    二 、是非题（本题共10 分，每小题2 分）
    1. （    ）Logistic 回归即可以解决回归问题，又可以解决分类问题。
    三、选择题（本题共20 分，每小题4 分）
    1. （    ）机器学习指一段计算机程序……
    A）可视化所有历史经验数据E
    A. 类先验概率   B. 后验概率   C. 类条件概率   D
"""
from __future__ import annotations

import re
from pathlib import Path

# --------------------------------------------------------------- type detect
MATERIAL_TYPES = ("exam_paper", "slides", "notes", "code", "other")

TYPE_LABELS = {
    "exam_paper": "试卷",
    "slides": "课件",
    "notes": "复习笔记",
    "code": "实验代码",
    "other": "其他",
}

_CODE_EXT = {".py", ".ipynb", ".java", ".cpp", ".cc", ".c", ".js", ".ts", ".sql",
             ".r", ".m", ".sh", ".ps1", ".html", ".css", ".json", ".yaml", ".yml"}
_EXAM_NAME_HINTS = ("试卷", "期末", "期中", "考试", "真题", "模拟", "quiz", "exam",
                    "midterm", "final")
_NOTE_NAME_HINTS = ("笔记", "复习", "总结", "考点", "提纲", "note", "summary",
                    "review", "cheatsheet")
_SLIDE_NAME_HINTS = ("课件", "讲义", "lecture", "slides", "ppt", "chapter", "第")
_EXAM_CONTENT_HINTS = ("单项选择题", "多项选择题", "选择题", "名词解释", "是非题",
                       "判断题", "填空题", "简答题", "计算题", "综合题",
                       "考试时间", "得分", "学号", "姓名", "共8 页", "共 8 页")


def detect_material_type(filename: str, text: str = "",
                         n_questions: int = 0) -> tuple[str, str]:
    """Return (type, human-readable reason). Filename + content, deterministic."""
    name = (filename or "").lower()
    ext = Path(name).suffix
    sample = (text or "")[:20000]

    if ext in _CODE_EXT:
        return "code", f"文件扩展名为 {ext}，按实验代码处理"

    exam_hits = [h for h in _EXAM_CONTENT_HINTS if h in sample]
    # A section header plus numbered questions is the strong signal.
    has_section = bool(re.search(
        r"[一二三四五六七八九十]\s*[、.．]\s*[^\n]{0,12}?题[^\n]{0,20}?[（(]",
        sample))
    numbered = len(re.findall(r"(?:^|\n)\s*\d{1,2}\s*[.．、]\s*\S", sample))
    if has_section and numbered >= 3:
        return ("exam_paper",
                f"识别到题型分区标题与 {numbered} 个带编号的题目"
                + (f"（关键词：{'、'.join(exam_hits[:3])}）" if exam_hits else ""))
    if len(exam_hits) >= 3 and numbered >= 3:
        return "exam_paper", f"识别到 {len(exam_hits)} 个试卷特征词与 {numbered} 个编号题目"

    if ext == ".pptx":
        return "slides", "PowerPoint 文件，按课件处理"
    # At upload time only the filename is available: an explicit 试卷/期末/真题
    # name is already a usable signal, and the content check runs again after
    # parsing (process_upload refines the type there).
    exam_names = [h for h in _EXAM_NAME_HINTS if h in name]
    if exam_names and (numbered >= 1 or not sample.strip()):
        return ("exam_paper",
                "文件名含试卷关键词（" + "、".join(exam_names[:3]) + "）"
                + ("且存在编号题目" if numbered >= 1
                   else "；正文解析后将再次确认"))

    if any(h in name for h in _SLIDE_NAME_HINTS):
        return "slides", "文件名含课件/讲义关键词"
    if any(h in name for h in _NOTE_NAME_HINTS):
        return "notes", "文件名含笔记/复习关键词"
    return "other", "未命中试卷/课件/笔记/代码特征，按其他资料处理"


# ------------------------------------------------------------ text normalise
_PAGE_HEADER = re.compile(r"^第\s*\d+\s*页\s*共\s*\d+\s*页\s*$")
_NOISE_LINES = {"草稿区", "草  稿  区", "草 稿 区", "得分", "得 分", "得分：",
                "试卷第", "注：", "注意事项"}


def _clean_lines(raw: str) -> list[str]:
    out = []
    for ln in (raw or "").splitlines():
        s = ln.replace("\u3000", " ").strip()
        if not s:
            continue
        compact = re.sub(r"\s+", "", s)
        if _PAGE_HEADER.match(compact):
            continue
        if compact in _NOISE_LINES or compact.startswith("草稿区"):
            continue
        if re.fullmatch(r"[_\-\s·—]{4,}", compact):
            continue
        out.append(s)
    return out


# ---------------------------------------------------------------- extraction
SECTION_RE = re.compile(
    r"^[\s#>*_\-]*([一二三四五六七八九十]+)\s*[、.．]\s*([^\n（(]{1,20}?)"
    r"(?:[（(]([^）)]*)[）)])?[\s*_]*$")
QNUM_RE = re.compile(r"^\s*(\d{1,2})\s*[.．、]\s*(.*)$")
OPT_RE = re.compile(r"(?:^|(?<=\s))([A-H])\s*[）).．、]\s*")
TRAILING_KEY = re.compile(r"\s([A-H])\s*$")
PER_Q_SCORE = re.compile(r"每\s*(?:小)?题\s*(\d+(?:\.\d+)?)\s*分")
TOTAL_SCORE = re.compile(r"本题共\s*(\d+(?:\.\d+)?)\s*分")
BLANK_MARK = re.compile(r"[（(]\s*[)）]")

SECTION_TYPE = (
    ("名词解释", "term"),
    ("是非", "true_false"),
    ("判断", "true_false"),
    ("多项选择", "multi_choice"),
    ("多选", "multi_choice"),
    ("选择", "single_choice"),
    ("填空", "short_answer"),
    ("简答", "short_answer"),
    ("计算", "derivation"),
    ("证明", "derivation"),
    ("推导", "derivation"),
    ("分析", "comprehensive"),
    ("综合", "comprehensive"),
    ("论述", "comprehensive"),
)


def _type_of_section(title: str) -> str:
    for kw, t in SECTION_TYPE:
        if kw in title:
            return t
    return "unknown"


def _split_options(line: str) -> tuple[str, list[list[str]]] | None:
    """Split a line into (prefix, [[key, text], ...]).

    Handles both `A）text` (one option per line) and
    `A. 类先验概率   B. 后验概率   C. 类条件概率   D` (several per line, the last
    one a bare letter). Returns None when the line is not an option line.
    """
    marks = [(m.group(1), m.start(), m.end()) for m in OPT_RE.finditer(line)]
    seq: list[tuple[str, int, int]] = []
    if marks:
        seq = [marks[0]]
        for mk in marks[1:]:
            if ord(mk[0]) == ord(seq[-1][0]) + 1:
                seq.append(mk)
    trailing = TRAILING_KEY.search(line)
    if trailing:
        next_key = chr(ord(seq[-1][0]) + 1) if seq else "A"
        if trailing.group(1) == next_key:
            seq.append((trailing.group(1), trailing.start(), trailing.end()))
    if not seq:
        return None
    prefix = line[:seq[0][1]].strip()
    opts = []
    for i, (k, _s, e) in enumerate(seq):
        end = seq[i + 1][1] if i + 1 < len(seq) else len(line)
        opts.append([k, line[e:end].strip()])
    return prefix, opts


def extract_paper(parsed: dict) -> dict:
    """Extract sections + questions from a parsed PDF (page-level raw text).

    Returns {"sections": [...], "questions": [...], "stats": {...}}.
    Question `no` keeps the original numbering, e.g. "三 第2题".
    """
    pages = parsed.get("pages") or parsed.get("units") or []
    sections: list[dict] = []
    questions: list[dict] = []

    cur_section: dict | None = None
    cur_q: dict | None = None
    order = 0

    def close_q() -> None:
        nonlocal cur_q
        if not cur_q:
            return
        q = cur_q
        q["stem"] = re.sub(r"\s+", " ", q["stem"]).strip()
        q["stem"] = BLANK_MARK.sub("（  ）", q["stem"]).strip()
        if q["options"]:
            q["question_type"] = ("multi_choice"
                                 if q["question_type"] == "multi_choice"
                                 else q["question_type"])
        for o in q["options"]:
            o["text"] = re.sub(r"\s+", " ", o["text"]).strip()
        if not q["stem"] and not q["options"]:
            cur_q = None
            return   # a bare number line such as a page artefact
        questions.append(q)
        cur_q = None

    for pg in pages:
        page_no = pg.get("page_no", pg.get("unit_no", 0))
        for line in _clean_lines(pg.get("raw_text", "")):
            sm = SECTION_RE.match(line)
            # A section header must look like a heading, not a sentence.
            if sm and len(line) <= 40 and ("题" in line or "分" in line):
                close_q()
                title = re.sub(r"\s+", "", sm.group(2))
                extra = sm.group(3) or ""
                per_q = PER_Q_SCORE.search(extra)
                total = TOTAL_SCORE.search(extra)
                sec = {
                    "section_index": len(sections) + 1,
                    "section": f"{sm.group(1)}、{title}",
                    "title": title,
                    "raw_header": line,
                    "declared_total_score": float(total.group(1)) if total else None,
                    "declared_per_question_score": (float(per_q.group(1))
                                                    if per_q else None),
                    "question_type": _type_of_section(title),
                    "pages": [page_no],
                }
                sections.append(sec)
                cur_section = sec
                continue

            qm = QNUM_RE.match(line)
            if qm:
                close_q()
                order += 1
                sec = cur_section or {}
                qtype = sec.get("question_type", "unknown")
                cur_q = {
                    "order": order,
                    "section": sec.get("section", ""),
                    "section_index": sec.get("section_index", 0),
                    "no": (f"{sec.get('section', '').split('、')[0]} 第{qm.group(1)}题"
                           if sec else f"第{qm.group(1)}题"),
                    "source_no": qm.group(1),
                    "question_type": qtype,
                    "stem": qm.group(2).strip(),
                    "options": [],
                    "score": sec.get("declared_per_question_score") or 1.0,
                    "pages": [page_no],
                    "flags": [],
                }
                if sec and page_no not in sec["pages"]:
                    sec["pages"].append(page_no)
                # Options may share the same line as the stem.
                so = _split_options(cur_q["stem"])
                if so and len(so[1]) >= 2:
                    cur_q["stem"] = so[0]
                    for k, t in so[1]:
                        cur_q["options"].append({"key": k, "text": t})
                continue

            if cur_q is None:
                continue
            if page_no not in cur_q["pages"]:
                cur_q["pages"].append(page_no)

            # Option line. A line that STARTS with a marker is always options;
            # a mid-line marker run is only accepted before any option exists.
            so = _split_options(line)
            if so and (so[0] == "" or not cur_q["options"]):
                if so[0] and not cur_q["options"]:
                    cur_q["stem"] += " " + so[0]
                for k, t in so[1]:
                    cur_q["options"].append({"key": k, "text": t})
                continue
            if cur_q["options"]:
                # Continuation of the last option's text (wrapped line).
                cur_q["options"][-1]["text"] += " " + line
            else:
                cur_q["stem"] += " " + line

    close_q()

    # Post-process: score fallback + flags.
    for q in questions:
        if q["question_type"] == "single_choice" and len(q["options"]) > 4:
            q["flags"].append("more_than_four_options")
        if q["question_type"] in ("single_choice", "multi_choice") and not q["options"]:
            q["flags"].append("options_missing_in_text_layer")
        if not q["stem"]:
            q["flags"].append("empty_stem")
        if len(q["pages"]) > 1:
            q["flags"].append("question_spans_pages")
        q["flags"] = sorted(set(q["flags"]))

    # If a section declared a total but no per-question score, derive it.
    for sec in sections:
        qs = [q for q in questions if q["section_index"] == sec["section_index"]]
        if qs and (not sec["declared_per_question_score"]) and \
                sec["declared_total_score"]:
            sec["derived_per_question_score"] = round(
                sec["declared_total_score"] / len(qs), 3)
            for q in qs:
                q["score"] = sec["derived_per_question_score"]

    return {
        "sections": sections,
        "questions": questions,
        "stats": {
            "sections": len(sections),
            "questions": len(questions),
            "by_type": _count_by(questions, "question_type"),
            "total_score": round(sum(q["score"] for q in questions), 3),
        },
    }


def _count_by(items: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for it in items:
        out[it.get(key, "")] = out.get(it.get(key, ""), 0) + 1
    return out
