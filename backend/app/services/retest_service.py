"""Retest task + paper services shared by the API and the LangGraph retest graph."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import (answer_pipeline, attempt_service,
                                  bank_service, skill_runtime)


def generate_retest_tasks(db: Session, user_id: str = "local_user") -> dict:
    """One new-transfer task per open wrong-book entry (idempotent)."""
    wrong = db.execute(select(E.WrongBookEntry).where(
        E.WrongBookEntry.user_id == user_id,
        E.WrongBookEntry.status != "verified")).scalars().all()
    created, skipped = [], 0
    for w in wrong:
        exists = db.execute(select(E.RetestTask).where(
            E.RetestTask.from_wrong_id == w.entry_id,
            E.RetestTask.status == "pending")).scalars().first()
        if exists:
            skipped += 1
            continue
        days = cfg.RETEST_INTERVALS_DAYS[0]
        t = E.RetestTask(
            task_id=E.new_id("rt"), user_id=user_id, kp_id=w.kp_id,
            from_wrong_id=w.entry_id, family_id=w.family_id,
            kind="new_transfer",
            due_at=(datetime.now(timezone.utc) + timedelta(days=days))
            .isoformat(timespec="seconds"),
            status="pending",
        )
        db.add(t)
        db.add(E.LearningEvent(
            event_id=E.new_id("evt"), user_id=user_id,
            event_type="retest_task_created", target_id=t.task_id,
            payload={"kp_id": w.kp_id, "from_wrong_id": w.entry_id,
                     "interval_days": days, "kind": "new_transfer"}))
        created.append({"task_id": t.task_id, "kp_id": w.kp_id,
                        "kind": "new_transfer", "due_at": t.due_at})
    db.commit()
    return {
        "created": len(created), "skipped_existing": skipped,
        "items": created,
        "message": (f"按项目规则 {cfg.RETEST_INTERVALS_DAYS[0]} 天生成 "
                    f"{len(created)} 个新题迁移验证任务；已有待办 {skipped} 个未重复创建。"
                    "1/3/7 天为可修改的项目规则，不声称最优间隔。"),
    }


def choose_new_questions(db: Session, task: E.RetestTask,
                         user_id: str, count: int = 2) -> tuple[list[E.Question], dict]:
    """Pick questions with a READY answer (human-verified OR auto-checked AI
    reference answer) of a DIFFERENT question family, unseen by user."""
    seen_rows = db.execute(select(E.Answer.question_id).join(
        E.Attempt, E.Attempt.attempt_id == E.Answer.attempt_id
    ).where(E.Attempt.user_id == user_id)).scalars().all()
    seen = set(seen_rows)
    all_q = db.execute(select(E.Question).where(
        E.Question.active.is_(True))).scalars().all()
    reasons = {"answer_not_ready": 0, "same_family": 0, "seen": 0,
               "kp_mismatch": 0, "excluded_by_family_spread": 0}
    candidates = []
    for q in all_q:
        if not answer_pipeline.is_answer_ready(q):
            reasons["answer_not_ready"] += 1
            continue
        if (q.question_family_id or q.question_id) == task.family_id:
            reasons["same_family"] += 1
            continue
        if q.question_id in seen:
            reasons["seen"] += 1
            continue
        if task.kp_id and q.main_kp_id != task.kp_id:
            reasons["kp_mismatch"] += 1
            continue
        candidates.append(q)
    # family spread
    picked, fams = [], set()
    for q in candidates:
        fam = q.question_family_id or q.question_id
        if fam in fams:
            reasons["excluded_by_family_spread"] += 1
            continue
        picked.append(q)
        fams.add(fam)
        if len(picked) >= count:
            break
    # Unrelated questions cannot verify this task's knowledge point.
    report = {
        "target_count": count, "selected": len(picked),
        "shortfall": max(0, count - len(picked)),
        "kp_constraint_relaxed": False, "question_source": "question_bank",
        "rejected_reasons": reasons,
        "message": ("已选择不同题族、答案已就绪的新题" if len(picked) >= count else
                    "答案已就绪的题库中符合要求的新题不足；报告缺口，不伪造题目、"
                    "不重复原题族，也不放宽答案就绪要求"),
    }
    return picked[:count], report


def create_retest_paper(db: Session, task: E.RetestTask,
                        user_id: str = "local_user") -> dict:
    # Task recovery / repeated "start": reuse the already generated paper
    # instead of silently producing a different paper for the same task.
    if task.paper_id:
        old = db.get(E.Paper, task.paper_id)
        if old:
            items = db.execute(select(E.PaperItem).where(
                E.PaperItem.paper_id == old.paper_id)).scalars().all()
            return {"status": "ok", "paper_id": old.paper_id,
                    "item_count": len(items),
                    "report": old.coverage_report,
                    "reused_existing_paper": True,
                    "message": "复用该复测任务已生成的试卷，保证任务恢复后题目不变"}
    picked, report = choose_new_questions(db, task, user_id, count=2)
    if not picked:
        return {"status": "no_question_available", "report": report,
                "message": "没有可用的已核验新题，无法生成复测卷；缺口已报告"}
    # 试卷标题面向学习者：显示考点名而不是 kp.nn.activation 这类内部 id
    kp = db.get(E.KnowledgePoint, task.kp_id) if task.kp_id else None
    kind_label = {"original_correction": "原题订正", "new_transfer": "新题迁移",
                  "delayed": "延迟复测"}.get(task.kind, "复测")
    paper = E.Paper(
        paper_id=E.new_id("paper"), user_id=user_id,
        title=f"{kind_label} · {kp.name if kp else '相关考点'}", mode="retest",
        blueprint={"kind": task.kind, "from_wrong_id": task.from_wrong_id,
                   "exclude_family": task.family_id, "count": len(picked)},
        total_score=sum(float(q.score) for q in picked),
        item_count=len(picked),
        estimated_minutes=sum(float(q.estimated_minutes) for q in picked),
        coverage_report=report, source="bank",
    )
    db.add(paper)
    for i, q in enumerate(picked, start=1):
        db.add(E.PaperItem(
            item_id=E.new_id("item"), paper_id=paper.paper_id, position=i,
            question_id=q.question_id, question_version=q.question_version,
            score=float(q.score), snapshot=bank_service.question_snapshot(q)))
    task.paper_id = paper.paper_id
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=user_id,
        event_type="retest_paper_created", target_id=task.task_id,
        payload={"paper_id": paper.paper_id, "report": report}))
    db.commit()
    return {"status": "ok", "paper_id": paper.paper_id,
            "item_count": len(picked), "report": report}


def start_retest(db: Session, task_id: str,
                 user_id: str = "local_user") -> dict:
    task = db.get(E.RetestTask, task_id)
    if not task:
        raise KeyError(task_id)
    if task.status == "completed":
        return {"status": "already_completed", "message": "该复测任务已完成"}
    r = create_retest_paper(db, task, user_id)
    if r["status"] != "ok":
        return r
    att = attempt_service.create_attempt(db, r["paper_id"], user_id, mode="quiz")
    return {**r, "attempt_id": att["attempt_id"],
            "message": "复测卷已生成（不同题族、已核验题）；交卷后由 verify_learning 验证"}


# --------------------------------------------------------------- V3.0 错题复测
class RetestError(Exception):
    """可直接展示给学生的失败原因。"""


def _wrong_entry(db: Session, entry_id: str) -> E.WrongBookEntry:
    entry = db.get(E.WrongBookEntry, entry_id)
    if entry is None:
        raise RetestError("这条错题记录不存在")
    return entry


def _original_question(db: Session, entry: E.WrongBookEntry) -> E.Question:
    q = db.execute(select(E.Question).where(
        E.Question.question_id == entry.question_id,
        E.Question.question_version == entry.question_version)).scalars().first()
    if q is None:
        raise RetestError("原题的这个版本已不可用，无法重做")
    return q


def _kp_label(db: Session, kp_id: str) -> str:
    kp = db.get(E.KnowledgePoint, kp_id) if kp_id else None
    return kp.name if kp else "相关考点"


def retest_original(db: Session, entry_id: str,
                    user_id: str = "local_user") -> dict:
    """原题重做：单题试卷 + 新答卷。原始错题记录一条都不改。"""
    entry = _wrong_entry(db, entry_id)
    q = _original_question(db, entry)
    task = E.RetestTask(
        task_id=E.new_id("rt"), user_id=user_id, kp_id=entry.kp_id,
        from_wrong_id=entry_id, family_id=entry.family_id or "",
        kind="original_correction", status="pending", paper_id="")
    db.add(task)
    paper = E.Paper(
        paper_id=E.new_id("paper"), user_id=user_id,
        title=f"原题重做 · {_kp_label(db, entry.kp_id)}", mode="retest",
        blueprint={"kind": "original_correction", "from_wrong_id": entry_id,
                   "question_id": q.question_id},
        total_score=float(q.score or 1), item_count=1,
        estimated_minutes=float(q.estimated_minutes or 5),
        coverage_report={"source": "wrong_book_original",
                         "note": "重做的是原题本身，不是同题族的新题"},
        source="wrong_book")
    db.add(paper)
    db.add(E.PaperItem(
        item_id=E.new_id("item"), paper_id=paper.paper_id, position=1,
        question_id=q.question_id, question_version=q.question_version,
        score=float(q.score or 1), snapshot=bank_service.question_snapshot(q)))
    task.paper_id = paper.paper_id
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=user_id,
        event_type="retest_original_started", target_id=entry_id,
        payload={"paper_id": paper.paper_id, "question_id": q.question_id,
                 "note": "重做原题，不覆盖原始错误记录"}))
    db.commit()
    att = attempt_service.create_attempt(db, paper.paper_id, user_id,
                                         mode="quiz")
    return {"ok": True, "task_id": task.task_id, "kind": "original_correction",
            "attempt_id": att["attempt_id"], "paper_id": paper.paper_id,
            "item_count": 1,
            "message": "重做原题；交卷后新增一条作答记录，原来的错题记录仍然保留"}


def generate_similar_questions(db: Session, entry_id: str, *,
                               count: int = 1,
                               evidence_type: str = "variant",
                               user_id: str = "local_user") -> dict:
    """用 generate_questions skill 基于错题生成检测题，并直接进入复测。

    `evidence_type` 决定题族归属（这是"不能靠随机换 id 伪造独立性"的落点）：
      concept  概念辨析：换问法、考同一个概念边界  → 同族
      variant  变式应用：换数值/条件/场景          → 同族
      transfer 迁移应用：要求换考查方式；未核实独立性前仍保留原族
    """
    entry = _wrong_entry(db, entry_id)
    q = _original_question(db, entry)
    kp = _kp_label(db, entry.kp_id)
    count = max(1, min(3, int(count or 1)))
    evidence_type = evidence_type if evidence_type in (
        "concept", "variant", "transfer") else "variant"
    instructions = {
        "concept": ("围绕同一知识点的概念边界重新设问：可用反向提问、辨析干扰项、"
                    "判断适用条件等方式，不得照抄原题干，必须给出答案与解析"),
        "variant": ("围绕原题同一知识点与题型改写：改数值/条件/场景，"
                    "不得照抄原题干，难度相近或略高，必须给出答案与解析"),
        "transfer": ("换成不同的应用场景或问题结构来考同一知识点："
                     "考查方式必须与原题不同（例如原题是计算，本题改为给结果求条件、"
                     "或换一个领域场景），不得照抄原题干，必须给出答案与解析"),
    }
    blueprint = {
        "mode": "similar_to_wrong",
        "evidence_type": evidence_type,
        "count": count,
        "kp_ids": [entry.kp_id] if entry.kp_id else [],
        "question_types": [q.question_type],
        "source_question": {"question_id": q.question_id, "stem": q.stem or "",
                            "question_type": q.question_type,
                            "score": q.score, "kp_name": kp,
                            "difficulty": q.difficulty},
        "instruction": instructions[evidence_type],
    }
    out = skill_runtime.run_skill(db, "generate_questions", blueprint,
                                  user_id=user_id)
    result = out.get("result") or {}
    candidates = result.get("candidates") or []
    if not candidates:
        return {"ok": False, "status": out.get("status") or "failed",
                "message": result.get("message") or "没有生成可用的相似题",
                "uncertainties": out.get("uncertainties") or [],
                "pipeline": result.get("pipeline") or []}

    # The skill returns an 80-character preview, not a complete question.
    # Resolve the saved candidate by ID and independently solve it before use.
    created = _validated_retest_candidates(db, candidates, count,
        kp_ids=[entry.kp_id], requested_types={q.question_type},
        source="wrong_book_similar", already_selected=[], score=float(q.score or 5))
    if len(created) < count:
        return {"ok": False, "status": "verification_failed",
                "verified_count": len(created), "requested": count,
                "message": "相似题独立验答或完整性检查未通过，本次未生成复测卷；可重新生成。"}
    for c in created:
        # A requested rewrite alone does not prove independent transfer.
        c.question_family_id = entry.family_id or q.question_family_id or q.question_id
        c.parent_question_id = q.question_id
        c.notes = (c.notes or "") + "\n由错题生成并独立验答；仅用于本次复测，不可发布到正式题库。"
    db.flush()

    task = E.RetestTask(
        task_id=E.new_id("rt"), user_id=user_id, kp_id=entry.kp_id,
        from_wrong_id=entry_id, family_id=entry.family_id or "",
        kind="new_transfer", status="pending", paper_id="",
        result={"ai_generated": True, "count": len(created),
                "model": out.get("model", ""),
                "source_question_id": q.question_id})
    db.add(task)
    paper = E.Paper(
        paper_id=E.new_id("paper"), user_id=user_id,
        title=f"相似题 · {kp}", mode="retest",
        blueprint={"kind": "new_transfer", "from_wrong_id": entry_id,
                   "similar_to": q.question_id, "ai_generated": True},
        total_score=sum(float(c.score or 5) for c in created),
        item_count=len(created),
        estimated_minutes=sum(float(c.estimated_minutes or 5) for c in created),
        coverage_report={"source": "wrong_book_similar",
                         "note": "AI 依据错题生成，完整候选已通过独立验答，保留AI参考答案标记"},
        source="ai_generated")
    db.add(paper)
    for i, c in enumerate(created, start=1):
        db.add(E.PaperItem(
            item_id=E.new_id("item"), paper_id=paper.paper_id, position=i,
            question_id=c.question_id, question_version=c.question_version,
            score=float(c.score or 5),
            snapshot=bank_service.question_snapshot(c)))
    task.paper_id = paper.paper_id
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=user_id,
        event_type="retest_similar_generated", target_id=entry_id,
        payload={"paper_id": paper.paper_id,
                 "question_ids": [c.question_id for c in created],
                 "model": out.get("model", "")}))
    db.commit()
    att = attempt_service.create_attempt(db, paper.paper_id, user_id,
                                         mode="quiz")
    return {"ok": True, "task_id": task.task_id, "kind": "new_transfer",
            "attempt_id": att["attempt_id"], "paper_id": paper.paper_id,
            "question_ids": [c.question_id for c in created],
            "item_count": len(created), "model": out.get("model", ""),
            "question_source": "ai_generated", "bank_selected_count": 0,
            "message": "相似题只用于本次复测，不进入正式题库；交卷后可与原题对比"}


def list_wrong_retests(db: Session, entry_id: str) -> dict:
    """这道错题的重做/相似题记录，含每次的得分。"""
    rows = db.execute(select(E.RetestTask).where(
        E.RetestTask.from_wrong_id == entry_id).order_by(
        E.RetestTask.created_at)).scalars().all()
    items = []
    for t in rows:
        paper = db.get(E.Paper, t.paper_id) if t.paper_id else None
        attempts = db.execute(select(E.Attempt).where(
            E.Attempt.paper_id == t.paper_id,
            E.Attempt.status == "submitted")).scalars().all() if t.paper_id else []
        items.append({
            "task_id": t.task_id, "kind": t.kind,
            "kind_label": {"original_correction": "重做原题",
                           "new_transfer": "相似题",
                           "delayed": "延迟复测"}.get(t.kind, "复测"),
            "status": t.status, "paper_id": t.paper_id,
            "ai_generated": bool((t.result or {}).get("ai_generated")),
            "attempt_count": len(attempts),
            "best_score": max((a.score or 0) for a in attempts) if attempts else None,
            "max_score": paper.total_score if paper else 0,
            "created_at": t.created_at,
        })
    return {"entry_id": entry_id, "total": len(items), "items": items}


# ------------------------------------------------- V3.1 页面内组卷复测
def _questions_from_candidates(db: Session, cands: list[dict],
                               limit: int) -> list[E.Question]:
    """generate_questions 返回的是摘要，完整题目它已写库，这里按 id 取回。"""
    out: list[E.Question] = []
    for cand in cands[:limit]:
        qid = cand.get("question_id") or ""
        if not qid:
            continue
        q = db.execute(select(E.Question).where(
            E.Question.question_id == qid).order_by(
            E.Question.question_version.desc())).scalars().first()
        if q is not None:
            out.append(q)
    return out


def _validated_retest_candidates(db, cands, limit, *, kp_ids, requested_types,
                                source, already_selected, score=None):
    from backend.app.services.paper_builder_service import _auto_validate_candidates
    candidates = _questions_from_candidates(db, cands, len(cands))
    candidates = list({q.question_id: q for q in candidates
        if q.origin in {"ai", "ai_generated"} and not q.active
        and q.review_status == "pending"}.values())
    for q in candidates:
        q.created_by = source
        if score is not None:
            q.score = score
    accepted = _auto_validate_candidates(db, candidates, kp_ids=kp_ids,
        requested_types=requested_types, already_selected=already_selected)
    for q in candidates:
        q.active = False
    db.commit()
    return accepted[:limit]


def build_retest_paper(db: Session, *, entry_ids: list[str] | None = None,
                       scope: str = "all", kp_id: str = "", count: int = 5,
                       difficulty: str = "same", use_original: bool = True,
                       use_ai: bool = True,
                       user_id: str = "local_user") -> dict:
    """错题复测组卷：原题为主，AI 补足差额，合成一张复测卷。

    范围：selected（选中的错题）/ kp（某知识点的错题）/ all（全部待复测）
    原题与 AI 题的 id 都记进 RetestTask.result，便于追溯来源。
    """
    count = max(1, min(15, int(count or 5)))
    stmt = select(E.WrongBookEntry).where(
        E.WrongBookEntry.user_id == user_id,
        E.WrongBookEntry.status != "verified")
    if scope == "selected" and entry_ids:
        stmt = stmt.where(E.WrongBookEntry.entry_id.in_(entry_ids))
    elif scope == "kp" and kp_id:
        stmt = stmt.where(E.WrongBookEntry.kp_id == kp_id)
    entries = db.execute(stmt.order_by(
        E.WrongBookEntry.created_at.desc())).scalars().all()
    if not entries and scope == "selected" and entry_ids:
        raise RetestError("选中的错题都不在待复测状态里")

    picks: list[E.Question] = []
    used_entries: list[E.WrongBookEntry] = []
    if use_original:
        for entry in entries:
            try:
                q = _original_question(db, entry)
            except RetestError:
                continue
            if not answer_pipeline.is_answer_ready(q) or any(x.question_id == q.question_id for x in picks):
                continue
            picks.append(q)
            used_entries.append(entry)
            if len(picks) >= count:
                break
    original_n = len(picks)

    ai_n = 0
    ai_note = ""
    need = count - original_n
    if use_ai and need > 0 and any(e.kp_id for e in entries):
        kps = sorted({e.kp_id for e in used_entries or entries if e.kp_id})
        source = (used_entries or entries)[0] if (used_entries or entries) else None
        blueprint = {
            "mode": "similar_to_wrong",
            "count": min(max(need + 3, need), 10),
            "kp_ids": kps[:3],
            "question_types": [],
            "difficulty_level": difficulty,
            "source_question": ({
                "stem": source.question_id and (_original_question(db, source).stem
                                                if source else ""),
                "kp_name": _kp_label(db, source.kp_id) if source else "",
            } if source else {}),
            "instruction": ("围绕这些错题的知识点出复测题：中文题干、公式 LaTeX，"
                            "客观题必须给正确答案；难度与原题相近或略高"),
        }
        try:
            out = skill_runtime.run_skill(db, "generate_questions", blueprint,
                                          user_id=user_id)
            cands = (out.get("result") or {}).get("candidates") or []
            ai_qs = _validated_retest_candidates(db, cands, need,
                kp_ids=kps, requested_types=set(), source="wrong_book_compose",
                already_selected=picks)
            picks.extend(ai_qs)
            ai_n = len(ai_qs)
            if ai_n < need:
                ai_note = f"AI 只补出 {ai_n}/{need} 道（其余候选未过校验）"
        except Exception as e:                                # noqa: BLE001
            ai_note = f"AI 补题失败：{type(e).__name__}"
    elif need > 0:
        ai_note = "缺少由错题支持的知识点，未调用AI泛化补题" if use_ai else "未开启 AI 补题"

    if not picks:
        return {"ok": False, "status": "failed",
                "message": ("没有可用于复测的题目：" +
                            (ai_note or "待复测错题为空，且 AI 未产出题目")),
                "requested": count, "original_count": 0, "ai_count": 0}

    kp_names = sorted({_kp_label(db, q.main_kp_id) for q in picks if q.main_kp_id})
    paper = bank_service.create_paper_from_questions(
        db, picks, title=f"错题复测卷（{len(picks)} 题）", mode="retest",
        source="wrong_book_compose",
        blueprint={"entry_ids": [e.entry_id for e in used_entries],
                   "scope": scope, "kp_id": kp_id, "difficulty": difficulty,
                   "source_counts": {"original_reused": original_n, "ai_generated": ai_n, "bank_selected": 0}},
        scores=[float(q.score or 2) for q in picks], user_id=user_id)

    task = E.RetestTask(
        task_id=E.new_id("rt"), user_id=user_id,
        kp_id=kp_id or (used_entries[0].kp_id if used_entries else ""),
        from_wrong_id=used_entries[0].entry_id if used_entries else "",
        family_id="", kind="new_transfer", status="pending",
        paper_id=paper["paper_id"],
        result={"compose": True, "requested": count,
                "original_ids": [q.question_id for q in picks[:original_n]],
                "ai_ids": [q.question_id for q in picks[original_n:]],
                "original_count": original_n, "ai_count": ai_n,
                "difficulty": difficulty, "note": ai_note})
    db.add(task)
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=user_id,
        event_type="retest_paper_composed", target_id=paper["paper_id"],
        payload={"original": original_n, "ai": ai_n, "requested": count}))
    db.commit()

    attempt = attempt_service.create_attempt(
        db, paper["paper_id"], user_id, mode="quiz")
    return {
        "ok": True,
        "status": "generated" if len(picks) >= count else "partial",
        "task_id": task.task_id, "paper_id": paper["paper_id"],
        "attempt_id": attempt["attempt_id"],
        "item_count": len(picks), "requested": count,
        "original_count": original_n, "ai_count": ai_n,
        "total_score": paper["total_score"],
        "estimated_minutes": paper["estimated_minutes"],
        "kp_names": kp_names, "note": ai_note,
        "message": (f"已生成 {len(picks)} 题（原题 {original_n} + AI {ai_n}）"
                    + ("；" + ai_note if ai_note else "")),
    }
