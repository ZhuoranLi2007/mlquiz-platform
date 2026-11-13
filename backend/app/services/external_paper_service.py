"""扩展学习 → 高校试卷资源库（V2.3）。

设计口径（缺数据一律留空，不用 0 / 「简单」冒充）：
  * 卡片只由 `ExternalPaper` 产生；零散题目与视频留在兼容区，不硬凑成试卷；
  * `course_match_level` 只由**已判别**的题目范围聚合，样本不足就是 unassessed；
  * `difficulty` 只在 ≥2 题有难度标注时聚合，否则 unknown，且排序时永远排最后；
  * PDF 分四态：本地可下载 / 外部 PDF 链接 / 来源网页 / 暂无可下载文件；
  * 在线作答分三态：整卷可作、只能做已收录的部分题、暂不支持。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import (attempt_service, bank_service,
                                  paper_localization, pdf_thumbnail)
from backend.app.services import answer_pipeline

USER_ID = "local_user"

ORIGIN_GROUPS = (
    ("overseas", "国外高校"),
    ("domestic", "国内高校"),
    ("industry", "企业面试"),
    ("other", "其他来源"),
)
ORIGIN_LABELS = dict(ORIGIN_GROUPS)

PAPER_TYPES = (
    ("final", "期末试卷"),
    ("midterm", "期中试卷"),
    ("mock", "模拟试卷"),
    ("topic_set", "专题练习"),
    ("interview", "面试题集"),
)
PAPER_TYPE_LABELS = dict(PAPER_TYPES)

DIFFICULTIES = (
    ("easy", "简单"),
    ("medium", "中等"),
    ("hard", "较难"),
    ("very_hard", "高难度"),
    ("unknown", "待评估"),
)
DIFFICULTY_LABELS = dict(DIFFICULTIES)
DIFF_RANK = {"easy": 1, "medium": 2, "hard": 3, "very_hard": 4}

MATCH_LEVELS = (
    ("high", "高度匹配"),
    ("partial", "部分拓展"),
    ("challenge", "拓展挑战"),
    ("unassessed", "尚未评估"),
)
MATCH_LABELS = dict(MATCH_LEVELS)
MATCH_RANK = {"high": 0, "partial": 1, "challenge": 2, "unassessed": 3}

SORTS = (
    ("match", "课程匹配度优先"),
    ("difficulty_desc", "难度从高到低"),
    ("difficulty_asc", "难度从低到高"),
    ("year_desc", "年份从新到旧"),
    ("year_asc", "年份从旧到新"),
)
SORT_LABELS = dict(SORTS)

REGION_LABELS = {"overseas": "境外", "domestic": "国内",
                 "mixed": "多地汇总", "unknown": "地区未登记"}
VERIFY_LABELS = {"ok": "来源已核验", "partial": "部分核实",
                 "blocked": "访问受限", "unreachable": "暂不可达",
                 "http_error": "链接异常", "unverified": "尚未核验"}

_SCORE = re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*分\s*\]")

RULES = (
    "卡片只来自已登记的试卷/题集，单条题目不会被包装成整套高校试卷；"
    "缺失的年份、时长、答案状态显示为「未记录 / 待评估」，不填 0 或猜测值；"
    "难度与课程匹配度均由已收录题目聚合，样本不足时保持「待评估」；"
    "外部 PDF 只提供原始链接，不做转载代理；扩展题成绩不计入期末掌握度。"
)


class NoOnlineQuestions(Exception):
    """没有可用于整卷/部分作答的结构化题目。"""


# --------------------------------------------------------------- 读取辅助
def _items_by_paper(db: Session) -> dict[str, list[E.ExternalItem]]:
    rows = db.execute(select(E.ExternalItem)).scalars().all()
    grouped: dict[str, list[E.ExternalItem]] = defaultdict(list)
    for it in rows:
        if it.paper_id:
            grouped[it.paper_id].append(it)
    for items in grouped.values():
        items.sort(key=lambda x: (x.position or 0, x.item_id))
    return grouped


def _context(db: Session) -> dict:
    papers = db.execute(select(E.ExternalPaper)).scalars().all()
    sources = {s.source_id: s for s in db.execute(
        select(E.ExternalSource)).scalars().all()}
    kp_names = {k.kp_id: k.name for k in db.execute(
        select(E.KnowledgePoint)).scalars().all()}
    favs = {f.target_id for f in db.execute(select(E.Favorite).where(
        E.Favorite.user_id == USER_ID,
        E.Favorite.kind == "external_paper")).scalars().all()}
    return {
        "db": db,
        "papers": papers,
        "items": _items_by_paper(db),
        "sources": sources,
        "kp_names": kp_names,
        "favorites": favs,
    }


def origin_group(paper: E.ExternalPaper,
                 source: E.ExternalSource | None) -> str:
    if paper.paper_type == "interview" or (source and source.kind == "interview"):
        return "industry"
    region = (paper.country_or_region or "unknown").lower()
    if region == "overseas":
        return "overseas"
    if region == "domestic":
        return "domestic"
    return "other"


def pdf_state(paper: E.ExternalPaper) -> dict:
    """四种状态，绝不生成假 PDF、也不让按钮空转。

    本地文件只有在**允许再分发**（allows_copy）时才提供下载；仅允许本地
    测试或仅允许预览的资源，仍然回到原始链接。
    """
    if paper.pdf_rel_path and paper.allows_copy:
        local = cfg.resolve_storage_ref(paper.pdf_rel_path)
        if local.exists():
            return {"state": "local", "label": "下载原卷",
                    "url": f"/api/external/papers/{paper.epaper_id}/pdf",
                    "downloadable": True,
                    "note": "平台已保存且确认可再分发的本地文件"}
    if paper.pdf_rel_path and not paper.allows_copy:
        # 有本地文件但未授权再分发：只用于生成缩略图/内部测试，不暴露原文
        if paper.pdf_url:
            return {"state": "external", "label": "打开原始 PDF",
                    "url": paper.pdf_url, "downloadable": False,
                    "note": "本地仅作测试缓存；未确认再分发，仍打开原始链接"}
    if paper.pdf_url:
        return {"state": "external", "label": "打开原始 PDF",
                "url": paper.pdf_url, "downloadable": False,
                "note": "外部原始链接，按来源授权由浏览器直接打开"}
    if paper.source_url:
        return {"state": "web", "label": "查看原始来源",
                "url": paper.source_url, "downloadable": False,
                "note": "该来源只有网页，没有独立 PDF"}
    return {"state": "none", "label": "暂无原卷",
            "url": "", "downloadable": False,
            "note": "尚未取得可下载文件或有效来源"}


def online_state(paper: E.ExternalPaper) -> dict:
    total = int(paper.question_count or 0)
    online = int(paper.online_question_count or 0)
    if total == 0:
        return {"state": "none", "label": "暂不支持在线作答",
                "note": "这套资源还没有完成题目结构化", "count": 0}
    if online == 0:
        return {"state": "none", "label": "暂不支持在线作答",
                "note": f"已收录 {total} 题，但都还没有结构化题目可用于作答",
                "count": 0}
    if online < total:
        return {"state": "partial", "label": f"先做已收录的 {online} 题",
                "note": (f"已收录 {total} 题，其中 {online} 题完成结构化；"
                         f"暂不支持整卷作答，进入后按原卷顺序做这 {online} 题"),
                "count": online}
    return {"state": "full", "label": "在线作答",
            "note": f"按原卷顺序作答全部 {total} 题", "count": online}


def _item_score(item: E.ExternalItem, question: E.Question | None) -> float:
    m = _SCORE.search(item.title or "")
    if m:
        return float(m.group(1))
    if question is not None and question.score:
        return float(question.score)
    return 5.0


def _intro(paper: E.ExternalPaper, source: E.ExternalSource | None,
           has_answer: bool | None) -> str:
    """学生端一句话介绍：只用真实字段，缺数据就略过，不编造年份/时长。"""
    type_label = PAPER_TYPE_LABELS.get(paper.paper_type, "试卷")
    institution = paper.institution or (source.org if source else "")
    head = f"{institution}的{type_label}" if institution else f"这套{type_label}"
    bits = [head]
    if paper.year:
        bits.append(f"{paper.year} 年" + (f" {paper.semester}" if paper.semester else ""))
    if paper.question_count:
        bits.append(f"已录入 {paper.question_count} 道小题")
    if paper.duration_minutes:
        bits.append(f"考试时长 {paper.duration_minutes} 分钟")
    if has_answer is True:
        bits.append("附参考答案")
    elif has_answer is False:
        bits.append("原卷未提供参考答案")
    return "，".join(bits) + "。"


def _card(ctx: dict, paper: E.ExternalPaper) -> dict:
    source = ctx["sources"].get(paper.source_id)
    items = ctx["items"].get(paper.epaper_id, [])
    kp_ids: list[str] = []
    for it in items:
        for kp in (it.kp_ids or []):
            if kp not in kp_ids:
                kp_ids.append(kp)
    return {
        "epaper_id": paper.epaper_id,
        "title": paper.title,
        "institution": paper.institution or (source.org if source else ""),
        "region": paper.country_or_region or "unknown",
        "region_label": REGION_LABELS.get(paper.country_or_region or "",
                                          "地区未登记"),
        "origin_group": origin_group(paper, source),
        "origin_group_label": ORIGIN_LABELS[origin_group(paper, source)],
        "year": paper.year,
        "semester": paper.semester,
        "paper_type": paper.paper_type,
        "paper_type_label": PAPER_TYPE_LABELS.get(paper.paper_type,
                                                  paper.paper_type),
        "difficulty": paper.difficulty or "unknown",
        "difficulty_label": DIFFICULTY_LABELS.get(paper.difficulty or "unknown",
                                                  "待评估"),
        "difficulty_known": (paper.difficulty or "unknown") in DIFF_RANK,
        "course_match_level": paper.course_match_level or "unassessed",
        "course_match_label": MATCH_LABELS.get(paper.course_match_level or "",
                                              "尚未评估"),
        "question_count": int(paper.question_count or 0),
        "online_question_count": int(paper.online_question_count or 0),
        "total_score": paper.total_score,
        "duration_minutes": paper.duration_minutes,
        "has_answer": paper.has_answer,
        "answer_note": paper.answer_note,
        "kp_names": [ctx["kp_names"].get(k, k) for k in kp_ids][:8],
        "pdf": pdf_state(paper),
        "thumbnail": pdf_thumbnail.thumbnail_state(paper),
        "zh": paper_localization.status(ctx["db"], paper),
        "intro": _intro(paper, source, paper.has_answer),
        "online": online_state(paper),
        "source": {
            "source_id": paper.source_id,
            "name": source.name if source else "",
            "org": (paper.institution or (source.org if source else "")),
            "url": paper.source_url or (source.url if source else ""),
            "verify_status": paper.review_status or "unverified",
            "verify_label": VERIFY_LABELS.get(paper.review_status or "",
                                             "尚未核验"),
            "license": paper.license_status or "unknown",
            "allows_copy": bool(paper.allows_copy),
        },
        "review_status": paper.review_status,
        "favorited": paper.epaper_id in ctx["favorites"],
        "origin_note": paper.origin_note,
    }


# --------------------------------------------------------------- 筛选/排序
def _search_blob(ctx: dict, paper: E.ExternalPaper) -> str:
    items = ctx["items"].get(paper.epaper_id, [])
    parts = [paper.title, paper.institution, paper.source_id]
    parts += [it.title for it in items]
    for it in items:
        parts += [ctx["kp_names"].get(k, k) for k in (it.kp_ids or [])]
    return " ".join(p or "" for p in parts).lower()


def _apply(ctx: dict, papers: list[E.ExternalPaper], *,
           origin: str = "", paper_type: str = "", difficulty: str = "",
           match: str = "", year_from: int | None = None,
           year_to: int | None = None, q: str = "") -> list[E.ExternalPaper]:
    out = papers
    if origin:
        out = [p for p in out
               if origin_group(p, ctx["sources"].get(p.source_id)) == origin]
    if paper_type:
        out = [p for p in out if p.paper_type == paper_type]
    if difficulty:
        out = [p for p in out if (p.difficulty or "unknown") == difficulty]
    if match:
        out = [p for p in out
               if (p.course_match_level or "unassessed") == match]
    if year_from is not None:
        out = [p for p in out if p.year is not None and p.year >= year_from]
    if year_to is not None:
        out = [p for p in out if p.year is not None and p.year <= year_to]
    needle = (q or "").strip().lower()
    if needle:
        out = [p for p in out if needle in _search_blob(ctx, p)]
    return out


def _sort(papers: list[E.ExternalPaper], sort: str) -> list[E.ExternalPaper]:
    """未知难度 / 缺失年份永远排最后，不当作简单或最旧。"""
    if sort == "difficulty_desc":
        known = [p for p in papers if (p.difficulty or "") in DIFF_RANK]
        unknown = [p for p in papers if (p.difficulty or "") not in DIFF_RANK]
        known.sort(key=lambda p: (-DIFF_RANK[p.difficulty], p.epaper_id))
        return known + unknown
    if sort == "difficulty_asc":
        known = [p for p in papers if (p.difficulty or "") in DIFF_RANK]
        unknown = [p for p in papers if (p.difficulty or "") not in DIFF_RANK]
        known.sort(key=lambda p: (DIFF_RANK[p.difficulty], p.epaper_id))
        return known + unknown
    if sort in ("year_desc", "year_asc"):
        known = [p for p in papers if p.year is not None]
        missing = [p for p in papers if p.year is None]
        known.sort(key=lambda p: (p.year, p.epaper_id),
                   reverse=(sort == "year_desc"))
        return known + missing
    # 默认：课程匹配度优先；同一匹配度内年份从新到旧，缺失年份排在同级最后。
    # 注意不能把所有「有年份」的卷子整体前置，否则会压过匹配度这个主排序键。
    return sorted(papers, key=lambda p: (
        MATCH_RANK.get(p.course_match_level or "unassessed", 3),
        0 if p.year is not None else 1,
        -(p.year or 0),
        p.epaper_id,
    ))


def _facets(ctx: dict, papers: list[E.ExternalPaper],
            filters: dict) -> dict:
    """每个维度用「去掉自身条件」后的结果计数，组合筛选时 chip 数字才准。"""
    def counts(key: str, values, getter) -> list[dict]:
        subset = _apply(ctx, papers, **{**filters, key: ""})
        counter = Counter(getter(p) for p in subset)
        return [{"value": v, "label": label, "count": counter.get(v, 0)}
                for v, label in values]

    years = sorted({p.year for p in papers if p.year is not None})
    year_subset = _apply(ctx, papers, **{**filters, "year_from": None,
                                         "year_to": None})
    year_counter = Counter(p.year for p in year_subset if p.year is not None)
    return {
        "origins": counts("origin", ORIGIN_GROUPS,
                          lambda p: origin_group(p,
                                                 ctx["sources"].get(p.source_id))),
        "paper_types": counts("paper_type", PAPER_TYPES,
                              lambda p: p.paper_type),
        "difficulties": counts("difficulty", DIFFICULTIES,
                               lambda p: p.difficulty or "unknown"),
        "matches": counts("match", MATCH_LEVELS,
                          lambda p: p.course_match_level or "unassessed"),
        "years": [{"value": y, "label": f"{y} 年",
                   "count": year_counter.get(y, 0)} for y in years],
    }


# --------------------------------------------------------------- 对外接口
def list_papers(db: Session, *, origin: str = "", paper_type: str = "",
                difficulty: str = "", match: str = "",
                year_from: int | None = None, year_to: int | None = None,
                q: str = "", sort: str = "match",
                page: int = 1, page_size: int = 12) -> dict:
    ctx = _context(db)
    papers = list(ctx["papers"])
    filters = {"origin": origin, "paper_type": paper_type,
               "difficulty": difficulty, "match": match,
               "year_from": year_from, "year_to": year_to, "q": q}
    matched = _apply(ctx, papers, **filters)
    matched = _sort(matched, sort if sort in SORT_LABELS else "match")
    total = len(matched)
    page = max(1, page)
    page_size = max(1, min(page_size, 60))
    start = (page - 1) * page_size
    page_rows = matched[start:start + page_size]
    return {
        "items": [_card(ctx, p) for p in page_rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "sort": sort if sort in SORT_LABELS else "match",
        "sort_options": [{"value": v, "label": l} for v, l in SORTS],
        "facets": _facets(ctx, papers, filters),
        "filters": filters,
        "rules": RULES,
        "note": ("只展示来源明确、可追溯的试卷与题集；"
                 "没有可靠数据的字段保持「未记录 / 待评估」"),
    }


def get_paper(db: Session, epaper_id: str) -> dict:
    ctx = _context(db)
    paper = next((p for p in ctx["papers"] if p.epaper_id == epaper_id), None)
    if paper is None:
        raise KeyError(epaper_id)
    card = _card(ctx, paper)
    items = []
    for it in ctx["items"].get(epaper_id, []):
        q = (answer_pipeline.latest_question(db, it.imported_question_id)
             if it.imported_question_id else None)
        items.append({
            "item_id": it.item_id,
            "position": it.position,
            "title": it.title,
            "question_type": it.question_type,
            "difficulty": it.difficulty,
            "scope_class": it.scope_class,
            "score": _item_score(it, q),
            "imported": bool(it.imported_question_id),
            "question_id": it.imported_question_id or "",
            "online_ready": bool(q and answer_pipeline.is_answer_ready(q)
                                 and answer_pipeline.is_publishable(q)),
            "excerpt": (it.content_excerpt or "")[:160],
            "source_url": it.url,
        })
    card.update({
        "items": items,
        "difficulty_basis": paper.difficulty_basis,
        "match_basis": paper.match_basis,
        "created_at": paper.created_at,
        "updated_at": paper.updated_at,
        "score_note": ("已收录题目分值合计，不是原卷总分"
                       if paper.total_score is not None else "分值未记录"),
        # 卡片上删掉的技术信息在详情里完整保留，口径写清楚。
        "structure": {
            "question_count": int(paper.question_count or 0),
            "online_question_count": int(paper.online_question_count or 0),
            "full_paper": (int(paper.online_question_count or 0)
                           == int(paper.question_count or 0)
                           and int(paper.question_count or 0) > 0),
            "review_status": paper.review_status,
            "thumb_generated_at": paper.thumb_generated_at,
            "note": ("已收录题目数与可在线作答数分开统计；"
                     "部分可作答时只按已结构化题目建卷，不冒充整卷"),
        },
        "preview_note": ("这里只是已收录题目的摘要，不是完整原卷；"
                         "正式作答请用在线作答入口"),
    })
    return card


def get_local_pdf(db: Session, epaper_id: str):
    paper = db.get(E.ExternalPaper, epaper_id)
    if paper is None:
        raise KeyError(epaper_id)
    # 先判「有没有文件」（404），再判「能不能分发」（403），语义更准确。
    if not paper.pdf_rel_path:
        raise FileNotFoundError("该试卷没有本地 PDF")
    path = cfg.resolve_storage_ref(paper.pdf_rel_path)
    if not path.exists():
        raise FileNotFoundError("本地 PDF 文件不存在")
    if not paper.allows_copy:
        # 本地缓存 ≠ 允许再分发。没有明确授权时，接口本身也必须拒绝，
        # 不能只靠前端不显示按钮。
        raise PermissionError("该资源未确认再分发授权，请使用原始链接")
    return paper, path


def start_attempt(db: Session, epaper_id: str,
                  user_id: str = USER_ID) -> dict:
    paper = db.get(E.ExternalPaper, epaper_id)
    if paper is None:
        raise KeyError(epaper_id)
    picked: list[tuple[E.ExternalItem, E.Question]] = []
    answer_pending = 0
    for it in _items_by_paper(db).get(epaper_id, []):
        if not it.imported_question_id:
            continue
        q = answer_pipeline.latest_question(db, it.imported_question_id)
        if q is None or not q.active:
            continue
        # 「不可发布」是硬门槛（如选项公式缺失）；「答案未就绪」只影响自动评分，
        # 不该让学生连题都看不到 —— 记进 answer_pending 如实说明即可。
        if not answer_pipeline.is_publishable(q):
            continue
        if not answer_pipeline.is_answer_ready(q):
            answer_pending += 1
        picked.append((it, q))
    if not picked:
        raise NoOnlineQuestions(
            "这套资源还没有可用于在线作答的结构化题目；"
            "可以先用「查看原卷」对照原文")

    questions = [q for _it, q in picked]
    scores = [_item_score(it, q) for it, q in picked]
    full = len(picked) == int(paper.question_count or 0)
    title = f"{paper.title}（按原卷顺序）"
    built = bank_service.create_paper_from_questions(
        db, questions, title=title, mode="quiz", source="external_paper",
        blueprint={
            "external_paper_id": epaper_id,
            "title": title,
            "mode": "quiz",
            "scope": "include_extension",
            "order_policy": "original_order",
            "partial_paper": not full,
            "source_url": paper.source_url or paper.pdf_url,
        },
        scores=scores,
        scope="include_extension",
        user_id=user_id,
    )
    attempt = attempt_service.create_attempt(db, built["paper_id"], user_id)
    note = (f"按原卷顺序作答全部 {len(picked)} 题"
            if full else
            f"原卷已收录 {paper.question_count} 题，本次按原卷顺序作答其中"
            f" {len(picked)} 题；这不是完整原卷")
    if answer_pending:
        note += f"；其中 {answer_pending} 题暂无参考答案，交卷后不自动评分"
    return {
        "attempt_id": attempt["attempt_id"],
        "paper_id": built["paper_id"],
        "item_count": len(picked),
        "total_score": built["total_score"],
        "full_paper": full,
        "answer_pending_count": answer_pending,
        "note": note,
    }


def orphan_resources(db: Session) -> dict:
    """兼容区：没有归入任何试卷的条目 + 只有来源主页、没有条目的台账。

    这些资源不能伪装成整套试卷，但也不能丢；统一以「专题练习 / 网课视频 /
    来源入口」三类展示。
    """
    ctx = _context(db)
    items = [it for it in db.execute(select(E.ExternalItem)).scalars().all()
             if not it.paper_id and it.active]
    item_rows = []
    for it in items:
        source = ctx["sources"].get(it.source_id)
        kind = "video" if it.kind == "video" else "topic"
        item_rows.append({
            "item_id": it.item_id,
            "kind": kind,
            "kind_label": "网课视频" if kind == "video" else "专题练习",
            "title": it.title,
            "url": it.url,
            "category": it.category,
            "scope_class": it.scope_class,
            "difficulty": it.difficulty,
            "excerpt": (it.content_excerpt or "")[:160],
            "source_name": source.name if source else it.source_id,
            "source_org": source.org if source else "",
            "source_region": (source.region if source else "unknown") or "unknown",
            "verify_status": source.verify_status if source else "unverified",
            "license": source.license if source else "unknown",
            "favorited": it.item_id in ctx["favorites"],
        })

    used = {it.source_id for it in db.execute(
        select(E.ExternalItem)).scalars().all()}
    entries = []
    for source in ctx["sources"].values():
        # 有条目的来源已经在上面按条目展示；这里只补「只有主页、没有条目」的台账，
        # 避免同一个来源既出现在资源列表又出现在来源入口里。
        if source.source_id in used:
            continue
        entries.append({
            "source_id": source.source_id,
            "name": source.name,
            "org": source.org,
            "url": source.url,
            "kind": source.kind,
            "region": source.region or "unknown",
            "region_label": REGION_LABELS.get(source.region or "", "地区未登记"),
            "verify_status": source.verify_status,
            "verify_label": VERIFY_LABELS.get(source.verify_status or "",
                                             "尚未核验"),
            "license": source.license,
            "note": "该来源目前只有主页入口，尚未登记具体试卷或题目",
        })
    return {
        "items": item_rows,
        "entries": entries,
        "total_items": len(item_rows),
        "total_entries": len(entries),
        "note": ("这些资源无法确认属于哪一套完整试卷，因此不建试卷记录；"
                 "保留原始链接与来源台账，后续核实后可再归套"),
    }
