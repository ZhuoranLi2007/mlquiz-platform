"""Student-facing DTO whitelist. This is the single choke point that keeps
answers, rubrics, explanations (and, in quiz mode, answer-revealing material
links) off the wire before submission.

Any new API that returns a question MUST pass through `student_view`.
"""
from __future__ import annotations

import re
from typing import Any

# 名词解释题的题干往往只有词条本身（如「SVM」），单独显示会被误认为题目残缺。
# 统一在 DTO 层补一句引导语，题干原文保持不动。
_TERM_PREFIX = "请解释下列名词："
_TERM_ALREADY_ASKED = re.compile(r"^(名词解释|请解释|请简述|请说明|解释下列|什么是|试述)")

PUBLIC_FIELDS = (
    "question_id", "question_version", "origin", "original_exam_id",
    "original_question_no", "question_type", "stem", "options", "stem_images",
    # V2.3：中英双语展示字段（都不是答案内容，提交前即可返回）
    "stem_zh", "original_language", "translation_status",
    "score", "chapter_id", "main_kp_id", "aux_kp_ids", "cognitive_level", "difficulty",
    "estimated_minutes", "question_family_id", "parent_question_id",
    "review_status", "answer_status", "active",
    # 有效题型：与评分层同一口径（见 student_view 里的说明）
    "question_type_effective",
    # provenance metadata (NOT answer content): lets the UI label an answer
    # "AI参考答案" without revealing the answer itself before submission.
    "answer_origin", "answer_meta", "answer_fixed_at",
)
FORBIDDEN_FIELDS = ("answer_payload", "rubric", "explanation", "notes",
                    "created_by", "row_id")


def display_prompt_prefix(question_type: str, stem: str) -> str:
    """名词解释题缺少提问语时补一句，题干原文不改。"""
    if (question_type or "") != "term":
        return ""
    text = (stem or "").strip()
    if not text or _TERM_ALREADY_ASKED.match(text):
        return ""
    return _TERM_PREFIX


def _safe_refs(refs: list, reveal: bool) -> list:
    """Before submission: keep textual provenance only; drop clickable asset
    links that could open a page containing the answer."""
    out = []
    for r in refs or []:
        if not isinstance(r, dict):
            continue
        safe = {k: r.get(k) for k in
                ("document_id", "document_version_id", "rel_path", "page",
                 "unit_label", "label", "chunk_id") if r.get(k) not in (None, "")}
        if reveal:
            safe["assets"] = r.get("assets", [])
            safe["chunk_id"] = r.get("chunk_id", "")
        out.append(safe)
    return out


CHOICE_TYPES = ("single_choice", "multi_choice")


def normalize_options(options: Any, question_type: str = "") -> tuple[list[dict], bool, str]:
    """统一选项结构：`{key,label,text,content,text_zh,missing_text}`。

    旧数据里选项可能是 `{key,text}`、`{label,content}` 或只有 key 没正文。
    这里统一成一套字段，并判断"选择题是否缺少可用选项"。

    V3.4（RC-5）：以前正文为空就直接 `continue` 丢掉这项，前端拿到空数组，
    于是"题干正常、整个选项区域消失"。任务书要求公式渲染失败时保留原文，
    选项同理：**保留占位并标记 `missing_text`**，由前端显示"选项正文缺失"，
    而不是让整块无声消失。仍然不做任何内容编造。
    """
    out: list[dict] = []
    for index, raw in enumerate(options or []):
        if isinstance(raw, dict):
            label = str(raw.get("key") or raw.get("label")
                        or chr(65 + index)).strip()
            text = str(raw.get("text") or raw.get("content") or "").strip()
            text_zh = str(raw.get("text_zh") or "").strip()
        elif isinstance(raw, str):
            # 历史数据里也有 ["选项一", "选项二"] 这种纯字符串形态
            label = chr(65 + index)
            text, text_zh = raw.strip(), ""
        else:
            continue
        body = text or text_zh
        out.append({"key": label, "label": label, "text": body,
                    "content": body, "text_zh": text_zh,
                    "missing_text": not bool(body)})
    missing = [o["key"] for o in out if o["missing_text"]]
    incomplete = question_type in CHOICE_TYPES and (len(out) < 2 or bool(missing))
    if incomplete and missing:
        hint = f"选项 {'/'.join(missing)} 正文缺失，该题待修复，暂不可作答"
    elif incomplete:
        hint = "该题选项缺失，暂不可作答"
    else:
        hint = ""
    return out, incomplete, hint


def student_view(src: dict | Any, *, reveal: bool = False,
                 mode: str = "practice") -> dict:
    """`src` may be an ORM Question or a paper-item snapshot dict."""
    if not isinstance(src, dict):
        getter = lambda k, d=None: getattr(src, k, d)  # noqa: E731
        data = {f: getter(f) for f in PUBLIC_FIELDS}
        refs = getter("source_refs", []) or []
        if reveal:
            data["answer_payload"] = getter("answer_payload", {}) or {}
            data["explanation"] = getter("explanation", "") or ""
            data["rubric"] = getter("rubric", {}) or {}
            data["answer_origin"] = getter("answer_origin", "none")
    else:
        data = {f: src.get(f) for f in PUBLIC_FIELDS if f in src}
        refs = src.get("source_refs", []) or []
        if reveal:
            data["answer_payload"] = src.get("answer_payload", {}) or {}
            data["explanation"] = src.get("explanation", "") or ""
            data["rubric"] = src.get("rubric", {}) or {}
            data["answer_origin"] = src.get("answer_origin", "none")

    if isinstance(src, dict):
        from backend.app.services.classroom_content import source_content
        data.update(source_content(src))

    # 选项统一结构 + 完整性标记（题干/答案不动）
    opts, incomplete, hint = normalize_options(
        data.get("options"), data.get("question_type") or "")
    data["options"] = opts
    data["options_normalized"] = [{"label": o["label"], "content": o["content"]}
                                  for o in opts]
    data["options_incomplete"] = incomplete or data.get("media_incomplete", False)
    data["repair_hint"] = data.get("content_warning") or hint

    # 有效题型：历史数据里有一批 question_type='unknown' 但实际是单选题的记录。
    # 评分层早就按答案形态推断（scoring.effective_type），但展示层还在用原始
    # 标签，于是这些题在任何题型筛选下都消失、却仍然会被计分。这里把推断结果
    # 一并返回，让筛选、标签与评分用同一个口径（不修改数据库原值）。
    from backend.app.services.scoring import effective_type
    raw_answer = (src.get("answer_payload") if isinstance(src, dict)
                  else getattr(src, "answer_payload", None)) or {}
    # Safe response schema: labels/counts only, never expected values.
    # It lets the browser render multiple numeric/blank inputs without leaking
    # the server-side answer payload.
    if isinstance(raw_answer.get("parts"), list):
        data["response_schema"] = {
            "kind": "numeric_multi",
            "parts": [{"label": str(p.get("label") or f"第{i + 1}项"),
                       "unit": str(p.get("unit") or "")}
                      for i, p in enumerate(raw_answer["parts"])
                      if isinstance(p, dict)],
        }
    elif isinstance(raw_answer.get("values"), list):
        data["response_schema"] = {
            "kind": "fill_blank",
            "count": len(raw_answer["values"]),
        }
    typed = effective_type({"question_type": data.get("question_type"),
                            "answer_payload": raw_answer})
    data["question_type_effective"] = typed
    data["question_type_inferred"] = bool(
        typed != (data.get("question_type") or "unknown")
        and (data.get("question_type") or "unknown") in ("", "unknown", "other"))

    data["source_refs"] = _safe_refs(refs, reveal)
    data["prompt_prefix"] = display_prompt_prefix(
        data.get("question_type") or "", data.get("stem") or "")
    data["solution_revealed"] = bool(reveal)
    data["mode"] = mode
    # Explicit, always-visible provenance label. This is a CATEGORY string,
    # never the answer content, so it is safe before submission.
    from backend.app.services.answer_pipeline import answer_label
    data["answer_label"] = answer_label(data.get("answer_origin") or "none")
    # 答案隔离的边界是「提交前」，不是「永远不返回」：交卷之后学生本来就该
    # 看到正确答案、解析与评分要点。旧实现把 reveal=True 刚写入的 explanation /
    # rubric 又当成禁用字段删掉了，导致成绩页与首页就地交卷都拿不到解析。
    if reveal:
        for f in ("notes", "created_by", "row_id"):
            data.pop(f, None)
    else:
        for f in FORBIDDEN_FIELDS:
            data.pop(f, None)
        data.pop("answer_origin", None)
    return data


def assert_no_answer_leak(payload: Any) -> None:
    """Test helper: recursively fail if a server-only key appears."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in FORBIDDEN_FIELDS or k in ("correct_answer", "expected"):
                raise AssertionError(f"答案字段泄露: {k}")
            assert_no_answer_leak(v)
    elif isinstance(payload, list):
        for v in payload:
            assert_no_answer_leak(v)
