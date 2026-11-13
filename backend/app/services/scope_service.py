"""V2.1 课程范围与扩展性学习：范围表构建、逐题匹配、外部题入库。

三条设计原则（针对本需求的三个陷阱）：

1. **考点相同 ≠ 深度相同**。范围表记录的是「本课程教到哪一步」（识记/理解/计算/推导），
   分类时把深度证据一起交给模型，并要求它说明超出的是哪一步。
2. **PPT 没检索命中 ≠ 超纲**。`coverage=not_found` 只是「页级片段里没直接命中」，
   分类结果只能是 `unconfirmed`（待确认），不能自动判成 `extension`。
3. **公开可访问 ≠ 允许转载**。外部题只保存「短摘录 + 链接 + 元数据」，
   正文不复制；是否允许转载单独记在 `external_sources.allows_copy`。

模型负责「读题判断」，代码负责「不许瞎判」：越界的 kp_id、不存在的 PPT 页码、
低置信度、缺超纲理由，全部在写库前被拦下或降级为待确认。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services import model_provider

SCOPE_LABELS = {
    "in_scope": "课程范围内",
    "in_scope_advanced": "课程内提高",
    "extension": "扩展性学习",
    "unconfirmed": "待确认",
}
SCOPE_ORDER = ["in_scope", "in_scope_advanced", "extension", "unconfirmed"]
CATEGORY_LABELS = {
    "university_exam": "其他高校试题",
    "interview": "机器学习面试题",
    "video": "视频学习资源",
}
DEPTH_ORDER = ["识记", "理解", "计算", "推导"]

# 「扩展学习」页的资源类型分组。一个条目可以同时属于多个组（例如 UT Austin
# 的试卷既是「外国资源」也是「高校试题」），所以是标签而不是互斥分类。
# 地区只取自 ExternalSource.region 台账，不从域名/标题猜测。
RESOURCE_GROUPS = (
    ("course", "网课资源"),
    ("overseas", "外国资源"),
    ("domestic", "国内资源"),
    ("interview", "面试题"),
    ("university_exam", "高校试题"),
)
GROUP_LABELS = dict(RESOURCE_GROUPS)
GROUP_ORDER = [key for key, _ in RESOURCE_GROUPS]


def resource_groups(item: E.ExternalItem, source: E.ExternalSource | None) -> list[str]:
    """按「条目类型 + 来源地区台账」派生可筛选的分组标签（确定性规则）。"""
    groups: list[str] = []
    if item.kind == "video" or item.category == "video" or (
            source is not None and source.kind == "video"):
        groups.append("course")
    region = (source.region if source is not None else "") or "unknown"
    if region == "overseas":
        groups.append("overseas")
    elif region == "domestic":
        groups.append("domestic")
    if item.category == "interview":
        groups.append("interview")
    if item.category == "university_exam":
        groups.append("university_exam")
    return [g for g in GROUP_ORDER if g in groups]

PROMPT_VERSION = "scope-match-1.0"

# 只有这些词出现在课件页里，"可支持的考查深度"才往上抬一级
_DEPTH_MARKS = {
    "推导": ("推导", "求导", "对偶", "拉格朗日", "闭式解", "证明", "拉格朗日乘子"),
    "计算": ("计算", "求解", "公式", "损失函数", "更新式", "梯度", "迭代"),
    "理解": ("定义", "概念", "性质", "思想", "原理", "为什么", "区别"),
}
_METHOD_WORDS = (
    "梯度下降", "随机梯度下降", "牛顿法", "拉格朗日乘子", "对偶问题", "SMO",
    "核函数", "软间隔", "反向传播", "softmax", "交叉熵", "极大似然",
    "贝叶斯估计", "EM 算法", "特征值分解", "奇异值分解", "AdaBoost",
    "随机森林", "k 均值", "层次聚类", "PCA", "NMF", "正则化", "交叉验证",
)
_FORMULA_RE = re.compile(r"\$[^$\n]{4,120}\$")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]{2,}|[\u4e00-\u9fa5]{2,12}")
# 「核技巧与核函数」这种考点名必须先切开，否则整串匹配不到任何课件页
_SPLIT_RE = re.compile(r"[与和及的、，,/（）()·;；:：\s]+")
_STOPWORDS = {"机器学习", "方法", "模型", "问题", "理论", "推导", "计算", "定义",
              "概念", "性质", "思想", "原理", "目标", "基本", "相关"}
# PDF 里的公式是纯文本（不是 $...$），只能按「含等号/求和/argmax」的行来认
_FORMULA_LINE = re.compile(r"(=|∑|≥|≤|\^T|\bargmax\b|\bargmin\b)")
# PDF 抽出来的「公式」其实是文本行；这两类假阳性要挡掉：
# 页码标题（page 57）和含 min/max 自然语言的英文句子
_PAGE_TITLE = re.compile(r"^(第\s*\d+\s*页|page\s*\d+|\d+)$", re.I)
_CJK = re.compile(r"[\u4e00-\u9fa5]")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).lower()


def _content_hash(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()


def _keywords(kp: E.KnowledgePoint) -> list[str]:
    """考点名与描述里的实词，用于在 PPT 页里找证据（不做同义词扩展）。

    中文没有空格，整串（如「核技巧与核函数」）在课件里几乎不会原样出现，
    所以先按「与/和/及/顿号/括号」切开，再补英文 token。
    """
    bag = f"{kp.name} {kp.description or ''} {kp.key_basis or ''}"
    parts: list[str] = []
    for seg in _SPLIT_RE.split(bag):
        seg = seg.strip()
        if not seg:
            continue
        parts.append(seg)
        parts.extend(_WORD_RE.findall(seg))
    out: list[str] = []
    for p in parts:
        p = p.strip("。，、；：()（）")
        if len(p) >= 2 and p not in _STOPWORDS and p not in out:
            out.append(p)
    return out[:16]


def _kp_slides(db: Session, kp: E.KnowledgePoint) -> list[dict]:
    """按关键词在「老师课件」页级片段里找命中页，返回带文件与页码的证据。"""
    rows = db.execute(
        select(E.Chunk, E.Document)
        .join(E.Document, E.Document.document_id == E.Chunk.document_id)
        .where(E.Document.source_category == "course_slides")
    ).all()
    kws = _keywords(kp)
    hits: list[dict] = []
    for chunk, doc in rows:
        text = chunk.content_raw or ""
        low = text.lower()
        score = 0
        matched: list[str] = []
        for w in kws:
            if w.lower() in low:
                if w == kp.name:
                    score += 5
                elif len(w) >= 4:
                    score += 3
                elif len(w) >= 3:
                    score += 2
                else:
                    score += 1
                matched.append(w)
        # 只有「长词命中」或「命中够多」才算证据页，避免单字泛词把整本课件都算进来
        if not matched or not any(len(m) >= 3 for m in matched):
            continue
        page = int(chunk.printed_page or 0)
        hits.append({
            "file": doc.rel_path,
            "page": page,
            "chunk_id": chunk.chunk_id,
            "score": score,
            "matched": matched[:6],
            "title": chunk.title or f"第 {page} 页",
            "text": text,
        })
    hits.sort(key=lambda h: (-h["score"], h["file"], h["page"]))
    return hits


def _bank_depth(questions: list[E.Question]) -> tuple[str, str]:
    """本课程实际考过的题目所体现的深度（比整页关键词更可靠）。

    「可明确支持的考查深度」如果本课程自己的卷子已经考过推导，那就是推导级；
    这比在课件页里扫到「梯度」两个字要可信得多。没有题就返回识记，
    并在依据里写明「无考题证据」。
    """
    if not questions:
        return "识记", "课件页无明确深度证据，且本课程题库暂无该考点的题目"
    best = 0
    hit_type = ""
    for q in questions:
        lvl = 1
        if q.question_type in ("derivation", "comprehensive", "proof"):
            lvl = 3
        elif q.question_type in ("numeric", "short_answer") or any(
                m in (q.stem or "") for m in ("计算", "求解", "推导", "证明")):
            lvl = 2
        if lvl > best:
            best, hit_type = lvl, q.question_type
    return DEPTH_ORDER[best], f"本课程题库中已有 {len(questions)} 道该考点题目（如 {hit_type}）"


def _depth_marks_in(text: str) -> str:
    """在一段文字里找最高层级的深度标记（不做关键词门控）。"""
    best = 0
    for depth, marks in _DEPTH_MARKS.items():
        if any(m in (text or "") for m in marks):
            best = max(best, DEPTH_ORDER.index(depth))
    return DEPTH_ORDER[best]


def _depth_of(texts: list[str], keywords: list[str]) -> str:
    """只在该考点关键词所在的行里找深度证据。

    整页扫关键词会把「代码页里出现的梯度」也算成推导级证据，
    实测把「MindSpore 框架概念」判成了推导——深度必须来自相关上下文。
    """
    best = 0
    for text in texts[:4]:
        for line in (text or "").split("\n"):
            if not any(k.lower() in line.lower() for k in keywords if len(k) >= 2):
                continue
            best = max(best, DEPTH_ORDER.index(_depth_marks_in(line)))
    return DEPTH_ORDER[best]


# --------------------------------------------------------------- 范围表构建
def build_course_scope(db: Session, *, max_pages_per_kp: int = 8,
                       force: bool = False) -> dict:
    """从老师 PPT 页级片段构建/刷新课程考点范围表。

    幂等：同一个考点重复构建只更新同一行（unit_id 由 kp_id 决定），
    已存在的分析结果不会被追加成重复行。
    """
    kps = db.execute(select(E.KnowledgePoint).where(
        E.KnowledgePoint.status == "active")).scalars().all()
    doc_count = db.execute(select(E.Document).where(
        E.Document.source_category == "course_slides")).scalars().all()
    # 深度证据只取课程自身的题目（历年卷/课堂题/笔记题），不含 AI 变式与外部题
    course_qs = db.execute(select(E.Question).where(
        E.Question.active.is_(True),
        E.Question.origin.in_(("original", "human", "note")))).scalars().all()
    questions_by_kp: dict[str, list[E.Question]] = {}
    for q in course_qs:
        if q.main_kp_id:
            questions_by_kp.setdefault(q.main_kp_id, []).append(q)
    built = covered = partial = not_found = 0
    _depth_records: list[dict] = []
    for kp in kps:
        hits = _kp_slides(db, kp)
        if not force:
            existing = db.execute(select(E.CourseScopeUnit).where(
                E.CourseScopeUnit.kp_id == kp.kp_id)).scalars().first()
            if existing is not None and existing.coverage in ("covered", "partial"):
                continue
        top = hits[:max_pages_per_kp]
        by_file: dict[str, dict] = {}
        for h in top:
            slot = by_file.setdefault(h["file"], {"file": h["file"], "pages": [],
                                                  "chunk_ids": [], "titles": []})
            if h["page"] not in slot["pages"]:
                slot["pages"].append(h["page"])
            slot["chunk_ids"].append(h["chunk_id"])
            if h["title"] and h["title"] not in slot["titles"]:
                slot["titles"].append(h["title"])
        for slot in by_file.values():
            slot["pages"].sort()
        ppt_refs = list(by_file.values())

        texts = [h["text"] for h in top]
        formulas: list[str] = []
        for t in texts:
            for line in (t or "").split("\n"):
                line = line.strip()
                if not (6 <= len(line) <= 90):
                    continue
                if not _FORMULA_LINE.search(line):
                    continue
                # 没有中文的长英文句子通常是正文说明，不是公式
                if len(line) > 60 and not _CJK.search(line):
                    continue
                if line not in formulas:
                    formulas.append(line)
        methods = [m for m in _METHOD_WORDS if any(m.lower() in t.lower() for t in texts)]
        concepts = [h["title"] for h in top
                    if h["title"] and not _PAGE_TITLE.match(h["title"].strip())
                    and not h["title"].startswith("第 ")]
        dedup_concepts: list[str] = []
        for c in concepts:
            if c not in dedup_concepts:
                dedup_concepts.append(c)

        coverage = ("covered" if len(top) >= 3 else
                    "partial" if top else "not_found")
        unit = db.execute(select(E.CourseScopeUnit).where(
            E.CourseScopeUnit.kp_id == kp.kp_id)).scalars().first()
        if unit is None:
            unit = E.CourseScopeUnit(unit_id=f"scope_{kp.kp_id}")
            db.add(unit)
        unit.kp_id = kp.kp_id
        unit.chapter = kp.chapter
        unit.name = kp.name
        unit.depth = _depth_of(texts, [k for h in top for k in h["matched"]]) \
            if top else "理解"
        ppt_depth = unit.depth
        bank_qs = questions_by_kp.get(kp.kp_id, [])
        bank_depth, bank_basis = _bank_depth(bank_qs)
        if DEPTH_ORDER.index(bank_depth) > DEPTH_ORDER.index(ppt_depth):
            unit.depth = bank_depth
        depth_basis = (f"课件页深度证据：{ppt_depth}；课程题目证据：{bank_depth}"
                       f"（{bank_basis}）；取较高者作为可支持深度")
        _depth_records.append({"unit": unit, "ppt": ppt_depth,
                               "bank": bank_depth, "basis": depth_basis})
        unit.ppt_refs = ppt_refs
        unit.concepts = dedup_concepts[:8]
        unit.formulas = formulas[:6]
        unit.methods = methods[:6]
        unit.teacher_basis = (kp.key_basis or "")[:600]
        unit.page_hits = len({(s["file"], p) for s in ppt_refs for p in s["pages"]})
        unit.chunk_hits = len(top)
        unit.coverage = coverage
        unit.evidence = {
            "slides_documents": len(doc_count),
            "depth_basis": depth_basis,
            "course_questions": len(bank_qs),
            "matched_pages": [
                {"file": h["file"], "page": h["page"], "score": h["score"],
                 "matched": h["matched"]} for h in top[:6]],
            "note": ("页码来自老师 PPT 的页级片段（printed_page）；"
                     "coverage=not_found 只表示页级片段未直接命中，不等同于超纲"),
        }
        unit.built_at = E.utcnow()
        built += 1
        covered += coverage == "covered"
        partial += coverage == "partial"
        not_found += coverage == "not_found"

    # 同章节的深度可以补「本考点自身没有题目证据」的空档，但要标出来源，
    # 不能伪装成这个考点自己的证据（例：SVM 核函数没有独立考题，同章对偶是推导级）。
    chapter_best: dict[str, str] = {}
    for rec in _depth_records:
        ch = rec["unit"].chapter or ""
        cur = chapter_best.get(ch, "识记")
        if DEPTH_ORDER.index(rec["unit"].depth) > DEPTH_ORDER.index(cur):
            chapter_best[ch] = rec["unit"].depth
    for rec in _depth_records:
        unit = rec["unit"]
        if unit.depth == "识记":
            sibling = chapter_best.get(unit.chapter or "", "识记")
            if DEPTH_ORDER.index(sibling) > 0:
                unit.depth = sibling
                ev = dict(unit.evidence or {})
                ev["depth_basis"] = (
                    rec["basis"] + f"；本考点无独立证据，取同章节最高深度 {sibling}"
                    "（仅作范围参考，不当作本考点的直接证据）")
                unit.evidence = ev
    db.commit()
    return {"units": built, "covered": covered, "partial": partial,
            "not_found": not_found, "kp_total": len(kps),
            "slides_documents": len(doc_count)}


# --------------------------------------------------------------- 范围查询
def scope_brief(db: Session, kp_ids: list[str] | None = None) -> list[dict]:
    stmt = select(E.CourseScopeUnit)
    if kp_ids:
        stmt = stmt.where(E.CourseScopeUnit.kp_id.in_(kp_ids))
    rows = db.execute(stmt).scalars().all()
    out = []
    for u in rows:
        out.append({
            "kp_id": u.kp_id, "name": u.name, "chapter": u.chapter,
            "depth": u.depth, "coverage": u.coverage,
            "ppt_refs": u.ppt_refs, "concepts": u.concepts,
            "formulas": u.formulas, "methods": u.methods,
            "page_hits": u.page_hits,
        })
    return out


def scope_overview(db: Session) -> dict:
    rows = db.execute(select(E.CourseScopeUnit)).scalars().all()
    by_coverage = Counter(u.coverage for u in rows)
    by_depth = Counter(u.depth for u in rows)
    return {
        "total": len(rows),
        "by_coverage": dict(by_coverage),
        "by_depth": dict(by_depth),
        "rules": ("页码来自老师 PPT 页级片段；深度为课件页里出现的最高层级证据"
                  "（识记<理解<计算<推导）；未命中的考点标为待确认依据不足，"
                  "而不是自动判为超纲"),
    }


def _scope_packet(db: Session, item: E.ExternalItem) -> dict:
    """把候选题可用的范围证据整理成模型可读的输入（不含答案）。"""
    kp_ids = list(item.kp_ids or [])
    units = {u.kp_id: u for u in db.execute(
        select(E.CourseScopeUnit).where(
            E.CourseScopeUnit.kp_id.in_(kp_ids))).scalars().all()} if kp_ids else {}
    packet = []
    for kp_id in kp_ids:
        u = units.get(kp_id)
        kp = db.get(E.KnowledgePoint, kp_id)
        if u is None:
            packet.append({"kp_id": kp_id,
                           "name": kp.name if kp else kp_id,
                           "coverage": "not_found",
                           "note": "该考点没有范围表记录，不能据此判定超纲"})
            continue
        packet.append({
            "kp_id": u.kp_id, "name": u.name, "chapter": u.chapter,
            "depth": u.depth, "coverage": u.coverage,
            "concepts": u.concepts[:6], "formulas": u.formulas[:3],
            "methods": u.methods[:5],
            "ppt_pages": [{"file": s["file"], "pages": s["pages"][:6]}
                          for s in (u.ppt_refs or [])][:3],
        })
    out = {"item_title": item.title, "excerpt": item.content_excerpt[:900],
           "category": item.category, "scope_packet": packet}
    if not packet:
        # 采集阶段没有给出候选考点（例如线性代数工具类题目）。
        # 这里明确告诉模型「没有候选」而不是让它自由发挥，避免它硬套一个考点。
        out["candidate_note"] = (
            "本题未预设候选考点。请先判断它考查的知识点是否落在本课程章节内；"
            "若不在，scope_class 给 extension、main_kp 留空并列出具体超纲点；"
            "若你认为落在课程内，main_kp 必须填本课程真实考点 id，"
            "但此时你没有 PPT 证据，应给 unconfirmed。")
    return out


_CLASSIFY_SYS = (
    "你是机器学习课程助教。你的任务是判断一道外部题目相对【本课程老师 PPT 的考查范围】"
    "属于哪一档，而不是判断它是不是机器学习题。\n"
    "严格规则：\n"
    "1. 考点相同不代表深度相同：要比较题目要求的推导/计算/证明程度是否超过 PPT 给出的层级。\n"
    "2. PPT 未命中（coverage=not_found）不能据此判为超纲；这种情况只能给 unconfirmed。\n"
    "3. 只要题目依赖 PPT 未涉及的方法或理论（如深度学习训练技巧、分布式训练、"
    "工程部署、超出课程章节的模型），判为 extension，并写明具体超纲点。\n"
    "4. 无法可靠判断（信息不足、跨多个考点、需要原题图）时给 unconfirmed，并写出不确定原因。\n"
    "只输出 JSON，不要解释文字。字段：main_kp, aux_kps, scope_class, confidence, "
    "ppt_refs, out_of_scope_points, rationale, uncertainties。\n"
    "scope_class 只能是 in_scope | in_scope_advanced | extension | unconfirmed；"
    "confidence 是 0-1 的小数；ppt_refs 是 [{\"file\":..., \"page\":...}]，"
    "必须来自给定证据里真实存在的文件与页码；不能编造。"
    "aux_kps / out_of_scope_points / uncertainties 一律输出 JSON 数组（即使只有一项），"
    "不要把它们写成一段字符串。"
)


def classify_item(db: Session, item: E.ExternalItem, *, force: bool = False) -> dict:
    """DeepSeek 逐题范围匹配；返回值与实际写库字段一致。"""
    if item.scope_class != "unconfirmed" and not force and item.review_status == "auto":
        return {"item_id": item.item_id, "skipped": True,
                "scope_class": item.scope_class,
                "note": "已分类，未重复调用模型"}
    packet = _scope_packet(db, item)
    msgs = [
        {"role": "system", "content": _CLASSIFY_SYS},
        {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
    ]
    try:
        res = model_provider.chat(
            db, messages=msgs, skill_name="scope_match",
            prompt_version=PROMPT_VERSION, temperature=0.1, max_tokens=900,
            timeout=60.0)
    except model_provider.ModelUnavailable as e:
        item.review_status = "pending"
        item.uncertainties = [f"模型不可用：{e.status}"]
        db.commit()
        return {"item_id": item.item_id, "status": e.status,
                "scope_class": "unconfirmed", "error": e.message}

    raw = (res.get("content") or "").strip()
    parsed = _parse_json(raw)
    problems: list[str] = []
    if parsed is None:
        item.scope_class = "unconfirmed"
        item.review_status = "pending"
        item.rationale = "模型输出不是合法 JSON，未采用"
        item.uncertainties = [raw[:300]]
        item.model = res.get("model", "")
        item.prompt_version = PROMPT_VERSION
        item.reviewed_at = E.utcnow()
        db.commit()
        return {"item_id": item.item_id, "scope_class": "unconfirmed",
                "parsed": False, "problems": ["json_invalid"]}

    valid_kps = {u.kp_id for u in db.execute(select(E.CourseScopeUnit)).scalars().all()}
    raw_main = str(parsed.get("main_kp") or "").strip()
    main_kp = _resolve_kp(db, raw_main)
    if raw_main and not main_kp:
        problems.append(f"main_kp 无法对应到本课程考点：{raw_main}")
    elif raw_main and raw_main != main_kp:
        problems.append(f"main_kp 由考点名「{raw_main}」解析为 {main_kp}")
    aux = [k for k in _as_list(parsed.get("aux_kps")) if k in valid_kps]

    # PPT 页码必须是该考点证据里真实存在的，否则丢弃（不允许编造出处）
    allowed_pairs = set()
    for kp_id in [main_kp] + aux:
        unit = db.execute(select(E.CourseScopeUnit).where(
            E.CourseScopeUnit.kp_id == kp_id)).scalars().first()
        if unit:
            for slot in unit.ppt_refs or []:
                for p in slot["pages"]:
                    allowed_pairs.add((slot["file"], int(p)))
    refs = []
    for r in parsed.get("ppt_refs") or []:
        if not isinstance(r, dict):
            continue
        f, p = str(r.get("file") or ""), r.get("page")
        try:
            p = int(p)
        except (TypeError, ValueError):
            continue
        if (f, p) in allowed_pairs:
            refs.append({"file": f, "page": p})
        else:
            problems.append(f"丢弃不存在的 PPT 出处：{f} p.{p}")

    scope_class = str(parsed.get("scope_class") or "").strip()
    if scope_class not in SCOPE_LABELS:
        problems.append(f"scope_class 非法：{scope_class}")
        scope_class = "unconfirmed"
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence"))))
    except (TypeError, ValueError):
        confidence = 0.0
    oos = [x[:200] for x in _as_list(parsed.get("out_of_scope_points"))]
    uncertainties = [x[:200] for x in _as_list(parsed.get("uncertainties"))]

    # 代码侧兜底：不满足证据要求就降级为待确认，而不是照抄模型结论
    coverage = {p["kp_id"]: p.get("coverage") for p in packet["scope_packet"]}
    if scope_class == "extension" and not oos:
        problems.append("判为超纲但没有给出具体超纲点")
        scope_class = "unconfirmed"
    if scope_class in ("in_scope", "in_scope_advanced") and not refs:
        problems.append("判为课内但没有可核对的 PPT 出处")
        scope_class = "unconfirmed"
    if scope_class != "extension" and coverage.get(main_kp) == "not_found":
        problems.append("主考点在 PPT 中没有命中，不能判为课内或超纲")
        scope_class = "unconfirmed"
    if confidence < 0.6 and scope_class != "unconfirmed":
        problems.append(f"置信度 {confidence:.2f} 低于阈值，转入待确认")
        scope_class = "unconfirmed"

    item.kp_ids = [main_kp] if main_kp else (item.kp_ids or [])
    item.aux_kp_ids = aux
    item.scope_class = scope_class
    item.scope_confidence = confidence
    item.ppt_refs = refs
    item.out_of_scope_points = oos
    item.rationale = str(parsed.get("rationale") or "")[:1200]
    item.uncertainties = uncertainties + [p for p in problems if p not in uncertainties]
    item.review_status = "auto" if scope_class != "unconfirmed" else "pending"
    item.model = res.get("model", "")
    item.prompt_version = PROMPT_VERSION
    item.reviewed_at = E.utcnow()
    db.commit()
    return {"item_id": item.item_id, "scope_class": scope_class,
            "confidence": confidence, "main_kp": main_kp, "aux_kps": aux,
            "ppt_refs": refs, "out_of_scope_points": oos,
            "rationale": item.rationale, "uncertainties": item.uncertainties,
            "problems": problems, "model": item.model,
            "call_id": res.get("call_id", ""), "parsed": True,
            "duration_ms": res.get("duration_ms", 0)}


def _as_list(value) -> list[str]:
    """模型有时把列表字段写成一段字符串：整段当一个元素，不要按字符拆开。"""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def _resolve_kp(db: Session, raw: str) -> str:
    """把模型返回的 main_kp 落到真实考点 id。

    模型有时不输出 kp_id 而直接写考点中文名。这里**只接受 id 命中或考点名精确命中**，
    不做相似度匹配——否则「线性回归基本假设」这种杜撰的名字会被张冠李戴到
    「线性回归损失函数（MSE）」上，反而制造假证据。
    """
    raw = str(raw or "").strip()
    if not raw:
        return ""
    units = db.execute(select(E.CourseScopeUnit)).scalars().all()
    if any(u.kp_id == raw for u in units):
        return raw
    key = _norm(raw)
    for u in units:
        if _norm(u.name) == key:
            return u.kp_id
    return ""


def _parse_json(text: str) -> dict | None:
    """模型常见输出：裸 JSON / ```json 包裹 / 前后有说明文字。"""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    body = fence.group(1) if fence else text
    if not fence:
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            return None
        body = body[start:end + 1]
    try:
        out = json.loads(body)
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


# --------------------------------------------------------------- 变式题复检
def judge_generated(db: Session, kp_id: str, stem: str) -> dict:
    """对 AI 新生成的变式题重新检查课程范围（不调用模型）。

    §四要求「新生成的变式题也必须重新检查课程范围」——原题在范围内，
    不代表变式题也在范围内（模型很容易把难度拉高）。这里用与建表同一套
    证据标准复判：
      * 考点不在范围表 → 待确认（不入题库）
      * 考点在 PPT 页级片段里未命中 → 待确认（不能据此判超纲）
      * 题干里要求的最高深度层级超过该考点的 PPT 深度 → 课程内提高
      * 否则 → 课程范围内
    已知局限：规则只能识别「深度超出」，识别不了「换了课程外的方法」；
    但变式题必须引用真实课件片段（source_chunk_ids 校验），话题越界受此约束。
    """
    kp_id = str(kp_id or "").strip()
    if not kp_id:
        return {"scope_class": "unconfirmed", "depth": "", "basis": "变式题没有标注考点"}
    unit = db.execute(select(E.CourseScopeUnit).where(
        E.CourseScopeUnit.kp_id == kp_id)).scalars().first()
    if unit is None:
        return {"scope_class": "unconfirmed", "depth": "",
                "basis": f"考点 {kp_id} 不在课程范围表内，无法判断是否超纲"}
    if unit.coverage == "not_found":
        return {"scope_class": "unconfirmed", "depth": unit.depth,
                "basis": (f"考点「{unit.name}」在 PPT 页级片段中没有命中；"
                          "按规则不能据此判为超纲，转待确认")}
    kp = db.get(E.KnowledgePoint, kp_id)
    # 生成题的题干本身就是「相关上下文」，不需要再做关键词门控；
    # 关键词门控是给整页课件用的，套到单句题干上会一律判成「识记」。
    stem_depth = _depth_marks_in(stem or "")
    try:
        over = DEPTH_ORDER.index(stem_depth) > DEPTH_ORDER.index(unit.depth)
    except ValueError:
        over = False
    cls = "in_scope_advanced" if over else "in_scope"
    basis = (f"课件支持深度={unit.depth}，变式题干体现的层级={stem_depth}（覆盖={unit.coverage}）")
    if over:
        basis += "；题干要求的推导/计算深度高于课件层级，归入课程内提高"
    return {"scope_class": cls, "depth": stem_depth, "basis": basis,
            "kp_depth": unit.depth, "coverage": unit.coverage}


# --------------------------------------------------------------- 入库
def find_duplicate(db: Session, item: E.ExternalItem) -> str:
    """按内容哈希 + 题族去重，返回已有 question_id（空表示不重复）。"""
    if not item.content_hash:
        item.content_hash = _content_hash(item.content_excerpt or item.title)
    row = db.execute(select(E.Question).where(
        E.Question.active.is_(True))).scalars().all()
    target = _norm(item.content_excerpt or item.title)
    for q in row:
        if _content_hash(q.stem) == item.content_hash:
            return q.question_id
        if len(target) >= 20 and _norm(q.stem)[:200] == target[:200]:
            return q.question_id
    return ""


def import_question(db: Session, item: E.ExternalItem) -> dict:
    """把「课程范围内/课程内提高」且有出处的候选题转成题库题目。

    超纲与待确认的题目**不入题库**：它们只留在扩展性学习列表里，
    避免默认混进期末考试题库。题干只保存短摘录，完整题目走来源链接。
    """
    if item.kind != "question":
        return {"item_id": item.item_id, "imported": False, "reason": "not_a_question"}
    if item.scope_class not in ("in_scope", "in_scope_advanced"):
        return {"item_id": item.item_id, "imported": False,
                "reason": f"scope_class={item.scope_class} 不入期末题库"}
    if item.imported_question_id:
        return {"item_id": item.item_id, "imported": False, "reused": True,
                "question_id": item.imported_question_id}
    dup = find_duplicate(db, item)
    if dup:
        item.duplicate_of = dup
        db.commit()
        return {"item_id": item.item_id, "imported": False,
                "duplicate_of": dup, "reason": "与现有题目内容重复"}

    src = db.get(E.ExternalSource, item.source_id)
    kp_id = (item.kp_ids or [""])[0]
    qid = f"extq_{item.item_id.split('_', 1)[-1]}"
    row = E.Question(
        row_id=E.new_id("qrow"), question_id=qid, question_version=1,
        origin="external", original_exam_id=item.item_id,
        original_question_no="", question_type=item.question_type or "short_answer",
        stem=item.content_excerpt, options=[], score=5.0, main_kp_id=kp_id,
        aux_kp_ids=list(item.aux_kp_ids or []),
        question_family_id=item.family_id or f"fam_{qid}",
        review_status="pending", answer_status="unknown", answer_origin="none",
        active=True, scope_class=item.scope_class, external_item_id=item.item_id,
        source_refs=[{"external_item_id": item.item_id, "url": item.url,
                      "source": src.name if src else "",
                      "scope_class": item.scope_class,
                      "ppt_refs": item.ppt_refs,
                      "note": "外部题目：题干为短摘录，完整题目见来源链接"}],
        notes=(f"外部来源：{src.name if src else item.source_id}；"
               f"范围分类：{SCOPE_LABELS.get(item.scope_class, item.scope_class)}；"
               "题干为短摘录，完整题目见来源链接；"
               "分类依据见 external_items.rationale"),
    )
    db.add(row)
    item.imported_question_id = qid
    item.content_hash = item.content_hash or _content_hash(item.content_excerpt)
    db.commit()
    return {"item_id": item.item_id, "imported": True, "question_id": qid,
            "scope_class": item.scope_class}


# --------------------------------------------------------------- 成绩分列
def attempt_scope_breakdown(db: Session, paper_id: str, attempt_id: str) -> dict:
    """把一次作答的成绩按「课程范围 / 扩展性学习」分开统计（§五 第 5 条）。

    扩展题成绩单独呈现，不并入期末掌握度：学生做扩展题错得多，
    不代表他没掌握课程内容。
    """
    items = db.execute(select(E.PaperItem).where(
        E.PaperItem.paper_id == paper_id)).scalars().all()
    scores = {s.question_id: s for s in db.execute(select(E.AnswerScore).where(
        E.AnswerScore.attempt_id == attempt_id)).scalars().all()}
    from backend.app.services.score_policy import admissible_score
    attempt = db.get(E.Attempt, attempt_id)
    version = db.get(E.AssessmentVersion, attempt.assessment_version_id) if attempt and attempt.assessment_version_id else None
    buckets = {
        "course": {"label": "课程范围内", "items": 0, "correct": 0, "scored": 0,
                   "awarded": 0.0, "max": 0.0, "nominal_max": 0.0, "pending_max": 0.0, "pending": 0},
        "extension": {"label": "扩展性学习", "items": 0, "correct": 0, "scored": 0,
                      "awarded": 0.0, "max": 0.0, "nominal_max": 0.0, "pending_max": 0.0, "pending": 0},
    }
    for it in items:
        snap = it.snapshot or {}
        cls = snap.get("scope_class") or "in_scope"
        key = "extension" if cls == "extension" else "course"
        b = buckets[key]
        b["items"] += 1
        b["nominal_max"] += float(it.score)
        s = scores.get(it.question_id)
        if not admissible_score(attempt, s, version):
            b["pending"] += 1
            b["pending_max"] += float(it.score)
            continue
        b["max"] += float(s.max_score or it.score)
        b["scored"] += 1
        b["awarded"] += float(s.awarded_score or 0)
        if s.correct:
            b["correct"] += 1
    for b in buckets.values():
        graded = b["scored"]
        b["graded"] = graded
        b["awarded"] = round(b["awarded"], 2)
        b["max"] = round(b["max"], 2)
        b["accuracy"] = round(b["correct"] / graded, 3) if graded else None
    return {
        "by_scope": buckets,
        "course_counts_for_mastery": True,
        "extension_counts_for_mastery": False,
        "note": ("课程范围内题目计入期末掌握度；扩展性学习题目单独统计正确率，"
                 "不计入期末掌握，也不影响阶段判定"),
    }


def external_items_for_ui(db: Session, *, category: str = "", scope: str = "",
                          kp_id: str = "", group: str = "",
                          limit: int = 200) -> dict:
    """「扩展学习」页的数据源。

    只返回链接与元数据，不返回完整正文（未获转载许可）。
    视频不给时间戳：只给 `?p=N` 分集跳转，分集序号来自平台接口。
    `group` 是资源类型分组（可多选标签式），与旧的 `category` 单选可以叠加使用。
    """
    stmt = select(E.ExternalItem).where(E.ExternalItem.active.is_(True))
    if category:
        stmt = stmt.where(E.ExternalItem.category == category)
    if scope:
        stmt = stmt.where(E.ExternalItem.scope_class == scope)
    rows = db.execute(stmt).scalars().all()
    src = {s.source_id: s for s in db.execute(select(E.ExternalSource)).scalars().all()}
    kp_names = {k.kp_id: k.name for k in db.execute(
        select(E.KnowledgePoint)).scalars().all()}
    if kp_id:
        rows = [r for r in rows if kp_id in (r.kp_ids or [])
                or kp_id in (r.aux_kp_ids or [])]

    # 分组计数在 group 过滤「之前」算，筛选 chip 上的数字才反映当前其它条件。
    tagged = [(r, resource_groups(r, src.get(r.source_id))) for r in rows]
    group_counts = {key: 0 for key in GROUP_ORDER}
    for _r, groups in tagged:
        for g in groups:
            group_counts[g] += 1
    base_count = len(tagged)
    if group:
        tagged = [(r, groups) for r, groups in tagged if group in groups]
    tagmap = {r.item_id: groups for r, groups in tagged}
    rows = [r for r, _ in tagged]
    rows.sort(key=lambda r: (CATEGORY_ORDER.get(r.category, 9),
                             SCOPE_ORDER.get(r.scope_class, 9),
                             r.item_id))
    out = []
    for r in rows[:limit]:
        s = src.get(r.source_id)
        groups = tagmap.get(r.item_id, [])
        out.append({
            "item_id": r.item_id, "kind": r.kind, "category": r.category,
            "category_label": CATEGORY_LABELS.get(r.category, r.category),
            "title": r.title, "url": r.url, "url_verified": r.url_verified,
            "excerpt": r.content_excerpt[:300],
            "source_name": s.name if s else r.source_id,
            "source_org": s.org if s else "",
            "source_region": (s.region if s else "unknown") or "unknown",
            "source_verified": bool(s and s.verified),
            "source_verify_status": s.verify_status if s else "unverified",
            "allows_copy": bool(s and s.allows_copy),
            "license": s.license if s else "unknown",
            "resource_groups": groups,
            "group_labels": [GROUP_LABELS[g] for g in groups],
            "kp_ids": r.kp_ids, "kp_names": [kp_names.get(k, k) for k in (r.kp_ids or [])],
            "aux_kp_names": [kp_names.get(k, k) for k in (r.aux_kp_ids or [])],
            "scope_class": r.scope_class,
            "scope_label": SCOPE_LABELS.get(r.scope_class, r.scope_class),
            "scope_confidence": r.scope_confidence,
            "ppt_refs": r.ppt_refs,
            "out_of_scope_points": r.out_of_scope_points,
            "rationale": r.rationale,
            "uncertainties": r.uncertainties,
            "answer_status": r.answer_status, "answer_ref": r.answer_ref,
            "recommended": _recommend_reason(r),
            "difficulty": r.difficulty, "question_type": r.question_type,
            "imported_question_id": r.imported_question_id,
            "duplicate_of": r.duplicate_of,
            "review_status": r.review_status,
            "reviewed_at": r.reviewed_at,
        })
    return {"items": out, "total": len(rows), "base_count": base_count,
            "group_counts": group_counts, "group_labels": GROUP_LABELS,
            "filters": {"category": category, "scope": scope,
                        "kp_id": kp_id, "group": group},
            "rules": ("只展示来源链接与元数据，正文以短摘录为限；"
                      "视频只给分集跳转，未核实的不给时间戳；"
                      "资源类型按条目类型与来源地区台账派生，一个条目可属于多组；"
                      "明确超纲的题不进期末题库")}


CATEGORY_ORDER = {"university_exam": 0, "interview": 1, "video": 2}
SCOPE_ORDER = {"in_scope": 0, "in_scope_advanced": 1, "extension": 2,
               "unconfirmed": 3}


def _recommend_reason(item: E.ExternalItem) -> str:
    """推荐理由必须落在「为什么值得看」上，而不是复述分类结果。"""
    if item.kind == "video":
        return ("课件没展开的推导步骤可以在这一分集里看完整过程；"
                "只跳分集，不给时间戳")
    if item.scope_class == "in_scope":
        return "考点与深度都在课程范围内，可直接用来做课内专项训练"
    if item.scope_class == "in_scope_advanced":
        return "考点在课件内但要求更完整，适合课内提高训练"
    if item.scope_class == "extension":
        return ("课件未涉及这部分内容，属于扩展视野；"
                "不计入期末掌握度")
    return ("证据不足，暂列待确认；确认前不用于出卷，也不计入统计")


def external_summary(db: Session) -> dict:
    items = db.execute(select(E.ExternalItem).where(
        E.ExternalItem.active.is_(True))).scalars().all()
    sources = db.execute(select(E.ExternalSource)).scalars().all()
    src = {s.source_id: s for s in sources}
    by_scope = Counter(i.scope_class for i in items)
    by_cat = Counter(i.category for i in items)
    by_group: Counter = Counter()
    for i in items:
        by_group.update(resource_groups(i, src.get(i.source_id)))
    return {
        "items": len(items),
        "by_scope": {SCOPE_LABELS[k]: v for k, v in by_scope.items()},
        "by_category": {CATEGORY_LABELS[k]: v for k, v in by_cat.items()},
        "by_group": {GROUP_LABELS[g]: by_group.get(g, 0) for g in GROUP_ORDER},
        "by_region": dict(Counter((s.region or "unknown") for s in sources)),
        "imported_to_bank": sum(1 for i in items if i.imported_question_id),
        "pending_confirmation": sum(1 for i in items if i.scope_class == "unconfirmed"),
        "sources": len(sources),
        "sources_verified": sum(1 for s in sources if s.verified),
        "rules": ("扩展性学习与期末复习分开统计；明确超纲的题目不入期末题库；"
                  "PPT 未命中只进待确认队列；资源类型分组来自来源地区台账，"
                  "一个条目可同时属于多组"),
    }
