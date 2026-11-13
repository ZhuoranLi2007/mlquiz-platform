"""AI reference-answer pipeline (DeepSeek / OpenAI-compatible).

Purpose: course material and past exams mostly ship WITHOUT answers. Instead
of requiring a human to fill them in first, any question without a usable
answer enters a generation queue. A generated answer that passes a structural
contract check and an EXECUTABLE automatic check is published straight into
the usable bank -- no per-question human confirmation.

Hard invariants:
* The generation request contains ONLY stem / options / question type / score /
  a few material snippets. It NEVER contains a student response, attempt id,
  score, or anything from the `answers` table. A SHA256 request fingerprint and
  `student_answer_included=False` are persisted so this is auditable.
* A succeeded AnswerGeneration is the cache keyed by
  (question_id, question_version, answer_version). Paper assembly reuses it.
* Published answers are labelled "AI参考答案". `source`/model/generated_at/
  version are stored; the answer is NEVER described as a teacher or original
  exam answer.
* Only concrete exceptions (missing figure/formula, insufficient conditions,
  contradictory result) go to `needs_attention`. Everything else keeps flowing.
* Model not configured => `pending_model_configuration`, no answer published,
  NO mock substituted for a real call.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import model_provider, retrieval
from backend.app.skills import prompts

READY_STATUSES = {"verified", "ai_auto_checked"}
AI_ORIGINS = {"deepseek_generated", "ai_generated"}
GENERATED_ORIGIN = "deepseek_generated"
EXCEPTION_CODES = {
    "missing_figure_or_formula",
    "insufficient_conditions",
    "contradictory_result",
}
MAX_STRUCTURE_RETRIES = 2
REQUEST_INPUT_KEYS = {"question_type", "score", "stem", "options", "context"}
FORBIDDEN_IN_REQUEST = (
    "student_response", "response", "attempt_id", "answer_score",
    "awarded_score", "given", "student_answer",
)
ANSWER_DISCLAIMER = ("AI参考答案：由模型依据题干与课程资料生成并通过自动检查，"
                     "不是教师或原试卷提供的标准答案")

LABELS = {
    "deepseek_generated": "AI参考答案",
    "ai_generated": "AI参考答案",
    "ai_candidate": "AI候选答案（待核验）",
    "computed": "独立计算答案",
    "source": "原资料所附答案",
    "human_cross_check": "人工复核答案",
    "human_correction": "人工修正答案",
    "teacher": "教师提供答案",
    "ai_auto_repaired": "AI 自动修复并独立校验答案",
    "deepseek_v2_source_ai_validated": "原资料答案（AI 独立校验）",
    "deepseek_v2_ai_reconstructed_validated": "AI 复原并独立校验答案",
    "none": "暂无参考答案",
}


def answer_label(origin: str) -> str:
    return LABELS.get(origin or "none", "其他来源答案")


def is_answer_ready(q: Any) -> bool:
    """A question is usable when it has an answer payload and the answer has
    passed EITHER a human cross-check OR the automatic check pipeline.
    There is deliberately no requirement for per-question human sign-off.

    V3.4: 额外要求答案与选项结构自洽。一个 `{"key": "Z"}` 的答案即使带着
    `ai_auto_checked` 也不能算「可用答案」—— 否则组卷会把它当成有效题目，
    学生无论如何作答都被判错。
    """
    if not (bool(q.answer_payload) and q.answer_status in READY_STATUSES):
        return False
    return answer_structurally_valid(q)[0]


# Some items are imported but deliberately HELD BACK: the text layer of the
# original page lost part of the option text (e.g. a formula rendered as an
# image), so the item must not be scored until a human checks it. The importer
# marks them in `notes`. Sending them to the model anyway wastes calls AND can
# accidentally "launder" a broken item into a graded one, so every path that
# could publish or score must honour the mark.
NOT_PUBLISHABLE_MARK = re.compile(r"(不得发布|不可发布|不参与计分及发布|待人工核对后发布)")


def option_keys_of(question: Any) -> list[str]:
    """选项标识（A/B/C…），兼容 list[{key,text}] 与 dict{key: text} 两种历史形态。"""
    raw = question.options or []
    keys: list[str] = []
    if isinstance(raw, dict):
        iterable: Any = raw.keys()
    else:
        iterable = raw
    for item in iterable:
        if isinstance(item, dict):
            key = str(item.get("key") or item.get("label") or "").strip()
        else:
            key = str(item).strip()
        if key:
            keys.append(key)
    return keys


def answer_structurally_valid(question: Any) -> tuple[bool, str]:
    """参考答案与题目本身是否自洽（只看结构，不判断答案对不对）。

    这是「不允许把无法验证的题伪装成有效题目」的落地检查。真实库里出现过
    `answer_payload = {"key": "Z"}` 而选项只有 A–D 的记录：它带着
    `answer_status=ai_auto_checked`，能通过旧的 `is_answer_ready()`，
    组卷后学生怎么答都判错。结构不自洽的答案不能作为评分依据。
    """
    from backend.app.services.scoring import effective_type

    ans = question.answer_payload or {}
    qtype = effective_type({"question_type": getattr(question, "question_type", ""),
                            "answer_payload": ans})
    keys = option_keys_of(question)
    upper = {k.upper() for k in keys}

    if qtype == "single_choice":
        if len(keys) < 2:
            return False, f"选择题只有 {len(keys)} 个选项"
        given = str(ans.get("key") or "").strip().upper()
        if not given:
            return False, "缺少正确答案 key"
        if given not in upper:
            return False, f"答案 {given!r} 不在选项 {sorted(keys)} 中"
        return True, ""

    if qtype == "matching":
        given = str(ans.get("key") or "").strip().upper()
        if not given:
            return False, "连线子题缺少匹配答案 key"
        # Matching sub-items may use one shared material instead of duplicating
        # A-E options on every question.  The importer records that material in
        # source_refs/notes; only require a non-empty key here.
        return True, ""

    if qtype == "fill_blank":
        values = ans.get("values") or ans.get("accepted_values")
        if not isinstance(values, list) or not values:
            return False, "填空题缺少按空位组织的等价答案集合"
        for index, cell in enumerate(values):
            accepted = (cell.get("accepted") or cell.get("values") or []) \
                if isinstance(cell, dict) else cell
            if not isinstance(accepted, list) or not any(
                    str(v).strip() for v in accepted):
                return False, f"第 {index + 1} 个空位没有可接受答案"
        return True, ""

    if qtype == "multi_choice":
        if len(keys) < 2:
            return False, f"多选题只有 {len(keys)} 个选项"
        given = ans.get("keys")
        if not isinstance(given, list) or not given:
            return False, "缺少正确答案 keys"
        bad = [str(k).strip().upper() for k in given
               if str(k).strip().upper() not in upper]
        if bad:
            return False, f"答案 {bad} 不在选项 {sorted(keys)} 中"
        if len(set(str(k).strip().upper() for k in given)) < 2:
            return False, "多选题只有 1 个正确选项"
        return True, ""

    if qtype == "true_false":
        if not isinstance(ans.get("value"), bool):
            return False, "判断题答案不是布尔值"
        return True, ""

    if qtype == "numeric":
        parts = ans.get("parts")
        if isinstance(parts, list) and parts:
            for index, part in enumerate(parts):
                if not isinstance(part, dict):
                    return False, f"多值计算第 {index + 1} 项结构无效"
                try:
                    float(part.get("value"))
                    float(part.get("tol", part.get("tolerance", 0.0)) or 0.0)
                except (TypeError, ValueError):
                    return False, f"多值计算第 {index + 1} 项答案或容差不是数值"
            return True, ""
        try:
            float(ans.get("value"))
        except (TypeError, ValueError):
            return False, "计算题答案不是数值"
        return True, ""

    if qtype in ("term", "short_answer", "derivation", "comprehensive"):
        has_ref = bool(str(ans.get("reference_answer") or "").strip())
        has_points = bool(ans.get("key_points") or ans.get("points"))
        if not (has_ref or has_points):
            return False, "主观题缺少参考答案或评分要点"
        return True, ""

    # 未知形态不擅自判定为无效，交给上层按「待人工」处理。
    return True, ""


def is_publishable(q: Any) -> bool:
    """False when the item is intentionally held back from papers/scoring."""
    if (q.review_status or "") in ("blocked", "rejected"):
        return False
    return not NOT_PUBLISHABLE_MARK.search(q.notes or "")


def latest_question(db: Session, question_id: str) -> E.Question | None:
    return db.execute(
        select(E.Question).where(E.Question.question_id == question_id)
        .order_by(E.Question.question_version.desc())
    ).scalars().first()


def iter_answerless_questions(db: Session, *, include_inactive: bool = True
                              ) -> list[E.Question]:
    """Latest version of each question that still needs an AI reference answer."""
    rows = db.execute(select(E.Question)).scalars().all()
    latest: dict[str, E.Question] = {}
    for q in rows:
        cur = latest.get(q.question_id)
        if cur is None or q.question_version > cur.question_version:
            latest[q.question_id] = q
    out = []
    for q in latest.values():
        if not include_inactive and not q.active:
            continue
        if not q.active:
            continue
        if not is_publishable(q):
            # Deliberately held back items never enter the queue: an answer
            # would be unusable anyway and the call is wasted.
            continue
        if not is_answer_ready(q):
            out.append(q)
    return sorted(out, key=lambda x: (x.origin, x.question_id))


def count_held_back(db: Session) -> int:
    """Active questions deliberately kept out of the answer queue."""
    rows = db.execute(select(E.Question)).scalars().all()
    latest: dict[str, E.Question] = {}
    for q in rows:
        cur = latest.get(q.question_id)
        if cur is None or q.question_version > cur.question_version:
            latest[q.question_id] = q
    return sum(1 for q in latest.values()
               if q.active and not is_publishable(q) and not is_answer_ready(q))


def answer_meta_for(db: Session, question_id: str,
                    question_version: int) -> dict:
    """Provenance for a published answer, safe to expose in a student DTO."""
    gen = db.execute(
        select(E.AnswerGeneration).where(
            E.AnswerGeneration.question_id == question_id,
            E.AnswerGeneration.question_version == question_version,
            E.AnswerGeneration.status == "succeeded")
        .order_by(E.AnswerGeneration.answer_version.desc())
    ).scalars().first()
    if not gen:
        return {}
    # Public-safe check summary only. `gen.checks` contains executable-check
    # VALUES (e.g. the expected numeric answer), which must never reach a
    # student-facing DTO before submission.
    raw = gen.checks or {}
    ac = raw.get("auto_check") or {}
    sc = raw.get("structure_check") or {}
    safe_checks = {
        "structure_check_passed": bool(sc.get("passed", True)),
        "auto_check_passed": bool(ac.get("passed", True)),
        "check_names": [c.get("name", "") for c in (ac.get("checks") or [])
                        if isinstance(c, dict)],
    }
    return {
        "label": answer_label(gen.source if gen.source in AI_ORIGINS
                              else GENERATED_ORIGIN),
        "source": gen.source,
        "model_provider": gen.model_provider,
        "model": gen.model_name,
        "prompt_version": gen.prompt_version,
        "generated_at": gen.generated_at,
        "answer_version": gen.answer_version,
        "question_version": gen.question_version,
        "generation_id": gen.generation_id,
        "checks": safe_checks,
        "student_answer_included": gen.student_answer_included,
        "disclaimer": ANSWER_DISCLAIMER,
    }


def _context_snippets(db: Session, q: E.Question, limit: int = 3) -> str:
    """A few real material snippets only. Never a whole document."""
    try:
        hits = retrieval.keyword_search(
            db, (q.stem or "")[:120], limit=limit).get("results", [])[:limit]
    except Exception:
        return ""
    parts = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        text = (h.get("text") or h.get("content") or h.get("snippet") or "")
        cid = h.get("chunk_id", "")
        if text:
            parts.append(f"[{cid}] {str(text)[:420]}")
    return "\n".join(parts)


def build_request(db: Session, q: E.Question) -> tuple[list[dict], str, dict]:
    """Construct the model request and its audit fingerprint.

    Returns (messages, sha256_fingerprint, input_contract). The input contract
    has an explicit key whitelist; anything resembling a student response is
    rejected before a request can ever be sent.
    """
    opts = q.options or []
    if isinstance(opts, dict):
        opts = [{"key": k, "text": v} for k, v in opts.items()]
    options_text = "\n".join(
        f"{o.get('key', '')}. {o.get('text', '')}" for o in opts
        if isinstance(o, dict)) or "(无选项)"
    inp = {
        "question_type": q.question_type or "unknown",
        "score": float(q.score or 1.0),
        "stem": q.stem or "",
        "options": options_text,
        "context": _context_snippets(db, q),
    }
    # Whitelist + explicit forbidden-key scan. This makes "the answer request
    # must not contain a student answer" an enforced invariant, not a promise.
    assert set(inp) <= REQUEST_INPUT_KEYS, "答案生成请求含未登记字段"
    serial = json.dumps(inp, ensure_ascii=False, sort_keys=True)
    for bad in FORBIDDEN_IN_REQUEST:
        assert f'"{bad}"' not in serial, f"答案生成请求禁止包含 {bad}"

    messages = [
        {"role": "system", "content": prompts.ANSWER_SOLVE_SYSTEM},
        {"role": "user", "content": prompts.ANSWER_SOLVE_USER.format(
            question_type=inp["question_type"], score=inp["score"],
            stem=inp["stem"], options=inp["options"],
            context=inp["context"] or "(本次未检索到相关资料片段)")},
    ]
    fingerprint = hashlib.sha256(
        json.dumps({"messages": messages, "input_contract": inp},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return messages, fingerprint, inp


# ------------------------------------------------------------------ checks
def structure_check(q: E.Question, out: Any) -> dict:
    """Basic structural contract. Returns {passed, errors, normalized}."""
    errors: list[str] = []
    if not isinstance(out, dict):
        return {"passed": False, "errors": ["模型输出不是 JSON 对象"],
                "normalized": None}
    norm = dict(out)
    # Source is decided by OUR system, never by the model claiming authority.
    norm["source"] = "ai_generated"
    ap, rub, expl = norm.get("answer_payload"), norm.get("rubric"), norm.get("explanation")
    if not isinstance(ap, dict) or not ap:
        errors.append("answer_payload 缺失或不是非空对象")
    if not isinstance(rub, dict):
        errors.append("rubric 缺失或不是对象")
    if not isinstance(expl, str) or len(expl.strip()) < 8:
        errors.append("explanation 缺失或过短（需给出推导链条）")

    qtype = q.question_type or "unknown"
    opts = {str(o.get("key", "")).strip().upper()
            for o in (q.options or []) if isinstance(o, dict)}
    if qtype in ("", "unknown", "other"):
        # The stored label can be missing while the item is plainly objective
        # (options + a single key). Judging it by the "subjective" branch made
        # 12 correct single-choice answers fail as `malformed_output` — a
        # checker false negative, not a model defect. Infer from the evidence.
        if isinstance(ap, dict) and isinstance(ap.get("keys"), list) \
                and any(str(k).strip() for k in ap["keys"]):
            qtype = "multi_choice"
        elif isinstance(ap, dict) and isinstance(ap.get("value"), bool):
            qtype = "true_false"
        elif isinstance(ap, dict) and str(ap.get("key") or "").strip():
            qtype = "single_choice"
        elif opts:
            qtype = "single_choice"
        else:
            qtype = "short_answer"
    if isinstance(ap, dict):
        if qtype == "single_choice":
            key = str(ap.get("key", "")).strip().upper()
            if not key or (opts and key not in opts):
                errors.append(f"单选答案 key={key!r} 不在选项 {sorted(opts)} 中")
        elif qtype == "multi_choice":
            keys = [str(k).strip().upper() for k in (ap.get("keys") or [])]
            if not keys or len(set(keys)) != len(keys):
                errors.append("多选答案 keys 为空或有重复")
            if opts and not set(keys) <= opts:
                errors.append("多选答案含不存在的选项")
        elif qtype == "true_false":
            if not isinstance(ap.get("value"), bool):
                errors.append("判断题答案 value 必须是布尔值")
        elif qtype == "numeric":
            try:
                float(ap.get("value"))
            except (TypeError, ValueError):
                errors.append("数值题答案 value 不是可解析数值")
        elif qtype == "matching":
            if not str(ap.get("key") or "").strip():
                errors.append("连线子题答案 key 为空")
        elif qtype == "fill_blank":
            values = ap.get("values") or ap.get("accepted_values")
            if not isinstance(values, list) or not values:
                errors.append("填空题答案缺少 values")
        else:  # term / short_answer / derivation / comprehensive / unknown
            if not str(ap.get("reference_answer", "")).strip():
                errors.append("主观题缺少 reference_answer")
            if not (ap.get("key_points") or []):
                errors.append("主观题缺少 key_points")

    if isinstance(rub, dict):
        pts = rub.get("points")
        if not isinstance(pts, list) or not pts:
            errors.append("rubric.points 必须是非空数组")
        else:
            for i, p in enumerate(pts):
                if not isinstance(p, dict):
                    errors.append(f"rubric.points[{i}] 不是对象")
                    continue
                if not str(p.get("text", "")).strip():
                    errors.append(f"rubric.points[{i}].text 为空")
                try:
                    float(p.get("score"))
                except (TypeError, ValueError):
                    errors.append(f"rubric.points[{i}].score 不是数值")
                if not p.get("accept"):
                    errors.append(
                        f"rubric.points[{i}].accept 为空：必须给出等价表达/其他解法")
    return {"passed": not errors, "errors": errors, "normalized": norm}


_ALLOWED_AST = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult,
    ast.Div, ast.Pow, ast.Mod, ast.FloorDiv, ast.USub, ast.UAdd,
    ast.Constant, ast.Call, ast.Name, ast.Load, ast.List, ast.Tuple,
    ast.GeneratorExp, ast.ListComp, ast.comprehension, ast.Store, ast.cmpop,
)
_ALLOWED_FUNCS = {"sum": sum, "round": round, "abs": abs, "min": min,
                  "max": max, "len": len, "pow": pow}


def safe_eval(expr: str) -> float:
    """Executable check: evaluate an independent arithmetic expression with a
    locked-down AST. No attributes, imports, strings or builtins."""
    if not isinstance(expr, str) or not expr.strip() or len(expr) > 600:
        raise ValueError("计算式为空或过长")
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_AST):
            raise ValueError(f"计算式含不允许的语法: {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("计算式只允许数字常量")
        if isinstance(node, ast.Call) and (
                not isinstance(node.func, ast.Name) or
                node.func.id not in _ALLOWED_FUNCS):
            raise ValueError("计算式只允许白名单函数")
    glob = {"__builtins__": {}}
    glob.update(_ALLOWED_FUNCS)
    value = eval(compile(tree, "<independent_check>", "eval"), glob, {})
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("计算结果不是数值")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ValueError("计算结果为 NaN/Inf")
    return float(value)


def auto_check(q: E.Question, out: dict) -> dict:
    """Executable automatic checks. Returns {passed, checks, exception_code,
    exception_detail}. A failure is a CONCRETE exception, not 'no teacher
    answer available'."""
    checks: list[dict] = []
    ap = out.get("answer_payload") or {}
    rub = out.get("rubric") or {}
    qtype = q.question_type or "unknown"
    score = float(q.score or 1.0)

    # 1. Independent arithmetic recomputation.
    #    Only meaningful when the payload actually carries a numeric answer: a
    #    choice item's payload is {"key": "B"}, and forcing the numeric check on
    #    it would fail for a reason that has nothing to do with the answer
    #    (observed as a bogus `contradictory_result` on a correct option).
    calc = (ap.get("calculation") or (out.get("self_check") or {}).get("calculation")
            or rub.get("calculation"))
    raw_value = ap.get("value")
    has_value = isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool)
    if calc and has_value:
        try:
            computed = safe_eval(calc)
            expected = float(raw_value)
            tol = max(float(ap.get("tol", 0) or 0), 1e-6)
            ok = abs(computed - expected) <= tol
            checks.append({"name": "independent_calculation", "passed": ok,
                           "expression": calc, "computed": computed,
                           "answer_value": expected, "tol": tol})
            if not ok:
                return {"passed": False, "checks": checks,
                        "exception_code": "contradictory_result",
                        "exception_detail": f"独立计算得 {computed}，与答案 {expected} 不符"}
        except Exception as e:                     # noqa: BLE001
            # NameError / ZeroDivisionError / ValueError all mean "this self
            # check cannot be executed" -- NOT that the answer is wrong. Calling
            # it `contradictory_result` would be a false accusation, and letting
            # it escape used to leave the generation stuck in `running`.
            checks.append({"name": "independent_calculation", "passed": False,
                           "expression": calc,
                           "error": f"{type(e).__name__}: {e}"})
            return {"passed": False, "checks": checks,
                    "exception_code": "calculation_unevaluable",
                    "exception_detail": (
                        f"自校验计算式无法独立求值（{type(e).__name__}: {e}）："
                        "计算式须为纯数字/白名单函数的算术表达式；请人工核对本题")}
    elif calc and not has_value:
        # Transparency, not a failure: nothing numeric to recompute.
        checks.append({"name": "independent_calculation", "passed": True,
                       "skipped": True, "expression": calc,
                       "note": "答案不是数值（如选项 key），跳过数值复算"})
    elif qtype == "numeric":
        checks.append({"name": "independent_calculation", "passed": False,
                       "note": "数值题未提供可独立执行的计算式"})
        return {"passed": False, "checks": checks,
                "exception_code": "contradictory_result",
                "exception_detail": "数值答案缺少可执行自校验，无法确认结果"}

    # 2. Rubric point totals must equal question score.
    pts = rub.get("points") or []
    if pts:
        try:
            total = sum(float(p.get("score", 0)) for p in pts)
        except (TypeError, ValueError):
            total = -1
        ok = abs(total - score) <= 1e-6
        checks.append({"name": "rubric_total", "passed": ok,
                       "points_total": total, "question_score": score})
        if not ok:
            return {"passed": False, "checks": checks,
                    "exception_code": "contradictory_result",
                    "exception_detail": f"评分要点合计 {total} 与题目分值 {score} 不符"}
        no_accept = [p.get("id", i) for i, p in enumerate(pts)
                     if not p.get("accept")]
        checks.append({"name": "equivalent_expression_accepted",
                       "passed": not no_accept, "points_without_accept": no_accept})
        if no_accept:
            return {"passed": False, "checks": checks,
                    "exception_code": "contradictory_result",
                    "exception_detail": "评分要点未声明等价表达/其他解法，无法公平评分"}

    # 3. Explanation must not contradict a single-choice key.
    if qtype == "single_choice" and ap.get("key"):
        m = re.search(r"(?:应选|答案为|正确选项(?:是|为)?)\s*[:：]?\s*([A-F])",
                      str(out.get("explanation", "")))
        if m and m.group(1).upper() != str(ap["key"]).strip().upper():
            checks.append({"name": "explanation_consistency", "passed": False,
                           "key": ap["key"], "mentioned": m.group(1)})
            return {"passed": False, "checks": checks,
                    "exception_code": "contradictory_result",
                    "exception_detail": "解析中给出的选项与答案 key 矛盾"}
        checks.append({"name": "explanation_consistency", "passed": True})

    # 4. Model-declared concrete exceptions.
    if out.get("insufficient"):
        code = str(out.get("reason_code") or "insufficient_conditions")
        if code not in EXCEPTION_CODES:
            code = "insufficient_conditions"
        checks.append({"name": "model_sufficiency", "passed": False,
                       "reason_code": code})
        return {"passed": False, "checks": checks, "exception_code": code,
                "exception_detail": str(out.get("reason_note")
                                        or "模型判定该题信息不足，已列入具体异常")}

    checks.append({"name": "all_checks", "passed": True})
    return {"passed": True, "checks": checks, "exception_code": "",
            "exception_detail": ""}


# ------------------------------------------------------------------ queue
def enqueue(db: Session, q: E.Question, trigger: str = "import_batch",
            *, force: bool = False) -> dict:
    """Create (or reuse) the generation record for this exact question version."""
    existing = db.execute(
        select(E.AnswerGeneration).where(
            E.AnswerGeneration.question_id == q.question_id,
            E.AnswerGeneration.question_version == q.question_version)
        .order_by(E.AnswerGeneration.answer_version.desc())
    ).scalars().first()
    if existing and existing.status == "succeeded" and not force:
        return {"generation_id": existing.generation_id, "status": "succeeded",
                "cached": True, "reused": True}
    if existing and existing.status in ("queued", "running",
                                        "pending_model_configuration") and not force:
        return {"generation_id": existing.generation_id,
                "status": existing.status, "cached": False, "reused": True}
    ver = (existing.answer_version + 1) if existing else 1
    gen = E.AnswerGeneration(
        generation_id=E.new_id("gen"), question_id=q.question_id,
        question_version=q.question_version, answer_version=ver,
        trigger=trigger, status="queued", source="deepseek",
        model_provider=cfg.MODEL_PROVIDER, model_name=cfg.MODEL_CHAT_MODEL,
        prompt_version=prompts.ANSWER_PROMPT_VERSION,
        student_answer_included=False,
    )
    db.add(gen)
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id="local_user",
        event_type="answer_generation_queued", target_id=q.question_id,
        payload={"generation_id": gen.generation_id, "trigger": trigger,
                 "question_version": q.question_version,
                 "answer_version": ver}))
    db.commit()
    return {"generation_id": gen.generation_id, "status": "queued",
            "cached": False, "reused": False, "answer_version": ver}


def publish_answer(db: Session, q: E.Question, gen: E.AnswerGeneration,
                   out: dict, checks: dict) -> dict:
    """Publish a checked answer into the usable bank. NO human sign-off needed.

    Does not create a new question version: the succeeded generation IS the
    answer cache for this exact version. A corrected answer (dispute) creates a
    new question version elsewhere, so published papers keep their snapshot.
    """
    others = db.execute(
        select(E.Question).where(
            E.Question.question_id == q.question_id,
            E.Question.active.is_(True),
            E.Question.row_id != q.row_id)
    ).scalars().all()
    for o in others:
        o.active = False
    q.answer_payload = out["answer_payload"]
    q.rubric = out["rubric"]
    q.explanation = out.get("explanation", "")
    q.answer_status = "ai_auto_checked"
    q.review_status = "auto_checked"
    q.answer_origin = GENERATED_ORIGIN
    q.active = True
    # 发布答案时只替换「答案元数据」那一段，不能整段覆盖 notes：外部导入题、
    # 上传试卷题的来源说明（例如「题干为短摘录，完整题目见来源链接」）也放在
    # notes 里，曾被这里冲掉，导致来源标注丢失（V2.1 回归项实测发现）。
    meta = (f"{ANSWER_DISCLAIMER}；generation_id={gen.generation_id}；"
            f"answer_version={gen.answer_version}；自动检查通过")
    kept = "\n".join(
        ln for ln in (q.notes or "").splitlines()
        if ln.strip() and ANSWER_DISCLAIMER not in ln
        and "generation_id=" not in ln).strip()
    q.notes = meta + (f"\n{kept}" if kept else "")
    gen.status = "succeeded"
    gen.result = out
    gen.checks = checks
    gen.generated_at = E.utcnow()
    gen.exception_code = ""
    gen.exception_detail = ""
    gen.updated_at = E.utcnow()
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id="local_user",
        event_type="ai_answer_published", target_id=q.question_id,
        payload={"generation_id": gen.generation_id,
                 "answer_version": gen.answer_version,
                 "model": gen.model_name, "label": "AI参考答案",
                 "checks": checks, "human_confirmation_required": False}))
    db.commit()
    return {"question_id": q.question_id, "question_version": q.question_version,
            "answer_version": gen.answer_version, "label": "AI参考答案",
            "human_confirmation_required": False}


# Phrases that mean "this item is answered BY LOOKING AT a figure/table". The
# answer request only carries text, so such an item cannot be verified — and a
# model that answers anyway is guessing (observed: a contour-plot question where
# the model picked an option and then described the same point as both the
# maximum and the valley floor).
FIGURE_CUE = re.compile(
    r"(等高线图|三维图像|三维图|示意图|折线图|散点图|柱状图|直方图|"
    r"如下图|下图|上图|见图|如图|图中的|图中|如下表|下表)")


def _precheck(q: E.Question) -> tuple[str, str]:
    """Deterministic blockers checked BEFORE spending a model call."""
    stem = (q.stem or "").strip()
    if not stem:
        return "", ""
    if re.search(r"[=＝]\s*[.。；;]\s*(\S|$)", stem):
        return ("incomplete_stem",
                "题干中的公式/向量在解析时被截断（如『v = .』），"
                "原卷该处为图片，文本层缺失；需核对原卷后手工补全或改用带图输入")
    cue = FIGURE_CUE.search(stem)
    if cue:
        return ("missing_figure_or_formula",
                f"题干要求依据『{cue.group(0)}』作答，而解答请求只有文字、没有图像；"
                "模型无法真正看到该图，作答即为猜测。请对照原卷人工补录答案，"
                "或接入可读图的模型后重新生成")
    return "", ""


def withdraw_answer(db: Session, q: E.Question, *, reason: str,
                    status: str = "unknown") -> dict:
    """Un-publish an answer that later proved unreliable.

    Needed because an answer can pass the structural checks and still be wrong
    (real case: a question whose options were answered by guessing the missing
    figure's content). Leaving it published would feed a fabricated answer into
    the booklet, the scoring rubric and the student view, so it is retracted
    explicitly and the reason is kept on the question.
    """
    prev = {"answer_status": q.answer_status, "answer_origin": q.answer_origin,
            "had_payload": bool(q.answer_payload)}
    q.answer_payload = {}
    q.rubric = {}
    q.explanation = ""
    q.answer_status = status
    q.answer_origin = "none"
    stamp = E.utcnow()
    q.notes = f"{(q.notes or '').rstrip()}\n[withdraw {stamp}] {reason}".strip()
    # The succeeded generation must be invalidated too: otherwise the next
    # `ensure_answer_ready()` finds it, treats the answer as a valid cache hit
    # and re-publishes exactly what was just retracted (observed for real).
    invalidated = []
    for g in db.execute(select(E.AnswerGeneration).where(
            E.AnswerGeneration.question_id == q.question_id,
            E.AnswerGeneration.question_version == q.question_version,
            E.AnswerGeneration.status == "succeeded")).scalars().all():
        g.status = "invalidated"
        g.exception_code = "answer_withdrawn"
        g.exception_detail = reason[:500]
        g.updated_at = stamp
        invalidated.append(g.generation_id)
    db.commit()
    return {"question_id": q.question_id, "withdrawn": True, "previous": prev,
            "invalidated_generations": invalidated,
            "reason": reason, "status": status}


def enqueue_background(db: Session, gen: E.AnswerGeneration | None,
                       trigger: str = "",
                       exclude_job_id: str = "") -> dict | None:
    """Give a `queued` generation record a worker job, idempotently.

    Why this exists: a generation row on its own is INERT. The worker only ever
    picks up `jobs` rows, so writing `AnswerGeneration(status="queued")` and
    returning is a silent dead end -- the answer never appears and the question
    sits in the queue forever (observed: 20 rows `queued`, `running 0`, model
    call count frozen for six minutes).

    Idempotency: at most one live (`queued`/`running`) job per generation; a
    retry round gets its own key (`...:r{retry_count}`) because the previous
    job is already `succeeded` and would otherwise be reused, leaving the new
    retry round unpicked again.

    `exclude_job_id` is the job currently executing this generation. It must be
    ignored: while the retry is being decided that job is still `running`, so a
    naive liveness check would "find" it, create nothing, and the row would be
    orphaned the moment the worker marks the job succeeded (observed once).
    """
    if gen is None or gen.status != "queued":
        return None
    from backend.app.workers.worker import create_job   # local: avoid import cycle
    gid = gen.generation_id
    live = db.execute(select(E.Job).where(
        E.Job.job_type == "answer_generation",
        E.Job.status.in_(("queued", "running")),
        E.Job.job_id != exclude_job_id,
        E.Job.idempotency_key.like(f"ansgen:{gid}%"))).scalars().first()
    if live is not None:
        return {"job_id": live.job_id, "status": live.status, "reused": True}
    return create_job(db, "answer_generation",
                      {"generation_id": gid,
                       "trigger": trigger or gen.trigger or "on_demand"},
                      "local_user",
                      idempotency_key=f"ansgen:{gid}:r{gen.retry_count}")


def generate(db: Session, generation_id: str, run_id: str = "",
             chat_timeout: float | None = None) -> dict:
    """Run one generation and never leave the row stuck in `running`.

    `_generate_once` can raise for reasons that are not model errors (a bug in a
    checker, an unexpected payload shape). Without this guard the row stays
    `running` forever and the question silently never gets an answer, so any
    unexpected failure is converted into an explicit, auditable status.
    """
    try:
        return _generate_once(db, generation_id, run_id,
                              chat_timeout=chat_timeout)
    except Exception as e:                          # noqa: BLE001
        db.rollback()
        gen = db.get(E.AnswerGeneration, generation_id)
        if gen is not None and gen.status in ("queued", "running"):
            gen.status = "needs_attention"
            gen.exception_code = "unexpected_error"
            gen.exception_detail = f"{type(e).__name__}: {e}"[:500]
            gen.updated_at = E.utcnow()
            db.commit()
            return {"status": "needs_attention", "generation_id": generation_id,
                    "exception_code": "unexpected_error",
                    "exception_detail": gen.exception_detail,
                    "note": "内部异常已记录；该题保留待处理，其余题目继续"}
        raise


def _generate_once(db: Session, generation_id: str, run_id: str = "",
                   chat_timeout: float | None = None) -> dict:
    """Run one generation. Never fabricates an answer when the model is absent."""
    gen = db.get(E.AnswerGeneration, generation_id)
    if not gen:
        raise KeyError(generation_id)
    if gen.status == "succeeded":
        return {"status": "succeeded", "cached": True,
                "generation_id": generation_id,
                "result": {"note": "同一题目版本已有成功结果，直接复用缓存，未重新调用模型"}}
    q = db.execute(select(E.Question).where(
        E.Question.question_id == gen.question_id,
        E.Question.question_version == gen.question_version)).scalars().first()
    if not q:
        gen.status, gen.exception_code = "failed", "question_version_missing"
        db.commit()
        return {"status": "failed", "exception_code": "question_version_missing"}

    code, detail = _precheck(q)
    if code:
        gen.status = "needs_attention"
        gen.exception_code = code
        gen.exception_detail = detail
        gen.updated_at = E.utcnow()
        db.commit()
        return {"status": "needs_attention", "generation_id": generation_id,
                "exception_code": code, "exception_detail": detail,
                "note": "未调用模型：题干不完整或依赖输入中没有的图/表，"
                        "此时作答只能是编造"}

    if not is_publishable(q):
        # Imported but intentionally held back (e.g. an option's formula was
        # lost by the text layer). A generated answer could not be used for
        # scoring, so do not spend a call on it.
        gen.status = "skipped"
        gen.exception_code = "not_publishable"
        gen.exception_detail = (
            "该题被导入流程标记为不可发布（如选项中的公式在文本层缺失，"
            "需对照原页人工补录）；已跳过生成，未调用模型")
        gen.updated_at = E.utcnow()
        db.commit()
        return {"status": "skipped", "generation_id": generation_id,
                "exception_code": "not_publishable",
                "note": "未调用模型：题目源码即标记不可发布"}

    messages, fingerprint, contract = build_request(db, q)
    gen.status = "running"
    gen.run_id = run_id or gen.run_id
    gen.request_fingerprint = fingerprint
    gen.student_answer_included = False
    gen.model_provider = cfg.MODEL_PROVIDER
    gen.model_name = cfg.MODEL_CHAT_MODEL
    gen.prompt_version = prompts.ANSWER_PROMPT_VERSION
    gen.updated_at = E.utcnow()
    db.commit()

    st = model_provider.chat_status()
    if not st["configured"]:
        gen.status = "pending_model_configuration"
        gen.exception_code = "model_not_configured"
        gen.exception_detail = st["message"]
        gen.updated_at = E.utcnow()
        db.commit()
        return {"status": "model_not_configured", "generation_id": generation_id,
                "message": st["message"],
                "note": "未生成答案、未写入题库、未使用模拟结果冒充真实调用"}

    try:
        # Long derivations / comprehensive answers need more room than a short
        # option key; a truncated JSON is indistinguishable from a malformed
        # one downstream, so the budget escalates on retry.
        budget = 3000 if gen.retry_count == 0 else 4400
        res = model_provider.chat(
            db, messages=messages, skill_name="solve_answer",
            prompt_version=prompts.ANSWER_PROMPT_VERSION, run_id=gen.run_id,
            temperature=0.15, max_tokens=budget,
            timeout=float(chat_timeout or 120.0))
    except model_provider.ModelUnavailable as e:
        # Transient transport problems are NOT a verdict on the question: the
        # background queue should retry them. Only a persistent failure (after
        # retries) is recorded as `failed`.
        transient = e.status in ("timeout", "network_error", "rate_limited",
                                 "provider_error", "call_failed")
        if transient and gen.retry_count < 2:
            gen.retry_count += 1
            gen.status = "queued"
            gen.exception_code = f"{e.status}_retry"
            gen.exception_detail = e.message
            gen.updated_at = E.utcnow()
            db.commit()
            return {"status": "retrying", "generation_id": generation_id,
                    "retry_count": gen.retry_count,
                    "exception_code": e.status, "message": e.message}
        gen.status = "failed"
        gen.exception_code = e.status
        gen.exception_detail = e.message
        gen.updated_at = E.utcnow()
        db.commit()
        return {"status": "failed", "exception_code": e.status,
                "message": e.message, "generation_id": generation_id}
    gen.call_id = res.get("call_id", "")
    finish_reason = res.get("finish_reason", "")
    if finish_reason == "length":
        # Concrete, diagnosable defect: the model was cut off mid-output.
        gen.checks = {"model": res.get("model", ""),
                      "finish_reason": finish_reason,
                      "usage": res.get("usage", {})}
        if gen.retry_count < 1:
            gen.retry_count += 1
            gen.status = "queued"
            gen.exception_code = "output_truncated_retry"
            gen.exception_detail = (
                f"输出被 max_tokens={budget} 截断（finish_reason=length），"
                "已用更大预算重试一次")
            gen.updated_at = E.utcnow()
            db.commit()
            return {"status": "retrying", "generation_id": generation_id,
                    "retry_count": gen.retry_count,
                    "reason": "output_truncated"}
        gen.status = "needs_attention"
        gen.exception_code = "output_truncated"
        gen.exception_detail = (
            f"输出仍被截断（max_tokens={budget}）：该题解析过长，"
            "需要更短的分步表述或人工拆分")
        gen.updated_at = E.utcnow()
        db.commit()
        return {"status": "needs_attention", "generation_id": generation_id,
                "exception_code": "output_truncated",
                "exception_detail": gen.exception_detail}
    out = model_provider.parse_json_content(res.get("content", ""))
    sc = structure_check(q, out)
    if not sc["passed"]:
        gen.checks = {"structure_check": sc}
        if gen.retry_count < MAX_STRUCTURE_RETRIES:
            gen.retry_count += 1
            gen.status = "queued"
            gen.exception_code = "malformed_output_retry"
            gen.exception_detail = "; ".join(sc["errors"])[:500]
            gen.updated_at = E.utcnow()
            db.commit()
            return {"status": "retrying", "generation_id": generation_id,
                    "retry_count": gen.retry_count, "errors": sc["errors"]}
        gen.status = "needs_attention"
        gen.exception_code = "malformed_output"
        gen.exception_detail = "; ".join(sc["errors"])[:500]
        gen.updated_at = E.utcnow()
        db.commit()
        return {"status": "needs_attention", "generation_id": generation_id,
                "exception_code": "malformed_output", "errors": sc["errors"]}

    norm = sc["normalized"]
    ac = auto_check(q, norm)
    checks = {"structure_check": {"passed": True, "errors": []},
              "auto_check": ac, "request_fingerprint": fingerprint,
              "input_contract_keys": sorted(contract),
              # provenance actually observed from the provider, not config
              "model": res.get("model", ""),
              "finish_reason": finish_reason,
              "usage": res.get("usage", {}),
              "duration_ms": res.get("duration_ms")}
    if gen.model_name != res.get("model"):
        gen.model_name = res.get("model") or gen.model_name
    if not ac["passed"]:
        gen.status = "needs_attention"
        gen.exception_code = ac["exception_code"]
        gen.exception_detail = ac["exception_detail"]
        gen.checks = checks
        gen.result = norm
        gen.updated_at = E.utcnow()
        db.commit()
        return {"status": "needs_attention", "generation_id": generation_id,
                "exception_code": ac["exception_code"],
                "exception_detail": ac["exception_detail"],
                "note": "仅具体异常进入待处理；其他题目继续自动处理"}

    published = publish_answer(db, q, gen, norm, checks)
    return {"status": "succeeded", "generation_id": generation_id,
            "published": published, "checks": checks, "cached": False}


def ensure_answer_ready(db: Session, q: E.Question, *,
                        trigger: str = "on_demand",
                        allow_sync: bool = True,
                        sync_timeout_s: float | None = None) -> dict:
    """Called BEFORE a question is put into a paper (i.e. before any attempt
    exists). Guarantees requirement 4: the formal quiz fixes the reference
    answer and rubric before the student starts, and reuses the version cache.

    `sync_timeout_s` bounds the synchronous model call so an interactive request
    (paper assembly) cannot hang behind a slow generation; on expiry the item is
    re-queued instead of failing.
    """
    if is_answer_ready(q):
        return {"ready": True, "cached": True, "source": q.answer_origin,
                "answer_status": q.answer_status}
    info = enqueue(db, q, trigger)
    gen = db.get(E.AnswerGeneration, info["generation_id"])
    if gen and gen.status == "succeeded":
        # Succeeded cache but question not yet published -> apply it, no model call.
        if gen.result:
            publish_answer(db, q, gen, gen.result, gen.checks or {})
            return {"ready": True, "cached": True,
                    "source": GENERATED_ORIGIN,
                    "generation_id": gen.generation_id}
    if allow_sync and model_provider.chat_status()["configured"]:
        r = generate(db, info["generation_id"], run_id=gen.run_id if gen else "",
                     chat_timeout=sync_timeout_s)
        if r.get("status") == "succeeded":
            return {"ready": True, "cached": r.get("cached", False),
                    "generation": r, "generation_id": info["generation_id"]}
        # A retry round (truncated / malformed / transient) leaves the row
        # `queued`; without a job nobody would ever retry it.
        job = enqueue_background(db, db.get(E.AnswerGeneration,
                                            info["generation_id"]), trigger)
        return {"ready": False, "cached": False, "generation": r,
                "generation_id": info["generation_id"],
                "status": r.get("status", ""),
                "job": job,
                "queued_for_background": bool(job and not job.get("reused"))}
    gen = db.get(E.AnswerGeneration, info["generation_id"])
    status = gen.status if gen else "queued"
    configured = model_provider.chat_status()["configured"]
    if status == "queued" and not configured:
        gen.status = "pending_model_configuration"
        gen.exception_code = "model_not_configured"
        gen.exception_detail = "模型未配置；题目保留在解答队列，未生成答案"
        db.commit()
        status = "pending_model_configuration"
    job = enqueue_background(db, gen, trigger) if configured else None
    return {"ready": False, "cached": False, "status": status,
            "generation_id": info["generation_id"],
            "job": job,
            "queued_for_background": bool(job and not job.get("reused")),
            "message": "参考答案尚未就绪；本题本次不进入试卷，配置模型后由队列自动补齐"}


def generation_summary(db: Session) -> dict:
    rows = db.execute(select(E.AnswerGeneration)).scalars().all()
    from collections import Counter
    by_status = Counter(r.status for r in rows)
    by_exception = Counter(r.exception_code for r in rows if r.exception_code)
    return {"total": len(rows), "by_status": dict(by_status),
            "by_exception_code": dict(by_exception),
            "answerless_questions": len(iter_answerless_questions(db)),
            "held_back_questions": count_held_back(db),
            "model_configured": model_provider.chat_status()["configured"],
            "label_policy": ANSWER_DISCLAIMER}


def skill_solve_answer(db: Session, payload: dict, *, run_id: str = "",
                       thread_id: str = "", job_id: str = "",
                       user_id: str = "local_user") -> dict:
    """Skill-compatible wrapper so generation runs are traced like other skills."""
    gid = payload.get("generation_id", "")
    if not gid:
        qid = payload.get("question_id", "")
        q = latest_question(db, qid) if qid else None
        if not q:
            return {"result": {"error": "question_not_found"},
                    "status": "failed", "engine": "deterministic"}
        gid = enqueue(db, q, payload.get("trigger", "import_batch"))["generation_id"]
    r = generate(db, gid, run_id=run_id)
    st = r.get("status", "failed")
    # A retry round leaves the row `queued`; the job that ran us is about to be
    # marked succeeded, so queue the next round explicitly or the retry never
    # happens (this is exactly how 20 rows sat `queued` with no job behind them).
    follow = None
    if st == "retrying":
        follow = enqueue_background(db, db.get(E.AnswerGeneration, gid),
                                    payload.get("trigger", "import_batch"),
                                    exclude_job_id=job_id)
    return {
        "result": {**r, "follow_up_job": follow},
        "evidence_refs": [{"generation_id": gid}],
        "uncertainties": (["模型未配置，答案未生成"]
                          if st == "model_not_configured" else []),
        "next_actions": (["配置 .env 中的 DeepSeek API 后由队列自动补齐"]
                         if st == "model_not_configured" else []),
        "status": st,
        "engine": "model" if st == "succeeded" else "deterministic",
        "model": cfg.MODEL_CHAT_MODEL if st == "succeeded" else "",
    }
