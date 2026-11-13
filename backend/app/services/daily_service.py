"""每日 3 题（V2.2 修订版：随机抽题 + 首页就地作答）。

用户要求：
  1. 「每日三题你应该从题库中随机抽取」，且「一定是题库中有的题目」；
  2. 「题库中有的题目来源一定是课内的知识点」；
  3. 「题目要显示完全，要凸显出来」——返回完整题干与完整选项；
  4. 「要有作答的按钮，可以作答之后直接提交，并且不用跳转页面，
     直接在当前页面就能看到解析和正确的答案」。

实现约定：
  * 用 random.SystemRandom 从**真实题库**随机抽题，不再按薄弱度排序，
    也不再用日期散列做确定性排序；抽中后把这一组题持久化到
    `daily_three_sets`，保证「卡片上看到的三题」与「提交时评分的三题」一致。
  * 每题必须挂靠**课内考点**：KnowledgePoint(active) ∩ CourseScopeUnit
    (coverage != not_found)，且题目本身 scope_class == in_scope、答案已就绪、
    可发布、不是外部导入题。抽不到就如实报缺失，不拿别的题顶替。
  * 提交复用平台真实链路：daily Paper -> Attempt -> save_answer ->
    submit_attempt。答案、解析与评分从**交卷后**的 attempt_state 取，
    提交前接口绝不返回答案内容（答案隔离）。
  * 「换一组」显式重新随机；旧的一组保留，已交卷的作答与学情记录不删除。
"""
from __future__ import annotations

import random
import re
from collections import Counter, defaultdict
from datetime import date as Date, timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services import answer_pipeline, attempt_service, bank_service

USER_ID = "local_user"
RECENT_DAYS = 7
SLOT_SCORE = 2.0          # 每日 3 题统一轻量计分，便于快速自测

SLOTS: tuple[dict, ...] = (
    {"key": "choice", "label": "选择题", "hint": "从选项中选出正确答案",
     "types": ("single_choice", "multi_choice", "true_false")},
    {"key": "term", "label": "名词解释", "hint": "用自己的话说清这个概念",
     "types": ("term",)},
    {"key": "formula", "label": "公式应用", "hint": "代入公式完成一步简短计算",
     "types": ("numeric", "derivation", "short_answer")},
)

TYPE_LABELS = {
    "single_choice": "单选题", "multi_choice": "多选题",
    "true_false": "判断题", "numeric": "公式计算题",
    "term": "名词解释", "short_answer": "简答题",
    "derivation": "公式推导题", "comprehensive": "综合题",
    "unknown": "待定题型",
}
ORIGIN_LABELS = {
    "original": "历年原卷", "human": "人工整理", "note": "复习笔记",
    "ai": "课件配套练习", "external": "外部资源",
}
OBJECTIVE_TYPES = {"single_choice", "multi_choice", "true_false", "numeric"}

# 公式题必须真的含公式特征，且题干不能太长（用户要的是「简单题」）。
_FORMULA_MARKS = re.compile(
    r"(=|∑|Σ|∫|∂|∇|argmax|argmin|\\frac|\\alpha|\\beta|\\theta|\^T|"
    r"exp\(|log|ln\(|σ\(|sigmoid|≥|≤|·\s*[a-zA-Z]|\bJ\(|\bP\()", re.I)
_FORMULA_MAX_LEN = 220

_RULES = {
    "selection": "三题从真实题库中随机抽取，固定为 选择题 / 名词解释 / 公式类简短计算题 各一道",
    "eligibility": "只抽挂靠课内考点（课程范围表中有 PPT 出处）的题目；答案已就绪、可发布、属于课程范围内，外部导入题不参与",
    "daily": "随机抽中后当天固定这一组，刷新页面不换题；点「换一组」立即重新随机",
    "answer": "提交前不返回答案、解析与评分要点；提交后原地显示正确答案、解析与得分",
    "scoring": "复用真实评分链路（客观题确定性判定、主观题按评分要点），作答记录进入错题本与学情诊断",
}


def _today(on_date: str | None = None) -> str:
    if on_date:
        try:
            return Date.fromisoformat(on_date).isoformat()
        except ValueError:
            pass
    return Date.today().isoformat()


def _latest_questions(db: Session) -> list[E.Question]:
    rows = db.execute(select(E.Question).where(
        E.Question.active.is_(True))).scalars().all()
    latest: dict[str, E.Question] = {}
    for q in rows:
        old = latest.get(q.question_id)
        if old is None or q.question_version > old.question_version:
            latest[q.question_id] = q
    return list(latest.values())


def _course_kps(db: Session) -> dict[str, dict[str, Any]]:
    """课内考点白名单：真实考点 ∩ 课程范围表中有 PPT 页级出处的考点。"""
    kps = {k.kp_id: k for k in db.execute(
        select(E.KnowledgePoint).where(
            E.KnowledgePoint.status == "active")).scalars().all()}
    units = {u.kp_id: u for u in db.execute(
        select(E.CourseScopeUnit)).scalars().all()}
    out: dict[str, dict[str, Any]] = {}
    for kp_id, k in kps.items():
        u = units.get(kp_id)
        if u is None or u.coverage == "not_found":
            continue
        refs = []
        for r in (u.ppt_refs or [])[:2]:
            if not isinstance(r, dict):
                continue
            pages = r.get("pages") or ([r["page"]] if r.get("page") else [])
            if pages:
                label = f"{r.get('file', '课件')} 第 {pages[0]} 页"
                if len(pages) > 1:
                    label += f" 等 {len(pages)} 页"
                refs.append(label)
        out[kp_id] = {"name": k.name, "chapter": k.chapter or "",
                      "ppt_refs": refs}
    return out


def _eligible(db: Session, course_kps: dict[str, dict]) -> list[E.Question]:
    return [q for q in _latest_questions(db)
            if answer_pipeline.is_answer_ready(q)
            and answer_pipeline.is_publishable(q)
            and (q.scope_class or "in_scope") == "in_scope"
            and (q.origin or "") != "external"
            and (q.main_kp_id or "") in course_kps
            and (q.stem or "").strip()]


def _recent_question_ids(db: Session, day: str, user_id: str) -> set[str]:
    cutoff = (Date.fromisoformat(day) - timedelta(days=RECENT_DAYS)).isoformat()
    paper_ids = [a.paper_id for a in db.execute(select(E.Attempt).where(
        E.Attempt.user_id == user_id,
        E.Attempt.started_at >= cutoff)).scalars().all()]
    if not paper_ids:
        return set()
    return set(db.execute(select(E.PaperItem.question_id).where(
        E.PaperItem.paper_id.in_(paper_ids))).scalars().all())


def _slot_predicate(slot: str) -> Callable[[E.Question], bool]:
    if slot == "choice":
        return lambda q: q.question_type in SLOTS[0]["types"]
    if slot == "term":
        return lambda q: q.question_type == "term"
    return lambda q: (q.question_type in SLOTS[2]["types"]
                      and len(q.stem or "") <= _FORMULA_MAX_LEN
                      and bool(_FORMULA_MARKS.search(q.stem or "")))


def _public(q: E.Question, slot: dict, *, kps: dict[str, dict],
            options_hint: str = "") -> dict:
    """展示数据：完整题干 + 完整选项；绝不含答案/解析/评分要点。"""
    kp = kps.get(q.main_kp_id or "", {})
    stem = (q.stem or "").strip()
    prefix = "请解释下列名词：" if slot["key"] == "term" else ""
    if prefix and re.match(r"^(名词解释|请解释|请简述|解释下列|什么是)", stem):
        prefix = ""
    opts = [{"key": str(o.get("key") or ""), "text": str(o.get("text") or "")}
            for o in (q.options or []) if isinstance(o, dict)]
    return {
        "slot": slot["key"],
        "slot_label": slot["label"],
        "slot_hint": slot["hint"],
        "question_id": q.question_id,
        "question_version": q.question_version,
        "question_type": q.question_type,
        "type_label": TYPE_LABELS.get(q.question_type, q.question_type),
        "stem": stem,                       # 完整题干，前端不再截断
        "stem_preview": (stem[:96] + "…") if len(stem) > 96 else stem,
        "stem_images": q.stem_images or [],
        "options": opts,                    # 完整选项，供直接作答
        "prompt_prefix": prefix,
        "kp_id": q.main_kp_id or "",
        "kp_name": kp.get("name", ""),
        "chapter": kp.get("chapter", ""),
        "ppt_refs": kp.get("ppt_refs", []),
        "kp_verified": True,
        "difficulty": q.difficulty or "",
        "score": SLOT_SCORE,
        "origin": q.origin or "",
        "origin_label": ORIGIN_LABELS.get(q.origin or "", q.origin or ""),
        "scope_class": q.scope_class or "in_scope",
        "in_bank": True,
        "answer_label": answer_pipeline.answer_label(q.answer_origin or "none"),
        "reason": options_hint or "从题库随机抽取",
    }


def select_questions(db: Session, *, day: str, user_id: str = USER_ID,
                     exclude_ids: set[str] | None = None) -> dict:
    """真随机抽题（纯函数，不写库）。`SystemRandom` 不按日期播种。"""
    kps = _course_kps(db)
    pool = _eligible(db, kps)
    recent = _recent_question_ids(db, day, user_id)
    banned = set(exclude_ids or set())
    rng = random.SystemRandom()
    items: list[dict] = []
    missing: list[str] = []
    pools: dict[str, int] = {}
    used_qids: set[str] = set()
    used_kps: set[str] = set()

    for slot in SLOTS:
        pred = _slot_predicate(slot["key"])
        all_hits = [q for q in pool if pred(q)
                    and q.question_id not in used_qids
                    and q.question_id not in banned]
        pools[slot["key"]] = len(all_hits)
        if slot["key"] == "choice":
            # 用户要「选择题」：有选项的题优先，判断题只在无选项题时兜底。
            with_options = [q for q in all_hits
                            if q.question_type in ("single_choice", "multi_choice")]
            all_hits = with_options or all_hits
        # 优先最近 7 天没做过的；若都被排除了，仍从全部候选里随机。
        fresh = [q for q in all_hits if q.question_id not in recent]
        candidates = fresh or all_hits
        if not candidates:
            missing.append(slot["label"])
            continue
        candidates = list(candidates)
        rng.shuffle(candidates)
        # 三题尽量覆盖不同考点；随机顺序里第一个不冲突的即为抽中题。
        q = next((x for x in candidates
                  if (x.main_kp_id or "") not in used_kps), candidates[0])
        used_qids.add(q.question_id)
        if q.main_kp_id:
            used_kps.add(q.main_kp_id)
        items.append(_public(
            q, slot, kps=kps,
            options_hint=("最近 7 天没练到，随机抽中" if fresh else "随机抽中")))
    return {"items": items, "missing": missing, "pools": pools,
            "complete": len(items) == len(SLOTS),
            "eligible_total": len(pool), "course_kp_total": len(kps)}


# ------------------------------------------------------------------ 当日题组
def _active_set(db: Session, day: str, user_id: str) -> E.DailyThreeSet | None:
    rows = db.execute(select(E.DailyThreeSet).where(
        E.DailyThreeSet.day == day,
        E.DailyThreeSet.user_id == user_id,
        E.DailyThreeSet.active.is_(True))
        .order_by(E.DailyThreeSet.created_at.desc(),
                  E.DailyThreeSet.seq.desc())).scalars().all()
    return rows[0] if rows else None


def _create_set(db: Session, day: str, user_id: str, seq: int,
                exclude_ids: set[str]) -> E.DailyThreeSet:
    picked = select_questions(db, day=day, user_id=user_id,
                              exclude_ids=exclude_ids)
    st = E.DailyThreeSet(
        set_id=E.new_id("d3"), user_id=user_id, day=day, seq=seq,
        active=True, items=picked["items"], pool_sizes=picked["pools"])
    db.add(st)
    db.commit()
    return st


def _enrich_items(db: Session, items: list[dict]) -> list[dict]:
    """补齐历史题组缺的课内出处，不改变已抽中的题目。

    早期版本的 `CourseScopeUnit.ppt_refs` 解析只认单条 `page`，而真实结构是
    `{"file": ..., "pages": [...]}`，因此 PPT 出处修复之前生成的题组里
    `ppt_refs` 为空。题组是快照、不会自动重抽，读出时按当前课程范围表补全，
    这样首页仍能核对「题目来源一定是课内知识点」。
    """
    kps = _course_kps(db)
    out: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            out.append(item)
            continue
        kp = kps.get(item.get("kp_id") or "", {})
        if not kp:
            out.append(item)
            continue
        row = dict(item)
        if not row.get("ppt_refs"):
            row["ppt_refs"] = kp.get("ppt_refs", [])
        if not row.get("chapter"):
            row["chapter"] = kp.get("chapter", "")
        if not row.get("kp_name"):
            row["kp_name"] = kp.get("name", "")
        out.append(row)
    return out


def _set_view(db: Session, st: E.DailyThreeSet, day: str) -> dict:
    attempts: list[E.Attempt] = []
    if st.attempt_id:
        a = db.get(E.Attempt, st.attempt_id)
        if a:
            attempts = [a]
    return {
        "date": day,
        "set_id": st.set_id,
        "seq": st.seq,
        "items": _enrich_items(db, st.items or []),
        "complete": len(st.items or []) == len(SLOTS),
        "missing": [s["label"] for s in SLOTS
                    if s["key"] not in {i.get("slot") for i in (st.items or [])}],
        "pools": st.pool_sizes or {},
        "submitted": bool(st.submitted),
        "result": st.result or {},
        "paper_id": st.paper_id or "",
        "active_attempt_id": (attempts[0].attempt_id if attempts
                              and attempts[0].status != "submitted" else ""),
        "completed_count": 1 if st.submitted else 0,
        "last_score": (st.result or {}).get("score"),
        "max_score": (st.result or {}).get("max_score"),
        "rules": _RULES,
    }


def daily_three(db: Session, *, on_date: str | None = None,
                user_id: str = USER_ID) -> dict:
    """首页卡片数据：当天随机抽中的三题（首次访问时生成并固定）。"""
    day = _today(on_date)
    st = _active_set(db, day, user_id)
    if st is None:
        st = _create_set(db, day, user_id, 1, set())
    return _set_view(db, st, day)


def refresh_daily(db: Session, *, on_date: str | None = None,
                  user_id: str = USER_ID) -> dict:
    """「换一组」：旧题组置为 inactive（已交卷记录保留），重新随机一组。"""
    day = _today(on_date)
    old = _active_set(db, day, user_id)
    exclude: set[str] = set()
    seq = 1
    if old is not None:
        exclude = {i.get("question_id", "") for i in (old.items or [])}
        seq = old.seq + 1
        old.active = False
        db.commit()
    st = _create_set(db, day, user_id, seq, exclude)
    return _set_view(db, st, day)


# ------------------------------------------------------------------ 试卷 / 提交
def _paper_for_set(db: Session, st: E.DailyThreeSet,
                   user_id: str) -> E.Paper:
    """按这一组题生成一张固定快照的试卷；已生成则复用。"""
    if st.paper_id:
        old = db.get(E.Paper, st.paper_id)
        if old is not None:
            return old
    rows = {}
    for item in st.items or []:
        qid = item.get("question_id")
        q = db.execute(select(E.Question).where(
            E.Question.question_id == qid).order_by(
            E.Question.question_version.desc())).scalars().first()
        if q:
            rows[qid] = q
    if len(rows) != len(st.items or []):
        raise ValueError("今日题目在题库中已不可用，请换一组后重试")
    paper = E.Paper(
        paper_id=E.new_id("paper"), user_id=user_id,
        title=f"每日 3 题 · {st.day}（第 {st.seq} 组）", mode="quiz",
        source="daily", scope="course_only",
        item_count=len(rows), total_score=SLOT_SCORE * len(rows),
        estimated_minutes=8.0,
        blueprint={"daily_date": st.day, "daily_set_id": st.set_id,
                   "kind": "daily_three",
                   "slots": [i.get("slot") for i in (st.items or [])],
                   "question_ids": list(rows)},
        coverage_report={
            "source": "daily_three", "date": st.day, "set_id": st.set_id,
            "selected_count": len(rows), "requested_count": len(SLOTS),
            "shortfall": len(SLOTS) - len(rows),
            "slots": {i.get("slot"): i.get("question_type")
                      for i in (st.items or [])},
            "score_policy": "每日 3 题统一每题 2 分，便于快速自测",
            "note": "题目随机抽自课内考点的题库记录，快照保存后题库变化不影响本次作答",
        })
    db.add(paper)
    for pos, item in enumerate(st.items or [], start=1):
        q = rows[item["question_id"]]
        snap = bank_service.question_snapshot(q)
        snap["score"] = SLOT_SCORE
        snap["position"] = pos
        snap["answer_fixed_at"] = E.utcnow()
        snap["daily_slot"] = item.get("slot")
        db.add(E.PaperItem(
            item_id=E.new_id("pi"), paper_id=paper.paper_id,
            position=pos, question_id=q.question_id,
            question_version=q.question_version, score=SLOT_SCORE,
            snapshot=snap))
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=user_id,
        event_type="daily_three_started", target_id=paper.paper_id,
        payload={"date": st.day, "set_id": st.set_id, "seq": st.seq,
                 "question_ids": list(rows),
                 "note": "每日 3 题：题库随机抽取，固定为选择题/名词解释/公式题"}))
    st.paper_id = paper.paper_id
    db.commit()
    return paper


def start_daily(db: Session, *, on_date: str | None = None,
                user_id: str = USER_ID) -> dict:
    """兼容旧入口：生成/复用当天题组的试卷并创建一次作答（会跳转）。"""
    day = _today(on_date)
    info = daily_three(db, on_date=day, user_id=user_id)
    if not info["complete"]:
        raise ValueError("题库还凑不齐今日 3 题，缺少：" +
                         "、".join(info["missing"]))
    st = db.get(E.DailyThreeSet, info["set_id"])
    paper = _paper_for_set(db, st, user_id)
    if st.attempt_id:
        a = db.get(E.Attempt, st.attempt_id)
        if a is not None and a.status != "submitted":
            return {"attempt_id": a.attempt_id, "paper_id": paper.paper_id,
                    "reused": True, "status": a.status,
                    "note": "这组题已有一份未交卷的作答，继续完成它"}
    out = attempt_service.create_attempt(db, paper.paper_id, user_id)
    st.attempt_id = out["attempt_id"]
    db.commit()
    return {**out, "reused": False, "date": day,
            "note": "今日 3 题已准备好，交卷后可查看解析与错因"}


def _option_text(key: str, options: list[dict]) -> str:
    k = str(key or "").strip().upper()
    for o in options or []:
        if str(o.get("key", "")).strip().upper() == k:
            return f"{k}. {o.get('text', '')}".strip()
    return k


def _answer_text(qtype: str, payload: dict, options: list[dict]) -> str:
    """把保存的答案 payload 渲染成可读文本（不重新推导答案）。"""
    a = payload or {}
    if qtype == "single_choice":
        return _option_text(a.get("key") or a.get("answer") or "", options)
    if qtype == "multi_choice":
        keys = a.get("keys") or ([a["key"]] if a.get("key") else [])
        return "、".join(_option_text(k, options) for k in keys)
    if qtype == "true_false":
        v = a.get("value", a.get("key"))
        if isinstance(v, bool):
            return "正确" if v else "错误"
        return str(v)
    if qtype == "numeric":
        v = a.get("value")
        return f"{v}{a.get('unit') or ''}" if v is not None else ""
    if a.get("keys") and isinstance(a["keys"], list):
        return "、".join(_option_text(k, options) for k in a["keys"])
    if str(a.get("key") or "").strip():
        return _option_text(a["key"], options)
    for key in ("reference_answer", "text", "answer", "content", "values"):
        if a.get(key):
            v = a[key]
            return "；".join(map(str, v)) if isinstance(v, list) else str(v)
    return ""


def _response_text(qtype: str, response: dict, options: list[dict]) -> str:
    a = response or {}
    if qtype == "single_choice":
        return _option_text(a.get("key", ""), options)
    if qtype == "multi_choice":
        raw = a.get("keys", a.get("key", []))
        keys = [c for c in raw if str(c).isalpha()] if isinstance(raw, str) else raw
        return "、".join(_option_text(k, options) for k in keys)
    if qtype == "true_false":
        v = a.get("value", a.get("key"))
        if isinstance(v, bool):
            return "正确" if v else "错误"
        return str(v or "")
    if qtype == "numeric":
        v = a.get("value", a.get("numeric", ""))
        return f"{v}{a.get('unit') or ''}" if v not in (None, "") else ""
    return str(a.get("text") or a.get("content") or "")


def _rubric_points(rubric: dict) -> list[str]:
    points = (rubric or {}).get("points") or []
    out = []
    for p in points[:4]:
        if isinstance(p, dict):
            out.append(str(p.get("point") or p.get("text") or "").strip())
        elif p:
            out.append(str(p))
    return [x for x in out if x]


def submit_daily(db: Session, *, set_id: str, answers: dict,
                 user_id: str = USER_ID) -> dict:
    """首页就地交卷：保存作答 -> 真实评分 -> 返回正确答案与解析，不跳转。"""
    st = db.get(E.DailyThreeSet, set_id)
    if st is None or st.user_id != user_id:
        raise KeyError(set_id)
    if st.submitted and st.result:
        return {**st.result, "replayed": True,
                "note": "这一组已经交过卷，显示的是上一次的作答与解析"}
    if not st.items:
        raise ValueError("这一组没有题目，请换一组后重试")
    items = _enrich_items(db, st.items or [])
    if items != (st.items or []):
        st.items = items          # 顺手把历史题组缺的课内出处补齐并持久化
        db.commit()

    paper = _paper_for_set(db, st, user_id)
    aid = st.attempt_id
    a = db.get(E.Attempt, aid) if aid else None
    if a is None or a.status == "submitted":
        out = attempt_service.create_attempt(db, paper.paper_id, user_id)
        aid = out["attempt_id"]
        st.attempt_id = aid
        db.commit()
        a = db.get(E.Attempt, aid)
    else:
        # 复用未交卷作答时，把这次提交作为最终答案覆盖保存。
        db.commit()

    for item in items:
        qid = item["question_id"]
        resp = (answers or {}).get(qid) or {}
        if not isinstance(resp, dict):
            resp = {"text": str(resp)}
        attempt_service.save_answer(db, aid, qid, resp)

    state, _ = attempt_service.submit_attempt(
        db, aid, request_key=f"daily3:{st.set_id}")
    qmap = {q["question_id"]: q for q in state.get("questions") or []}
    amap = state.get("answers") or {}
    smap = state.get("scores") or {}

    results = []
    for item in items:
        qid = item["question_id"]
        q = qmap.get(qid, {})
        opts = q.get("options") or item.get("options") or []
        sc = smap.get(qid) or {}
        points = _rubric_points(q.get("rubric") or {})
        exp = str(q.get("explanation") or "").strip()
        # 题库里不少老题的解析只有「A。」这种答案字母；先补评分要点，
        # 仍过短时再补考点与课件出处。旧版只处理「完全为空」，随机抽到
        # 短解析且无评分要点的老题时会把「A。」原样返回。
        if len(exp) < 8 and points:
            exp = "评分要点：" + "；".join(points)
        if len(exp) < 8:
            refs = item.get("ppt_refs") or []
            kp = item.get("kp_name") or "课内考点"
            hint = (f"本题考查「{kp}」，可对照 {refs[0]} 复习；参考答案见上。"
                    if refs else
                    f"本题考查「{kp}」；题库未登记更详细的解析，参考答案见上。")
            exp = f"{exp} {hint}".strip()
        results.append({
            "question_id": qid,
            "slot": item.get("slot"),
            "slot_label": item.get("slot_label"),
            "question_type": item.get("question_type"),
            "type_label": item.get("type_label"),
            "stem": item.get("stem"),
            "prompt_prefix": item.get("prompt_prefix", ""),
            "options": opts,
            "kp_name": item.get("kp_name", ""),
            "chapter": item.get("chapter", ""),
            "ppt_refs": item.get("ppt_refs", []),
            "your_answer": _response_text(
                item.get("question_type", ""),
                (amap.get(qid) or {}).get("response") or {}, opts),
            "correct_answer": _answer_text(
                item.get("question_type", ""), q.get("answer_payload") or {}, opts),
            "explanation": exp,
            "rubric_points": points,
            "answer_label": q.get("answer_label") or "",
            "correct": sc.get("correct"),
            "awarded_score": sc.get("awarded_score", 0.0),
            "max_score": sc.get("max_score", SLOT_SCORE),
            "scoring_status": sc.get("scoring_status", ""),
        })
    manual = sum(1 for r in results
                 if r["scoring_status"] in ("needs_manual", "needs_model_scoring"))
    result = {
        "set_id": st.set_id, "attempt_id": aid, "paper_id": paper.paper_id,
        "results": results,
        "score": state.get("score", 0.0),
        "max_score": state.get("max_score", 0.0),
        "manual_pending_count": manual,
        "note": ("已按真实评分链路交卷；正确答案与解析来自题库中固定的答案版本。"
                 + ("部分主观题待按评分要点复核，未计入已得分。" if manual else "")),
    }
    st.submitted = True
    st.result = result
    db.commit()
    return result
