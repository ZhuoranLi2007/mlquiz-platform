"""Real implementations of the eight skills. All are invoked by LangGraph
nodes via the static registry; nothing here is a placeholder.

Deterministic statistics are computed from saved facts. Model calls (when
configured) only add explanations/candidates; if the model is missing or
fails, the deterministic result still succeeds and says so explicitly.
"""
from __future__ import annotations

import ast
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import (
    classroom_diagnosis,
    model_provider,
    retrieval,
    scope_service,
)
from backend.app.skills import prompts

# ---------------------------------------------------------------- helpers

def _parse_time(s: str) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _days(a: datetime | None, b: datetime | None) -> float:
    if not a or not b:
        return 0.0
    return abs((b - a).total_seconds()) / 86400.0


def _kp_name(db: Session, kp_id: str) -> str:
    kp = db.get(E.KnowledgePoint, kp_id)
    return kp.name if kp else kp_id


def _snippet_context(results: list[dict], limit: int = 5) -> str:
    parts = []
    for r in results[:limit]:
        parts.append(f"[{r['chunk_id']}] {r.get('title','')}\n{r.get('snippet','')}")
    return "\n\n".join(parts)


def _safe_eval(expr: str) -> float | None:
    """Independent arithmetic check for numeric items. No names/calls allowed."""
    try:
        tree = ast.parse(expr, mode="eval")
        allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                   ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub,
                   ast.UAdd, ast.Mod)
        for node in ast.walk(tree):
            if not isinstance(node, allowed):
                return None
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                return None
        return float(eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {}))
    except Exception:
        return None


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", (s or "").lower()))


def _jaccard(a: str, b: str) -> float:
    x, y = _tokens(a), _tokens(b)
    return len(x & y) / len(x | y) if x | y else 0.0


def _evidence(db: Session, user_id: str) -> dict[str, list[dict]]:
    """Valid evidence only. Hints / seen answers / unverified items excluded
    from state promotion."""
    out: dict[str, list[dict]] = defaultdict(list)
    scores = db.execute(select(E.AnswerScore)).scalars().all()
    for s in scores:
        att = db.get(E.Attempt, s.attempt_id)
        if not att or att.user_id != user_id or att.status != "submitted":
            continue
        if s.scoring_status not in ("auto", "rescored") or s.correct is None:
            continue
        # 没有标注考点的题（多为扩展/外部题）无法归因到某个考点，
        # 不参与考点状态计算，避免出现「未标注」这种假考点。
        if not (s.kp_id or "").strip():
            continue
        ans = db.execute(select(E.Answer).where(
            E.Answer.attempt_id == s.attempt_id,
            E.Answer.question_id == s.question_id)).scalars().first()
        q = db.execute(select(E.Question).where(
            E.Question.question_id == s.question_id,
            E.Question.question_version == s.question_version)).scalars().first()
        hints = ans.hints_used if ans else 0
        viewed = ans.solution_viewed if ans else False
        seen = ans.seen_before if ans else False
        independent = bool(ans and ans.is_independent_evidence and not hints
                           and not viewed and not seen)
        # A question counts as having a usable answer when it passed a human
        # cross-check OR the automatic AI-answer checks. Historically this
        # required answer_status == "verified", which wrongly excluded every
        # AI reference answer from being independent evidence.
        from backend.app.services import answer_pipeline
        if q and not answer_pipeline.is_answer_ready(q):
            independent = False
        out[s.kp_id].append({
            "attempt_id": s.attempt_id, "score_id": s.score_id,
            "question_id": s.question_id,
            "family_id": s.family_id or s.question_id,
            "correct": bool(s.correct), "awarded_score": s.awarded_score,
            "max_score": s.max_score, "scoring_status": s.scoring_status,
            "independent": independent, "hints": hints,
            "solution_viewed": viewed, "seen_before": seen,
            "time": _parse_time(att.started_at) or datetime.now(timezone.utc),
            "question_answer_status": q.answer_status if q else "unknown",
        })
    for k in out:
        out[k].sort(key=lambda x: x["time"])
    return out


def _state_for(ev: list[dict], *, delayed_ok: bool,
               hint_count: int) -> tuple[str, str, dict]:
    valid = [e for e in ev if e["independent"]]
    fams = {e["family_id"] for e in valid}
    span = _days(valid[0]["time"], valid[-1]["time"]) if len(valid) >= 2 else 0.0
    info = {"evidence_count": len(valid),
            "independent_family_count": len(fams),
            "time_span_days": round(span, 2),
            "hint_count": hint_count,
            "excluded_non_independent": len(ev) - len(valid),
            "delayed_retest_passed": delayed_ok}
    if not ev:
        return "未测", "尚无作答记录；未测不等于不会", info
    if not valid or len(valid) < cfg.STATE_MIN_EVIDENCE or not fams:
        return "证据不足", ("独立有效证据不足；提示、已见答案、同题族重复与待核查成绩"
                       "不参与状态提升"), info
    last3 = valid[-3:]
    if not last3[-1]["correct"]:
        return "需加强", "最近一次独立作答为错误，先回到需加强；需新题验证", info
    if len(fams) >= cfg.STATE_MIN_FAMILIES and all(e["correct"] for e in last3):
        if span >= cfg.STATE_STABLE_MIN_DAYS and delayed_ok:
            return "阶段稳定", "跨日期、不同题族与延迟复测证据均满足", info
        return "初步通过", "近期独立作答正确且覆盖多个题族；尚缺跨日期/延迟证据", info
    return "证据不足", "近期正确但独立题族或连续正确次数不足", info


def refresh_states(db: Session, user_id: str) -> dict[str, dict]:
    evidence = _evidence(db, user_id)
    delayed = db.execute(select(E.RetestTask).where(
        E.RetestTask.user_id == user_id, E.RetestTask.kind == "delayed",
        E.RetestTask.status == "completed")).scalars().all()
    delayed_ok = {t.kp_id for t in delayed if (t.result or {}).get("correct")}
    out: dict[str, dict] = {}
    for kp_id, ev in evidence.items():
        hints = sum(e["hints"] for e in ev)
        state, why, info = _state_for(ev, delayed_ok=kp_id in delayed_ok,
                                      hint_count=hints)
        row = db.get(E.KnowledgeState, (user_id, kp_id))
        if row is None:
            row = E.KnowledgeState(user_id=user_id, kp_id=kp_id)
            db.add(row)
        row.state = state
        row.evidence_count = info["evidence_count"]
        row.independent_family_count = info["independent_family_count"]
        row.time_span_days = info["time_span_days"]
        row.hint_count = info["hint_count"]
        row.last_evidence_at = ev[-1]["time"].isoformat(timespec="seconds") if ev else ""
        row.explanation = why
        row.updated_at = E.utcnow()
        out[kp_id] = {"state": state, **info, "explanation": why}
    db.commit()
    return out


# ---------------------------------------------------------------- 1. diagnose_question

def diagnose_question(db: Session, payload: dict, *, run_id: str,
                      thread_id: str, job_id: str, user_id: str) -> dict:
    aid, qid = payload.get("attempt_id", ""), payload.get("question_id", "")
    sc = db.execute(select(E.AnswerScore).where(
        E.AnswerScore.attempt_id == aid,
        E.AnswerScore.question_id == qid)).scalars().first()
    ans = db.execute(select(E.Answer).where(
        E.Answer.attempt_id == aid, E.Answer.question_id == qid)).scalars().first()
    wrong = db.execute(select(E.WrongBookEntry).where(
        E.WrongBookEntry.attempt_id == aid,
        E.WrongBookEntry.question_id == qid)).scalars().first()
    att = db.get(E.Attempt, aid)
    item = None
    if att:
        item = db.execute(select(E.PaperItem).where(
            E.PaperItem.paper_id == att.paper_id,
            E.PaperItem.question_id == qid)).scalars().first()
    if not sc:
        return {"result": {"message": "未找到该题的评分记录", "attempt_id": aid,
                           "question_id": qid},
                "uncertainties": ["无作答事实，无法诊断"],
                "next_actions": ["先完成作答并交卷"],
                "engine": "deterministic", "status": "no_data"}

    snap = item.snapshot if item else {}
    observed = {
        "question_type": snap.get("question_type"),
        "kp_id": sc.kp_id, "family_id": sc.family_id,
        "response": ans.response if ans else {},
        "correct": sc.correct, "awarded_score": sc.awarded_score,
        "max_score": sc.max_score, "scoring_status": sc.scoring_status,
        "hints_used": ans.hints_used if ans else 0,
        "solution_viewed": ans.solution_viewed if ans else False,
        "seen_before": ans.seen_before if ans else False,
        "independent_evidence": ans.is_independent_evidence if ans else True,
        "answer_status": snap.get("answer_status", "unknown"),
    }
    candidates = list(wrong.misconception_candidates) if wrong else []
    if sc.scoring_status == "needs_manual":
        candidates.append("本题未自动评分；不能据此判断错误类型")
    evidence_refs = [
        {"score_id": sc.score_id, "attempt_id": aid, "question_id": qid},
        {"answer_id": ans.answer_id if ans else ""},
    ] + (snap.get("source_refs") or [])[:3]
    next_actions = ["用不同题族的新题独立验证，再据此更新状态",
                    "允许补充自我说明（不会 / 算错 / 读错 / 猜测）"]
    if sc.correct is False:
        next_actions.insert(0, "先在错题本订正，再生成迁移验证新题")
    uncertainties = ["单题错误只支持候选误区，不能确定学生的心理原因",
                     "若使用提示、看过答案或同题族重复作答，本次不是独立新题证据"]
    engine = "deterministic"
    model_note = None
    if cfg.MODEL_API_KEY and payload.get("use_model", True):
        try:
            kp = _kp_name(db, sc.kp_id)
            found = retrieval.keyword_search(db, kp, limit=3)
            out = model_provider.chat(db, messages=[
                {"role": "system", "content": prompts.DIAGNOSE_QUESTION_SYSTEM},
                {"role": "user", "content": prompts.DIAGNOSE_QUESTION_USER.format(
                    facts=json.dumps(observed, ensure_ascii=False),
                    context=_snippet_context(found["results"], 3))},
            ], skill_name="diagnose_question",
                prompt_version=prompts.PROMPT_VERSION, run_id=run_id)
            parsed = model_provider.parse_json_content(out["content"])
            if isinstance(parsed, dict):
                model_note = parsed
                engine = "deterministic+model"
            else:
                uncertainties.append("模型返回非结构化内容，已忽略，不影响确定性结论")
        except model_provider.ModelUnavailable as e:
            uncertainties.append(f"未使用模型解释：{e.message}")
    else:
        uncertainties.append("模型未配置：本题诊断使用确定性事实与候选规则，未调用模型")

    result = {"observed": observed, "candidate_causes": candidates,
              "evidence": evidence_refs, "next_verification": next_actions,
              "model_explanation": model_note}
    return {"result": result, "evidence_refs": evidence_refs,
            "uncertainties": uncertainties, "next_actions": next_actions,
            "engine": engine, "status": "succeeded",
            "diagnosis_level": "question", "diagnosis_target": f"{aid}:{qid}"}


# ---------------------------------------------------------------- 2. diagnose_paper

def diagnose_paper(db: Session, payload: dict, *, run_id: str,
                   thread_id: str, job_id: str, user_id: str) -> dict:
    aid = payload.get("attempt_id", "")
    att = db.get(E.Attempt, aid)
    if not att:
        return {"result": {"message": "作答记录不存在"},
                "uncertainties": ["无数据"], "next_actions": [],
                "engine": "deterministic", "status": "no_data"}
    scores = db.execute(select(E.AnswerScore).where(
        E.AnswerScore.attempt_id == aid)).scalars().all()
    by_kp: dict[str, dict] = defaultdict(
        lambda: {"items": 0, "auto": 0, "correct": 0, "lost": 0.0,
                 "manual": 0, "families": set(), "evidence_ids": []})
    for s in scores:
        k = s.kp_id or "(未标注)"
        b = by_kp[k]
        b["items"] += 1
        b["lost"] += (s.max_score - s.awarded_score)
        b["families"].add(s.family_id or s.question_id)
        b["evidence_ids"].append(s.score_id)
        if s.scoring_status in ("auto", "rescored") and s.correct is not None:
            b["auto"] += 1
            b["correct"] += 1 if s.correct else 0
        else:
            b["manual"] += 1
    coverage = []
    for k, b in by_kp.items():
        coverage.append({
            "kp_id": k, "kp_name": _kp_name(db, k),
            "items": b["items"], "auto_scored_items": b["auto"],
            "correct_items": b["correct"], "manual_pending": b["manual"],
            "lost_points": round(b["lost"], 2),
            "independent_families": len(b["families"]),
            "evidence_ids": b["evidence_ids"],
            "accuracy_if_scored": (round(b["correct"] / b["auto"], 2)
                                   if b["auto"] else None),
        })
    coverage.sort(key=lambda x: (-x["lost_points"], x["kp_id"]))
    weak = [c for c in coverage if c["auto_scored_items"] and
            (c["lost_points"] > 0 or c["accuracy_if_scored"] < 1.0)]
    priority = [{"kp_id": c["kp_id"], "reason": f"失分 {c['lost_points']} 分，"
                 f"覆盖 {c['items']} 题，独立题族 {c['independent_families']} 个"}
                for c in coverage if c["lost_points"] > 0][:6]
    evidence_refs = [{"attempt_id": aid, "score_id": s.score_id,
                      "question_id": s.question_id} for s in scores]
    uncertainties = [
        "未出现在本次试卷中的知识点是『未测』，不等于不会",
        "文本题/答案未知题未评分，其知识点证据数量不足",
        "不同卷的总分差不能直接解释为能力变化",
    ]
    result = {
        "attempt_id": aid, "paper_id": att.paper_id,
        "score": att.score, "max_score": att.max_score,
        "manual_pending_count": att.manual_pending_count,
        "covered_knowledge_points": coverage,
        "weak_points": [c["kp_id"] for c in weak],
        "review_priority": priority,
    }
    engine = "deterministic"
    if cfg.MODEL_API_KEY and payload.get("use_model", True):
        try:
            out = model_provider.chat(db, messages=[
                {"role": "system", "content": prompts.PAPER_SUMMARY_SYSTEM},
                {"role": "user", "content": prompts.PAPER_SUMMARY_USER.format(
                    facts=json.dumps(result, ensure_ascii=False, default=str))},
            ], skill_name="diagnose_paper",
                prompt_version=prompts.PROMPT_VERSION, run_id=run_id)
            parsed = model_provider.parse_json_content(out["content"])
            if isinstance(parsed, dict):
                result["model_summary"] = parsed
                engine = "deterministic+model"
        except model_provider.ModelUnavailable as e:
            uncertainties.append(f"未使用模型摘要：{e.message}")
    else:
        uncertainties.append("模型未配置：报告由确定性统计生成")
    return {"result": result, "evidence_refs": evidence_refs,
            "uncertainties": uncertainties,
            "next_actions": ["对薄弱知识点生成不同题族的新题复测",
                             "补充答案未知题的评分细则后再纳入证据"],
            "engine": engine, "status": "succeeded",
            "diagnosis_level": "paper", "diagnosis_target": aid}


# ---------------------------------------------------------------- 3. diagnose_history

def diagnose_history(db: Session, payload: dict, *, run_id: str,
                     thread_id: str, job_id: str, user_id: str) -> dict:
    evidence = _evidence(db, user_id)
    states = refresh_states(db, user_id)
    attempts = db.execute(select(E.Attempt).where(
        E.Attempt.user_id == user_id,
        E.Attempt.status == "submitted")).scalars().all()
    comparisons = []
    for kp_id, ev in evidence.items():
        valid = [e for e in ev if e["independent"]]
        comparisons.append({
            "kp_id": kp_id, "kp_name": _kp_name(db, kp_id),
            "state": states.get(kp_id, {}).get("state", "未测"),
            "total_attempt_items": len(ev),
            "independent_items": len(valid),
            "independent_families": len({e["family_id"] for e in valid}),
            "time_span_days": states.get(kp_id, {}).get("time_span_days", 0),
            "assisted_items": sum(1 for e in ev if not e["independent"]),
            "series": [{"attempt_id": e["attempt_id"],
                        "correct": e["correct"], "family_id": e["family_id"],
                        "time": e["time"].isoformat(timespec="seconds")}
                       for e in ev],
        })
    comparisons.sort(key=lambda x: (-x["total_attempt_items"], x["kp_id"]))
    result = {
        "submitted_attempts": len(attempts),
        "per_knowledge_point": comparisons,
        "attempt_totals": [{"attempt_id": a.attempt_id, "score": a.score,
                            "max_score": a.max_score, "started_at": a.started_at}
                           for a in attempts],
        "state_summary": {k: v["state"] for k, v in states.items()},
        "rule": "按有效证据的时间顺序计算；待核查成绩、提示、已见答案、同题族重复作答不提升状态",
    }
    return {
        "result": result,
        "evidence_refs": [{"attempt_id": a.attempt_id} for a in attempts],
        "uncertainties": [
            "不同试卷的总分差受题目难度、题量与辅助情况影响，不能直接解释为能力变化",
            "未测知识点不显示掌握概率；证据不足不给精确数值",
            "历次比较只使用同一知识点、相近认知要求的独立作答",
        ],
        "next_actions": ["对『需加强』知识点安排延迟复测",
                         "补齐答案未知题的评分以扩大证据覆盖"],
        "engine": "deterministic", "status": "succeeded",
        "diagnosis_level": "history", "diagnosis_target": user_id,
    }


# ---------------------------------------------------------- 3b. diagnose_classroom

def diagnose_classroom(db: Session, payload: dict, *, run_id: str,
                       thread_id: str, job_id: str, user_id: str) -> dict:
    """Run one evidence-bound classroom diagnosis through the Skill runtime.

    The classroom service remains the single owner of denominator, privacy and
    immutable-paper-snapshot rules.  This adapter adds the uniform SkillRun
    envelope and deliberately takes the actor from ``run_skill(user_id=...)``
    instead of accepting a spoofable actor ID in the payload.
    """
    scope_type = str(payload.get("scope_type") or "").strip()
    allowed = {
        "question_analysis", "student_attempt", "student_history",
        "class_assessment",
    }
    if scope_type not in allowed:
        raise classroom_diagnosis.ClassroomDiagnosisError(
            "scope_type 必须是 question_analysis、student_attempt、"
            "student_history 或 class_assessment")

    direct = classroom_diagnosis.run_diagnosis(
        db,
        scope_type=scope_type,
        actor_user_id=user_id or "local_user",
        classroom_id=str(payload.get("classroom_id") or "").strip(),
        student_id=str(payload.get("student_id") or "").strip(),
        assessment_version_id=str(
            payload.get("assessment_version_id") or "").strip(),
        paper_item_id=str(payload.get("paper_item_id") or "").strip(),
        attempt_id=str(payload.get("attempt_id") or "").strip(),
    )
    # Link the domain Diagnosis to the enclosing SkillRun so either audit row
    # can lead to the other.  Direct service callers keep their native version;
    # only this adapter upgrades the row to the registered Skill identity.
    diagnosis = db.get(E.Diagnosis, direct.get("diagnosis_id"))
    if diagnosis is not None:
        diagnosis.run_id = run_id
        diagnosis.skill_version = (
            f"{cfg.SKILL_REGISTRY_VERSION}/diagnose_classroom-1.0")
        db.add(diagnosis)
        db.commit()
    return {
        "result": direct.get("result") or {},
        "evidence_refs": list(direct.get("evidence_refs") or []),
        "uncertainties": list(direct.get("uncertainties") or []),
        "next_actions": list(direct.get("next_actions") or []),
        "engine": direct.get("engine") or "deterministic",
        "status": "succeeded",
        "extra": {
            "diagnosis_id": direct.get("diagnosis_id"),
            "diagnosis_run_id": run_id,
            "scope_type": direct.get("scope_type"),
            "level": direct.get("level"),
            "target_id": direct.get("target_id"),
        },
    }


# ---------------------------------------------------------------- 4. recommend_resources

def recommend_resources(db: Session, payload: dict, *, run_id: str,
                        thread_id: str, job_id: str, user_id: str) -> dict:
    kp_ids = list(payload.get("kp_ids") or [])
    if not kp_ids and payload.get("attempt_id"):
        scores = db.execute(select(E.AnswerScore).where(
            E.AnswerScore.attempt_id == payload["attempt_id"])).scalars().all()
        kp_ids = sorted({s.kp_id for s in scores if s.kp_id})
    if not kp_ids:
        states = db.execute(select(E.KnowledgeState).where(
            E.KnowledgeState.user_id == user_id,
            E.KnowledgeState.state.in_(["需加强", "证据不足"]))).scalars().all()
        kp_ids = [s.kp_id for s in states]
    recs: list[dict] = []
    search_summary = []
    for kp_id in kp_ids[:8]:
        name = _kp_name(db, kp_id)
        found = retrieval.keyword_search(db, name, limit=3)
        search_summary.append({"kp_id": kp_id, "query": name,
                               "hits": found["total"], "mode": found["mode"]})
        for r in found["results"][:2]:
            src = (r["source_refs"] or [{}])[0]
            page = src.get("page")
            recs.append({
                "type": "local_chunk", "kp_id": kp_id,
                "title": f"{r['title']} · {name}",
                "snippet": r["snippet"][:180],
                "source_refs": r["source_refs"],
                "review_status": r["review_status"],
                "quality_note": ("该页需视觉/公式复核，查看原页图"
                                 if r["review_status"].startswith("needs_") else ""),
                "url": (f"/api/materials/{src.get('document_id')}/page/{page}/image"
                        if src.get("document_id") and page else None),
                "reason": "本地资料关键词检索命中，可回到实际页序",
            })
        from backend.app.services.classroom_content import is_search_entry
        for res in db.execute(select(E.Resource).where(
                E.Resource.kp_ids.contains(kp_id))).scalars().all():
            if is_search_entry(res.url, res.title):
                continue
            recs.append({
                "type": "external", "kp_id": kp_id, "title": res.title,
                "url": res.url, "platform": res.platform,
                "reason": res.reason, "verified": res.verified,
                "verifiable_segment": res.verifiable_segment,
                "note": "外部入口，未读取正文/字幕",
            })
    return {
        "result": {"kp_ids": kp_ids, "recommendations": recs,
                   "search_summary": search_summary,
                   "retrieval_mode": retrieval.retrieval_status()["mode"]},
        "evidence_refs": [r.get("source_refs", []) for r in recs
                          if r["type"] == "local_chunk"][:10],
        "uncertainties": ["外部资源仅登记可访问入口，未读取正文/字幕，不编造内容",
                         "标记为需复核的页，文本可能缺公式，请打开原页图"],
        "next_actions": ["打开本地原页或外部链接学习", "学完后用新题验证"],
        "engine": "deterministic+keyword_retrieval", "status": "succeeded",
    }


# ---------------------------------------------------------------- 5. generate_questions

def generate_questions(db: Session, payload: dict, *, run_id: str,
                       thread_id: str, job_id: str, user_id: str) -> dict:
    """blueprint -> retrieve -> generate -> structure check -> answer verify
    -> dedupe -> review queue. NEVER auto-publishes, NEVER mocks a model."""
    bp = payload.get("blueprint") or payload
    count = int(bp.get("count", 3))
    kp_ids = [k for k in (bp.get("kp_ids") or []) if k]
    pipeline: list[dict] = []

    def stage(name: str, status: str, detail: Any):
        pipeline.append({"stage": name, "status": status, "detail": detail})

    stage("blueprint", "done", {"count": count, "kp_ids": kp_ids,
                                "question_types": bp.get("question_types", [])})
    # --- retrieve evidence (a few snippets only) ---
    context: list[dict] = []
    for kp_id in (kp_ids or ["机器学习"])[:4]:
        name = _kp_name(db, kp_id) if kp_ids else "机器学习 损失函数 梯度"
        found = retrieval.keyword_search(db, name, limit=4)
        context.extend(found["results"])
    seen_ids: set[str] = set()
    context = [c for c in context
               if not (c["chunk_id"] in seen_ids or seen_ids.add(c["chunk_id"]))][:6]
    stage("retrieve_evidence", "done",
          {"chunks": len(context), "mode": "keyword",
           "chunk_ids": [c["chunk_id"] for c in context]})

    status = model_provider.chat_status()
    if not status["configured"]:
        stage("generate_candidates", "blocked", status)
        return {
            "result": {"candidates": [], "pipeline": pipeline,
                       "message": "模型 API 未配置：未生成任何题目，也未使用模拟结果冒充真实调用。"
                                  "请填写 .env 后重新运行；当前仍可使用已核验题库组卷。",
                       "model_status": status},
            "uncertainties": ["未配置 provider/model/api_key，无法验证输出结构、引用与题目质量"],
            "next_actions": ["配置 .env 中的 MLQ_MODEL_* 并运行连接诊断",
                             "配置后先对少量样例验证再扩大范围"],
            "engine": "not_run",
            "status": "model_not_configured",
            "extra": {"pipeline": pipeline},
        }
    # --- real model call ---
    try:
        out = model_provider.chat(db, messages=[
            {"role": "system", "content": prompts.QUESTION_GEN_SYSTEM},
            {"role": "user", "content": prompts.QUESTION_GEN_USER.format(
                context=_snippet_context(context, 6),
                blueprint=json.dumps(bp, ensure_ascii=False))},
        ], skill_name="generate_questions",
            prompt_version=prompts.PROMPT_VERSION, run_id=run_id,
            max_tokens=3200, timeout=120.0)
    except model_provider.ModelUnavailable as e:
        stage("generate_candidates", "failed", e.message)
        return {"result": {"candidates": [], "pipeline": pipeline},
                "uncertainties": ["模型调用失败，未产生可信候选题"],
                "next_actions": ["检查 base_url / api_key / 额度后重试"],
                "engine": "model_failed", "status": "call_failed",
                "error_code": e.status,
                "extra": {"pipeline": pipeline}}
    # A JSON array cut off by the token budget parses as garbage or as a
    # shorter list; judge it here so truncation is never mistaken for a
    # legitimately small question set.
    if out.get("finish_reason") == "length":
        stage("generate_candidates", "failed",
              "输出被 max_tokens 截断，候选题 JSON 不完整")
        return {"result": {"candidates": [], "pipeline": pipeline},
                "uncertainties": ["模型输出被截断，未产生可信候选题"],
                "next_actions": ["减少本次题量或重试"],
                "engine": "model_failed", "status": "output_truncated",
                "extra": {"pipeline": pipeline, "model": out.get("model", ""),
                          "finish_reason": "length",
                          "usage": out["usage"]}}
    parsed = model_provider.parse_json_content(out["content"])
    raw = parsed if isinstance(parsed, list) else (
        parsed.get("questions", []) if isinstance(parsed, dict) else [])
    stage("generate_candidates", "done",
          {"raw_count": len(raw), "usage": out["usage"],
           "usage_missing": out["usage_missing"],
           "model": out.get("model", ""),
           "duration_ms": out.get("duration_ms")})

    # --- structure check ---
    valid_types = {"single_choice", "multi_choice", "true_false", "numeric",
                   "short_answer"}
    checked, rejected = [], []
    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            rejected.append({"i": i, "reason": "不是对象"})
            continue
        qt = c.get("question_type")
        stem = (c.get("stem") or "").strip()
        opts = c.get("options") or []
        ans = c.get("answer_payload") or {}
        refs = c.get("source_chunk_ids") or []
        problems = []
        if qt not in valid_types:
            problems.append("题型不合法或未知")
        if len(stem) < 6:
            problems.append("题干过短")
        if qt in ("single_choice", "multi_choice") and len(opts) < 2:
            problems.append("选项不足")
        if not ans:
            problems.append("缺少答案")
        if not refs or not any(r in {x["chunk_id"] for x in context} for r in refs):
            problems.append("缺少真实来源引用")
        if qt == "single_choice" and ans.get("key") not in {o.get("key") for o in opts}:
            problems.append("答案不在选项内")
        if qt == "multi_choice" and not set(ans.get("keys", [])) <= {o.get("key") for o in opts}:
            problems.append("多选答案含无效选项")
        if problems:
            rejected.append({"i": i, "reason": "；".join(problems)})
        else:
            checked.append(c)
    stage("structure_check", "done", {"valid": len(checked),
                                      "rejected": rejected})

    # --- V2.1 课程范围复检（变式题也要查，不能因为原题在范围内就免检）---
    scope_rejected = []
    kept_by_scope = []
    for c in checked:
        verdict = scope_service.judge_generated(db, c.get("kp_id", ""), c.get("stem", ""))
        c["scope_class"] = verdict["scope_class"]
        c["scope_basis"] = verdict["basis"]
        if verdict["scope_class"] == "unconfirmed":
            scope_rejected.append({"stem": (c.get("stem") or "")[:50],
                                   "reason": verdict["basis"]})
        else:
            kept_by_scope.append(c)
    checked = kept_by_scope
    stage("scope_check", "done", {
        "kept": len(checked), "unconfirmed": len(scope_rejected),
        "rejected": scope_rejected,
        "note": "按老师 PPT 范围表复判；判不清的进待确认，不发布"})
    # --- independent answer verification ---
    verified: list[dict] = []
    for c in checked:
        note = "结构检查通过；答案仍为 AI 候选，需人工/独立计算核验"
        if c["question_type"] == "numeric":
            calc = _safe_eval(str(c.get("calculation", "")))
            try:
                val = float(c["answer_payload"]["value"])
            except (KeyError, TypeError, ValueError):
                val = None
            if calc is None or val is None:
                note = "数值题缺少可独立计算的计算式，标为待核查"
            elif abs(calc - val) <= float(c["answer_payload"].get("tol", 0.01)) + 1e-9:
                note = f"独立计算复核一致：{c.get('calculation')} = {calc}"
            else:
                note = f"独立计算复核不一致（计算 {calc} / 候选答案 {val}），标为待核查"
        verified.append({"candidate": c, "verification_note": note})
    stage("answer_verification", "done",
          {"checked": len(verified),
           "numeric_items": sum(1 for v in verified
                                if v["candidate"]["question_type"] == "numeric")})

    # --- dedupe ---
    existing = db.execute(select(E.Question.stem)).scalars().all()
    deduped = []
    for v in verified:
        stem = v["candidate"]["stem"]
        sim = max((_jaccard(stem, s or "") for s in existing), default=0.0)
        if sim >= 0.78:
            rejected.append({"stem": stem[:50], "reason": f"与题库重复 (相似度 {sim:.2f})"})
        else:
            deduped.append(v)
    stage("dedupe", "done", {"kept": len(deduped), "total_rejected": len(rejected)})

    # --- review queue: never auto-publish ---
    created = []
    for i, v in enumerate(deduped[:count]):
        c = v["candidate"]
        qid = E.new_id("aiq")
        row = E.Question(
            row_id=qid, question_id=qid, question_version=1, origin="ai",
            question_type=c["question_type"], stem=c["stem"],
            options=c.get("options") or [], stem_images=[], score=2.0,
            main_kp_id=c.get("kp_id", ""),
            cognitive_level=c.get("cognitive_level", "理解"),
            difficulty=c.get("difficulty", "中等"),
            source_refs=[{"chunk_id": r} for r in c.get("source_chunk_ids", [])],
            question_family_id=E.new_id("fam"), review_status="pending",
            answer_status="candidate", answer_origin="ai_candidate",
            active=False,  # 审核通过后才发布
            answer_payload=c["answer_payload"],
            explanation=v["verification_note"],
            scope_class=c.get("scope_class") or "unconfirmed",
            notes=("AI 候选：未核验，成绩不用于上调阶段稳定状态；待人工审核后发布。"
                   f"范围复检：{c.get('scope_basis', '')}"),
            created_by="skill:generate_questions",
        )
        db.add(row)
        created.append({"question_id": qid, "stem": c["stem"][:80],
                        # 调用方（retest_service 生成复测题）需要这两项才能写对
                        # answer_payload 与题型；以前不回传，导致生成题答案为空、
                        # 无法真实判分，复测只能靠"模拟答对"。
                        "question_type": c["question_type"],
                        "answer_payload": c["answer_payload"],
                        "verification_note": v["verification_note"],
                        "scope_class": c.get("scope_class"),
                        "scope_basis": c.get("scope_basis", ""),
                        "review_status": "pending", "active": False})
    db.commit()
    stage("review_or_publish", "queued",
          {"created_candidates": len(created), "published": 0,
           "note": "候选写入题库但 active=False，需审核后发布"})
    return {
        "result": {"candidates": created, "pipeline": pipeline,
                   "rejected": rejected,
                   "message": "已生成候选并完成结构/答案/去重检查；未自动发布，未标为已核验。"},
        "evidence_refs": [{"chunk_id": c["chunk_id"]} for c in context],
        "uncertainties": ["模型自检只提供线索，不能独立认证答案正确",
                         "候选答案未经人工核验，不参与阶段稳定状态"],
        "next_actions": ["人工审核候选题并发布", "数值题以独立计算复核"],
        "engine": "model+deterministic_validation", "status": "succeeded",
        "model": cfg.MODEL_CHAT_MODEL,
        "extra": {"pipeline": pipeline},
    }


# ---------------------------------------------------------------- 6. verify_learning

def verify_learning(db: Session, payload: dict, *, run_id: str,
                    thread_id: str, job_id: str, user_id: str) -> dict:
    task_id = payload.get("retest_task_id", "")
    attempt_id = payload.get("attempt_id", "")
    task = db.get(E.RetestTask, task_id) if task_id else None
    if not task and attempt_id:
        task = db.execute(select(E.RetestTask).where(
            E.RetestTask.paper_id == (db.get(E.Attempt, attempt_id).paper_id
                                      if db.get(E.Attempt, attempt_id) else ""),
        )).scalars().first()
    if not task:
        return {"result": {"message": "未找到到期复测任务"},
                "uncertainties": ["无复测任务"], "next_actions": [],
                "engine": "deterministic", "status": "no_data"}
    # Repeated job delivery after a restart must not create a second delayed
    # retest or a second learning_verified event for the same task.
    if task.status == "completed":
        return {"result": {"message": "该复测任务已完成验证，未重复生成延迟任务",
                           "task_id": task.task_id, "passed": True,
                           "already_completed": True},
                "evidence_refs": [{"retest_task_id": task.task_id}],
                "uncertainties": [], "next_actions": ["等待下一次延迟复测"],
                "engine": "deterministic", "status": "succeeded"}
    aid = attempt_id or ""
    if not aid and task.paper_id:
        atts = db.execute(select(E.Attempt).where(
            E.Attempt.paper_id == task.paper_id,
            E.Attempt.status == "submitted").order_by(
            E.Attempt.started_at.desc())).scalars().first()
        aid = atts.attempt_id if atts else ""
    scores = db.execute(select(E.AnswerScore).where(
        E.AnswerScore.attempt_id == aid)).scalars().all() if aid else []
    answers = db.execute(select(E.Answer).where(
        E.Answer.attempt_id == aid)).scalars().all() if aid else []
    amap = {a.question_id: a for a in answers}
    independent = bool(scores) and all(
        not amap.get(s.question_id) or
        (amap[s.question_id].is_independent_evidence
         and amap[s.question_id].hints_used == 0
         and not amap[s.question_id].solution_viewed
         and not amap[s.question_id].seen_before)
        for s in scores)
    all_correct = bool(scores) and all(s.correct is True for s in scores)
    new_family = bool(scores) and all(
        (s.family_id or s.question_id) != task.family_id for s in scores)
    # 同族但换了考查形式（题型/认知层次不同）只能算"变式证据"，
    # 不能当作迁移证据；两者都不同才升为 transfer。
    q_meta = {}
    for s in scores:
        q = db.execute(select(E.Question).where(
            E.Question.question_id == s.question_id,
            E.Question.question_version == s.question_version)).scalars().first()
        if q is not None:
            q_meta[s.question_id] = (q.question_type or "", q.cognitive_level or "")
    variant_only = bool(scores) and not new_family
    level_changed = bool(scores) and all(
        q_meta.get(s.question_id, ("", ""))[1] != "" for s in scores)
    if variant_only and level_changed:
        evidence_tier = "variant_same_family"
    elif new_family:
        evidence_tier = "transfer_distinct_family"
    else:
        evidence_tier = "insufficient"
    result = {
        "task_id": task.task_id, "attempt_id": aid, "kp_id": task.kp_id,
        "scored_items": len(scores), "independent": independent,
        "all_correct": all_correct, "new_question_family": new_family,
        "evidence_tier": evidence_tier,
        "passed": bool(independent and all_correct and new_family),
    }
    uncertainties: list[str] = []
    if not independent:
        uncertainties.append("使用了提示/已见答案或同题族重复，不能冒充独立新题证据")
    if not new_family:
        uncertainties.append("仍为原题族，不能作为迁移验证")
    if not scores:
        uncertainties.append("未找到评分记录（可能尚未作答或全为待评分题）")
    if result["passed"]:
        task.status = "completed"
        task.result = {**result, "completed_at": E.utcnow()}
        wrong = db.get(E.WrongBookEntry, task.from_wrong_id) if task.from_wrong_id else None
        if wrong:
            wrong.status = "verified"
            wrong.updated_at = E.utcnow()
        # schedule delayed retest (project rule 1/3/7 days, next unused interval)
        existing = db.execute(select(E.RetestTask).where(
            E.RetestTask.from_wrong_id == task.from_wrong_id,
            E.RetestTask.kind == "delayed")).scalars().all()
        idx = min(len(existing), len(cfg.RETEST_INTERVALS_DAYS) - 1)
        days = cfg.RETEST_INTERVALS_DAYS[idx]
        db.add(E.RetestTask(
            task_id=E.new_id("rt"), user_id=user_id, kp_id=task.kp_id,
            from_wrong_id=task.from_wrong_id, family_id=task.family_id,
            kind="delayed",
            due_at=(datetime.now(timezone.utc) + timedelta(days=days))
            .isoformat(timespec="seconds"),
            status="pending",
        ))
        db.add(E.LearningEvent(
            event_id=E.new_id("evt"), user_id=user_id,
            event_type="learning_verified", target_id=task.task_id,
            payload=result))
    elif scores:
        # 学生确实做了，但证据不够（答错 / 非独立 / 仍是同族变式）。
        # 不能永远挂在 pending，也不能伪造成 completed。
        task.status = "needs_more_evidence"
        task.result = {**result, "checked_at": E.utcnow(),
                       "reason": ("同族变式题答对，只能证明会做这一类，不能证明迁移"
                                  if (result["all_correct"] and variant_only) else
                                  "存在答错或非独立作答，证据不足以判定掌握")}
        db.add(E.LearningEvent(
            event_id=E.new_id("evt"), user_id=user_id,
            event_type="learning_needs_more_evidence", target_id=task.task_id,
            payload=task.result))
    states = refresh_states(db, user_id)
    db.commit()
    result["state_after"] = states.get(task.kp_id, {})
    return {
        "result": result,
        "evidence_refs": [{"attempt_id": aid, "score_id": s.score_id}
                          for s in scores],
        "uncertainties": uncertainties + [
            "做对一次只更新状态，不删除旧错题记录",
            "阶段稳定还需跨日期、不同题族与延迟复测证据"],
        "next_actions": (["安排延迟复测任务"] if result["passed"] else
                         ["订正后用不同题族的新题再次验证"]),
        "engine": "deterministic", "status": "succeeded",
        "diagnosis_level": "question", "diagnosis_target": f"verify:{task.task_id}",
    }


# ---------------------------------------------------------------- 7. build_study_plan

def build_study_plan(db: Session, payload: dict, *, run_id: str,
                     thread_id: str, job_id: str, user_id: str) -> dict:
    exam_date = payload.get("exam_date") or cfg.EXAM_DATE
    budget = int(payload.get("daily_minutes") or cfg.DEFAULT_DAILY_MINUTES)
    today = datetime.now().strftime("%Y-%m-%d")
    weak = db.execute(select(E.KnowledgeState).where(
        E.KnowledgeState.user_id == user_id,
        E.KnowledgeState.state.in_(["需加强", "证据不足"]))).scalars().all()
    due = db.execute(select(E.RetestTask).where(
        E.RetestTask.user_id == user_id,
        E.RetestTask.status == "pending")).scalars().all()
    open_wrong = db.execute(select(E.WrongBookEntry).where(
        E.WrongBookEntry.user_id == user_id,
        E.WrongBookEntry.status != "verified")).scalars().all()
    candidates: list[dict] = []
    for t in due:
        candidates.append({"kind": "retest", "kp_id": t.kp_id, "minutes": 20,
                           "title": f"复测：{_kp_name(db, t.kp_id)}",
                           "priority": 100,
                           "rationale": f"到期复测任务 {t.task_id}（{t.kind}）",
                           "question_ids": [], "task_ref": t.task_id})
    for w in open_wrong[:8]:
        candidates.append({"kind": "correction", "kp_id": w.kp_id,
                           "minutes": 15, "title": f"订正：{_kp_name(db, w.kp_id)}",
                           "priority": 80, "rationale": f"错题 {w.entry_id} 待订正",
                           "question_ids": [w.question_id], "task_ref": w.entry_id})
    for s in weak[:10]:
        candidates.append({"kind": "review", "kp_id": s.kp_id, "minutes": 25,
                           "title": f"复习：{_kp_name(db, s.kp_id)}",
                           "priority": 60 if s.state == "需加强" else 40,
                           "rationale": f"知识状态 {s.state}，证据 {s.evidence_count} 条",
                           "question_ids": [], "task_ref": ""})
    # Keep total within the daily budget without silently dropping the reason.
    candidates.sort(key=lambda x: (-x["priority"], x["kp_id"]))
    chosen, total, dropped = [], 0, 0
    for c in candidates:
        if total + c["minutes"] <= budget:
            chosen.append(c)
            total += c["minutes"]
        else:
            dropped += 1
    old = db.execute(select(E.StudyPlan).where(
        E.StudyPlan.user_id == user_id,
        E.StudyPlan.active.is_(True))).scalars().all()
    for p in old:
        p.active = False
    plan = E.StudyPlan(
        plan_id=E.new_id("plan"), user_id=user_id, exam_date=exam_date,
        daily_minutes=budget,
        payload={"rule": "按到期复测 > 错题订正 > 需加强 > 证据不足 排序；"
                         "教师重点为依据来源陈述，未经核查不升级为教师要求",
                 "dropped_by_budget": dropped,
                 "total_minutes": total,
                 "rationale": f"考试日期 {exam_date}，每日预算 {budget} 分钟；"
                              f"共安排 {len(chosen)} 项、{total} 分钟"},
    )
    db.add(plan)
    created = []
    for c in chosen:
        # bind a REAL material ref: local chunk or a verified external entry
        material_ref = ""
        name = _kp_name(db, c["kp_id"])
        found = retrieval.keyword_search(db, name, limit=1)
        if found["results"]:
            src = (found["results"][0]["source_refs"] or [{}])[0]
            material_ref = f"{src.get('rel_path', '')} p.{src.get('page', '')}"
        if not material_ref:
            res = db.execute(select(E.Resource).where(
                E.Resource.kp_ids.contains(c["kp_id"]))).scalars().first()
            material_ref = res.url if res else ""
        if not material_ref and c["question_ids"]:
            material_ref = "题目 " + ", ".join(c["question_ids"])
        row = E.PlanTask(
            task_id=E.new_id("pt"), plan_id=plan.plan_id, user_id=user_id,
            kp_id=c["kp_id"], title=c["title"], kind=c["kind"],
            material_ref=material_ref, question_ids=c["question_ids"],
            minutes=c["minutes"], due_date=today,
            completion_criteria=("完成一次独立新题作答并交卷" if c["kind"] == "retest"
                                 else "写出订正与自我说明" if c["kind"] == "correction"
                                 else "阅读绑定材料并完成配套题目"),
            rationale=c["rationale"], status="pending",
        )
        db.add(row)
        created.append({"task_id": row.task_id, "title": row.title,
                        "minutes": row.minutes, "material_ref": material_ref,
                        "completion_criteria": row.completion_criteria})
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=user_id,
        event_type="study_plan_generated", target_id=plan.plan_id,
        payload={"total_minutes": total, "tasks": len(created),
                 "dropped_by_budget": dropped}))
    db.commit()
    return {
        "result": {"plan_id": plan.plan_id, "exam_date": exam_date,
                   "daily_minutes": budget, "total_minutes": total,
                   "dropped_by_budget": dropped, "tasks": created,
                   "message": f"已生成 {len(created)} 项任务、合计 {total} 分钟"
                              f"（预算 {budget} 分钟）"},
        "evidence_refs": [{"retest_task_id": t.task_id} for t in due[:5]] +
                         [{"wrong_entry_id": w.entry_id} for w in open_wrong[:5]],
        "uncertainties": ["任务时长是项目估算值，可由用户调整",
                         "资料中的『老师重点』是来源陈述，未升级为已确认教师要求"],
        "next_actions": ["逐项完成任务", "完成后用新题验证并复测"],
        "engine": "deterministic", "status": "succeeded",
    }


# ---------------------------------------------------------------- 8. tutor_answer

def tutor_answer(db: Session, payload: dict, *, run_id: str,
                 thread_id: str, job_id: str, user_id: str) -> dict:
    message = (payload.get("message") or "").strip()
    mode = payload.get("mode", "practice")
    session_id = payload.get("session_id", "default")
    context = payload.get("context") or {}
    if not isinstance(context, dict):
        context = {}
    attempt_id = str(context.get("attempt_id") or "").strip()
    invalid_attempt_context = False
    if attempt_id:
        attempt = db.get(E.Attempt, attempt_id)
        if attempt is None or attempt.user_id != user_id:
            # Skill calls may also come from workers rather than the HTTP
            # route.  Unknown attempt context therefore fails closed.
            mode = "quiz"
            invalid_attempt_context = True
        else:
            mode = attempt.mode
    db.add(E.ChatMessage(
        message_id=E.new_id("msg"), user_id=user_id, session_id=session_id,
        role="user", content=message, mode=mode, context=context,
        run_id=run_id))
    db.commit()
    if mode == "quiz":
        answer = (
            "无法验证关联答卷，助手已安全禁用。" if invalid_attempt_context else
            "测验模式下助手已明确禁用，避免答案泄露。请先交卷后查看成绩与解析。"
        )
        db.add(E.ChatMessage(
            message_id=E.new_id("msg"), user_id=user_id, session_id=session_id,
            role="assistant", content=answer, mode=mode, engine="blocked",
            run_id=run_id))
        db.commit()
        return {"result": {"answer": answer, "blocked": True},
                "uncertainties": [], "next_actions": ["交卷后再提问"],
                "engine": "blocked_by_quiz_mode", "status": "succeeded"}

    question_stem = str(context.get("question_stem") or "").strip()[:1600]
    kp_name = str(context.get("kp_name") or "").strip()[:160]
    # The student's short request (for example “给我一个提示”) has few useful
    # keywords.  The current stem and knowledge-point name make retrieval
    # relevant, while only these allow-listed fields enter the model prompt.
    search_query = "\n".join(
        part for part in (message, kp_name, question_stem) if part)
    found = retrieval.keyword_search(db, search_query, limit=3)
    refs = []
    for r in found["results"]:
        refs.extend(r.get("source_refs") or [])
    ctx = _snippet_context(found["results"], 3)
    engine = "deterministic_retrieval"
    model_used = False
    model_name = ""
    prompt_question = message
    if question_stem or kp_name:
        prompt_question = (
            f"【当前题目】{question_stem or '未提供'}\n"
            f"【相关考点】{kp_name or '未提供'}\n"
            f"【学生问题】{message}"
        )
    if cfg.MODEL_API_KEY:
        try:
            out = model_provider.chat(db, messages=[
                {"role": "system", "content": prompts.TUTOR_SYSTEM},
                {"role": "user", "content": prompts.TUTOR_USER.format(
                    context=ctx or "（无命中片段）", mode=mode,
                    question=prompt_question)},
            ], skill_name="tutor_answer",
                prompt_version=prompts.PROMPT_VERSION, run_id=run_id,
                max_tokens=1600, timeout=120.0)
            model_name = out.get("model", "")
            parsed = model_provider.parse_json_content(out["content"])
            if out.get("finish_reason") == "length":
                answer = ("模型输出被 max_tokens 截断（回答过长），已丢弃这段不完整内容。"
                          "以下是本地检索结果，可先据此对照原页：\n" + _fallback(ctx))
            elif isinstance(parsed, dict) and parsed.get("answer"):
                answer = parsed["answer"]
                engine = "model+retrieval"
                model_used = True
                if parsed.get("insufficient"):
                    answer += "\n\n（课程资料依据不足，以上可能包含通用补充。）"
            else:
                answer = "模型返回结构不符合约定，已忽略；以下是本地检索结果。\n" + _fallback(ctx)
        except model_provider.ModelUnavailable as e:
            answer = f"模型调用失败（{e.message}）。以下是本地检索结果。\n" + _fallback(ctx)
    else:
        answer = ("模型 API 未配置，本次不生成模型结论。以下是基于本地资料的真实检索结果，"
                  "请结合原页自行判断：\n" + _fallback(ctx))
    db.add(E.ChatMessage(
        message_id=E.new_id("msg"), user_id=user_id, session_id=session_id,
        role="assistant", content=answer, mode=mode, context=context,
        source_refs=refs[:6], engine=engine, run_id=run_id,
    ))
    db.commit()
    return {
        "result": {"answer": answer, "source_refs": refs[:6],
                   "retrieval_mode": "keyword", "model_used": model_used,
                   "note": "聊天中的『我懂了』不会被记为掌握证据"},
        "evidence_refs": refs[:6],
        "uncertainties": ([] if model_used else
                          ["未配置模型或未成功调用，未生成解释性结论"]),
        "next_actions": ["打开引用原页核对", "用新题独立验证理解"],
        "engine": engine, "model": model_name if model_used else "",
        "status": "succeeded",
    }


def _fallback(ctx: str) -> str:
    if not ctx.strip():
        return "（本地资料未命中相关片段，依据不足。）"
    return ctx[:900]


# ---------------------------------------------------------------- 9. learning_diagnosis

LAYER_DEFS = [
    ("layer1", "基础知识理解", "概念是否掌握、定义是否理解、基本公式是否记得准"),
    ("layer2", "方法应用能力", "能否选对算法、完成基本计算、把知识点用到具体问题"),
    ("layer3", "综合分析与推理", "综合题、数学推导、算法之间的联系、变化条件下的问题"),
]
OBJECTIVE_TYPES = {"single_choice", "multi_choice", "true_false"}
HARD_TYPES = {"derivation", "comprehensive", "short_answer", "proof"}


def _layer_of(question_type: str, cognitive: str) -> str:
    """把题目归到三层之一：先看题型，再看认知层次。"""
    if question_type in HARD_TYPES:
        return "layer3"
    if cognitive and any(k in cognitive for k in ("综合", "分析", "推导")):
        return "layer3"
    if question_type == "numeric" or cognitive == "应用":
        return "layer2"
    return "layer1"


def _three_layers(db: Session, attempt_id: str) -> dict:
    """按真实评分记录算三层得分率；没有证据的层明确标「未测」。"""
    scores = db.execute(select(E.AnswerScore).where(
        E.AnswerScore.attempt_id == attempt_id)).scalars().all()
    buckets: dict[str, dict] = {
        key: {"key": key, "name": name, "meaning": meaning,
              "items": 0, "scored": 0, "correct": 0, "lost": 0.0,
              "kp_ids": set()}
        for key, name, meaning in LAYER_DEFS}
    for s in scores:
        q = db.execute(select(E.Question).where(
            E.Question.question_id == s.question_id,
            E.Question.question_version == s.question_version)).scalars().first()
        if q is None:
            continue
        key = _layer_of(q.question_type or "", q.cognitive_level or "")
        b = buckets[key]
        b["items"] += 1
        if s.kp_id:
            b["kp_ids"].add(s.kp_id)
        # 只有自动/重评过的分才算证据；待人工评的不进分母
        if (s.scoring_status or "auto") not in ("auto", "rescored"):
            continue
        b["scored"] += 1
        b["lost"] += (s.max_score or 0) - (s.awarded_score or 0)
        if (s.awarded_score or 0) >= (s.max_score or 0) - 1e-6:
            b["correct"] += 1
    out = []
    for key, name, meaning in LAYER_DEFS:
        b = buckets[key]
        acc = round(b["correct"] / b["scored"], 2) if b["scored"] else None
        out.append({
            "key": key, "name": name, "meaning": meaning,
            "items": b["items"], "scored_items": b["scored"],
            "correct_items": b["correct"], "lost_points": round(b["lost"], 2),
            "accuracy": acc,
            "status": ("掌握良好" if acc is not None and acc >= 0.85 else
                       "存在薄弱" if acc is not None and acc >= 0.6 else
                       "需要重点提升" if acc is not None else "未测"),
            "kp_ids": sorted(b["kp_ids"]),
        })
    return {"layers": out,
            "note": ("分层依据题目题型与认知层次；本次试卷没覆盖到的层次标「未测」，"
                     "不当作已掌握")}


def learning_diagnosis(db: Session, payload: dict, *, run_id: str,
                       thread_id: str, job_id: str, user_id: str) -> dict:
    """诊断 → 推荐 → 出题 → 复测，一条工作流串起来（V3.0）。

    每一步都调用已有的 skill，不复制第二套 AI 逻辑；任一步失败都记录在
    `steps` 里并继续，不伪造下一位的结果。
    """
    attempt_id = (payload.get("attempt_id") or "").strip()
    ctx = dict(run_id=run_id, thread_id=thread_id, job_id=job_id,
               user_id=user_id)
    steps: list[dict] = []
    uncertainties: list[str] = []
    evidence: list[dict] = []

    def step(name: str, status: str, detail: Any) -> None:
        steps.append({"step": name, "status": status, "detail": detail})

    if not attempt_id:
        return {"result": {"steps": steps},
                "uncertainties": ["缺少 attempt_id，无法诊断"],
                "next_actions": ["先完成一次作答，再运行学习诊断"],
                "engine": "deterministic", "status": "no_data"}

    # 1) 试卷层诊断
    diag = diagnose_paper(db, {"attempt_id": attempt_id,
                               "use_model": payload.get("use_model", True)}, **ctx)
    diag_result = diag.get("result") or {}
    weak = list(diag_result.get("weak_points") or [])
    evidence.extend(diag.get("evidence_refs") or [])
    step("diagnose_paper", diag.get("status", "failed"),
         {"weak_points": weak, "covered": len(diag_result.get(
             "covered_knowledge_points") or [])})
    if diag.get("status") != "succeeded":
        uncertainties.append("试卷诊断未完成，后续推荐与出题降级为全部薄弱点")

    # 2) 三层认知诊断
    layers = _three_layers(db, attempt_id)
    step("three_layers", "done",
         {l["key"]: l["accuracy"] for l in layers["layers"]})
    weak_layers = [l["key"] for l in layers["layers"]
                   if l["accuracy"] is not None and l["accuracy"] < 0.6]
    if weak_layers:
        uncertainties.append(
            "认知层次 " + "、".join(weak_layers) + " 得分率低于 0.6，"
            "但单次试卷不足以断定能力缺陷")

    # 3) 学习资源推荐
    rec = recommend_resources(db, {"attempt_id": attempt_id,
                                   "kp_ids": weak[:6]}, **ctx)
    rec_result = rec.get("result") or {}
    step("recommend_resources", rec.get("status", "failed"),
         {"count": len(rec_result.get("recommendations") or [])})
    evidence.extend(rec.get("evidence_refs") or [])
    uncertainties.extend(rec.get("uncertainties") or [])

    # 4) 新题生成（显式开启才做，避免每次诊断都烧额度）
    generated = {"skipped": True,
                 "reason": "默认不生成新题，需要时由 request.generate_questions 开启"}
    if payload.get("generate_questions") and weak:
        count = max(1, min(3, int(payload.get("question_count") or 2)))
        gen = generate_questions(db, {"count": count, "kp_ids": weak[:3]},
                                 **ctx)
        gen_result = gen.get("result") or {}
        generated = {
            "skipped": False, "status": gen.get("status"),
            "candidates": len(gen_result.get("candidates") or []),
            "message": gen_result.get("message", ""),
        }
        step("generate_questions", gen.get("status", "failed"),
             {"count": generated["candidates"]})
        if gen.get("status") != "succeeded":
            uncertainties.append("新题未生成成功：" +
                                  (gen_result.get("message") or "未知原因"))
    else:
        step("generate_questions", "skipped", generated["reason"])

    # 5) 复测状态
    ver = verify_learning(db, {"attempt_id": attempt_id}, **ctx)
    ver_result = ver.get("result") or {}
    step("verify_learning", ver.get("status", "failed"),
         {"has_retest": bool(ver_result)})

    return {
        "result": {
            "attempt_id": attempt_id,
            "diagnosis": diag_result,
            "three_layers": layers,
            "recommendations": rec_result,
            "generated_questions": generated,
            "verification": ver_result,
            "steps": steps,
        },
        "evidence_refs": evidence,
        "uncertainties": uncertainties,
        "next_actions": ["按推荐顺序复习薄弱知识点",
                         "用错题复测的重做/相似题检验掌握程度",
                         "复测后再跑一次诊断，对比前后变化"],
        "engine": diag.get("engine", "deterministic"),
        "status": "succeeded",
        "extra": {"steps": steps},
    }
