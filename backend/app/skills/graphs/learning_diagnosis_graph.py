"""学习闭环的 LangGraph 实现（V3.0 第二轮，V3.4 修复落库断点）。

这一层只做**编排**，节点内部通过 `skill_runtime.run_skill()` 调用已有 skill，
既不复制任何 AI 逻辑，也不绕过 skill 的统一执行记录。

V3.4 修复（RC-2）：早期版本节点直接调用 `implementations.diagnose_paper(...)`，
而 `Diagnosis` 行只在 `run_skill()` 内部写入，于是图跑完 `skill_runs` 成功、
`diagnoses` 表却零行，`/api/reports/three-layer` 因此返回 `paper: null`。
现在每个节点都走 `run_skill`，三层诊断结果再以 merge 方式并回试卷层诊断，
保证「图能跑」与「结果留痕」是同一件事。

图结构：

    START → diagnose → three_layers → recommend
                 ┌────────────┴────────────┐
        (要新题) │                         │ (不出题)
                 ▼                         ▼
             generate ─────────────────► verify → END

State 里除业务字段外保留 `trace`（每个节点的输入输出摘要，含 run_id）与 `errors`
（节点级异常），任何一步失败都不会中断整张图，失败原因会如实记录下来。
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from backend.app.services import skill_runtime
from backend.app.skills import implementations as impl


class DiagnosisState(TypedDict, total=False):
    """Graph State：显式声明每个节点读写的字段。"""
    attempt_id: str
    user_id: str
    want_questions: bool
    question_count: int
    use_model: bool
    run_id: str
    thread_id: str
    job_id: str
    diagnosis: dict[str, Any]
    layers: dict[str, Any]
    recommendations: dict[str, Any]
    generated: dict[str, Any]
    verification: dict[str, Any]
    trace: list[dict[str, Any]]
    errors: list[dict[str, str]]
    status: str


def _ctx(state: DiagnosisState) -> dict:
    return {"run_id": state.get("run_id", ""),
            "thread_id": state.get("thread_id", ""),
            "job_id": state.get("job_id", ""),
            "user_id": state.get("user_id", "local_user")}


def _user(state: DiagnosisState) -> str:
    return state.get("user_id") or "local_user"


def _step(state: DiagnosisState, node: str, summary: dict) -> list[dict]:
    return list(state.get("trace") or []) + [{"node": node, "summary": summary}]


def _skill_version(name: str) -> str:
    from backend.app.skills.registry import REGISTRY
    spec = REGISTRY.get(name)
    return spec.version if spec else "skills-1.0"


def build_graph(db: Session):
    """把 db 绑进闭包，节点函数保持纯函数式签名 (state) -> partial_state。"""

    def diagnose(state: DiagnosisState) -> dict:
        try:
            # 走 run_skill：既写 skill_runs，也由 skill_runtime 落 Diagnosis(level=paper)
            out = skill_runtime.run_skill(
                db, "diagnose_paper",
                {"attempt_id": state["attempt_id"],
                 "use_model": state.get("use_model", True)},
                thread_id=state.get("thread_id", ""),
                job_id=state.get("job_id", ""),
                user_id=_user(state))
            result = out.get("result") or {}
            return {"diagnosis": result,
                    "trace": _step(state, "diagnose_paper", {
                        "status": out.get("status"),
                        "run_id": out.get("run_id"),
                        "persisted_level": "paper",
                        "weak_points": len(result.get("weak_points") or []),
                        "covered": len(result.get("covered_knowledge_points") or [])})}
        except Exception as e:                                    # noqa: BLE001
            return {"diagnosis": {},
                    "errors": (state.get("errors") or []) + [
                        {"node": "diagnose_paper", "error": f"{type(e).__name__}: {e}"}],
                    "trace": _step(state, "diagnose_paper", {"status": "error"})}

    def three_layers(state: DiagnosisState) -> dict:
        try:
            layers = impl._three_layers(db, state["attempt_id"])
            tested = [l["key"] for l in layers["layers"] if l["accuracy"] is not None]
            # 把三层结果并回已落库的试卷层诊断，报告才拿得到 layers，
            # 而不是只存在于这次图的返回值里。
            diag = state.get("diagnosis") or {}
            if diag.get("attempt_id"):
                skill_runtime.upsert_diagnosis(
                    db, user_id=_user(state), level="paper",
                    target_id=state["attempt_id"],
                    payload={"three_layers": layers["layers"],
                             "three_layers_note": layers.get("note", "")},
                    skill_version=_skill_version("diagnose_paper"),
                    run_id=(state.get("run_id") or ""),
                    engine="deterministic", merge_payload=True)
            return {"layers": layers,
                    "trace": _step(state, "three_layers", {
                        "tested": tested,
                        "persisted": bool(diag.get("attempt_id"))})}
        except Exception as e:                                    # noqa: BLE001
            return {"layers": {"layers": []},
                    "errors": (state.get("errors") or []) + [
                        {"node": "three_layers", "error": f"{type(e).__name__}: {e}"}],
                    "trace": _step(state, "three_layers", {"status": "error"})}

    def recommend(state: DiagnosisState) -> dict:
        try:
            weak = (state.get("diagnosis") or {}).get("weak_points") or []
            out = skill_runtime.run_skill(
                db, "recommend_resources",
                {"attempt_id": state["attempt_id"], "kp_ids": weak[:6]},
                thread_id=state.get("thread_id", ""),
                job_id=state.get("job_id", ""),
                user_id=_user(state))
            result = out.get("result") or {}
            return {"recommendations": result,
                    "trace": _step(state, "recommend_resources", {
                        "status": out.get("status"),
                        "run_id": out.get("run_id"),
                        "count": len(result.get("recommendations") or [])})}
        except Exception as e:                                    # noqa: BLE001
            return {"recommendations": {},
                    "errors": (state.get("errors") or []) + [
                        {"node": "recommend_resources", "error": f"{type(e).__name__}: {e}"}],
                    "trace": _step(state, "recommend_resources", {"status": "error"})}

    def generate(state: DiagnosisState) -> dict:
        try:
            weak = (state.get("diagnosis") or {}).get("weak_points") or []
            if not weak:
                return {"generated": {"skipped": True, "reason": "没有薄弱知识点"},
                        "trace": _step(state, "generate_questions",
                                       {"status": "skipped", "reason": "no_weak_points"})}
            count = max(1, min(3, int(state.get("question_count") or 2)))
            out = skill_runtime.run_skill(
                db, "generate_questions",
                {"count": count, "kp_ids": weak[:3]},
                thread_id=state.get("thread_id", ""),
                job_id=state.get("job_id", ""),
                user_id=_user(state))
            result = out.get("result") or {}
            return {"generated": {"skipped": False,
                                  "status": out.get("status"),
                                  "run_id": out.get("run_id"),
                                  "candidates": len(result.get("candidates") or []),
                                  "message": result.get("message", "")},
                    "trace": _step(state, "generate_questions", {
                        "status": out.get("status"),
                        "candidates": len(result.get("candidates") or [])})}
        except Exception as e:                                    # noqa: BLE001
            return {"generated": {"skipped": False, "error": str(e)},
                    "errors": (state.get("errors") or []) + [
                        {"node": "generate_questions", "error": f"{type(e).__name__}: {e}"}],
                    "trace": _step(state, "generate_questions", {"status": "error"})}

    def skip_generate(state: DiagnosisState) -> dict:
        return {"generated": {"skipped": True,
                              "reason": "本次未开启新题生成"},
                "trace": _step(state, "generate_questions", {"status": "skipped"})}

    def verify(state: DiagnosisState) -> dict:
        try:
            out = skill_runtime.run_skill(
                db, "verify_learning", {"attempt_id": state["attempt_id"]},
                thread_id=state.get("thread_id", ""),
                job_id=state.get("job_id", ""),
                user_id=_user(state))
            return {"verification": out.get("result") or {},
                    "trace": _step(state, "verify_learning", {
                        "status": out.get("status"),
                        "run_id": out.get("run_id")})}
        except Exception as e:                                    # noqa: BLE001
            return {"verification": {},
                    "errors": (state.get("errors") or []) + [
                        {"node": "verify_learning", "error": f"{type(e).__name__}: {e}"}],
                    "trace": _step(state, "verify_learning", {"status": "error"})}


    def route_generate(state: DiagnosisState) -> str:
        return "generate" if state.get("want_questions") else "skip"

    graph = StateGraph(DiagnosisState)
    graph.add_node("diagnose", diagnose)
    graph.add_node("three_layers", three_layers)
    graph.add_node("recommend", recommend)
    graph.add_node("generate", generate)
    graph.add_node("skip_generate", skip_generate)
    graph.add_node("verify", verify)

    graph.add_edge(START, "diagnose")
    graph.add_edge("diagnose", "three_layers")
    graph.add_edge("three_layers", "recommend")
    graph.add_conditional_edges("recommend", route_generate, {
        "generate": "generate", "skip": "skip_generate"})
    graph.add_edge("generate", "verify")
    graph.add_edge("skip_generate", "verify")
    graph.add_edge("verify", END)
    return graph.compile()


def run_graph(db: Session, attempt_id: str, *, user_id: str = "local_user",
              want_questions: bool = False, question_count: int = 2,
              use_model: bool = True, run_id: str = "",
              thread_id: str = "") -> dict:
    """执行整张图，返回最终 state + 图结构（供前端展示执行轨迹）。"""
    app = build_graph(db)
    initial: DiagnosisState = {
        "attempt_id": attempt_id, "user_id": user_id,
        "want_questions": want_questions, "question_count": question_count,
        "use_model": use_model, "run_id": run_id, "thread_id": thread_id,
        "trace": [], "errors": [],
    }
    final = app.invoke(initial, config={"recursion_limit": 20})
    final["status"] = "succeeded" if not final.get("errors") else "partial"
    final["graph"] = {
        "engine": "langgraph",
        "nodes": ["diagnose_paper", "three_layers", "recommend_resources",
                  "generate_questions", "verify_learning"],
        "mermaid": app.get_graph().draw_mermaid(),
    }
    return final
