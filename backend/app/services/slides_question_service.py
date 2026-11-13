"""老师课件导入后的后台静默出题（V2.2 需求 4）。

用户原话：「如果我们导入了老师的课件，deepseek 也应该自动识别出来有哪些题目，
当然课件上的题目并不多，但是我希望 deepseek 能根据内容自动形成相应考点的题目，
并且加到题库中去，这个过程系统后台自己做就好，不用告知用户去做这个事情。」

实现约定：
  * 一个课件只调用一次模型（限 6 题），用幂等字段保证重试不重复花钱。
  * 模型输出必须给出真实页码、真实考点 id、可核对的答案，代码逐项校验。
  * 沿用 V2.1 的 `judge_generated` 做课程范围复检，疑似超纲的题不发布。
  * 题目**直接发布到题库**（用户明确要求），但在 notes / answer_origin 里
    如实标注「答案由模型生成、未经独立核验」，不冒充人工核验过的题。
  * 整个过程不写 UI 通知、不要求用户点击。
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import model_provider, scope_service

PROMPT_VERSION = "slides-questions-1.0"
MAX_QUESTIONS = 6
VALID_TYPES = {"single_choice", "multi_choice", "true_false", "term",
               "short_answer", "numeric"}
OBJECTIVE_TYPES = {"single_choice", "multi_choice", "true_false"}

_SYS = (
    "你是机器学习课程助教，正在为一份老师课件的页面内容整理配套练习题。\n"
    "任务：\n"
    "1. 先找出课件正文里本来就有的题目（例题、思考题、习题），若有就原样保留题干，"
    "标 is_existing=true；课件里题目通常很少，没有也正常。\n"
    "2. 再依据课件内容，针对其中涉及的考点补充生成练习题，让每个考点至少有 1 题，"
    "优先出选择题、名词解释、以及需要写出或代入公式的简短题。\n"
    "3. 题目必须能由给定课件内容回答，不要引入课件没讲过的方法、结论或超纲推导。\n"
    "4. 每题必须给出确定的答案与简短解析，并标注它出自哪一页（source_page 只能是"
    "给定页码之一）以及属于哪个考点（kp_id 只能是给定候选之一）。\n"
    "只输出 JSON 数组，不要解释文字。每个元素字段：\n"
    "{\"question_type\":\"single_choice|true_false|term|short_answer|numeric\", "
    "\"stem\":\"...\", \"options\":[{\"key\":\"A\",\"text\":\"...\"}], "
    "\"answer_payload\":{...}, \"explanation\":\"...\", "
    "\"kp_id\":\"...\", \"source_page\":1, \"is_existing\":false}\n"
    "answer_payload 格式：单选/多选/判断用 {\"key\":\"A\"} / {\"keys\":[\"A\",\"B\"]}"
    " / {\"value\":true}；名词解释、简答、公式题用 {\"reference_answer\":\"...\"}。\n"
    "选项题的 options 至少 2 项且答案 key 必须在选项里；其它题 options 给 []。"
)


def _pages_for_upload(up: E.MaterialUpload) -> tuple[list[dict], list[int]]:
    if not up.document_id:
        return [], []
    f = cfg.PARSED_DIR / up.document_id / "units.json"
    if not f.exists():
        return [], []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return [], []
    pages = [p for p in (data.get("pages") or [])
             if (p.get("raw_text") or "").strip()]
    return pages, [int(p["page_no"]) for p in pages]


def _kp_candidates(db: Session, text: str) -> list[dict]:
    """优先给本课件真正涉及的考点，避免模型在 35 个考点里乱选。"""
    units = db.execute(select(E.CourseScopeUnit)).scalars().all()
    scored = []
    for u in units:
        name = re.sub(r"[（(].*?[）)]", "", u.name or "")
        kws = [w for w in re.split(r"[与和及的、，,/·\s]+", name) if len(w) >= 2]
        hits = sum(1 for k in kws if k.lower() in text.lower())
        if hits:
            scored.append((hits, u))
    scored.sort(key=lambda x: -x[0])
    picked = [u for _, u in scored[:8]]
    if not picked:
        picked = units[:8]
    return [{"kp_id": u.kp_id, "name": u.name, "chapter": u.chapter,
             "depth": u.depth} for u in picked]


def _parse_array(raw: str) -> list[dict] | None:
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.S)
    body = fence.group(1) if fence else raw
    if not fence:
        s, e = body.find("["), body.rfind("]")
        if s < 0 or e <= s:
            return None
        body = body[s:e + 1]
    try:
        out = json.loads(body)
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, list) else None


def _validate(items: list[dict], *, allowed_pages: set[int],
              allowed_kps: set[str], existing_stems: set[str]) -> tuple[list[dict], list[str]]:
    kept, rejected, seen = [], [], set()
    for i, c in enumerate(items[:MAX_QUESTIONS * 2]):
        if not isinstance(c, dict):
            rejected.append(f"#{i} 不是对象")
            continue
        qt = str(c.get("question_type") or "").strip()
        stem = re.sub(r"\s+", " ", str(c.get("stem") or "")).strip()
        ans = c.get("answer_payload") or {}
        opts = c.get("options") or []
        problems = []
        if qt not in VALID_TYPES:
            problems.append(f"题型不支持：{qt}")
        if len(stem) < 8:
            problems.append("题干过短")
        if not isinstance(ans, dict) or not ans:
            problems.append("缺少答案")
        try:
            page = int(c.get("source_page"))
        except (TypeError, ValueError):
            page = 0
        if page not in allowed_pages:
            problems.append(f"页码 {page} 不在课件里")
        kp_id = str(c.get("kp_id") or "").strip()
        if kp_id not in allowed_kps:
            problems.append(f"考点 {kp_id} 不在候选中")
        if qt in OBJECTIVE_TYPES:
            keys = {str(o.get("key")) for o in opts if isinstance(o, dict)}
            if len(opts) < 2:
                problems.append("客观题选项不足")
            elif qt == "single_choice" and ans.get("key") not in keys:
                problems.append("单选答案不在选项里")
            elif qt == "multi_choice" and not set(ans.get("keys") or []) <= keys:
                problems.append("多选答案含无效选项")
        elif qt == "true_false" and not isinstance(ans.get("value"), bool):
            problems.append("判断题答案不是布尔值")
        elif not str(ans.get("reference_answer") or "").strip():
            problems.append("主观题缺少参考答案")
        norm = re.sub(r"\s+", "", stem).lower()
        if norm in existing_stems or norm in seen:
            problems.append("与已有题目重复")
        if problems:
            rejected.append(f"#{i} " + "；".join(problems))
            continue
        seen.add(norm)
        kept.append({
            "question_type": qt, "stem": stem, "options": opts,
            "answer_payload": ans,
            "explanation": str(c.get("explanation") or "")[:600],
            "kp_id": kp_id, "source_page": page,
            "is_existing": bool(c.get("is_existing")),
        })
        if len(kept) >= MAX_QUESTIONS:
            break
    return kept, rejected


def generate_for_upload(db: Session, upload_id: str) -> dict:
    up = db.get(E.MaterialUpload, upload_id)
    if up is None:
        raise KeyError(upload_id)
    if up.resolved_type != "slides":
        return {"upload_id": upload_id, "status": "skipped",
                "reason": f"资料类型为 {up.resolved_type}，不是老师课件"}
    if up.slides_question_count > 0:
        return {"upload_id": upload_id, "status": "skipped",
                "reason": "已完成过后台出题，不重复调用模型",
                "questions": up.slides_question_count}

    pages, page_nos = _pages_for_upload(up)
    if not pages:
        return {"upload_id": upload_id, "status": "skipped",
                "reason": "课件解析结果里没有可读的正文页"}
    # 取内容最密的若干页，控制 prompt 长度与花费
    ranked = sorted(pages, key=lambda p: -len(p.get("raw_text") or ""))
    chosen = sorted(ranked[:14], key=lambda p: int(p["page_no"]))
    sample_pages = [{"page": int(p["page_no"]),
                     "text": (p.get("raw_text") or "")[:900]} for p in chosen]
    body = "\n\n".join(f"[课件第 {p['page']} 页]\n{p['text']}"
                       for p in sample_pages)
    kps = _kp_candidates(db, body)
    user_msg = (f"可用页码：{sorted({p['page'] for p in sample_pages})}\n\n"
                f"可用考点（只能从中选 kp_id）：\n"
                + json.dumps(kps, ensure_ascii=False)
                + f"\n\n最多出 {MAX_QUESTIONS} 题，其中已有题目优先。\n\n"
                + body[:9000])

    try:
        res = model_provider.chat(
            db, messages=[{"role": "system", "content": _SYS},
                          {"role": "user", "content": user_msg}],
            skill_name="slides_questions", prompt_version=PROMPT_VERSION,
            temperature=0.3, max_tokens=2600, timeout=90.0)
    except model_provider.ModelUnavailable as e:
        return {"upload_id": upload_id, "status": "deferred",
                "reason": f"模型不可用：{e.status}"}
    raw = (res.get("content") or "").strip()
    if res.get("finish_reason") == "length":
        return {"upload_id": upload_id, "status": "failed",
                "reason": "模型输出被 max_tokens 截断，未采用（避免半截题目入库）"}
    items = _parse_array(raw)
    if items is None:
        return {"upload_id": upload_id, "status": "failed",
                "reason": "模型输出不是合法 JSON 数组",
                "raw_excerpt": raw[:200]}

    existing = {re.sub(r"\s+", "", (s or "")).lower()
                for s in db.execute(select(E.Question.stem)).scalars().all()}
    kept, rejected = _validate(
        items, allowed_pages={p["page"] for p in sample_pages},
        allowed_kps={k["kp_id"] for k in kps}, existing_stems=existing)

    created, scope_rejected = [], []
    for i, c in enumerate(kept, start=1):
        verdict = scope_service.judge_generated(db, c["kp_id"], c["stem"])
        if verdict["scope_class"] == "unconfirmed":
            scope_rejected.append({"stem": c["stem"][:50],
                                   "reason": verdict["basis"]})
            continue
        qid = f"slq_{up.sha256[:10]}_p{c['source_page']}_{i}"
        if db.execute(select(E.Question.question_id).where(
                E.Question.question_id == qid)).first() is not None:
            continue
        ref = {"document_id": up.document_id,
               "document_version_id": up.document_version_id,
               "rel_path": up.stored_rel_path, "page": c["source_page"],
               "unit_label": "page", "upload_id": up.upload_id,
               "note": "课件后台自动出题；来源为该课件对应页"}
        rubric = {"points": [{"point": c["explanation"][:160] or "要点与参考答案一致",
                              "score": 2.0}]}
        row = E.Question(
            row_id=E.new_id("qrow"), question_id=qid, question_version=1,
            origin="ai", original_exam_id=up.upload_id,
            original_question_no="",
            question_type=c["question_type"], stem=c["stem"],
            options=c["options"], score=2.0,
            main_kp_id=c["kp_id"], aux_kp_ids=[],
            cognitive_level="理解", difficulty="基础",
            question_family_id=f"fam_{qid}",
            review_status="auto_checked", answer_status="ai_auto_checked",
            answer_origin="ai_generated", active=True,
            answer_payload=c["answer_payload"], explanation=c["explanation"],
            rubric=rubric, scope_class=verdict["scope_class"],
            source_refs=[ref],
            notes=("课件后台自动生成（"
                   + ("课件原题" if c["is_existing"] else "按课件考点补充")
                   + f"），来源第 {c['source_page']} 页；"
                   "答案由模型给出、未经独立核验；"
                   f"范围复检：{verdict['basis']}"),
            created_by="job:slides_questions",
        )
        db.add(row)
        created.append(qid)

    up.slides_question_count = len(created)
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=up.user_id,
        event_type="slides_questions_generated", target_id=up.upload_id,
        payload={"questions": len(created), "scope_rejected": len(scope_rejected),
                 "rejected": rejected[:8], "model": res.get("model", ""),
                 "prompt_version": PROMPT_VERSION,
                 "call_id": res.get("call_id", ""),
                 "background": True, "user_prompted": False}))
    db.commit()
    return {"upload_id": upload_id, "status": "succeeded",
            "questions": len(created), "question_ids": created,
            "existing_in_slides": sum(1 for c in kept if c["is_existing"]),
            "rejected": rejected[:8], "scope_rejected": scope_rejected,
            "model": res.get("model", ""), "call_id": res.get("call_id", "")}
