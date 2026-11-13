"""错题「选题依据」：把一道题为什么被选中、出处在哪，整理成可跳转的清单。

三类信息分开记录，互不冒充：
  1. 组卷条件   —— 来自该次答卷对应 Paper.blueprint（考点/难度/范围/题型）；
  2. 题目来源   —— 题目自身的 origin / 原卷题号（原题、课件自动出题、人工录入）；
  3. 页级出处   —— material_refs / source_refs 里的资料、页码、原页图与原件。

本模块只读，不写库、不改题目；资料类型优先用「按正文判别」的
MaterialUpload.resolved_type，其次用 Document.source_category，最后才
按文件名关键字兜底，且会明确标注推断来源，避免把猜的当作事实。
"""
from __future__ import annotations

from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services import material_naming

KIND_LABELS = material_naming.KIND_LABELS

SCOPE_LABELS = {
    "course_only": "仅课程范围",
    "course_plus_advanced": "课程范围 + 课内提高",
    "include_extension": "包含扩展学习",
}

TYPE_LABELS = {
    "single_choice": "单选题", "multi_choice": "多选题",
    "true_false": "判断题", "numeric": "数值题", "term": "名词解释",
    "short_answer": "简答题", "derivation": "推导题",
    "comprehensive": "综合题", "unknown": "题目",
}


class Context:
    """一次 /api/wrong-book 请求内共享的只读索引，避免逐条查库。"""

    def __init__(self, db: Session):
        self.uploads = {u.document_id: u for u in db.execute(
            select(E.MaterialUpload)).scalars().all() if u.document_id}
        self.docs = {d.document_id: d for d in db.execute(
            select(E.Document)).scalars().all()}
        self.papers = {p.paper_id: p for p in db.execute(
            select(E.Paper)).scalars().all()}
        self.attempts = {a.attempt_id: a for a in db.execute(
            select(E.Attempt)).scalars().all()}
        self.kps = {k.kp_id: k.name for k in db.execute(
            select(E.KnowledgePoint)).scalars().all()}


def build(ctx: Context, *, entry, question: dict, kp_label: str = "") -> dict:
    """`entry` 是 WrongBookEntry ORM 行，`question` 是 student_view(reveal=True)。"""
    refs = list(entry.material_refs or [])
    if not refs:
        refs = list(question.get("source_refs") or [])

    sources: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        doc_id = str(ref.get("document_id") or "")
        page = int(ref.get("page") or 0)
        key = (doc_id, page)
        if key in seen:
            continue
        seen.add(key)

        up = ctx.uploads.get(doc_id)
        doc = ctx.docs.get(doc_id)
        rel = str(ref.get("rel_path") or (up.filename if up else "")
                  or (doc.rel_path if doc else "") or "未命名资料")
        raw_name = PurePosixPath(rel).name
        naming = material_naming.normalize(
            raw_name,
            (up.resolved_type if up else "") or "",
            (doc.source_category if doc else "") or "",
        )
        upload_id = str(ref.get("upload_id") or (up.upload_id if up else "") or "")
        # 有些笔记类出处只记录了文件名，没有页码或文档 ID。
        # 不臆造精确页码：有 doc_id 但无页码时明确标记「资料首页（页码未记录）」；
        # 连 doc_id 都没有时给一个可执行的「在我的资料中按文件名检索」入口。
        effective_page = page or (1 if doc_id else 0)
        page_inferred = bool(doc_id and not page)
        if upload_id:
            jump_kind = "original"
        elif doc_id and effective_page:
            jump_kind = "page"
        else:
            jump_kind = "search"
        sources.append({
            "kind": naming["kind"],
            "kind_label": naming["kind_label"],
            "kind_basis": naming["kind_basis"],
            "display_name": naming["display_name"],
            "raw_name": raw_name,
            "rel_path": rel,
            "page": page or None,
            "effective_page": effective_page or None,
            "page_inferred": page_inferred,
            "jump_kind": jump_kind,
            "lookup_term": raw_name,
            "materials_url": "/materials",
            "document_id": doc_id,
            "upload_id": upload_id,
            "mime": (up.mime if up else "") or "",
            "chunk_label": str(ref.get("chunk_label") or ref.get("label") or ""),
            "note": str(ref.get("note") or ""),
            "page_image_url": (f"/api/materials/{doc_id}/page/{effective_page}/image"
                               if doc_id and effective_page else ""),
            "original_url": (f"/api/uploads/{upload_id}/original"
                             if upload_id else ""),
            "can_open_original": bool(upload_id),
        })

    # 组卷条件（真实的选题依据，来自当次答卷对应的蓝图）
    attempt = ctx.attempts.get(entry.attempt_id)
    paper = ctx.papers.get(attempt.paper_id) if attempt else None
    bp = (paper.blueprint or {}) if paper else {}
    conditions: list[str] = []
    kp_ids = bp.get("kp_ids") or []
    if kp_ids:
        conditions.append("考点：" + "、".join(
            ctx.kps.get(k, k) for k in kp_ids[:4]))
    elif kp_label or entry.kp_id:
        conditions.append(f"考点：{kp_label or ctx.kps.get(entry.kp_id, entry.kp_id)}")
    if bp.get("difficulty"):
        conditions.append("难度：" + "、".join(map(str, bp["difficulty"])))
    if bp.get("question_types"):
        conditions.append("题型：" + "、".join(
            TYPE_LABELS.get(t, t) for t in bp["question_types"][:4]))
    if bp.get("scope"):
        conditions.append("范围：" + SCOPE_LABELS.get(bp["scope"], bp["scope"]))
    if bp.get("exclude_seen"):
        conditions.append("排除已见题")
    if bp.get("family_spread"):
        conditions.append("题族分散")

    origin = str(question.get("origin") or "")
    no = str(question.get("original_question_no") or "")
    if origin == "ai":
        origin_text = "由平台依据该课件考点在后台自动出题，出处定位到对应页。"
    elif origin == "original":
        origin_text = "直接取自原卷，按原卷题号保留。" + (f"（{no}）" if no else "")
    elif origin == "human":
        origin_text = "人工录入的题目，出处按资料页级定位。"
    else:
        origin_text = "题目来源以页级出处为准。"
    if no and origin != "original":
        origin_text += f" 原卷题号：{no}。"
    if question.get("main_kp_id"):
        origin_text += f" 主考点：{ctx.kps.get(question['main_kp_id'], question['main_kp_id'])}。"

    return {
        "reason": origin_text,
        "conditions": conditions,
        "blueprint_title": (paper.title if paper else "") or "",
        "paper_id": (paper.paper_id if paper else "") or "",
        "sources": sources,
        "source_count": len(sources),
        "note": ("组卷条件取自当次答卷的试卷蓝图；资料类型优先依据正文判别结果，"
                 "未核实的推断已逐条标注。未记录页码的出处只跳转到资料首页或"
                 "按文件名检索，不臆造精确页码。"),
    }
