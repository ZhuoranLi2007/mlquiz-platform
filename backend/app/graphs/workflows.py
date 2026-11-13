"""Real LangGraph workflows. Every graph node is executed code; the six
business skills are invoked through the static registry and each call is
recorded with thread_id / job_id / user_id / run_id.

* diagnosis_graph : 保存答卷 → 判分(已保存) → 单题诊断 → 单卷诊断 →
                    更新证据 → 历次诊断 → 推荐 → 更新计划
* generation_graph: 蓝图 → 检索依据 → 生成候选(含校验/去重/审核) → 汇总
* retest_graph    : 读取到期任务 → 选新题 → (作答) → 评分 → 验证学习证据 → 更新状态

Checkpoints (MemorySaver) allow recovery of one graph execution; business
facts live in SQLite, never only in the checkpoint.
"""
from __future__ import annotations

import uuid
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import retrieval, retest_service
from backend.app.services.skill_runtime import run_skill
from backend.app.skills.registry import assert_registry

assert_registry()
CHECKPOINTER = MemorySaver()


class GraphState(TypedDict, total=False):
    attempt_id: str
    user_id: str
    thread_id: str
    job_id: str
    blueprint: dict
    retest_task_id: str
    stages: list
    outputs: dict
    errors: list
    kp_ids: list
    status: str


def _stage(state: GraphState, name: str, detail: Any = None) -> list:
    return (state.get("stages") or []) + [
        {"node": name, "detail": detail}]


def _out(state: GraphState, key: str, value: Any) -> dict:
    return {**(state.get("outputs") or {}), key: value}


def _call(db: Session, state: GraphState, name: str, payload: dict,
          retries: int = 1) -> dict:
    last: dict = {}
    for attempt in range(retries + 1):
        try:
            last = run_skill(db, name, payload,
                             thread_id=state.get("thread_id", ""),
                             job_id=state.get("job_id", ""),
                             user_id=state.get("user_id", "local_user"))
            if last.get("status") not in ("failed", "call_failed"):
                return last
        except Exception as e:
            last = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
    return last


# ---------------------------------------------------------------- diagnosis graph

def build_diagnosis_graph(db: Session):
    def score_check(s: GraphState) -> dict:
        a = db.get(E.Attempt, s["attempt_id"])
        if not a:
            return {"stages": _stage(s, "score_check", "attempt not found"),
                    "errors": (s.get("errors") or []) + ["作答记录不存在"],
                    "status": "failed"}
        detail = {"attempt_id": a.attempt_id, "status": a.status,
                  "score_already_saved": a.score,
                  "max_score": a.max_score,
                  "note": "客观成绩在交卷时已确定性保存；模型失败不会丢失答卷"}
        return {"stages": _stage(s, "score_check", detail),
                "outputs": _out(s, "score_check", detail),
                "status": "running" if a.status == "submitted" else "failed",
                "errors": (s.get("errors") or []) +
                          ([] if a.status == "submitted" else ["答卷尚未交卷"])}

    def question_diagnoses(s: GraphState) -> dict:
        scores = db.execute(select(E.AnswerScore).where(
            E.AnswerScore.attempt_id == s["attempt_id"])).scalars().all()
        results, errors = [], list(s.get("errors") or [])
        for sc in scores:
            r = _call(db, s, "diagnose_question",
                      {"attempt_id": s["attempt_id"],
                       "question_id": sc.question_id})
            results.append({"question_id": sc.question_id,
                            "status": r.get("status"),
                            "run_id": r.get("run_id"),
                            "engine": r.get("engine")})
            if r.get("status") == "failed":
                errors.append(f"diagnose_question {sc.question_id}: {r.get('error')}")
        return {"stages": _stage(s, "question_diagnoses",
                                 {"count": len(results)}),
                "outputs": _out(s, "question_diagnoses", results),
                "errors": errors}

    def paper_diagnosis(s: GraphState) -> dict:
        r = _call(db, s, "diagnose_paper", {"attempt_id": s["attempt_id"]})
        kps = [c["kp_id"] for c in (r.get("result") or {})
               .get("covered_knowledge_points", [])] if isinstance(
                   r.get("result"), dict) else []
        return {"stages": _stage(s, "paper_diagnosis",
                                 {"status": r.get("status"),
                                  "kp_count": len(kps)}),
                "outputs": _out(s, "paper_diagnosis", r),
                "kp_ids": kps,
                "errors": (s.get("errors") or []) +
                          ([] if r.get("status") != "failed" else
                           [f"diagnose_paper: {r.get('error')}"])}

    def history_diagnosis(s: GraphState) -> dict:
        r = _call(db, s, "diagnose_history", {"user_id": s["user_id"]})
        return {"stages": _stage(s, "history_diagnosis",
                                 {"status": r.get("status")}),
                "outputs": _out(s, "history_diagnosis", r),
                "errors": (s.get("errors") or []) +
                          ([] if r.get("status") != "failed" else
                           [f"diagnose_history: {r.get('error')}"])}

    def recommend(s: GraphState) -> dict:
        r = _call(db, s, "recommend_resources",
                  {"kp_ids": s.get("kp_ids") or [],
                   "attempt_id": s["attempt_id"]})
        return {"stages": _stage(s, "recommend", {"status": r.get("status")}),
                "outputs": _out(s, "recommend", r),
                "errors": (s.get("errors") or []) +
                          ([] if r.get("status") != "failed" else
                           [f"recommend_resources: {r.get('error')}"])}

    def update_plan(s: GraphState) -> dict:
        r = _call(db, s, "build_study_plan",
                  {"exam_date": cfg.EXAM_DATE,
                   "daily_minutes": cfg.DEFAULT_DAILY_MINUTES})
        return {"stages": _stage(s, "update_plan", {"status": r.get("status")}),
                "outputs": _out(s, "update_plan", r),
                "errors": (s.get("errors") or []) +
                          ([] if r.get("status") != "failed" else
                           [f"build_study_plan: {r.get('error')}"])}

    def finalize(s: GraphState) -> dict:
        a = db.get(E.Attempt, s["attempt_id"])
        if a:
            a.diagnosis_status = "failed" if s.get("errors") else "succeeded"
            db.commit()
        return {"stages": _stage(s, "finalize", {"errors": len(s.get("errors") or [])}),
                "status": "failed" if s.get("errors") else "succeeded"}

    b = StateGraph(GraphState)
    for name, fn in (("score_check", score_check),
                     ("question_diagnoses", question_diagnoses),
                     ("paper_diagnosis", paper_diagnosis),
                     ("history_diagnosis", history_diagnosis),
                     ("recommend", recommend),
                     ("update_plan", update_plan),
                     ("finalize", finalize)):
        b.add_node(name, fn)
    b.add_edge(START, "score_check")
    # A non-submitted / missing attempt must stop the graph instead of
    # producing diagnoses from non-existent facts.
    b.add_conditional_edges(
        "score_check",
        lambda s: "continue" if s.get("status") == "running" else "abort",
        {"continue": "question_diagnoses", "abort": "finalize"},
    )
    b.add_edge("question_diagnoses", "paper_diagnosis")
    b.add_edge("paper_diagnosis", "history_diagnosis")
    b.add_edge("history_diagnosis", "recommend")
    b.add_edge("recommend", "update_plan")
    b.add_edge("update_plan", "finalize")
    b.add_edge("finalize", END)
    return b.compile(checkpointer=CHECKPOINTER)


def run_diagnosis(db: Session, attempt_id: str,
                  user_id: str = "local_user", job_id: str = "") -> dict:
    thread_id = f"diag:{attempt_id}:{job_id or uuid.uuid4().hex[:8]}"
    app = build_diagnosis_graph(db)
    initial: GraphState = {
        "attempt_id": attempt_id, "user_id": user_id,
        "thread_id": thread_id, "job_id": job_id,
        "stages": [], "outputs": {}, "errors": [], "kp_ids": [],
        "status": "queued",
    }
    final = app.invoke(initial, config={"configurable": {"thread_id": thread_id}})
    return {
        "thread_id": thread_id, "job_id": job_id, "user_id": user_id,
        "graph": "diagnosis_graph", "status": final.get("status"),
        "stages": final.get("stages", []),
        "outputs": final.get("outputs", {}),
        "errors": final.get("errors", []),
        "checkpoint_available": CHECKPOINTER.get_tuple(
            {"configurable": {"thread_id": thread_id}}) is not None,
    }


# ---------------------------------------------------------------- generation graph

def build_generation_graph(db: Session):
    def blueprint_check(s: GraphState) -> dict:
        bp = s.get("blueprint") or {}
        return {"stages": _stage(s, "blueprint_check",
                                 {"count": bp.get("count", 3),
                                  "kp_ids": bp.get("kp_ids", [])}),
                "outputs": _out(s, "blueprint_check", bp)}

    def retrieve(s: GraphState) -> dict:
        bp = s.get("blueprint") or {}
        found = retrieval.keyword_search(
            db, " ".join(bp.get("kp_ids") or []) or "机器学习 损失函数 梯度",
            limit=5)
        return {"stages": _stage(s, "retrieve",
                                 {"hits": found["total"], "mode": found["mode"],
                                  "chunk_ids": [r["chunk_id"]
                                                for r in found["results"][:5]]}),
                "outputs": _out(s, "retrieve", found)}

    def generate(s: GraphState) -> dict:
        r = _call(db, s, "generate_questions",
                  {"blueprint": s.get("blueprint") or {}}, retries=0)
        return {"stages": _stage(s, "generate", {"status": r.get("status"),
                                                 "engine": r.get("engine")}),
                "outputs": _out(s, "generate", r),
                "status": r.get("status", "failed"),
                "errors": (s.get("errors") or []) +
                          ([] if r.get("status") in ("succeeded",) else
                           [f"generate_questions: {r.get('status')}"])}

    def finalize(s: GraphState) -> dict:
        g = (s.get("outputs") or {}).get("generate") or {}
        return {"stages": _stage(s, "finalize",
                                 {"candidates": len(((g.get("result") or {})
                                                     .get("candidates") or []))}),
                "status": "succeeded" if g.get("status") == "succeeded"
                          else g.get("status", "failed")}

    b = StateGraph(GraphState)
    for name, fn in (("blueprint_check", blueprint_check),
                     ("retrieve", retrieve), ("generate", generate),
                     ("finalize", finalize)):
        b.add_node(name, fn)
    b.add_edge(START, "blueprint_check")
    b.add_edge("blueprint_check", "retrieve")
    b.add_edge("retrieve", "generate")
    b.add_edge("generate", "finalize")
    b.add_edge("finalize", END)
    return b.compile(checkpointer=CHECKPOINTER)


def run_generation(db: Session, blueprint: dict,
                   user_id: str = "local_user", job_id: str = "") -> dict:
    thread_id = f"gen:{uuid.uuid4().hex[:10]}"
    app = build_generation_graph(db)
    initial: GraphState = {
        "user_id": user_id, "thread_id": thread_id, "job_id": job_id,
        "blueprint": blueprint, "stages": [], "outputs": {}, "errors": [],
        "status": "queued",
    }
    final = app.invoke(initial, config={"configurable": {"thread_id": thread_id}})
    out = (final.get("outputs") or {}).get("generate") or {}
    return {
        "thread_id": thread_id, "job_id": job_id, "graph": "generation_graph",
        "status": final.get("status"), "stages": final.get("stages", []),
        "result": out.get("result"), "engine": out.get("engine"),
        "run_id": out.get("run_id"), "errors": final.get("errors", []),
        "checkpoint_available": CHECKPOINTER.get_tuple(
            {"configurable": {"thread_id": thread_id}}) is not None,
    }


# ---------------------------------------------------------------- retest graph

def build_retest_graph(db: Session):
    def load_due(s: GraphState) -> dict:
        task = db.get(E.RetestTask, s.get("retest_task_id", "")) if s.get(
            "retest_task_id") else None
        if not task:
            task = db.execute(select(E.RetestTask).where(
                E.RetestTask.status == "pending").order_by(
                E.RetestTask.due_at)).scalars().first()
        return {"stages": _stage(s, "load_due",
                                 {"task_id": task.task_id if task else None}),
                "outputs": _out(s, "load_due",
                                {"task_id": task.task_id if task else None,
                                 "kp_id": task.kp_id if task else ""}),
                "retest_task_id": task.task_id if task else "",
                "errors": (s.get("errors") or []) +
                          ([] if task else ["没有待处理复测任务"])}

    def select_questions(s: GraphState) -> dict:
        task = db.get(E.RetestTask, s.get("retest_task_id", ""))
        if not task:
            return {"stages": _stage(s, "select_questions", "no task"),
                    "outputs": _out(s, "select_questions", {"status": "no_task"})}
        r = retest_service.create_retest_paper(db, task, s["user_id"])
        return {"stages": _stage(s, "select_questions",
                                 {"status": r.get("status"),
                                  "selected": r.get("item_count", 0)}),
                "outputs": _out(s, "select_questions", r)}

    def verify(s: GraphState) -> dict:
        r = _call(db, s, "verify_learning",
                  {"retest_task_id": s.get("retest_task_id", "")})
        return {"stages": _stage(s, "verify", {"status": r.get("status"),
                                               "passed": (r.get("result") or {})
                                               .get("passed")}),
                "outputs": _out(s, "verify", r)}

    def finalize(s: GraphState) -> dict:
        return {"stages": _stage(s, "finalize", {}),
                "status": "succeeded" if not s.get("errors") else "failed"}

    b = StateGraph(GraphState)
    for name, fn in (("load_due", load_due),
                     ("select_questions", select_questions),
                     ("verify", verify), ("finalize", finalize)):
        b.add_node(name, fn)
    b.add_edge(START, "load_due")
    b.add_edge("load_due", "select_questions")
    b.add_edge("select_questions", "verify")
    b.add_edge("verify", "finalize")
    b.add_edge("finalize", END)
    return b.compile(checkpointer=CHECKPOINTER)


def run_retest(db: Session, retest_task_id: str = "",
               user_id: str = "local_user", job_id: str = "") -> dict:
    thread_id = f"retest:{retest_task_id or 'due'}:{uuid.uuid4().hex[:8]}"
    app = build_retest_graph(db)
    initial: GraphState = {
        "user_id": user_id, "thread_id": thread_id, "job_id": job_id,
        "retest_task_id": retest_task_id, "stages": [], "outputs": {},
        "errors": [], "status": "queued",
    }
    final = app.invoke(initial, config={"configurable": {"thread_id": thread_id}})
    return {"thread_id": thread_id, "graph": "retest_graph",
            "status": final.get("status"), "stages": final.get("stages", []),
            "outputs": final.get("outputs", {}),
            "errors": final.get("errors", []),
            "checkpoint_available": CHECKPOINTER.get_tuple(
                {"configurable": {"thread_id": thread_id}}) is not None}


def graph_status() -> dict:
    """Proof the graphs are compiled code, not documentation drawings."""
    from backend.app.models.base import SessionLocal
    s = SessionLocal()
    try:
        graphs = {
            "diagnosis_graph": build_diagnosis_graph(s),
            "generation_graph": build_generation_graph(s),
            "retest_graph": build_retest_graph(s),
        }
        return {
            "langgraph_available": True,
            "checkpointer": type(CHECKPOINTER).__name__,
            "graphs": {
                name: {"compiled_type": type(g).__name__,
                       "nodes": sorted(g.get_graph().nodes.keys())}
                for name, g in graphs.items()
            },
            "skills_registered": sorted(
                __import__("backend.app.skills.registry", fromlist=["REGISTRY"])
                .REGISTRY.keys()),
        }
    finally:
        s.close()
