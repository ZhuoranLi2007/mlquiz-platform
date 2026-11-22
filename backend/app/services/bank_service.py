"""Question bank + paper assembly.

Blueprint-driven selection from questions with a READY answer. "Ready" means
the answer passed either a human cross-check OR the automatic AI-answer check
pipeline -- per-question human confirmation is NOT a prerequisite.

A question with no answer is not silently dropped: it enters the AI answer
queue on first selection (on-demand trigger), and if the model is not yet
configured it is reported in coverage_report.answer_pending.

Every paper item stores an immutable snapshot of the exact question version,
answer, rubric, scoring rule, order and score. Those answers are fixed BEFORE
any attempt exists, so the student's response can never influence them.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services import answer_pipeline
from backend.app.services.question_dto import student_view
from backend.app.services.scoring import effective_type

# V2.1 §四/§五：考查范围三档。默认只出「课程范围内」的题，避免超纲题
# 悄悄混进期末训练。scope_class 由 scope_service 逐题判定并在题目上留痕。
SCOPE_POOLS: dict[str, tuple[str, ...]] = {
    "course_only": ("in_scope",),
    "course_plus_advanced": ("in_scope", "in_scope_advanced"),
    "include_extension": ("in_scope", "in_scope_advanced", "extension"),
}
SCOPE_NOTES = {
    "course_only": "只从标注为「课程范围内」的题目中选，最贴近期末范围",
    "course_plus_advanced": "加入「课程内提高」题目：考点在 PPT 内但推导/计算更深",
    "include_extension": ("再加入「扩展性学习」题目；这一档的题单独记成绩，"
                          "不计入期末掌握度"),
}


def scope_pool(scope: str) -> tuple[str, ...] | None:
    """非法取值返回 None，由调用方回退到默认档并如实上报。"""
    return SCOPE_POOLS.get((scope or "").strip())


def _extension_recommendations(db: Session, kp_ids: list[str],
                               limit: int = 6) -> list[dict]:
    """把匹配考点的「扩展性学习」条目作为外链推荐附在试卷报告里。

    超纲/待确认的题**不进试卷**（题干只有短摘录、且未经许可不宜成卷），
    但学生做完课内题后可以顺着链接去看——这正是「扩展性学习」该有的位置。
    """
    rows = db.execute(select(E.ExternalItem).where(
        E.ExternalItem.active.is_(True),
        E.ExternalItem.scope_class.in_(("extension", "unconfirmed")),
    )).scalars().all()
    out = []
    for it in rows:
        if kp_ids and not (set(it.kp_ids or []) & set(kp_ids)):
            continue
        out.append({
            "item_id": it.item_id, "title": it.title, "url": it.url,
            "category": it.category, "kind": it.kind,
            "scope_class": it.scope_class,
            "url_verified": it.url_verified,
            "reason": (it.out_of_scope_points or [])[:2] or [it.rationale[:160]],
        })
        if len(out) >= limit:
            break
    return out


def question_snapshot(q: E.Question, *, answer_meta: dict | None = None) -> dict:
    """Full server-side snapshot. NEVER return this directly to a browser."""
    out = {
        "question_id": q.question_id,
        "question_version": q.question_version,
        "origin": q.origin,
        "original_exam_id": q.original_exam_id,
        "original_question_no": q.original_question_no,
        "question_type": q.question_type,
        "stem": q.stem,
        # V2.3：原文保持不动，译文单独存；答题页按语言切换取用
        "stem_zh": q.stem_zh or "",
        "original_language": q.original_language or "",
        "translation_status": q.translation_status or "",
        "options": q.options,
        "stem_images": q.stem_images,
        "score": q.score,
        "chapter_id": q.chapter_id or "",
        "main_kp_id": q.main_kp_id,
        "aux_kp_ids": q.aux_kp_ids,
        "cognitive_level": q.cognitive_level,
        "difficulty": q.difficulty,
        # V2.1：范围标签随快照固定，答题记录据此把扩展题成绩与期末掌握分开统计
        "scope_class": q.scope_class or "in_scope",
        "external_item_id": q.external_item_id or "",
        "estimated_minutes": q.estimated_minutes,
        "source_refs": q.source_refs,
        "question_family_id": q.question_family_id,
        "parent_question_id": q.parent_question_id,
        "review_status": q.review_status,
        "answer_status": q.answer_status,
        "answer_origin": q.answer_origin,
        "active": q.active,
        # server-side only:
        "answer_payload": q.answer_payload,
        "rubric": q.rubric,
        "explanation": q.explanation,
    }
    if answer_meta is not None:
        out["answer_meta"] = answer_meta
    return out


def bank_stats(db: Session) -> dict:
    """题库统计。

    V3.4（RC-7）：`by_type/by_kp/by_origin/by_answer_status` 以前遍历**全部版本行**，
    于是 `single_choice` 报 113，而 `/api/questions` 实际只返回 29 条 active ——
    页头写「共 113 道单选题」、下面只有 29 张卡。现在分两个口径：
      * `by_*`      只统计 active（= 题库里真正能看到的题）
      * `all_versions_*` 保留历史版本口径，避免调用方误以为数据丢了
    """
    rows = db.execute(select(E.Question)).scalars().all()
    active_rows = [r for r in rows if r.active]
    out: dict[str, Any] = {
        "total_versions": len(rows),
        "active": len(active_rows),
        "verified": sum(1 for r in active_rows if r.review_status == "verified"),
        "answer_verified": sum(1 for r in active_rows
                               if r.answer_status == "verified"),
        "usable": 0, "ai_reference_answers": 0,
        "by_type": {}, "by_kp": {}, "by_origin": {}, "by_answer_status": {},
        "all_versions_by_type": {},
        "answer_inconsistent": 0,
    }
    for r in active_rows:
        ready = answer_pipeline.is_answer_ready(r)
        if ready:
            out["usable"] += 1
        if r.answer_origin in answer_pipeline.AI_ORIGINS:
            out["ai_reference_answers"] += 1
        if not answer_pipeline.answer_structurally_valid(r)[0]:
            out["answer_inconsistent"] += 1
        # 按「有效题型」统计，与 DTO 的 question_type_effective 保持同一口径，
        # 否则 18 条 unknown（实际是单选）会让题型计数与筛选结果对不上。
        qtype = effective_type({"question_type": r.question_type,
                                "answer_payload": r.answer_payload or {}})
        out["by_type"][qtype] = out["by_type"].get(qtype, 0) + 1
        out["by_kp"][r.main_kp_id or "(未标注)"] = out["by_kp"].get(
            r.main_kp_id or "(未标注)", 0) + 1
        out["by_origin"][r.origin] = out["by_origin"].get(r.origin, 0) + 1
        out["by_answer_status"][r.answer_status] = out["by_answer_status"].get(
            r.answer_status, 0) + 1
    for r in rows:
        out["all_versions_by_type"][r.question_type] = \
            out["all_versions_by_type"].get(r.question_type, 0) + 1
    return out


def list_questions(db: Session, *, kp_id: str = "", qtype: str = "",
                   origin: str = "", review_status: str = "",
                   include_inactive: bool = False, limit: int = 200) -> list[dict]:
    stmt = select(E.Question)
    if not include_inactive:
        stmt = stmt.where(E.Question.active.is_(True))
    rows = db.execute(stmt.order_by(E.Question.created_at, E.Question.row_id)).scalars().all()
    out = []
    for q in rows:
        if kp_id and kp_id not in [q.main_kp_id] + list(q.aux_kp_ids or []):
            continue
        if qtype and q.question_type != qtype:
            continue
        if origin and q.origin != origin:
            continue
        if review_status and q.review_status != review_status:
            continue
        out.append(student_view(q, reveal=False))
        if len(out) >= limit:
            break
    return out


def _seen_question_ids(db: Session, user_id: str) -> set[str]:
    """Questions whose answer was already revealed or which were answered
    before: not eligible as 'first independent test' when exclude_seen=True."""
    rows = db.execute(
        select(E.Answer.question_id).join(
            E.Attempt, E.Attempt.attempt_id == E.Answer.attempt_id
        ).where(E.Attempt.user_id == user_id)
    ).scalars().all()
    return set(rows)


def assemble_paper(db: Session, blueprint: dict, user_id: str = "local_user") -> dict:
    """blueprint keys: title, kp_ids[], question_types[], count, total_score,
    estimated_minutes, cognitive_levels[], difficulty[], exclude_seen,
    family_spread, mode, prefer_unseen, allow_unverified (explicit only)."""
    bp = dict(blueprint or {})
    count = int(bp.get("count", 5))
    kp_ids = [k for k in (bp.get("kp_ids") or []) if k]
    qtypes = [t for t in (bp.get("question_types") or []) if t]
    levels = [c for c in (bp.get("cognitive_levels") or []) if c]
    diffs = [d for d in (bp.get("difficulty") or []) if d]
    exclude_seen = bool(bp.get("exclude_seen", True))
    allow_unverified = bool(bp.get("allow_unverified", False))
    mode = bp.get("mode", "quiz")
    # 组卷取值范围：默认档保证历史行为不变（历史题目 scope_class 全为 in_scope）
    scope_raw = str(bp.get("scope") or "course_only").strip()
    pool = scope_pool(scope_raw)
    scope_invalid = pool is None
    if pool is None:
        pool = SCOPE_POOLS["course_only"]
        scope_raw = "course_only"

    stmt = select(E.Question).where(E.Question.active.is_(True))
    rows = db.execute(stmt).scalars().all()
    seen = _seen_question_ids(db, user_id) if exclude_seen else set()

    eligible: list[E.Question] = []
    answer_pending: list[dict] = []
    rejected = {"answer_not_ready": 0, "kp_mismatch": 0,
                "type_mismatch": 0, "seen": 0, "level_mismatch": 0,
                "difficulty_mismatch": 0, "not_publishable": 0,
                "answer_inconsistent": 0, "scope_excluded": 0}
    # A formal quiz must have its reference answer AND rubric fixed before any
    # attempt exists. Practice mode may explicitly allow an answerless item,
    # but it still cannot be used for a graded quiz.
    require_ready = (mode == "quiz") or not allow_unverified
    # Generating an answer is a network round-trip to the model (seconds each).
    # Doing that for a whole paper inside the request made 组卷 time out once a
    # real model was configured, so the synchronous part gets a time budget;
    # whatever is left goes to the background queue and is simply not included
    # in THIS paper (reported honestly in `answer_pending`).
    budget = float(os.getenv("MLQ_ANSWER_SYNC_BUDGET_S", "25") or 0)
    started = time.time()
    sync_used = 0
    for q in rows:
        if not answer_pipeline.is_publishable(q):
            # Held back at import time (e.g. part of an option's text was an
            # image and never entered the text layer). Scoring it would grade a
            # student against an answer we cannot vouch for.
            rejected["not_publishable"] += 1
            continue
        if (q.scope_class or "in_scope") not in pool:
            rejected["scope_excluded"] += 1
            continue
        # 答案与题目结构自洽性：`{"key":"Z"}` 这类答案即使带着
        # ai_auto_checked 也不能进卷，否则学生怎么答都判错。显式计数，
        # 让 coverage_report 说明「为什么这题没被选上」。
        ok_answer, answer_reason = answer_pipeline.answer_structurally_valid(q)
        if not ok_answer:
            rejected["answer_inconsistent"] += 1
            if len(answer_pending) < 12:
                answer_pending.append({"question_id": q.question_id,
                                       "reason": answer_reason})
            continue
        if kp_ids and q.main_kp_id not in kp_ids and not (
                set(q.aux_kp_ids or []) & set(kp_ids)):
            rejected["kp_mismatch"] += 1
            continue
        if qtypes and q.question_type not in qtypes:
            rejected["type_mismatch"] += 1
            continue
        if levels and q.cognitive_level not in levels:
            rejected["level_mismatch"] += 1
            continue
        if diffs and q.difficulty not in diffs:
            rejected["difficulty_mismatch"] += 1
            continue
        if q.question_id in seen:
            rejected["seen"] += 1
            continue
        if require_ready and not answer_pipeline.is_answer_ready(q):
            # On-demand generation happens HERE, before any attempt exists.
            # The request contains no student response by construction.
            allow_sync = (time.time() - started) < budget
            if allow_sync:
                sync_used += 1
            r = answer_pipeline.ensure_answer_ready(
                db, q, trigger="on_demand", allow_sync=allow_sync,
                sync_timeout_s=max(8.0, budget - (time.time() - started))
                if allow_sync else None)
            if r.get("ready"):
                db.refresh(q)
            else:
                rejected["answer_not_ready"] += 1
                gen_info = r.get("generation") or {}
                msg = (r.get("message") or gen_info.get("message")
                       or gen_info.get("exception_detail") or "")
                if not allow_sync:
                    msg = ("已在后台队列生成参考答案，本次组卷未纳入该题；"
                           "生成完成后重新组卷即可选到")
                elif not msg:
                    msg = ("本次同步生成未完成（" + str(
                        r.get("status") or "queued") + "），已转后台队列继续；"
                        "生成完成后重新组卷即可选到")
                answer_pending.append({
                    "question_id": q.question_id,
                    "generation_id": r.get("generation_id", ""),
                    "status": r.get("status", "queued"),
                    "message": msg,
                })
                continue
        eligible.append(q)

    # Prefer family spread, then deterministic order for reproducibility.
    eligible.sort(key=lambda q: (q.question_family_id or q.question_id,
                                 q.question_id, q.question_version))
    picked: list[E.Question] = []
    used_families: set[str] = set()
    for q in eligible:
        fam = q.question_family_id or q.question_id
        if fam in used_families:
            continue
        picked.append(q)
        used_families.add(fam)
        if len(picked) >= count:
            break
    if len(picked) < count and bp.get("family_spread", True):
        for q in eligible:
            if q not in picked:
                picked.append(q)
            if len(picked) >= count:
                break

    # ---- V3.1：题库不足时用 AI 补足，空卷不再当成功 ----
    ai_filled: list[E.Question] = []
    ai_note = ""
    need = count - len(picked)
    if need > 0 and bp.get("ai_fill", True):
        ai_filled = _ai_fill_questions(db, bp, kp_ids, need, qtypes)
        picked.extend(ai_filled)
        if len(ai_filled) < need:
            ai_note = (f"AI 补题产出 {len(ai_filled)}/{need} 道，"
                       "其余候选未通过结构或课程范围校验")
        else:
            ai_note = f"题库不足，已由 AI 补足 {len(ai_filled)} 道"
    elif need > 0:
        ai_note = "本次关闭了 AI 补题，题库不足部分没有补充"

    if not picked:
        # 绝不创建 0 题试卷：直接返回失败，让前端显示原因
        return {
            "status": "failed",
            "paper_id": "", "title": bp.get("title") or "自动组卷",
            "item_count": 0, "total_score": 0, "estimated_minutes": 0,
            "coverage_report": {
                "requested_count": count, "selected_count": 0,
                "shortfall": count, "rejected_reasons": rejected,
                "answer_pending": answer_pending,
                "ai_fill": {"enabled": bool(bp.get("ai_fill", True)),
                            "filled": 0, "note": ai_note},
                "message": ("没有组出任何题目：" + (ai_note or
                            "题库中没有符合条件的题，AI 也未产出可用题")),
            },
            "message": ("没有组出任何题目：" + (ai_note or
                        "题库中没有符合条件的题，AI 也未产出可用题")),
        }

    coverage = {
        "requested_count": count,
        "selected_count": len(picked),
        "ai_fill": {"enabled": bool(bp.get("ai_fill", True)),
                    "filled": len(ai_filled), "note": ai_note},
        "ai_filled_count": len(ai_filled),
        "shortfall": max(0, count - len(picked)),
        "target_changed": False,
        "rejected_reasons": rejected,
        "answer_pending": answer_pending,
        "answers_fixed_before_attempt": require_ready,
        "sync_generation": {"budget_s": budget, "generated_sync": sync_used,
                            "note": "超出预算的题目转入后台队列，本次组卷不纳入"},
        "answer_policy": ("参考答案与评分标准在作答前写入 paper_item.snapshot，"
                          "同一题目版本复用缓存，不随作答重新生成"),
        "scope": scope_raw,
        "scope_valid": not scope_invalid,
        "scope_note": SCOPE_NOTES.get(scope_raw, ""),
        "scope_pool": list(pool),
        "scope_invalid_fallback": (
            f"未知的考查范围取值 {bp.get('scope')!r}，已回退为 course_only"
            if scope_invalid else ""),
        "recommended_extension": _extension_recommendations(db, kp_ids),
        "message": ("题量已满足" if len(picked) == count else
                    f"只组出 {len(picked)}/{count} 题。") + (
                    ("；" + ai_note) if ai_note else ""),
        "kp_coverage": {},
    }
    for q in picked:
        k = q.main_kp_id or "(未标注)"
        coverage["kp_coverage"][k] = coverage["kp_coverage"].get(k, 0) + 1

    target_total = bp.get("total_score")
    if target_total and picked:
        # Distribute requested total over picked items, 2 decimals.
        each = round(float(target_total) / len(picked), 2)
        scores = [each] * len(picked)
        scores[-1] = round(float(target_total) - each * (len(picked) - 1), 2)
    else:
        scores = [float(q.score) for q in picked]

    paper = E.Paper(
        paper_id=E.new_id("paper"),
        user_id=user_id,
        title=bp.get("title") or "自动组卷",
        mode=mode,
        blueprint=bp,
        total_score=round(sum(float(s) for s in scores), 2),
        item_count=len(picked),
        estimated_minutes=round(sum(float(q.estimated_minutes) for q in picked), 1),
        coverage_report=coverage,
        source="bank",
        scope=scope_raw,
    )
    db.add(paper)
    fixed_at = E.utcnow()
    for i, (q, sc) in enumerate(zip(picked, scores), start=1):
        meta = answer_pipeline.answer_meta_for(
            db, q.question_id, q.question_version)
        if not meta:
            meta = {"label": answer_pipeline.answer_label(q.answer_origin),
                    "source": q.answer_origin or "none",
                    "answer_version": 0,
                    "question_version": q.question_version,
                    "disclaimer": "该题暂无 AI 生成记录；答案来源以题目字段为准"}
        snap = question_snapshot(q, answer_meta=meta)
        snap["score"] = sc
        snap["position"] = i
        snap["answer_fixed_at"] = fixed_at
        db.add(E.PaperItem(
            item_id=E.new_id("item"), paper_id=paper.paper_id, position=i,
            question_id=q.question_id, question_version=q.question_version,
            chapter_id=q.chapter_id or "", score=float(sc), snapshot=snap,
        ))
    db.commit()
    return {
        "status": ("generated" if len(picked) >= count else "partial"),
        "paper_id": paper.paper_id,
        "title": paper.title,
        "item_count": paper.item_count,
        "total_score": paper.total_score,
        "estimated_minutes": paper.estimated_minutes,
        "coverage_report": coverage,
    }


def _ai_fill_questions(db: Session, bp: dict, kp_ids: list[str], need: int,
                       qtypes: list[str]) -> list[E.Question]:
    """题库不够时用 generate_questions 补足缺口（V3.1）。

    注意：`generate_questions` 返回的 candidates 只是摘要
    （question_id + 题干 + 校验说明），完整题目它自己已经写进 questions 表
    （active=False）。这里**按 id 取回完整记录**，不再重复造题。

    取回的题 active=False，只进当前这张卷，不进入常规抽题池。
    """
    if need <= 0:
        return []
    from backend.app.services import skill_runtime
    # 多要几道：skill 内部还有范围复检与去重，会损耗一部分候选
    ask = min(max(need + 3, need), 10)
    payload = {
        "count": ask,
        "kp_ids": kp_ids or [],
        "question_types": qtypes or [],
        "difficulty": bp.get("difficulty") or [],
        "mode": "fill_shortfall",
        "instruction": ("为组卷补足缺口：考查点必须落在给定知识点内，"
                        "中文题干、公式用 LaTeX，客观题必须有正确答案"),
    }
    try:
        out = skill_runtime.run_skill(db, "generate_questions", payload)
    except Exception as e:                                   # noqa: BLE001
        print(f"[assemble] AI 补题调用失败: {type(e).__name__}: {e}", flush=True)
        return []
    cands = (out.get("result") or {}).get("candidates") or []
    picked: list[E.Question] = []
    for cand in cands[:need]:
        qid = cand.get("question_id") or ""
        if not qid:
            continue
        q = db.execute(select(E.Question).where(
            E.Question.question_id == qid).order_by(
            E.Question.question_version.desc())).scalars().first()
        if q is None:
            continue
        picked.append(q)
    return picked


def ai_fill_questions(db: Session, blueprint: dict, kp_ids: list[str],
                      need: int, qtypes: list[str]) -> list[E.Question]:
    """公开入口：题库不足时用 DeepSeek 补题。

    组卷（assemble）与「手动选题 + 补题」（from-questions）共用这一条路径，
    避免出现两套 AI 出题逻辑。返回的题目 `active=False`，只进当前这张卷。
    """
    return _ai_fill_questions(db, blueprint, kp_ids, need, qtypes)


def get_paper_items(db: Session, paper_id: str) -> list[E.PaperItem]:
    return db.execute(
        select(E.PaperItem).where(E.PaperItem.paper_id == paper_id)
        .order_by(E.PaperItem.position)
    ).scalars().all()


def get_paper_student_view(db: Session, paper_id: str, *, reveal: bool = False,
                           mode: str = "quiz") -> list[dict]:
    out = []
    for it in get_paper_items(db, paper_id):
        v = student_view(it.snapshot, reveal=reveal, mode=mode)
        v.update(position=it.position, score=it.score,
                 question_id=it.question_id,
                 question_version=it.question_version,
                 item_id=it.item_id)
        out.append(v)
    return out


def create_paper_from_questions(db: Session, questions: list[E.Question], *,
                                title: str, mode: str = "quiz",
                                source: str = "external_paper",
                                blueprint: dict | None = None,
                                scores: list[float] | None = None,
                                scope: str = "course_only",
                                user_id: str = "local_user",
                                commit: bool = True) -> dict:
    """按调用方给定的顺序建卷：不抽题、不重排、不随机。

    用于「按原卷顺序作答」——外部试卷的题序与分值来自原卷，重新组卷会改变
    原卷结构。快照、答案固定与评分仍然复用同一套 PaperItem 机制。
    """
    if not questions:
        raise ValueError("没有可作答的题目")
    score_list = ([float(s) for s in scores] if scores is not None
                  else [float(q.score) for q in questions])
    if len(score_list) != len(questions):
        raise ValueError("分值与题目数量不一致")
    bp = dict(blueprint or {})
    bp.setdefault("title", title)
    bp.setdefault("mode", mode)
    bp.setdefault("scope", scope)
    bp["order_policy"] = "original_order"
    bp["item_count"] = len(questions)

    coverage = {
        "requested_count": len(questions),
        "selected_count": len(questions),
        "shortfall": 0,
        "order_policy": "original_order",
        "source": source,
        "scope": scope,
        "message": "按原卷顺序建卷，题目顺序与分值未被重新抽取或重排",
    }
    paper = E.Paper(
        paper_id=E.new_id("paper"),
        user_id=user_id,
        title=title,
        mode=mode,
        blueprint=bp,
        total_score=round(sum(score_list), 2),
        item_count=len(questions),
        estimated_minutes=round(
            sum(float(q.estimated_minutes or 0) for q in questions), 1),
        coverage_report=coverage,
        source=source,
        scope=scope,
    )
    db.add(paper)
    fixed_at = E.utcnow()
    for i, (q, sc) in enumerate(zip(questions, score_list), start=1):
        meta = answer_pipeline.answer_meta_for(
            db, q.question_id, q.question_version)
        if not meta:
            meta = {"label": answer_pipeline.answer_label(q.answer_origin),
                    "source": q.answer_origin or "none",
                    "answer_version": 0,
                    "question_version": q.question_version,
                    "disclaimer": "该题暂无 AI 生成记录；答案来源以题目字段为准"}
        snap = question_snapshot(q, answer_meta=meta)
        snap["score"] = sc
        snap["position"] = i
        snap["answer_fixed_at"] = fixed_at
        db.add(E.PaperItem(
            item_id=E.new_id("item"), paper_id=paper.paper_id, position=i,
            question_id=q.question_id, question_version=q.question_version,
            chapter_id=q.chapter_id or "", score=float(sc), snapshot=snap,
        ))
    db.commit() if commit else db.flush()
    return {
        "paper_id": paper.paper_id,
        "title": paper.title,
        "item_count": paper.item_count,
        "total_score": paper.total_score,
        "estimated_minutes": paper.estimated_minutes,
        "coverage_report": coverage,
    }


def revise_question(db: Session, question_id: str, patch: dict, *,
                    actor_user_id: str = "", previous: E.Question | None = None) -> dict:
    """Create a NEW question version. Published papers keep their snapshots."""
    old = previous or db.execute(
        select(E.Question).where(E.Question.question_id == question_id)
        .order_by(E.Question.question_version.desc())
    ).scalars().first()
    if not old:
        raise KeyError(question_id)
    cur = question_snapshot(old)
    new_ver = old.question_version + 1
    # Retire the superseded version so the same question_id can never be
    # selected twice for one paper. Published papers keep their own snapshot.
    was_active = old.active
    old.active = False
    allowed = {"stem", "options", "stem_images", "score", "chapter_id",
               "question_type", "main_kp_id",
               "aux_kp_ids", "cognitive_level", "difficulty", "source_refs",
               "explanation", "rubric", "answer_payload", "answer_status",
               "answer_origin", "review_status", "notes"}
    new = E.Question(
        row_id=E.new_id("qrow"), question_id=question_id,
        question_version=new_ver,
        origin=old.origin, original_exam_id=old.original_exam_id,
        original_question_no=old.original_question_no,
        question_type=cur["question_type"],
        stem=cur["stem"], options=cur["options"], stem_images=cur["stem_images"],
        score=cur["score"], chapter_id=cur.get("chapter_id", ""),
        main_kp_id=cur["main_kp_id"],
        aux_kp_ids=cur["aux_kp_ids"], cognitive_level=cur["cognitive_level"],
        difficulty=cur["difficulty"], estimated_minutes=cur.get("estimated_minutes", 2.0),
        source_refs=cur["source_refs"],
        question_family_id=old.question_family_id,
        parent_question_id=old.parent_question_id or old.question_id,
        review_status=cur["review_status"], answer_status=cur["answer_status"],
        answer_origin=cur["answer_origin"], active=True,
        answer_payload=cur["answer_payload"], rubric=cur["rubric"],
        explanation=cur["explanation"],
        created_by="admin_revise",
    )
    if actor_user_id:
        # Preserve provenance and scope for the teacher workbench. Clear a
        # translated display value only when its underlying content changes.
        for column in E.Question.__table__.columns:
            if column.key not in {"row_id", "question_version", "active",
                                  "created_at", "created_by"}:
                setattr(new, column.key, getattr(old, column.key))
        if patch["stem"] != old.stem or patch["options"] != old.options:
            new.stem_zh = ""
            new.translation_status = "stale"
        new.created_by = actor_user_id
        if was_active:
            new.review_status = "verified"
            new.answer_status = "verified"
            new.answer_origin = "teacher"
        new.active = was_active
    for k, v in (patch or {}).items():
        if k in allowed and hasattr(new, k):
            setattr(new, k, v)
    db.add(new)
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=actor_user_id or "local_user",
        event_type="question_revised", target_id=question_id,
        payload={"from_version": old.question_version, "to_version": new_ver,
                 "changed_fields": sorted((patch or {}).keys())},
    ))
    db.commit()
    return {"question_id": question_id, "new_version": new_ver,
            "note": "已发布试卷仍使用其 paper_item.snapshot 中的旧版本与评分规则"}
