# -*- coding: utf-8 -*-
"""V3.3 唯一题目识别：内容指纹。

设计原则（对应用户提出的去重约束）：

* 指纹 = 题型 + 规范化后的**完整题干** + **完整选项及其顺序** +
  **正确答案 / 答案载荷** + 必要的题目条件（评分参考、数值容差等）。
  绝不只用标题、也绝不只是「题干相似度」。
* 只做不影响语义的规范化：HTML 实体、无意义换行、行首尾空白、
  连续空白、全角空格。**不删除数字、不改公式、不重排选项**。
  因此不同数字、不同选项、不同条件、不同正确答案的题一定得到不同指纹。
* 指纹是展示层的分组依据，不用于删除或覆盖任何历史记录。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any

_WS = re.compile(r"[ \t\f\v]+")


def norm_text(value: Any) -> str:
    """规范化文本：HTML 实体 / 换行 / 空白，但保留全部数字与公式字符。"""
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = (text.replace("\u00a0", " ").replace("\u3000", " ")
            .replace("\r\n", "\n").replace("\r", "\n"))
    lines = []
    for line in text.split("\n"):
        cleaned = _WS.sub(" ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def _canonical(value: Any) -> Any:
    """递归规范化，dict 按 key 排序，list 保持顺序。"""
    if isinstance(value, dict):
        return {str(k): _canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    if isinstance(value, str):
        return norm_text(value)
    return value


def norm_options(options: Any) -> list[dict]:
    """保留所有选项及其原始顺序；空正文也保留占位，避免不同选项数被合并。"""
    out: list[dict] = []
    for index, raw in enumerate(options or []):
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or raw.get("label")
                  or chr(65 + index)).strip()
        text = raw.get("text")
        if text in (None, ""):
            text = raw.get("content", "")
        if text in (None, ""):
            text = raw.get("text_zh", "")
        out.append({"key": key, "text": norm_text(text)})
    return out


def fingerprint_parts(q: Any) -> dict:
    """`q` 可以是 ORM Question，也可以是 question_snapshot 字典。"""
    get = (lambda k, d=None: getattr(q, k, d)) if not isinstance(q, dict) \
        else (lambda k, d=None: q.get(k, d))

    rubric = get("rubric", {}) or {}
    points = rubric.get("points") or [] if isinstance(rubric, dict) else []
    point_texts = []
    for p in points:
        if isinstance(p, dict):
            point_texts.append(norm_text(p.get("point") or p.get("text") or ""))

    return {
        "question_type": str(get("question_type") or "").strip(),
        "stem": norm_text(get("stem", "")),
        "options": norm_options(get("options", [])),
        "stem_images": _canonical(get("stem_images", []) or []),
        "answer_payload": _canonical(get("answer_payload", {}) or {}),
        "rubric_reference": (norm_text(rubric.get("reference_answer") or "")
                             if isinstance(rubric, dict) else ""),
        "rubric_points": point_texts,
    }


def question_fingerprint(q: Any) -> str:
    """返回 32 位十六进制内容指纹。同一份内容 -> 同一个值。"""
    raw = json.dumps(fingerprint_parts(q), ensure_ascii=False,
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def fingerprint_basis() -> dict:
    """给前端/验收看的说明，明确指纹包含什么、不包含什么。"""
    return {
        "includes": ["题型", "规范化完整题干", "完整选项及顺序",
                     "正确答案/答案载荷", "评分参考答案", "关键得分点", "题干图片"],
        "normalizes": ["HTML 实体", "无意义换行", "连续空白", "行首尾空白", "全角空格"],
        "never_merges": ["不同数字", "不同选项", "不同条件", "不同正确答案", "仅标题相同"],
        "algorithm": "sha256(canonical_json)[:32]",
    }
