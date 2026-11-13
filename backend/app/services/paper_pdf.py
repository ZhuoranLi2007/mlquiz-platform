"""组卷 PDF 导出（只渲染学生端可见的题目快照，绝不含答案）。

网页端用 KaTeX 渲染 LaTeX；PDF 是纯文本排版，因此复用
`latex_plain.to_plain` 把同一份 LaTeX 转成可读的 Unicode 数学文本。
存储里的题目、答案、评分标准保持原样，不做任何改写。

答案隔离：调用方必须传 `student_view(..., reveal=False)` 的结果，
本模块还会再兜一层，遇到答案字段直接拒绝渲染，避免 PDF 成为旁路泄露。
"""
from __future__ import annotations

from pathlib import Path

from backend.app.services.booklet_pdf import Writer, font_path
from backend.app.services.latex_plain import to_plain

TYPE_LABELS = {
    "single_choice": "单选题", "multi_choice": "多选题",
    "true_false": "判断题", "numeric": "数值题", "term": "名词解释",
    "short_answer": "简答题", "derivation": "推导题",
    "comprehensive": "综合题", "unknown": "题目",
}

FORBIDDEN = {"answer_payload", "rubric", "explanation", "notes",
             "correct_answer", "reference_answer", "answer_text"}


def render_paper_pdf(*, title: str, meta: dict, questions: list[dict],
                     out_path: Path) -> dict:
    """Render a blank exam paper. `questions` is the student-view list."""
    for q in questions:
        leaked = FORBIDDEN.intersection(q)
        if leaked:
            raise ValueError(f"试卷 PDF 拒绝渲染含答案字段的题目：{sorted(leaked)}")

    w = Writer(font_path())
    w.para(title or "练习卷", size=18, gap=2)
    w.para("机器学习复习平台 · 练习卷（不含答案）", size=9.5,
           color=(0.45, 0.45, 0.45), gap=8)
    w.rule()
    for key, value in meta.items():
        if value in ("", None):
            continue
        w.para(f"{key}：{value}", size=9.5, color=(0.35, 0.35, 0.35), gap=1)
    w.y += 8
    w.para("姓名：____________    学号：____________    得分：____________",
           size=10, gap=6)
    w.rule(gap=10)

    for index, q in enumerate(questions, start=1):
        w._ensure(52)
        qtype = TYPE_LABELS.get(q.get("question_type") or "", "题目")
        score = q.get("score")
        head = f"{q.get('position') or index}．{qtype}"
        if score is not None:
            head += f"　（{score} 分）"
        w.para(head, size=11, color=(0.16, 0.32, 0.55), gap=3)
        w.para(to_plain(str(q.get("stem") or "")), size=10.5, indent=12, gap=3)
        for option in q.get("options") or []:
            if not isinstance(option, dict):
                continue
            text = to_plain(str(option.get("text") or "")) or "[公式缺失]"
            w.para(f"{option.get('key', '')}. {text}", size=10,
                   indent=24, gap=1)
        # 主观题留出作答空间；选择题不需要额外空白。
        if not q.get("options"):
            blank = 3 if q.get("question_type") in ("term", "short_answer") else 5
            w.y += blank * 11
        w.y += 8

    size = w.save(out_path)
    return {"pdf_bytes": size, "pages": w.page_no, "path": str(out_path),
            "question_count": len(questions),
            "unsupported_chars": sorted(w._dropped)}
