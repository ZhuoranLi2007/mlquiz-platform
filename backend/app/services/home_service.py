"""Home overview + about-page facts.

Two rules drive this module:

1. **No invented numbers.** Every value is computed from the project database.
   When the evidence does not exist (no questions tagged with a knowledge point,
   no completed practice) the field is None/0 and the UI must say "待统计"
   instead of showing a decorative figure.
2. **AI variants are not "past-exam frequency."** `origin == "ai"` rows are
   counted separately, because an AI question generated five minutes ago says
   nothing about how often the course exams test that point.

`已复习次数` counts COMPLETED practice sessions touching the point, not question
rows: one paper containing ten questions on the same point is one session, and
merely opening a material page is not a review.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import material_naming

# A point is only "in the exam" for frequency purposes when the question came
# from real material (original exam / human-curated from notes), not from AI.
ORIGINAL_ORIGINS = ("original", "human", "note")


def _latest_questions(rows: list[E.Question]) -> list[E.Question]:
    latest: dict[str, E.Question] = {}
    for q in rows:
        cur = latest.get(q.question_id)
        if cur is None or q.question_version > cur.question_version:
            latest[q.question_id] = q
    return list(latest.values())


def _sessions_by_kp(db: Session) -> dict[str, dict]:
    """Completed practice sessions per knowledge point.

    One submitted attempt counts once per point, however many questions of that
    point it contains. Uses the paper-item snapshot so an item keeps counting
    for the point it was tested on even if the question is later revised.
    """
    items = db.execute(select(E.PaperItem)).scalars().all()
    kp_of_item: dict[str, str] = {}
    for it in items:
        snap = it.snapshot or {}
        kp = snap.get("main_kp_id") or ""
        if kp:
            kp_of_item[it.paper_id + "|" + it.question_id] = kp
            kp_of_item.setdefault(it.item_id, kp)
    paper_kps: dict[str, set[str]] = defaultdict(set)
    for key, kp in kp_of_item.items():
        paper_id = key.split("|")[0] if "|" in key else ""
        if paper_id:
            paper_kps[paper_id].add(kp)
    out: dict[str, dict] = {}
    attempts = db.execute(select(E.Attempt).where(
        E.Attempt.status == "submitted")).scalars().all()
    for a in attempts:
        for kp in paper_kps.get(a.paper_id, set()):
            rec = out.setdefault(kp, {"count": 0, "last_at": ""})
            rec["count"] += 1
            if (a.submitted_at or "") > rec["last_at"]:
                rec["last_at"] = a.submitted_at or ""
    return out


def _wrong_count_by_kp(db: Session) -> dict[str, int]:
    rows = db.execute(select(E.WrongBookEntry)).scalars().all()
    out: dict[str, int] = defaultdict(int)
    for w in rows:
        if (w.status or "") in ("verified", "corrected"):
            continue
        kp = w.kp_id or ""
        if kp:
            out[kp] += 1
    return dict(out)


def focus_points(db: Session, *, limit: int = 8,
                 sort: str = "exam") -> dict:
    """High-frequency points with真实 counts, or an honest empty result."""
    kps = db.execute(select(E.KnowledgePoint).where(
        E.KnowledgePoint.status == "active")).scalars().all()
    qs = _latest_questions(db.execute(
        select(E.Question).where(E.Question.active.is_(True))).scalars().all())
    states = {s.kp_id: s for s in db.execute(
        select(E.KnowledgeState)).scalars().all()}
    sessions = _sessions_by_kp(db)
    wrong = _wrong_count_by_kp(db)

    by_kp: dict[str, list[E.Question]] = defaultdict(list)
    for q in qs:
        if q.main_kp_id:
            by_kp[q.main_kp_id].append(q)

    rows = []
    for kp in kps:
        items = by_kp.get(kp.kp_id, [])
        original = [q for q in items if (q.origin or "") in ORIGINAL_ORIGINS]
        ai_items = [q for q in items if (q.origin or "") == "ai"]
        # V2.1 §5：外部题（其他高校/面试题）单独计数，绝不并入「历年高频」
        ext_items = [q for q in items if (q.origin or "") == "external"]
        exam_ids = {q.original_exam_id for q in original if q.original_exam_id}
        st = states.get(kp.kp_id)
        s = sessions.get(kp.kp_id) or {"count": 0, "last_at": ""}
        rows.append({
            "kp_id": kp.kp_id,
            "name": kp.name,
            "chapter": kp.chapter,
            "question_count": len(original),
            "ai_question_count": len(ai_items),
            "external_question_count": len(ext_items),
            "paper_count": len(exam_ids),
            "review_count": s["count"],
            "last_reviewed_at": s["last_at"],
            "state": (st.state if st else ""),
            "state_label": (st.state if st else "未测"),
            "evidence_count": (st.evidence_count if st else 0),
            "wrong_count": wrong.get(kp.kp_id, 0),
        })

    if sort == "weak":
        # Weak first, then least reviewed, then most-tested.
        rows.sort(key=lambda r: (r["state_label"] in ("阶段稳定", "初步通过"),
                                 -r["wrong_count"], r["review_count"],
                                 -r["question_count"], r["name"]))
    else:
        rows.sort(key=lambda r: (-r["question_count"], -r["paper_count"],
                                 r["name"]))
    top = rows[:limit]
    return {
        "items": top,
        "sort": sort,
        "totals": {
            "knowledge_points": len(kps),
            "tagged_original_questions": sum(r["question_count"] for r in rows),
            "points_with_questions": sum(1 for r in rows if r["question_count"]),
            "points_reviewed": sum(1 for r in rows if r["review_count"]),
            "ai_variant_questions": sum(r["ai_question_count"] for r in rows),
            "external_questions": sum(r["external_question_count"] for r in rows),
        },
        "rules": {
            "question_count": "统计不同原始题目中包含该考点的题数；AI 变式单独计数，不计入历年高频",
            "external_question_count": ("外部题（其他高校试题/面试题）单独计数，"
                                        "不并入历年高频出现次数，也不进入期末掌握度统计"),
            "paper_count": "含该考点的不同原卷/资料来源数量",
            "review_count": "一次有完成记录的测验计一次；同一份卷子含该考点多题不重复计数，浏览资料不算复习",
            "state": "由独立有效证据数、题族覆盖与时间跨度决定；证据不足时显示『未测/证据不足』",
        },
    }


def today_tasks(db: Session, *, limit: int = 5) -> list[dict]:
    plans = db.execute(select(E.StudyPlan).where(
        E.StudyPlan.active.is_(True))).scalars().all()
    plan_ids = [p.plan_id for p in plans]
    if not plan_ids:
        return []
    rows = db.execute(select(E.PlanTask).where(
        E.PlanTask.plan_id.in_(plan_ids),
        E.PlanTask.status.notin_(("done", "completed")))).scalars().all()
    kp_names = {k.kp_id: k.name for k in db.execute(
        select(E.KnowledgePoint)).scalars().all()}
    rows.sort(key=lambda t: (t.due_date or "9999", t.title or ""))
    out = []
    for t in rows[:limit]:
        out.append({
            "task_id": t.task_id,
            "title": t.title,
            "kind": t.kind,
            "kp_id": t.kp_id or "",
            "kp_name": kp_names.get(t.kp_id or "", ""),
            "minutes": t.minutes,
            "due_date": t.due_date or "",
            "status": t.status,
            "rationale": t.rationale or "",
            "material_ref": t.material_ref or "",
        })
    return out


def recent_activity(db: Session, *, limit: int = 5) -> list[dict]:
    papers = {p.paper_id: p for p in db.execute(select(E.Paper)).scalars().all()}
    hidden = _hidden_paper_ids(db)
    rows = db.execute(select(E.Attempt).order_by(
        E.Attempt.started_at.desc())).scalars().all()
    out = []
    for a in rows:
        if a.paper_id in hidden:
            continue
        p = papers.get(a.paper_id)
        out.append({
            "attempt_id": a.attempt_id,
            "paper_id": a.paper_id,
            "paper_title": (p.title if p else "试卷已删除"),
            "status": a.status,
            "status_label": ("作答中" if a.status == "in_progress"
                             else "已交卷" if a.status == "submitted"
                             else a.status),
            "started_at": a.started_at,
            "submitted_at": a.submitted_at or "",
            "score": a.score,
            "max_score": a.max_score,
            "manual_pending_count": a.manual_pending_count,
        })
        if len(out) >= limit:
            break
    return out


def overview(db: Session, *, sort: str = "exam") -> dict:
    focus = focus_points(db, sort=sort)
    tasks = today_tasks(db)
    recent = recent_activity(db)
    # Honest first-steps: only claim a next action when data actually supports it.
    if not focus["totals"]["tagged_original_questions"]:
        path = ["导入一份课件或往年试卷", "让系统识别题目与考点", "组一套练习开始答题"]
    elif not focus["totals"]["points_reviewed"]:
        path = ["选一个高频考点开始专项练习", "交卷查看错因", "对错题安排复测"]
    else:
        path = ["复习待巩固考点对应课件", "练习新题验证", "查看错因并安排复测"]
    return {
        "focus": focus,
        "tasks": tasks,
        "recent": recent,
        "path": path,
        "model_configured": cfg.MODEL_CHAT_MODEL != "",  # refined by caller
    }


def about_facts(db: Session) -> dict:
    """Facts for 关于我们. Only values we can verify from the running system."""
    from backend.app.services import model_provider
    st = model_provider.chat_status()
    counts = {
        "documents": len(db.execute(select(E.Document)).scalars().all()),
        "questions": len(db.execute(select(E.Question).where(
            E.Question.active.is_(True))).scalars().all()),
        "papers": len(db.execute(select(E.Paper)).scalars().all()),
        "attempts": len(db.execute(select(E.Attempt)).scalars().all()),
        "knowledge_points": len(db.execute(select(E.KnowledgePoint)).scalars().all()),
        "model_calls": len(db.execute(select(E.ModelCall)).scalars().all()),
    }
    return {
        "app_name": "机器学习学情诊断平台",
        "version": cfg.APP_VERSION,
        "build_date": cfg.BUILD_DATE,
        "project_nature": "机器学习课程实践项目 / 个人学习辅助工具",
        "storage": {
            "database": "本机 SQLite 单文件数据库",
            "database_path": "data/db/（项目目录内，可整体备份导出）",
            "materials": "原始资料保存在项目 data/uploads/ 下，按内容哈希去重",
            "exports": "支持从「个人设置 → 数据管理」导出备份；备份包内含恢复说明",
        },
        "model": {
            "configured": st["configured"],
            "provider": st.get("provider", ""),
            "model": st.get("model", ""),
            "base_url": st.get("base_url", ""),
            "note": ("未配置时基础资料、答题与确定性评分仍可用；"
                     "AI 相关功能会显示为需要配置，不会用模拟结果冒充真实调用"),
        },
        "external_calls": [
            "生成参考答案与解析（含上传试卷的整卷答案册）",
            "AI 变式出题",
            "主观题按评分要点的自动评阅",
            "智能学习助手对话",
            "单题/整卷诊断的证据摘要生成",
        ],
        "sent_to_model": ("题目题干与选项、为引用而选取的资料片段、用户作答文本"
                          "（仅在主观题评阅时）；当前未接入图像识别，"
                          "因此不会发送图片给模型"),
        "retention": {
            "model_calls": "每次模型调用的模型名、耗时、token 用量、finish_reason 均留痕，可在报告中审计",
            "chat": "助手对话按会话保存在本机数据库",
            "delete": "数据清理入口在「个人设置 → 数据管理」，删除前会提示影响范围",
        },
        "counts": counts,
        "disclaimer": ("AI 参考答案、解析、主观题评分与学习建议均由算法生成，"
                       "不等同于教师或课程官方答案；遇到疑问请对照原卷与课件，"
                       "并使用「答案有疑问」反馈"),
    }


def search_all(db: Session, q: str, *, limit: int = 6) -> dict:
    """Grouped global search: 考点 / 试卷 / 资料 / 我的内容.

    The user searches in product terms ("支持向量机"), not in chunk ids. Each
    group returns display fields only, plus the route the UI should open.
    """
    term = (q or "").strip().lower()
    if not term:
        return {"query": "", "groups": [], "total": 0}

    def hit(*texts: str) -> bool:
        return any(term in (t or "").lower() for t in texts)

    kps = [k for k in db.execute(select(E.KnowledgePoint)).scalars().all()
           if hit(k.name, k.chapter, k.description)][:limit]
    papers = [p for p in db.execute(select(E.Paper).order_by(
        E.Paper.created_at.desc())).scalars().all() if hit(p.title)][:limit]
    docs = [d for d in db.execute(select(E.Document)).scalars().all()
            if hit(getattr(d, "rel_path", ""), getattr(d, "title", ""))][:limit]
    questions = [x for x in db.execute(select(E.Question).where(
        E.Question.active.is_(True))).scalars().all() if hit(x.stem)][:limit]

    groups = []
    if kps:
        groups.append({"kind": "kp", "label": "考点", "items": [
            {"id": k.kp_id, "title": k.name, "sub": k.chapter,
             "route": "/reports"} for k in kps]})
    if papers:
        groups.append({"kind": "paper", "label": "试卷", "items": [
            {"id": p.paper_id, "title": p.title,
             "sub": f"{p.item_count} 题 · 总分 {p.total_score:g}",
             "route": "/me?tab=papers"} for p in papers]})
    if docs:
        groups.append({"kind": "material", "label": "资料", "items": [
            {"id": d.document_id,
             "title": material_naming.normalize(
                 str(getattr(d, "rel_path", "")), "",
                 str(getattr(d, "source_category", "") or ""))["display_name"],
             "sub": "课程资料", "route": "/materials"} for d in docs]})
    if questions:
        groups.append({"kind": "question", "label": "题目", "items": [
            {"id": x.question_id, "title": x.stem[:60],              "sub": "题库",
             "route": "/compose?tab=create"} for x in questions]})
    return {"query": q, "groups": groups,
            "total": sum(len(g["items"]) for g in groups)}


# ------------------------------------------------------------------ 个人中心
def my_papers(db: Session, *, keyword: str = "", status: str = "",
              source: str = "", include_archived: bool = False) -> dict:
    """我的组卷：列表带真实题量、来源、作答次数与状态。

    `status` is derived, not stored: a paper that has any submitted attempt is
    `completed`, an in-progress attempt means `in_progress`, else `ready`.
    """
    papers = db.execute(select(E.Paper).order_by(
        E.Paper.created_at.desc())).scalars().all()
    attempts = db.execute(select(E.Attempt)).scalars().all()
    by_paper: dict[str, list] = defaultdict(list)
    for a in attempts:
        by_paper[a.paper_id].append(a)
    kp_names = {k.kp_id: k.name for k in db.execute(
        select(E.KnowledgePoint)).scalars().all()}
    out = []
    for p in papers:
        if getattr(p, "archived", False) and not include_archived:
            continue
        if keyword and keyword.strip().lower() not in (p.title or "").lower():
            continue
        items = db.execute(select(E.PaperItem).where(
            E.PaperItem.paper_id == p.paper_id)).scalars().all()
        kps: list[str] = []
        for it in items:
            kp = (it.snapshot or {}).get("main_kp_id") or ""
            nm = kp_names.get(kp, "")
            if nm and nm not in kps:
                kps.append(nm)
        atts = by_paper.get(p.paper_id, [])
        atts.sort(key=lambda a: (a.started_at or "", a.attempt_id), reverse=True)
        submitted = [a for a in atts if a.status == "submitted"]
        running = [a for a in atts if a.status != "submitted"]
        src = (p.source or "bank")
        src_label = {"bank": "已有题库", "ai": "AI 变式", "mixed": "混合",
                     "original": "原卷"}.get(src, "已有题库")
        st = ("completed" if submitted else
              "in_progress" if running else "ready")
        if status and status != st:
            continue
        if source and source != src:
            continue
        out.append({
            "paper_id": p.paper_id, "title": p.title, "mode": p.mode,
            "item_count": p.item_count, "total_score": p.total_score,
            "estimated_minutes": p.estimated_minutes,
            "created_at": p.created_at, "source": src, "source_label": src_label,
            "kp_names": kps[:4], "attempt_count": len(submitted),
            "status": st,
            "status_label": {"completed": "已完成", "in_progress": "作答中",
                             "ready": "已生成"}[st],
            "active_attempt_id": (running[0].attempt_id if running else ""),
            "latest_attempt_id": (atts[0].attempt_id if atts else ""),
            "open_attempt_id": (running[0].attempt_id if running
                                else (atts[0].attempt_id if atts else "")),
            "coverage_summary": _coverage_summary(p.coverage_report or {}),
            "archived": bool(getattr(p, "archived", False)),
        })
    return {"items": out, "total": len(out)}


def _coverage_summary(rep: dict) -> str:
    """Turn the internal coverage report into one readable line."""
    if not rep:
        return ""
    got = int(rep.get("selected_count") or 0)
    want = int(rep.get("requested_count") or 0)
    short = int(rep.get("shortfall") or 0)
    rej = rep.get("rejected_reasons") or {}
    bits = [f"选入 {got}/{want} 题"]
    if short:
        bits.append(f"缺口 {short} 题")
    pending = rep.get("answer_pending") or []
    if pending:
        bits.append(f"{len(pending)} 题答案未就绪")
    if rej.get("not_publishable"):
        bits.append(f"{rej['not_publishable']} 题因资料缺陷被排除")
    return " · ".join(bits)


def _hidden_paper_ids(db: Session) -> set[str]:
    """已归档或标记为测试样本的试卷：列表与「继续练习」里都不应出现。"""
    rows = db.execute(select(E.Paper)).scalars().all()
    return {p.paper_id for p in rows
            if getattr(p, "archived", False) or getattr(p, "is_test_fixture", False)}


def my_attempts(db: Session, *, keyword: str = "") -> dict:
    papers = {p.paper_id: p for p in db.execute(select(E.Paper)).scalars().all()}
    hidden = _hidden_paper_ids(db)
    rows = db.execute(select(E.Attempt).order_by(
        E.Attempt.started_at.desc())).scalars().all()
    out = []
    for a in rows:
        if a.paper_id in hidden:
            continue
        p = papers.get(a.paper_id)
        title = p.title if p else "试卷已归档"
        if keyword and keyword.strip().lower() not in title.lower():
            continue
        out.append({
            "attempt_id": a.attempt_id, "paper_id": a.paper_id,
            "paper_title": title,
            "status": a.status,
            "status_label": ("作答中" if a.status != "submitted" else
                             "待评阅" if a.manual_pending_count else "已完成"),
            "started_at": a.started_at, "submitted_at": a.submitted_at or "",
            "score": a.score, "max_score": a.max_score,
            "manual_pending_count": a.manual_pending_count,
            "diagnosis_status": a.diagnosis_status,
            "diagnosis_status_label": {
                "not_requested": "尚未生成",
                "queued": "诊断排队中",
                "running": "诊断生成中",
                "succeeded": "诊断已完成",
                "completed": "诊断已完成",
                "failed": "诊断失败",
            }.get(a.diagnosis_status, a.diagnosis_status),
            "elapsed_minutes": _elapsed_minutes(a.started_at, a.submitted_at),
        })
    return {"items": out, "total": len(out)}


def _elapsed_minutes(start: str, end: str) -> int | None:
    from datetime import datetime
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return max(0, int((e - s).total_seconds() // 60))
    except Exception:                                # noqa: BLE001
        return None


def my_materials(db: Session) -> dict:
    ups = db.execute(select(E.MaterialUpload).order_by(
        E.MaterialUpload.created_at.desc())).scalars().all()
    type_label = {"exam_paper": "试卷", "notes": "复习笔记", "slides": "课程 PPT",
                  "code": "实验代码", "other": "其他资料"}
    out = []
    for u in ups:
        out.append({
            "upload_id": u.upload_id, "filename": u.filename,
            "type": u.resolved_type or u.declared_type or "other",
            "type_label": type_label.get(u.resolved_type or "", "其他资料"),
            "status": u.status, "status_label": _upload_status_label(u.status, u.stage),
            "question_count": u.question_count,
            "answer_ready_count": u.answer_ready_count,
            "booklet_id": u.booklet_id, "paper_id": u.paper_id,
            "updated_at": u.updated_at,
        })
    return {"items": out, "total": len(out)}


def _upload_status_label(status: str, stage: str) -> str:
    if status == "completed":
        return "已完成"
    if status == "partial":
        return "部分完成"
    if status == "processing":
        return f"处理中（{stage or '解析'}）"
    if status == "failed":
        return "需要处理"
    return "排队中"


FAVORITE_KINDS = ("question", "paper", "kp", "chunk", "external",
                  "external_paper", "document")
# 与 scope_service.SCOPE_LABELS 保持一致；这里单独一份避免收藏列表引入模型依赖
_SCOPE_LABELS = {"in_scope": "课程范围内", "in_scope_advanced": "课程内提高",
                 "extension": "扩展性学习", "unconfirmed": "待确认"}


def scope_labels(scope: str) -> str:
    return _SCOPE_LABELS.get(scope or "", "待确认")


def list_favorites(db: Session, user_id: str = "local_user") -> dict:
    rows = db.execute(select(E.Favorite).where(
        E.Favorite.user_id == user_id).order_by(
        E.Favorite.created_at.desc())).scalars().all()
    kp_names = {k.kp_id: k.name for k in db.execute(
        select(E.KnowledgePoint)).scalars().all()}
    ext = {e.item_id: e for e in db.execute(
        select(E.ExternalItem)).scalars().all()}
    epapers = {p.epaper_id: p for p in db.execute(
        select(E.ExternalPaper)).scalars().all()}
    out = []
    for f in rows:
        # 组卷中心取代原「练习中心」；与个人记录有关的收藏统一回个人中心。
        route = {"question": "/compose?tab=create", "paper": "/me?tab=records",
                 "kp": "/reports", "chunk": "/materials",
                 "external": "/extension",
                 "external_paper": "/extension",
                 "document": "/materials"}.get(f.kind, "/materials")
        title = f.title
        url, scope_label = "", ""
        if f.kind == "kp":
            title = kp_names.get(f.target_id, f.title)
        if f.kind == "external":
            it = ext.get(f.target_id)
            if it:
                # 外部条目的标题与链接以当前台账为准，避免改名后收藏里还是旧标题
                title = it.title or title
                url = it.url
                scope_label = scope_labels(it.scope_class)
                if it.kind == "video":
                    route = "/extension?group=course"
            else:
                scope_label = "来源已下线"
        if f.kind == "external_paper":
            paper = epapers.get(f.target_id)
            if paper:
                title = paper.title or title
                url = paper.source_url or paper.pdf_url
                scope_label = "试卷资源"
                route = f"/extension?paper={paper.epaper_id}"
            else:
                scope_label = "试卷已下线"
        out.append({"entry_id": f.entry_id, "kind": f.kind, "target_id": f.target_id,
                    "title": title, "note": f.note, "route": route,
                    "url": url, "scope_label": scope_label,
                    "created_at": f.created_at})
    return {"items": out, "total": len(out)}


def add_favorite(db: Session, *, kind: str, target_id: str, title: str = "",
                 note: str = "", user_id: str = "local_user") -> dict:
    kind = (kind or "").strip()
    if kind not in FAVORITE_KINDS:
        raise ValueError("收藏类型必须是 " + " / ".join(FAVORITE_KINDS))
    old = db.execute(select(E.Favorite).where(
        E.Favorite.user_id == user_id, E.Favorite.kind == kind,
        E.Favorite.target_id == target_id)).scalars().first()
    if old:
        if note:
            old.note = note
            db.commit()
        return {"entry_id": old.entry_id, "reused": True,
                "note": "该对象已在收藏中"}
    row = E.Favorite(entry_id=E.new_id("fav"), user_id=user_id, kind=kind,
                     target_id=target_id, title=title[:250], note=note)
    db.add(row)
    db.commit()
    return {"entry_id": row.entry_id, "reused": False}


def remove_favorite(db: Session, entry_id: str,
                    user_id: str = "local_user") -> dict:
    row = db.get(E.Favorite, entry_id)
    if not row or row.user_id != user_id:
        raise KeyError(entry_id)
    db.delete(row)
    db.commit()
    return {"entry_id": entry_id, "removed": True}


def get_settings(db: Session, user_id: str = "local_user") -> dict:
    row = db.get(E.UserSetting, user_id)
    plan = db.execute(select(E.StudyPlan).where(
        E.StudyPlan.active.is_(True))).scalars().first()
    if row is None:
        row = E.UserSetting(user_id=user_id,
                            exam_date=(plan.exam_date if plan else ""),
                            daily_minutes=(plan.daily_minutes if plan else 60))
        db.add(row)
        db.commit()
    return {
        "nickname": row.nickname, "exam_date": row.exam_date or (plan.exam_date if plan else ""),
        "daily_minutes": row.daily_minutes or 60,
        "default_mode": row.default_mode, "default_difficulty": row.default_difficulty,
        "payload": row.payload or {},
        # V2 §5.4 rule 9: an upload must not spend money on its own. This switch
        # is the only way to make answer generation automatic, and it is OFF.
        "auto_generate_answers": bool((row.payload or {}).get(
            "auto_generate_answers", False)),
        "api_key_policy": "API 密钥只保存在后端环境变量（.env），前端不读取、不显示、不写入浏览器存储",
    }


def save_settings(db: Session, patch: dict, user_id: str = "local_user") -> dict:
    row = db.get(E.UserSetting, user_id)
    if row is None:
        row = E.UserSetting(user_id=user_id)
        db.add(row)
    for field in ("nickname", "exam_date", "default_mode", "default_difficulty"):
        if patch.get(field) is not None:
            setattr(row, field, str(patch[field])[:120])
    if patch.get("daily_minutes") is not None:
        try:
            row.daily_minutes = max(10, min(480, int(patch["daily_minutes"])))
        except (TypeError, ValueError):
            raise ValueError("每日学习分钟必须是 10–480 的整数")
    if patch.get("auto_generate_answers") is not None:
        row.payload = {**(row.payload or {}),
                       "auto_generate_answers":
                           bool(patch["auto_generate_answers"])}
    if isinstance(patch.get("payload"), dict):
        row.payload = {**(row.payload or {}), **patch["payload"]}
    row.updated_at = E.utcnow()
    # 让当前生效的学习计划立刻按新值重新分配时间，避免"设置改了但计划没变"
    plan = db.execute(select(E.StudyPlan).where(
        E.StudyPlan.active.is_(True))).scalars().first()
    if plan is not None and row.daily_minutes:
        plan.daily_minutes = row.daily_minutes
        if row.exam_date:
            plan.exam_date = row.exam_date
    db.commit()
    return get_settings(db, user_id)
