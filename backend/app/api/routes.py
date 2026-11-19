"""HTTP API. Student-facing question data ALWAYS goes through
services.question_dto.student_view (answer isolation)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.ingest import parsers
from backend.app.models import entities as E
from backend.app.models.base import get_session
from backend.app.schemas import dto
from backend.app.services import (answer_attachment_service, answer_ocr,
                                  answer_pipeline, attempt_service, auth_service,
                                  bank_service,
                                  daily_service,
                                  external_paper_service, home_service,
                                  model_provider, paper_localization, paper_pdf,
                                  pdf_thumbnail, retest_service, retrieval,
                                  scope_service, selection_basis,
                                  skill_runtime)
from backend.app.services.question_dto import (assert_no_answer_leak,
                                               display_prompt_prefix,
                                               student_view)
from backend.app.services.question_fingerprint import (
    fingerprint_basis as question_fingerprint_basis,
    question_fingerprint,
)
from backend.app.workers import worker

router = APIRouter(prefix="/api")

USER_ID = "local_user"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _knowledge_point_labels(db: Session) -> dict[str, dict[str, str]]:
    """Return presentation-only labels without changing persisted records."""
    rows = db.execute(select(E.KnowledgePoint)).scalars().all()
    return {
        row.kp_id: {"kp_name": row.name or "", "chapter": row.chapter or ""}
        for row in rows
    }


def _labels_for(
        kp_labels: dict[str, dict[str, str]], kp_id: str | None) -> dict[str, str]:
    return kp_labels.get(kp_id or "", {"kp_name": "", "chapter": ""})


WRONG_STATUS_LABELS = {
    "open": "待订正",
    "corrected": "已订正",
    "verified": "已巩固",
}
RETEST_STATUS_LABELS = {
    "pending": "待复测",
    "in_progress": "复测中",
    "completed": "已完成",
    "expired": "已过期",
}
RETEST_KIND_LABELS = {
    "original_correction": "原题订正",
    "new_transfer": "新题迁移",
    "delayed": "延时复测",
}
PLAN_STATUS_LABELS = {
    "pending": "待完成",
    "in_progress": "进行中",
    "completed": "已完成",
    "done": "已完成",
    "skipped": "已跳过",
}
PLAN_KIND_LABELS = {
    "review": "资料复习",
    "practice": "专项练习",
    "retest": "巩固复测",
    "correction": "错题订正",
}
KNOWLEDGE_STATE_LABELS = {
    "untested": "未测",
    "insufficient": "证据不足",
    "needs_work": "需加强",
    "initial_pass": "初步通过",
    "stable": "阶段稳定",
}


# ------------------------------------------------------------------ system
@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ml-quiz-platform",
            "time_utc": _now_iso(), "parser_config_version": cfg.PARSER_CONFIG_VERSION,
            "skill_registry_version": cfg.SKILL_REGISTRY_VERSION}


@router.get("/config/status")
def config_status() -> dict:
    chat_ready = bool(cfg.MODEL_PROVIDER and cfg.MODEL_CHAT_MODEL and cfg.MODEL_API_KEY)
    return {
        "model": {
            "configured": chat_ready,
            "status": "ready_for_validation" if chat_ready else "not_configured",
            "provider": cfg.MODEL_PROVIDER or None,
            "base_url": cfg.MODEL_BASE_URL or None,
            "chat_model": cfg.MODEL_CHAT_MODEL or None,
            "api_key_present": bool(cfg.MODEL_API_KEY),
            "note": "未配置时所有依赖模型的功能标记为『待配置』，不会用 mock 冒充真实调用",
        },
        "embedding": {
            "configured": bool(cfg.EMBED_PROVIDER and cfg.EMBED_MODEL and cfg.EMBED_API_KEY),
            "model": cfg.EMBED_MODEL or None,
            "dim": cfg.EMBED_DIM or None,
            "note": "聊天模型与嵌入模型分开配置；不假定聊天 API 提供 embeddings",
        },
        "retrieval": retrieval.retrieval_status(),
        "project_rules": {
            "retest_intervals_days": list(cfg.RETEST_INTERVALS_DAYS),
            "state_min_evidence": cfg.STATE_MIN_EVIDENCE,
            "state_min_families": cfg.STATE_MIN_FAMILIES,
            "state_stable_min_days": cfg.STATE_STABLE_MIN_DAYS,
            "exam_date": cfg.EXAM_DATE,
            "note": "这些是本项目规则参数，不声称对所有学习者最优",
        },
        "data_paths": {
            "database": str(cfg.DB_PATH),
            "manifest": str(cfg.MANIFEST_DIR),
            "parsed": str(cfg.PARSED_DIR),
            "assets": str(cfg.ASSET_DIR),
            "material_root": str(cfg.MATERIAL_ROOT),
            "material_root_read_only": True,
        },
    }


# ------------------------------------------------------------------ bank
@router.get("/knowledge-points")
def knowledge_points(db: Session = Depends(get_session)) -> dict:
    rows = db.execute(select(E.KnowledgePoint).order_by(
        E.KnowledgePoint.chapter, E.KnowledgePoint.kp_id)).scalars().all()
    return {"total": len(rows), "items": [
        {"kp_id": r.kp_id, "chapter": r.chapter, "name": r.name,
         "description": r.description, "prerequisites": r.prerequisites,
         "key_basis": r.key_basis, "material_refs": r.material_refs}
        for r in rows]}


@router.get("/questions")
def list_questions(kp_id: str = "", qtype: str = "", origin: str = "",
                   review_status: str = "", include_inactive: bool = False,
                   limit: int = 200,
                   db: Session = Depends(get_session)) -> dict:
    # V3.3 统一题库页需要「全部真实题目」；limit 可调，默认仍是旧值，
    # 其他调用方的行为不变。上限 2000 防止误传超大值拖慢页面。
    limit = max(1, min(int(limit), 2000))
    items = bank_service.list_questions(
        db, kp_id=kp_id, qtype=qtype, origin=origin,
        review_status=review_status, include_inactive=include_inactive,
        limit=limit)
    return {"total": len(items), "items": items,
            "stats": bank_service.bank_stats(db),
            "note": "列表不含答案/解析/评分细则；答案状态字段仅表示状态，不泄露答案内容"}


@router.get("/questions/{question_id}")
def get_question(question_id: str, db: Session = Depends(get_session)) -> dict:
    q = db.execute(select(E.Question).where(
        E.Question.question_id == question_id).order_by(
        E.Question.question_version.desc())).scalars().first()
    if not q:
        raise HTTPException(404, "题目不存在")
    return student_view(q, reveal=False)


@router.post("/papers/assemble")
def assemble_paper(bp: dto.BlueprintIn, db: Session = Depends(get_session)) -> dict:
    if bp.count < 1 or bp.count > 100:
        raise HTTPException(400, "题量需在 1-100 之间")
    return bank_service.assemble_paper(db, bp.model_dump(), user_id=USER_ID)


@router.post("/papers/from-questions")
def paper_from_questions(body: dto.ManualPaperIn,
                         db: Session = Depends(get_session)) -> dict:
    """V3.3 手动选题建卷：从真实题库按勾选顺序建卷，不做 AI 补题。

    建卷后逐项复查 Paper / PaperItem 是否真的落库，题量、总分、顺序、
    快照内容不一致就报错；绝不返回「0 题 0 分却成功」。
    """
    raw_ids = [str(x).strip() for x in (body.question_ids or []) if str(x).strip()]
    ids = list(dict.fromkeys(raw_ids))
    if not ids:
        raise HTTPException(400, "请至少选择一道题目")
    if len(ids) != len(raw_ids):
        raise HTTPException(400, "同一道题不能重复加入同一份试卷")
    if len(ids) > 100:
        raise HTTPException(400, "单份试卷最多 100 道题")

    questions: list[Any] = []
    for qid in ids:
        q = db.execute(select(E.Question).where(
            E.Question.question_id == qid,
            E.Question.active.is_(True)).order_by(
            E.Question.question_version.desc())).scalars().first()
        if q is None:
            raise HTTPException(400, f"题目不存在或已停用：{qid}")
        questions.append(q)

    mode = body.mode if body.mode in ("practice", "quiz") else "practice"

    # 题目完整性门槛：待修复的题（选项缺失 / 答案与选项不自洽 / 被明确扣下）
    # 不能进入正式测验——否则学生答了也无法得到可靠评分。练习模式允许保留，
    # 但要如实告知哪些题不会自动计分。
    blocked: list[dict] = []
    for q in questions:
        problems = []
        if not answer_pipeline.is_publishable(q):
            problems.append("该题已被标记为待修复（不可发布）")
        ok_answer, reason = answer_pipeline.answer_structurally_valid(q)
        if not ok_answer:
            problems.append(reason)
        if problems:
            blocked.append({"question_id": q.question_id,
                            "problems": problems,
                            "answer_label": answer_pipeline.answer_label(
                                q.answer_origin or "none")})
    answer_ready = [q for q in questions
                    if answer_pipeline.is_answer_ready(q)]
    if blocked and mode == "quiz":
        detail = "；".join(f"{b['question_id']}：{'，'.join(b['problems'])}"
                           for b in blocked[:5])
        raise HTTPException(
            400, f"以下题目还不能用于正式测验（共 {len(blocked)} 道）：{detail}。"
                 "请在题库里取消勾选，或改用练习模式")

    if body.scores:
        if len(body.scores) != len(questions):
            raise HTTPException(400, "分值数量与题目数量不一致")
        scores = [float(s) for s in body.scores]
    else:
        scores = [float(q.score or 1.0) for q in questions]
    if any(not (0 < s <= 100) for s in scores):
        raise HTTPException(400, "每题分值需在 0-100 之间且大于 0")

    # ---------------- V3.4：用户显式开启时的 AI 补题 ----------------
    ai_fill_info: dict = {"enabled": bool(body.ai_fill), "requested": 0, "filled": 0,
                          "note": ""}
    if body.ai_fill:
        target = int(body.target_count or len(questions))
        need = max(0, target - len(questions))
        ai_fill_info["requested"] = need
        if need > 0:
            kp_ids = [k for k in (body.ai_kp_ids or []) if k]
            if not kp_ids:
                kp_ids = list(dict.fromkeys(
                    q.main_kp_id for q in questions if q.main_kp_id))
            qtypes = [t for t in (body.ai_question_types or []) if t]
            if not qtypes:
                qtypes = list(dict.fromkeys(
                    q.question_type for q in questions if q.question_type))
            filled = bank_service.ai_fill_questions(
                db, {"title": body.title, "mode": mode}, kp_ids, need, qtypes)
            # 补出来的题同样要过结构校验，不能把不自洽的答案塞进试卷
            usable = [q for q in filled
                      if answer_pipeline.answer_structurally_valid(q)[0]]
            for q in usable:
                questions.append(q)
                scores.append(float(q.score or 2.0))
            ai_fill_info["filled"] = len(usable)
            ai_fill_info["note"] = (
                f"题库不足，已由 AI 补足 {len(usable)}/{need} 道" if usable
                else "AI 未产出通过校验的题目，本次试卷只含已选题")
            ai_fill_info["question_ids"] = [q.question_id for q in usable]
        else:
            ai_fill_info["note"] = "已选题量已达到目标题量，本次未调用 AI 补题"

    if any(not (0 < s <= 100) for s in scores):
        raise HTTPException(400, "每题分值需在 0-100 之间且大于 0")
    total = round(sum(scores), 2)
    if total <= 0:
        raise HTTPException(400, "总分必须大于 0")

    title = (body.title or "").strip() or f"机器学习专项练习 - {_today()}"
    minutes = (float(body.estimated_minutes) if body.estimated_minutes
               else round(sum(float(q.estimated_minutes or 2.0)
                              for q in questions), 1))
    try:
        out = bank_service.create_paper_from_questions(
            db, questions, title=title, mode=mode,
            source="manual_selection", scores=scores,
            user_id=USER_ID,
            blueprint={"source": "manual_selection",
                       "manual_question_ids": ids,
                       "manual_scores": scores,
                       "estimated_minutes_override": minutes})
        paper = db.get(E.Paper, out["paper_id"])
        if paper is None:
            raise ValueError("试卷未能写入数据库")
        paper.estimated_minutes = minutes
        if body.mark_as_test:
            paper.is_test_fixture = True
        db.add(E.LearningEvent(
            event_id=E.new_id("evt"), user_id=USER_ID,
            event_type="manual_paper_created", target_id=paper.paper_id,
            payload={"title": title, "item_count": len(ids),
                     "total_score": total, "mode": mode,
                     "source": "manual_selection",
                     "is_test_fixture": bool(body.mark_as_test)},
        ))
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))

    # ---- 真实落库复查：题量 / 顺序 / 分值 / 快照内容 ----
    items = db.execute(select(E.PaperItem).where(
        E.PaperItem.paper_id == paper.paper_id).order_by(
        E.PaperItem.position)).scalars().all()
    problems: list[str] = []
    if len(items) != len(questions):
        problems.append(f"PaperItem {len(items)} 条，期望 {len(questions)} 条")
    if paper.item_count != len(questions):
        problems.append(f"paper.item_count={paper.item_count}")
    if abs(float(paper.total_score) - total) > 0.01:
        problems.append(f"paper.total_score={paper.total_score}，期望 {total}")
    for i, (it, q, sc) in enumerate(zip(items, questions, scores), start=1):
        snap = it.snapshot or {}
        if it.position != i or it.question_id != q.question_id:
            problems.append(f"第 {i} 题顺序/ID 不符")
        if abs(float(it.score) - sc) > 0.01:
            problems.append(f"第 {i} 题分值不符")
        if snap.get("stem") != q.stem or snap.get("options") != q.options:
            problems.append(f"第 {i} 题题干/选项快照不完整")
        for key in ("answer_payload", "rubric", "explanation"):
            if key not in snap:
                problems.append(f"第 {i} 题缺 {key} 字段")
    if problems:
        raise HTTPException(500, "建卷落库校验失败：" + "；".join(problems[:5]))

    return {
        **out,
        "paper_id": paper.paper_id,
        "title": paper.title,
        "mode": paper.mode,
        "item_count": paper.item_count,
        "total_score": paper.total_score,
        "estimated_minutes": paper.estimated_minutes,
        "question_ids": [q.question_id for q in questions],
        "scores": scores,
        "is_test_fixture": bool(paper.is_test_fixture),
        "ai_fill": ai_fill_info,
        "completeness": {
            "blocked": blocked,
            "blocked_count": len(blocked),
            "answer_ready_count": len(answer_ready),
            "note": ("练习模式下这些题仍会显示，但不会自动计分；"
                     "正式测验会直接拒绝包含它们的试卷"
                     if blocked else "所选题目均有可用参考答案"),
        },
        "verification": {
            "paper_exists": True,
            "paper_items": len(items),
            "order_matches": True,
            "total_score_matches": abs(float(paper.total_score) - total) <= 0.01,
            "snapshots_complete": True,
            "ai_generated_questions": int(ai_fill_info.get("filled") or 0),
            "source": "manual_selection",
        },
    }


@router.get("/scope/overview")
def scope_overview(db: Session = Depends(get_session)) -> dict:
    return scope_service.scope_overview(db)


@router.get("/scope/units")
def scope_units(db: Session = Depends(get_session)) -> dict:
    rows = db.execute(select(E.CourseScopeUnit)
                      .order_by(E.CourseScopeUnit.chapter,
                                E.CourseScopeUnit.kp_id)).scalars().all()
    return {"items": [{
        "kp_id": u.kp_id, "chapter": u.chapter, "name": u.name,
        "depth": u.depth, "coverage": u.coverage, "ppt_refs": u.ppt_refs,
        "concepts": u.concepts, "formulas": u.formulas, "methods": u.methods,
        "teacher_basis": u.teacher_basis,
    } for u in rows], "total": len(rows)}


@router.get("/external/summary")
def external_summary(db: Session = Depends(get_session)) -> dict:
    return scope_service.external_summary(db)


@router.get("/external/items")
def external_items(category: str = "", scope: str = "", kp_id: str = "",
                   group: str = "", db: Session = Depends(get_session)) -> dict:
    """`group` 为扩展学习页的资源类型分组（course/overseas/domestic/
    interview/university_exam）。分组依据来源地区台账，不从文件名或域名猜。"""
    if group and group not in scope_service.GROUP_LABELS:
        raise HTTPException(400, f"未知资源类型：{group}")
    return scope_service.external_items_for_ui(
        db, category=category, scope=scope, kp_id=kp_id, group=group)


@router.post("/external/items/{item_id}/classify")
def classify_external(item_id: str, body: dict | None = None,
                      db: Session = Depends(get_session)) -> dict:
    it = db.get(E.ExternalItem, item_id)
    if it is None:
        raise HTTPException(404, "外部条目不存在")
    return scope_service.classify_item(db, it, force=bool((body or {}).get("force")))


@router.post("/external/items/{item_id}/import")
def import_external(item_id: str, db: Session = Depends(get_session)) -> dict:
    it = db.get(E.ExternalItem, item_id)
    if it is None:
        raise HTTPException(404, "外部条目不存在")
    return scope_service.import_question(db, it)


# ------------------------------------------------- 扩展学习：试卷资源库（V2.3）
@router.get("/external/papers")
def external_papers(origin: str = "", paper_type: str = "", difficulty: str = "",
                    match: str = "", year_from: int | None = None,
                    year_to: int | None = None, q: str = "",
                    sort: str = "match", page: int = Query(1, ge=1),
                    page_size: int = Query(12, ge=1, le=60),
                    db: Session = Depends(get_session)) -> dict:
    """整套试卷/题集列表。所有筛选可组合；未知难度与缺失年份排最后。"""
    for value, allowed, label in (
            (origin, external_paper_service.ORIGIN_LABELS, "来源地区"),
            (paper_type, external_paper_service.PAPER_TYPE_LABELS, "资源类型"),
            (difficulty, external_paper_service.DIFFICULTY_LABELS, "难度"),
            (match, external_paper_service.MATCH_LABELS, "课程匹配度"),
            (sort, external_paper_service.SORT_LABELS, "排序方式")):
        if value and value not in allowed:
            raise HTTPException(400, f"未知{label}：{value}")
    return external_paper_service.list_papers(
        db, origin=origin, paper_type=paper_type, difficulty=difficulty,
        match=match, year_from=year_from, year_to=year_to, q=q, sort=sort,
        page=page, page_size=page_size)


@router.get("/external/papers/{epaper_id}")
def external_paper_detail(epaper_id: str,
                          db: Session = Depends(get_session)) -> dict:
    try:
        return external_paper_service.get_paper(db, epaper_id)
    except KeyError:
        raise HTTPException(404, "试卷不存在")


@router.get("/external/papers/{epaper_id}/pdf")
def external_paper_pdf(epaper_id: str, db: Session = Depends(get_session)):
    """只服务平台本地已保存且有授权的 PDF；外部 PDF 由前端直接打原始链接。"""
    try:
        paper, path = external_paper_service.get_local_pdf(db, epaper_id)
    except KeyError:
        raise HTTPException(404, "试卷不存在")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    safe = re.sub(r'[\\/:*?"<>|\r\n]', "_", paper.title or "试卷")[:80]
    return FileResponse(str(path), media_type="application/pdf",
                        filename=f"{safe}.pdf")


@router.get("/external/papers/{epaper_id}/thumbnail")
def external_paper_thumbnail(epaper_id: str,
                            db: Session = Depends(get_session)):
    """真实 PDF 第一页缩略图。

    只在 `preview_allowed` 时才渲染；未授权、无 PDF、文件损坏一律 404，
    前端退回类型化占位封面，绝不拿别的图片冒充。
    """
    paper = db.get(E.ExternalPaper, epaper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    info = pdf_thumbnail.ensure_thumbnail(db, paper)
    if not info.get("ok"):
        raise HTTPException(404, info.get("message") or "缩略图不可用")
    path = cfg.resolve_storage_ref(info["thumb_rel_path"])
    if not path.exists():
        raise HTTPException(404, "缩略图文件不存在")
    return FileResponse(str(path), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400",
                                 "X-Thumb-Cached": "1" if info.get("cached")
                                 else "0"})


@router.get("/external/processing")
def external_processing(db: Session = Depends(get_session)) -> dict:
    """资源处理清单：每套试卷的处理状态、计数与失败原因。"""
    papers = db.execute(select(E.ExternalPaper)).scalars().all()
    items = []
    summary: dict[str, int] = {}
    for paper in papers:
        st = paper_localization.status(db, paper)
        summary[st["status"]] = summary.get(st["status"], 0) + 1
        items.append({
            "epaper_id": paper.epaper_id,
            "title": paper.title,
            "institution": paper.institution,
            "source_type": paper.source_type,
            "status": st["status"],
            "label": st["label"],
            "counts": st["counts"],
            "note": st["note"],
            "failed_reason": st["failed_reason"],
            "pdf_available": st["pdf_available"],
            "allows_copy": paper.allows_copy,
            "localized_at": paper.localized_at,
        })
    items.sort(key=lambda x: (x["status"] not in ("partial", "failed", "review"),
                              x["title"]))
    return {"items": items, "total": len(items), "summary": summary,
            "note": ("状态与计数都来自真实数据；没有 PDF 的网页题集只做结构化翻译，"
                     "不会生成虚假的中文 PDF")}


@router.get("/external/papers/{epaper_id}/zh-status")
def external_paper_zh_status(epaper_id: str,
                             db: Session = Depends(get_session)) -> dict:
    paper = db.get(E.ExternalPaper, epaper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    return {"epaper_id": epaper_id, **paper_localization.status(db, paper)}


@router.post("/external/papers/{epaper_id}/localize")
def external_paper_localize(epaper_id: str, body: dict | None = None,
                            db: Session = Depends(get_session)) -> dict:
    """结构化 + 翻译 + 生成中文 PDF。

    默认入队（耗时较长，避免请求超时）；`sync=true` 时同步执行，供验证脚本使用。
    幂等：PDF、模型与提示词版本都没变时直接复用已有结果。
    """
    paper = db.get(E.ExternalPaper, epaper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    force = bool((body or {}).get("force"))
    if (body or {}).get("sync"):
        try:
            out = paper_localization.localize(db, paper, force=force)
        except paper_localization.LocalizeError as e:
            paper_localization.mark_failed(db, paper, str(e))
            raise HTTPException(409, str(e))
        except model_provider.ModelUnavailable as e:
            paper_localization.mark_failed(db, paper, str(e))
            raise HTTPException(503, str(e))
        return {**out, **paper_localization.status(db, paper)}
    job = worker.create_job(
        db, "external_paper_localize",
        {"epaper_id": epaper_id, "force": force},
        idempotency_key=f"localize:{epaper_id}:{paper.localize_version or 'new'}")
    return {"job_id": job["job_id"], "status": job["status"],
            "note": "已加入后台处理队列；完成后详情页会自动显示中文版"}


@router.get("/external/papers/{epaper_id}/zh.pdf")
def external_paper_zh_pdf(epaper_id: str, download: int = 0,
                          db: Session = Depends(get_session)):
    """中文版 PDF。

    在线查看（inline）允许；`download=1` 需要 `allows_copy`，否则 403 ——
    翻译属于改编，不能因为「生成了」就默认可再分发。
    """
    paper = db.get(E.ExternalPaper, epaper_id)
    if paper is None:
        raise HTTPException(404, "试卷不存在")
    if not paper.zh_pdf_rel_path:
        raise HTTPException(404, "中文版尚未生成")
    path = cfg.resolve_storage_ref(paper.zh_pdf_rel_path)
    if not path.exists():
        raise HTTPException(404, "中文版文件不存在")
    if download and not paper.allows_copy:
        raise HTTPException(403, "未确认改编/再分发授权，仅提供在线查看")
    safe = re.sub(r'[\\/:*?"<>|\r\n]', "_", paper.title or "试卷")[:80]
    name = f"{safe}_中文版.pdf"
    disposition = "attachment" if download else "inline"
    return FileResponse(
        str(path), media_type="application/pdf",
        headers={"Content-Disposition":
                 f'{disposition}; filename="paper-zh.pdf"; '
                 f"filename*=UTF-8''{quote(name)}"})


@router.post("/external/papers/{epaper_id}/attempt")
def external_paper_attempt(epaper_id: str,
                           db: Session = Depends(get_session)) -> dict:
    """按原卷顺序作答：不随机抽题、不重排。

    只把已完成结构化且答案可用的题目纳入；不足整卷时在 note 里明确说明。
    """
    try:
        return external_paper_service.start_attempt(db, epaper_id)
    except KeyError:
        raise HTTPException(404, "试卷不存在")
    except external_paper_service.NoOnlineQuestions as e:
        raise HTTPException(409, str(e))


@router.get("/external/orphans")
def external_orphans(db: Session = Depends(get_session)) -> dict:
    """兼容区：未归入任何试卷的零散题目、网课视频与只有主页的来源。"""
    return external_paper_service.orphan_resources(db)


@router.get("/papers")
def list_papers(limit: int = Query(50, ge=1, le=500),
                offset: int = Query(0, ge=0), keyword: str = "",
                source: str = "",
                db: Session = Depends(get_session)) -> dict:
    """试卷列表。

    V3.4（RC-8/RC-9）：以前一次性返回全部试卷（含 blueprint / coverage_report，
    66 份就有 226 KB），而且历史上的空试卷会和正常试卷长得一样。
    现在支持分页与关键字/来源过滤，并对空卷显式标 `invalid_empty` / `answerable`。
    """
    stmt = select(E.Paper).where(or_(E.Paper.archived.is_(False),
                                     E.Paper.archived.is_(None)))
    if keyword:
        stmt = stmt.where(E.Paper.title.contains(keyword))
    if source:
        stmt = stmt.where(E.Paper.source == source)
    all_rows = db.execute(stmt.order_by(desc(E.Paper.created_at))).scalars().all()
    total = len(all_rows)
    rows = all_rows[offset:offset + limit]
    item_counts = {
        pid: n for pid, n in db.execute(
            select(E.PaperItem.paper_id, func.count(E.PaperItem.item_id))
            .group_by(E.PaperItem.paper_id)).all()
    }
    items = []
    version_rows = db.execute(
        select(E.AssessmentVersion, E.Assessment).join(
            E.Assessment,
            E.Assessment.assessment_id == E.AssessmentVersion.assessment_id,
        ).where(E.AssessmentVersion.paper_id.in_([p.paper_id for p in rows]))
    ).all() if rows else []
    classroom_by_paper = {
        version.paper_id: assessment.classroom_id
        for version, assessment in version_rows
    }
    for p in rows:
        real = int(item_counts.get(p.paper_id, 0))
        empty = real == 0
        items.append({
            "paper_id": p.paper_id, "title": p.title, "mode": p.mode,
            "item_count": p.item_count, "real_item_count": real,
            "total_score": p.total_score,
            "estimated_minutes": p.estimated_minutes, "source": p.source,
            "coverage_report": p.coverage_report, "created_at": p.created_at,
            "blueprint": p.blueprint,
            "assessment_classroom_id": classroom_by_paper.get(p.paper_id, ""),
            "frozen": p.paper_id in classroom_by_paper,
            "invalid_empty": empty,
            "answerable": not empty,
            "invalid_note": ("历史遗留的空试卷：数据库中没有任何题目关联，"
                             "不能作答。保留记录只为不丢历史，不删除。"
                             if empty else ""),
        })
    return {"total": total, "returned": len(items),
            "offset": offset, "limit": limit,
            "empty_count": sum(1 for i in items if i["invalid_empty"]),
            "items": items,
            "note": "不存在的题目关联不计入；空试卷标 invalid_empty 且不可作答"}


@router.get("/papers/{paper_id}")
def get_paper(paper_id: str, db: Session = Depends(get_session)) -> dict:
    p = db.get(E.Paper, paper_id)
    if not p:
        raise HTTPException(404, "试卷不存在")
    real_items = db.execute(select(func.count(E.PaperItem.item_id)).where(
        E.PaperItem.paper_id == paper_id)).scalar() or 0
    return {
        "paper_id": p.paper_id, "title": p.title, "mode": p.mode,
        "total_score": p.total_score, "item_count": p.item_count,
        "real_item_count": int(real_items),
        "invalid_empty": int(real_items) == 0,
        "answerable": int(real_items) > 0,
        "invalid_note": ("历史遗留的空试卷：没有任何题目关联，不能作答"
                         if int(real_items) == 0 else ""),
        "estimated_minutes": p.estimated_minutes,
        "coverage_report": p.coverage_report, "created_at": p.created_at,
        "questions": bank_service.get_paper_student_view(
            db, paper_id, reveal=False, mode=p.mode),
        "note": "测验模式：未交卷不返回答案、解析、评分细则与可揭示答案的资料链接",
    }


@router.get("/papers/{paper_id}/export.pdf")
def export_paper_pdf(paper_id: str, db: Session = Depends(get_session)):
    """导出组卷 PDF（空白卷）。

    与网页预览读取同一份 PaperItem 快照，但强制 reveal=False：
    PDF 绝不成为绕过「提交前不揭示答案」的旁路。PDF 是纯文本排版，
    LaTeX 经 latex_plain 转成可读 Unicode 数学文本，网页端仍由 KaTeX 渲染。
    """
    p = db.get(E.Paper, paper_id)
    if not p:
        raise HTTPException(404, "试卷不存在")
    questions = bank_service.get_paper_student_view(
        db, paper_id, reveal=False, mode=p.mode)
    # 双保险：白名单之外若混入任何答案字段，宁可报错也不导出。
    for q in questions:
        assert_no_answer_leak(q)
    scope_label = {"course_only": "仅课程范围",
                   "course_plus_advanced": "课程范围 + 课内提高",
                   "include_extension": "包含扩展学习"}.get(p.scope or "", "")
    meta = {
        "题量": p.item_count, "总分": p.total_score,
        "建议用时": f"{p.estimated_minutes} 分钟" if p.estimated_minutes else "",
        "模式": "模拟测验" if p.mode == "quiz" else "练习模式",
        "考查范围": scope_label,
        "生成时间": (p.created_at or "")[:19].replace("T", " "),
    }
    out = cfg.PROJECT_ROOT / "data" / "exports" / f"paper_{paper_id}.pdf"
    try:
        paper_pdf.render_paper_pdf(
            title=p.title or "练习卷", meta=meta, questions=questions,
            out_path=out)
    except ValueError as e:
        raise HTTPException(409, str(e))
    safe = re.sub(r'[\\/:*?"<>|\r\n]', "_", p.title or "练习卷")[:80]
    return FileResponse(str(out), media_type="application/pdf",
                        filename=f"{safe}.pdf",
                        headers={"X-Paper-Question-Count": str(len(questions))})


@router.post("/papers/{paper_id}/attempts")
def create_attempt(
    paper_id: str,
    _: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    # RC-9：空试卷不能进入作答。历史遗留的 0 题记录保留，但拒绝在此开始答题。
    real_items = db.execute(select(func.count(E.PaperItem.item_id)).where(
        E.PaperItem.paper_id == paper_id)).scalar() or 0
    if real_items == 0:
        exists = db.get(E.Paper, paper_id)
        if exists is None:
            raise HTTPException(404, "试卷不存在")
        raise HTTPException(
            409, "这份试卷没有任何题目（历史遗留的空试卷），无法作答。"
                 "请从题库重新组卷")
    try:
        out = attempt_service.create_attempt(db, paper_id, USER_ID)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    out.update(attempt_service.attempt_state(db, out["attempt_id"]))
    return out


# ------------------------------------------------------------------ attempts
@router.get("/attempts")
def list_attempts(
    _: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    rows = db.execute(select(E.Attempt).where(E.Attempt.user_id == USER_ID)
                      .order_by(desc(E.Attempt.started_at))).scalars().all()
    return {"total": len(rows), "items": [
        {"attempt_id": a.attempt_id, "paper_id": a.paper_id, "status": a.status,
         "mode": a.mode, "started_at": a.started_at, "submitted_at": a.submitted_at,
         "score": a.score, "max_score": a.max_score,
         "manual_pending_count": a.manual_pending_count,
         "diagnosis_status": a.diagnosis_status} for a in rows]}


@router.get("/attempts/{attempt_id}")
def get_attempt(
    attempt_id: str,
    principal: auth_service.Principal = Depends(auth_service.get_principal),
    db: Session = Depends(get_session),
) -> dict:
    auth_service.assert_attempt_access(db, principal, attempt_id)
    try:
        return attempt_service.attempt_state(db, attempt_id)
    except KeyError:
        raise HTTPException(404, "作答记录不存在")


@router.put("/attempts/{attempt_id}/answers/{question_id}")
def save_answer(attempt_id: str, question_id: str, body: dto.AnswerIn,
                principal: auth_service.Principal = Depends(
                    auth_service.get_principal),
                db: Session = Depends(get_session)) -> dict:
    auth_service.assert_attempt_access(db, principal, attempt_id)
    try:
        return attempt_service.save_answer(
            db, attempt_id, question_id, body.response,
            hints_used=body.hints_used,
            solution_viewed=body.solution_viewed)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except attempt_service.ConflictError as e:
        raise HTTPException(409, str(e))


@router.post("/attempts/{attempt_id}/attachments")
def upload_answer_attachment(attempt_id: str, body: dict,
                             principal: auth_service.Principal = Depends(
                                 auth_service.get_principal),
                             db: Session = Depends(get_session)) -> dict:
    """上传手写答案图片。原图是答案本体，OCR 只是辅助。"""
    attempt = db.get(E.Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "答卷不存在")
    auth_service.assert_attempt_access(db, principal, attempt_id)
    question_id = str((body or {}).get("question_id") or "")
    image = (body or {}).get("image") or ""
    if not question_id or not image:
        raise HTTPException(400, "需要 question_id 与 image")
    try:
        return answer_attachment_service.save(
            db, attempt, question_id, image,
            position=int((body or {}).get("position") or 0))
    except answer_attachment_service.AttachmentError as e:
        raise HTTPException(400, str(e))


@router.get("/attempts/{attempt_id}/attachments")
def list_answer_attachments(attempt_id: str,
                            principal: auth_service.Principal = Depends(
                                auth_service.get_principal),
                            db: Session = Depends(get_session)) -> dict:
    auth_service.assert_attempt_access(db, principal, attempt_id)
    return answer_attachment_service.list_for_attempt(db, attempt_id)


@router.get("/attachments/{attachment_id}/file")
def download_answer_attachment(attachment_id: str,
                               principal: auth_service.Principal = Depends(
                                   auth_service.get_principal),
                               db: Session = Depends(get_session)):
    """受控读取：只按附件 ID，不暴露目录，不允许猜路径访问他人图片。"""
    try:
        row, path = answer_attachment_service.file_path(db, attachment_id)
    except KeyError:
        raise HTTPException(404, "附件不存在")
    auth_service.assert_attempt_access(db, principal, row.attempt_id)
    return FileResponse(str(path), media_type=row.mime,
                        headers={"Cache-Control": "private, max-age=3600",
                                 "Content-Disposition": "inline"})


@router.delete("/attachments/{attachment_id}")
def delete_answer_attachment(attachment_id: str,
                             principal: auth_service.Principal = Depends(
                                 auth_service.get_principal),
                             db: Session = Depends(get_session)) -> dict:
    row = db.get(E.AnswerAttachment, attachment_id)
    if row is None:
        raise HTTPException(404, "附件不存在")
    attempt = db.get(E.Attempt, row.attempt_id)
    if attempt is None:
        raise HTTPException(404, "答卷不存在")
    auth_service.assert_attempt_access(db, principal, attempt.attempt_id)
    try:
        return answer_attachment_service.delete(db, attachment_id, attempt)
    except answer_attachment_service.AttachmentError as e:
        raise HTTPException(409, str(e))
    except KeyError:
        raise HTTPException(404, "附件不存在")


@router.post("/attachments/{attachment_id}/ocr")
def ocr_answer_attachment(attachment_id: str,
                          principal: auth_service.Principal = Depends(
                              auth_service.get_principal),
                          db: Session = Depends(get_session)) -> dict:
    """对手写图片做一次真实 OCR。失败也返回 200 + status=failed，原图仍然有效。"""
    row = db.get(E.AnswerAttachment, attachment_id)
    if row is None or not row.active:
        raise HTTPException(404, "附件不存在")
    attempt = db.get(E.Attempt, row.attempt_id)
    if attempt is None:
        raise HTTPException(404, "答卷不存在")
    auth_service.assert_attempt_access(db, principal, attempt.attempt_id)
    if attempt.status == "submitted":
        raise HTTPException(409, "已交卷，不能再识别")
    try:
        return answer_attachment_service.run_ocr(db, row)
    except answer_attachment_service.AttachmentError as e:
        raise HTTPException(400, str(e))


@router.post("/ocr/{ocr_id}/confirm")
def confirm_ocr_result(ocr_id: str, body: dict,
                       principal: auth_service.Principal = Depends(
                           auth_service.get_principal),
                       db: Session = Depends(get_session)) -> dict:
    row = db.get(E.OCRResult, ocr_id)
    if row is None:
        raise HTTPException(404, "识别记录不存在")
    attempt = db.get(E.Attempt, row.attempt_id)
    if attempt is None:
        raise HTTPException(404, "答卷不存在")
    auth_service.assert_attempt_access(db, principal, attempt.attempt_id)
    try:
        return answer_attachment_service.confirm(
            db, ocr_id, str((body or {}).get("text") or ""), attempt)
    except answer_attachment_service.AttachmentError as e:
        raise HTTPException(409, str(e))
    except KeyError:
        raise HTTPException(404, "识别记录不存在")


@router.post("/attempts/{attempt_id}/ocr")
def attempt_answer_ocr(attempt_id: str, body: dict,
                       principal: auth_service.Principal = Depends(
                           auth_service.get_principal),
                       db: Session = Depends(get_session)) -> dict:
    """手写答案照片 → 文字。只转录，不评分；结果由学生核对后自行提交。"""
    attempt = db.get(E.Attempt, attempt_id)
    if attempt is None:
        raise HTTPException(404, "答卷不存在")
    auth_service.assert_attempt_access(db, principal, attempt.attempt_id)
    if attempt.status == "submitted":
        raise HTTPException(409, "已交卷，不能再修改作答")
    question_id = str((body or {}).get("question_id") or "")
    image = (body or {}).get("image") or ""
    if not question_id or not image:
        raise HTTPException(400, "需要 question_id 与 image")
    try:
        out = answer_ocr.transcribe(db, image)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except model_provider.ModelUnavailable as e:
        raise HTTPException(503, str(e))
    return {"attempt_id": attempt_id, "question_id": question_id, **out}


@router.post("/attempts/{attempt_id}/submit")
def submit_attempt(attempt_id: str, body: dto.SubmitIn | None = None,
                   principal: auth_service.Principal = Depends(
                       auth_service.get_principal),
                   db: Session = Depends(get_session)) -> dict:
    auth_service.assert_attempt_access(db, principal, attempt_id)
    try:
        result, newly = attempt_service.submit_attempt(
            db, attempt_id, (body.request_key if body else None))
    except KeyError:
        raise HTTPException(404, "作答记录不存在")
    except attempt_service.ConflictError as e:
        raise HTTPException(409, str(e))
    result["newly_submitted"] = newly
    return JSONResponse(result)  # type: ignore[return-value]


# ------------------------------------------------------------------ wrong book / records
@router.get("/wrong-book")
def wrong_book(db: Session = Depends(get_session)) -> dict:
    """V3.3：同一道题只返回一张复盘卡片，历次作答全部挂在 history 上。

    去重只在**展示分组**层面发生：wrong_book 表一行不删、一行不改，
    `total` 仍是原始错题记录数；`card_count` 是去重后的卡片数。
    指纹取题目自身内容（题型/完整题干/完整选项及顺序/正确答案/条件），
    仅规范化 HTML 实体与空白，不按标题或相似度合并。
    """
    rows = db.execute(select(E.WrongBookEntry).where(
        E.WrongBookEntry.user_id == USER_ID).order_by(
        desc(E.WrongBookEntry.created_at))).scalars().all()
    kp_labels = _knowledge_point_labels(db)
    ctx = selection_basis.Context(db)
    groups: dict[str, list[tuple[Any, dict]]] = {}

    for w in rows:
        q = db.execute(select(E.Question).where(
            E.Question.question_id == w.question_id,
            E.Question.question_version == w.question_version)).scalars().first()
        view = student_view(q, reveal=True) if q else {}
        # 孤儿题没有原题，绝不互相合并：用自身标识兜底。
        fp = (question_fingerprint(bank_service.question_snapshot(q)) if q
              else f"orphan:{w.question_id}:{w.question_version}")
        item = {
            "entry_id": w.entry_id, "attempt_id": w.attempt_id,
            "question_id": w.question_id, "question_version": w.question_version,
            "kp_id": w.kp_id, "family_id": w.family_id,
            "original_response": w.original_response,
            "lost_points": w.lost_points,
            "misconception_candidates": w.misconception_candidates,
            "correction": w.correction, "self_report": w.self_report,
            "status": w.status,
            "status_label": WRONG_STATUS_LABELS.get(w.status, w.status),
            "created_at": w.created_at,
            "material_refs": w.material_refs,
            "question": view if q else None,
            "question_fingerprint": fp,
            "stored_fingerprint": w.question_fingerprint or "",
            # V2.2：选题依据（组卷条件 + 题目来源 + 可跳转的页级出处）
            "selection_basis": selection_basis.build(
                ctx, entry=w, question=view,
                kp_label=kp_labels.get(w.kp_id, {}).get("kp_name", "")),
            **_labels_for(kp_labels, w.kp_id),
        }
        groups.setdefault(fp, []).append((w, item))

    cards: list[dict] = []
    for fp, members in groups.items():
        members.sort(key=lambda pair: (pair[0].created_at or "",
                                      pair[0].entry_id), reverse=True)
        # 「最近一次有效作答」：优先最近一条有真实 response 的记录；
        # 整组都未作答时退回最近一条，绝不推测学生当时选了什么。
        effective = next((m for m in members if m[1]["original_response"]),
                         members[0])
        primary = effective[1]
        canonical_w = members[-1][0]  # 组内最早一条 = 规范记录，稳定不漂移
        primary["attempt_count"] = len(members)
        primary["duplicate_count"] = len(members) - 1
        primary["canonical_question_id"] = canonical_w.question_id
        primary["canonical_entry_id"] = (
            effective[0].canonical_entry_id or canonical_w.entry_id)
        primary["source_question_ids"] = sorted(
            {m[0].question_id for m in members})
        primary["history"] = [{
            "entry_id": m[0].entry_id, "attempt_id": m[0].attempt_id,
            "question_id": m[0].question_id,
            "question_version": m[0].question_version,
            "created_at": m[0].created_at,
            "original_response": m[1]["original_response"],
            "lost_points": m[1]["lost_points"],
            "status": m[1]["status"],
            "status_label": m[1]["status_label"],
        } for m in members]
        cards.append(primary)

    cards.sort(key=lambda x: (x.get("created_at") or "",
                              x.get("entry_id") or ""), reverse=True)
    return {
        "total": len(rows),           # 原始错题记录数，保持不变（兼容旧调用）
        "card_count": len(cards),     # 去重后实际展示的卡片数
        "items": cards,
        "dedup": {
            "raw_count": len(rows),
            "unique_count": len(cards),
            "merged_count": len(rows) - len(cards),
            "basis": question_fingerprint_basis(),
            "note": "只合并展示；历史作答记录与原始来源全部保留，未删除或覆盖任何行",
        },
        "note": "候选误区只是可能解释，不代表已确定学生的心理原因",
    }


@router.post("/wrong-book/retest-paper")
def wrong_retest_paper(body: dict, db: Session = Depends(get_session)) -> dict:
    """错题复测组卷：原题为主 + AI 补足，在当前页面内生成复测卷。"""
    data = body or {}
    try:
        return retest_service.build_retest_paper(
            db,
            entry_ids=[str(x) for x in (data.get("entry_ids") or [])],
            scope=str(data.get("scope") or "all"),
            kp_id=str(data.get("kp_id") or ""),
            count=int(data.get("count") or 5),
            difficulty=str(data.get("difficulty") or "same"),
            use_original=bool(data.get("use_original", True)),
            use_ai=bool(data.get("use_ai", True)),
            user_id=USER_ID)
    except retest_service.RetestError as e:
        raise HTTPException(400, str(e))


@router.post("/wrong-book/{entry_id}/retest")
def wrong_retest_original(entry_id: str,
                          db: Session = Depends(get_session)) -> dict:
    """重做原题：新建答卷，不动原始错误记录。"""
    try:
        return retest_service.retest_original(db, entry_id, USER_ID)
    except retest_service.RetestError as e:
        raise HTTPException(404, str(e))


@router.post("/wrong-book/{entry_id}/similar")
def wrong_generate_similar(entry_id: str, body: dict | None = None,
                           db: Session = Depends(get_session)) -> dict:
    """AI 生成相似题并进入复测；模型不可用时如实返回原因，不返回假题。"""
    count = int((body or {}).get("count") or 1)
    try:
        out = retest_service.generate_similar_questions(
            db, entry_id, count=count, user_id=USER_ID)
    except retest_service.RetestError as e:
        raise HTTPException(400, str(e))
    except model_provider.ModelUnavailable as e:
        raise HTTPException(503, e.message)
    if not out.get("ok"):
        # 200 + ok=false：前端要能区分「没生成」和「接口坏了」
        return out
    return out


@router.get("/wrong-book/{entry_id}/retests")
def wrong_retest_history(entry_id: str,
                         db: Session = Depends(get_session)) -> dict:
    return retest_service.list_wrong_retests(db, entry_id)


@router.patch("/wrong-book/{entry_id}")
def patch_wrong(entry_id: str, body: dto.WrongPatchIn,
                db: Session = Depends(get_session)) -> dict:
    w = db.get(E.WrongBookEntry, entry_id)
    if not w:
        raise HTTPException(404, "错题记录不存在")
    if body.correction is not None:
        w.correction = body.correction
    if body.self_report is not None:
        w.self_report = body.self_report
    if body.status is not None:
        w.status = body.status
    w.updated_at = _now_iso()
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=USER_ID,
        event_type="wrong_book_updated", target_id=entry_id,
        payload=body.model_dump(exclude_none=True)))
    db.commit()
    return {"entry_id": entry_id, "correction": w.correction,
            "self_report": w.self_report, "status": w.status}


@router.get("/learning-records")
def learning_records(limit: int = 100, db: Session = Depends(get_session)) -> dict:
    ev = db.execute(select(E.LearningEvent).where(
        E.LearningEvent.user_id == USER_ID).order_by(
        desc(E.LearningEvent.occurred_at)).limit(limit)).scalars().all()
    att = db.execute(select(E.Attempt).where(E.Attempt.user_id == USER_ID)
                     .order_by(desc(E.Attempt.started_at))).scalars().all()
    return {
        "events": [{"event_id": e.event_id, "event_type": e.event_type,
                    "target_id": e.target_id, "payload": e.payload,
                    "occurred_at": e.occurred_at} for e in ev],
        "attempts": [{"attempt_id": a.attempt_id, "status": a.status,
                      "score": a.score, "max_score": a.max_score,
                      "submitted_at": a.submitted_at} for a in att],
        "note": "事件与作答为事实记录，报告可据此重新计算",
    }


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_session)) -> dict:
    today = _today()
    plans = db.execute(select(E.PlanTask).where(
        E.PlanTask.user_id == USER_ID, E.PlanTask.status == "pending")
        .order_by(E.PlanTask.due_date)).scalars().all()
    retests = db.execute(select(E.RetestTask).where(
        E.RetestTask.user_id == USER_ID, E.RetestTask.status == "pending")
        .order_by(E.RetestTask.due_at)).scalars().all()
    weak = db.execute(select(E.KnowledgeState).where(
        E.KnowledgeState.user_id == USER_ID,
        E.KnowledgeState.state.in_(["需加强", "证据不足"]))).scalars().all()
    attempts = db.execute(select(E.Attempt).where(E.Attempt.user_id == USER_ID)
                          .order_by(desc(E.Attempt.started_at)).limit(8)).scalars().all()
    # 开发期的测试卷（已归档 / 标记为测试样本）不应出现在「继续未完成的练习」里
    hidden_papers = {p.paper_id for p in db.execute(select(E.Paper).where(
        or_(E.Paper.archived.is_(True), E.Paper.is_test_fixture.is_(True)))
    ).scalars().all()}
    attempts = [a for a in attempts if a.paper_id not in hidden_papers]
    in_progress = [a for a in attempts if a.status == "in_progress"]
    return {
        "today": today, "exam_date": cfg.EXAM_DATE,
        "todo": [{"task_id": t.task_id, "title": t.title, "minutes": t.minutes,
                  "due_date": t.due_date, "kind": t.kind} for t in plans[:10]],
        "pending_retests": [{"task_id": r.task_id, "kp_id": r.kp_id,
                             "due_at": r.due_at, "kind": r.kind}
                            for r in retests[:10]],
        "weak_points": [{"kp_id": w.kp_id, "state": w.state,
                         "evidence_count": w.evidence_count,
                         "explanation": w.explanation} for w in weak],
        "recent_attempts": [{"attempt_id": a.attempt_id, "paper_id": a.paper_id,
                             "status": a.status, "score": a.score,
                             "max_score": a.max_score,
                             "started_at": a.started_at} for a in attempts],
        "continue_attempt": ({"attempt_id": in_progress[0].attempt_id,
                              "paper_id": in_progress[0].paper_id}
                             if in_progress else None),
        "model_status": "not_configured" if not cfg.MODEL_API_KEY else "configured",
    }


# ------------------------------------------------------------------ materials
@router.get("/materials")
def materials(db: Session = Depends(get_session)) -> dict:
    mf = cfg.MANIFEST_DIR / "materials_manifest.json"
    if not mf.exists():
        return {
            "summary": {"total_files": 0, "distinct_content_hashes": 0,
                        "duplicate_groups": 0, "excluded_paths": [],
                        "unsupported_or_unreadable": []},
            "total": 0, "items": [], "documents_in_db": 0,
            "note": "尚未生成课程资料清单；仍可从“我的资料”导入文件",
        }
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    versions = db.execute(select(E.DocumentVersion)).scalars().all()
    vmap = {v.sha256: v for v in versions}
    docs = db.execute(select(E.Document)).scalars().all()
    chunk_counts: dict[str, int] = {}
    for c in db.execute(select(E.Chunk.document_id, E.Chunk.chunk_id)).all():
        chunk_counts[c[0]] = chunk_counts.get(c[0], 0) + 1
    items = []
    for r in manifest["records"]:
        v = vmap.get(r["sha256"])
        item = dict(r)
        item["document_id"] = "doc_" + r["sha256"][:14]
        item["parse_status"] = v.parse_status if v else r["processing_status"]
        item["quality_summary"] = v.quality_summary if v else {}
        item["chunk_count"] = chunk_counts.get(item["document_id"], 0)
        items.append(item)
    return {"summary": manifest["summary"], "total": len(items), "items": items,
            "documents_in_db": len(docs),
            "note": "文件已导入 / 文本可提取 / 内容已完整核验 是三种不同状态"}


@router.get("/materials/{doc_id}/page/{page_no}/image")
def material_page_image(doc_id: str, page_no: int) -> FileResponse:
    if not re.fullmatch(r"doc_[0-9a-f]{14}", doc_id) or page_no < 1:
        raise HTTPException(400, "非法参数")
    db_path = cfg.MATERIAL_ROOT
    from backend.app.models.base import SessionLocal
    s = SessionLocal()
    try:
        d = s.get(E.Document, doc_id)
        if not d:
            raise HTTPException(404, "文档不存在")
        rel = parsers.ensure_page_image(doc_id, d.rel_path, page_no)
    finally:
        s.close()
    if not rel:
        raise HTTPException(404, "页图不可用")
    full = cfg.resolve_storage_ref(rel)
    if not full.exists():
        raise HTTPException(404, "页图文件不存在")
    return FileResponse(str(full), media_type="image/png")


@router.get("/materials/{doc_id}/chunks")
def material_chunks(doc_id: str, kind: str = "", limit: int = 80,
                    db: Session = Depends(get_session)) -> dict:
    stmt = select(E.Chunk).where(E.Chunk.document_id == doc_id)
    if kind:
        stmt = stmt.where(E.Chunk.kind == kind)
    rows = db.execute(stmt.order_by(E.Chunk.chunk_id).limit(limit)).scalars().all()
    return {"total": len(rows), "items": [
        {"chunk_id": c.chunk_id, "kind": c.kind, "title": c.title,
         "unit_refs": c.unit_refs, "source_refs": c.source_refs,
         "assets": c.assets, "review_status": c.review_status,
         "quality": c.quality,
         "content_raw": c.content_raw[:1500],
         "content_normalized": c.content_normalized[:1500]} for c in rows]}


@router.get("/search")
def search(q: str = Query("", min_length=0), kp_id: str = "",
           chapter: str = "", limit: int = 12,
           db: Session = Depends(get_session)) -> dict:
    return retrieval.keyword_search(db, q, kp_ids=[kp_id] if kp_id else None,
                                    chapter=chapter, limit=limit)


PLATFORM_LABELS = {"bilibili": "B 站", "douyin": "抖音", "zhihu": "知乎",
                   "csdn": "CSDN", "blog": "技术博客", "docs": "官方文档",
                   "internal": "站内检索", "other": "其他"}

# V3.4（RC-6）：历史数据里同一平台存在多种写法（bilibili / B站 / B 站），
# 而筛选是精确匹配，于是 `platform=bilibili` 会漏掉标成「B站」的条目，
# 平台下拉里也看不到未登记的取值。统一在这里归一，过滤与计数都走它。
PLATFORM_ALIASES = {
    "bilibili": "bilibili", "b站": "bilibili", "b 站": "bilibili",
    "哔哩哔哩": "bilibili", "bili": "bilibili",
    "douyin": "douyin", "抖音": "douyin",
    "zhihu": "zhihu", "知乎": "zhihu",
    "csdn": "csdn", "csdn博客": "csdn",
    "blog": "blog", "技术博客": "blog", "博客": "blog",
    "docs": "docs", "官方文档": "docs", "文档": "docs",
    "internal": "internal", "站内检索": "internal", "站内": "internal",
}


def normalize_platform(raw: str | None) -> str:
    """把平台的多种历史写法归一成稳定标识。无法识别时一律归为 other。"""
    key = (raw or "").strip()
    if not key:
        return "other"
    return PLATFORM_ALIASES.get(key.lower(), "other")
# 三段状态分开：能不能打开 / 内容核到什么程度 / 与课程的关系
SCOPE_LABELS = {"in_scope": "课程范围内", "partial": "部分拓展",
                "extension": "拓展学习", "unassessed": "尚未评估"}
MATCH_LABELS = {"high": "高度匹配", "partial": "部分匹配",
                "extension": "拓展内容", "unassessed": "待确认"}
ACCESS_LABELS = {"ok": "可访问", "login_required": "需登录",
                 "restricted": "访问受限", "dead": "链接失效",
                 "unchecked": "待检查"}
CONTENT_LABELS = {"title_only": "仅核验标题", "intro_checked": "已核验简介",
                  "full_text": "已阅读正文", "subtitle_checked": "已核验字幕"}


@router.get("/resources/{resource_id}/cover")
def resource_cover(resource_id: str):
    """资源封面：只按资源 ID 提供，文件名不外露；没有就 404 让前端降级。"""
    path = cfg.ASSET_DIR / "covers" / f"{resource_id}.jpg"
    if not path.exists():
        raise HTTPException(404, "该资源没有本地封面")
    return FileResponse(str(path), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/resources")
def resources(kp_id: str = "", platform: str = "", scope: str = "",
              q: str = "", db: Session = Depends(get_session)) -> dict:
    """在线资源列表：只登记可访问入口与「核验到哪一步」，不猜视频正文。"""
    from backend.app.services.classroom_content import is_search_entry
    rows = db.execute(select(E.Resource)).scalars().all()
    kp_labels = _knowledge_point_labels(db)
    platform_want = normalize_platform(platform) if platform else ""
    items = []
    for r in rows:
        if kp_id and kp_id not in (r.kp_ids or []):
            continue
        if platform_want and normalize_platform(r.platform) != platform_want:
            continue
        if scope and (r.scope or "unconfirmed") != scope:
            continue
        if q:
            hay = " ".join([r.title or "", r.intro or "", r.reason or "",
                            r.author or ""]).lower()
            if q.lower() not in hay:
                continue
        items.append({
            "resource_id": r.resource_id,
            "platform": normalize_platform(r.platform),
            "platform_raw": r.platform,
            "platform_label": PLATFORM_LABELS.get(normalize_platform(r.platform),
                                                  r.platform),
            "url": r.url, "title": r.title, "kp_ids": r.kp_ids,
            "kp_names": [kp_labels.get(k, {}).get("kp_name", k)
                         for k in (r.kp_ids or [])],
            "author": r.author, "content_type": r.content_type,
            "intro": r.intro or r.reason, "reason": r.reason,
            "match_level": r.match_level or "unassessed",
            "match_label": MATCH_LABELS.get(r.match_level or "", "待确认"),
            "scope": ("unassessed" if (r.scope or "") in ("", "unconfirmed")
                      else r.scope),
            "scope_label": SCOPE_LABELS.get(
                r.scope if r.scope != "unconfirmed" else "unassessed", "尚未评估"),
            "access_status": r.access_status or (
                "ok" if r.verified else "unknown"),
            "access_label": ACCESS_LABELS.get(r.access_status or "", "未核验"),
            "thumbnail": r.thumbnail, "checked_date": r.checked_date,
            "cover_source": r.cover_source or "",
            "verified_scope": r.verified_scope or "title_only",
            "verifiable_segment": r.verifiable_segment,
            "verified": r.verified, "source_type": r.source_type,
            "local_ref": r.local_ref,
            # 双语标题：卡片主标题用中文，原始标题留作副标题
            "title_zh": r.title_zh or r.title,
            "original_title": r.original_title or r.title,
            "content_status": r.content_status or "title_only",
            "content_label": CONTENT_LABELS.get(
                r.content_status or "title_only", "仅核验标题"),
        })
    # 课程范围内、匹配度高、有简介的排前面；入口页不占前排
    scope_order = {"in_scope": 0, "extension": 1, "unconfirmed": 2}
    items.sort(key=lambda x: (
        scope_order.get(x["scope"], 3),
        0 if x["thumbnail"] else 1,          # 有真实封面的排前面
        0 if x["content_type"] == "video" else 1,
        0 if x["match_level"] == "high" else 1,
        x["title"]))
    by_platform: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    for r in rows:
        p = normalize_platform(r.platform)
        by_platform[p] = by_platform.get(p, 0) + 1
        by_scope[r.scope or "unconfirmed"] = by_scope.get(
            r.scope or "unconfirmed", 0) + 1
    for item in items:
        if is_search_entry(item.get("url"), item.get("title")):
            item.update(verified=False, verified_scope="search_entry", content_type="search_entry",
                        content_label="检索导航，未核验具体内容", verification_note="搜索入口不代表具体课程或文章已验证")
    return {"total": len(items), "items": items,
            "all_total": len(rows),
            "platforms": [{"value": k, "label": v, "count": by_platform.get(k, 0)}
                          for k, v in PLATFORM_LABELS.items() if by_platform.get(k)],
            "scopes": [{"value": k, "label": v, "count": by_scope.get(k, 0)}
                       for k, v in SCOPE_LABELS.items() if by_scope.get(k)],
            "note": ("外部资源只登记可访问入口与推荐理由；没有读到字幕或正文的，"
                     "核验深度标为「仅标题与简介」，不描述视频具体内容")}


# ------------------------------------------------------------------ read-only views for stage 2 data
@router.get("/diagnoses")
def diagnoses(level: str = "", target_id: str = "",
              limit: int | None = Query(None, ge=1, le=500),
              offset: int = Query(0, ge=0),
              db: Session = Depends(get_session)) -> dict:
    stmt = select(E.Diagnosis).where(E.Diagnosis.user_id == USER_ID)
    if level:
        stmt = stmt.where(E.Diagnosis.level == level)
    if target_id:
        stmt = stmt.where(E.Diagnosis.target_id == target_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(desc(E.Diagnosis.created_at)).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = db.execute(stmt).scalars().all()
    return {"total": total, "items": [
        {"diagnosis_id": d.diagnosis_id, "level": d.level,
         "target_id": d.target_id, "payload": d.payload,
         "evidence_refs": d.evidence_refs, "uncertainties": d.uncertainties,
         "next_actions": d.next_actions, "skill_version": d.skill_version,
         "run_id": d.run_id, "engine": d.engine, "created_at": d.created_at}
        for d in rows], "returned": len(rows), "limit": limit, "offset": offset}


@router.get("/reports/three-layer")
def three_layer_report(attempt_id: str = "",
                       recent: int = Query(20, ge=1, le=100),
                       db: Session = Depends(get_session)) -> dict:
    """题目 / 试卷 / 历次试卷 三层诊断，一次取全。

    课程要求的三层报告对应三张表：`diagnoses.level=question/paper/history`。
    这里把选中的那一次作答的三层内容组装成一个响应，前端不再自己拼数据。
    """
    kp_labels = _knowledge_point_labels(db)
    papers = {p.paper_id: p for p in db.execute(select(E.Paper)).scalars().all()}

    submitted = db.execute(select(E.Attempt).where(
        E.Attempt.user_id == USER_ID, E.Attempt.status == "submitted",
        E.Attempt.submitted_at != "").order_by(
        desc(E.Attempt.submitted_at)).limit(recent)).scalars().all()
    attempts = []
    for a in submitted:
        p = papers.get(a.paper_id)
        attempts.append({
            "attempt_id": a.attempt_id,
            "paper_id": a.paper_id,
            "title": (p.title if p else "试卷已删除"),
            "item_count": (p.item_count if p else 0),
            "score": a.score, "max_score": a.max_score,
            "submitted_at": a.submitted_at or a.started_at,
            "diagnosis_status": a.diagnosis_status,
        })
    known = {item["attempt_id"] for item in attempts}
    # 默认选「最近一次**已经有试卷层诊断**的作答」：之前直接取最近一次，
    # 若那次是 diagnosis_status=not_requested，页面就是一片空白，
    # 看起来像诊断功能坏了。没有可用诊断时才退回最近一次，并显式告知。
    diagnosed: set[str] = set()
    if known:
        rows = db.execute(select(E.Diagnosis.target_id).where(
            E.Diagnosis.user_id == USER_ID, E.Diagnosis.level == "paper",
            E.Diagnosis.target_id.in_(known))).scalars().all()
        diagnosed = set(rows)
    with_diagnosis = [a["attempt_id"] for a in attempts
                      if a["attempt_id"] in diagnosed]
    if attempt_id in known:
        selected = attempt_id
    elif with_diagnosis:
        selected = with_diagnosis[0]
    else:
        selected = attempts[0]["attempt_id"] if attempts else ""
    diagnosis_missing = bool(selected) and selected not in diagnosed

    def _diag(level: str, target: str):
        row = db.execute(select(E.Diagnosis).where(
            E.Diagnosis.user_id == USER_ID, E.Diagnosis.level == level,
            E.Diagnosis.target_id == target).order_by(
            desc(E.Diagnosis.created_at))).scalars().first()
        if not row:
            return None
        return {"diagnosis_id": row.diagnosis_id, "level": row.level,
                "target_id": row.target_id, "payload": row.payload,
                "evidence_refs": row.evidence_refs,
                "uncertainties": row.uncertainties,
                "next_actions": row.next_actions,
                "skill_version": row.skill_version, "run_id": row.run_id,
                "engine": row.engine, "created_at": row.created_at}

    question_layer: list[dict] = []
    if selected:
        rows = db.execute(select(E.Diagnosis).where(
            E.Diagnosis.user_id == USER_ID, E.Diagnosis.level == "question",
            E.Diagnosis.target_id.like(selected + ":%")).order_by(
            desc(E.Diagnosis.created_at))).scalars().all()
        qids = [r.target_id.split(":", 1)[1] for r in rows]
        qmap: dict[str, E.Question] = {}
        if qids:
            for q in db.execute(select(E.Question).where(
                    E.Question.question_id.in_(qids))).scalars().all():
                prev = qmap.get(q.question_id)
                if prev is None or (q.question_version or 0) > (prev.question_version or 0):
                    qmap[q.question_id] = q
        for row in rows:
            qid = row.target_id.split(":", 1)[1]
            q = qmap.get(qid)
            payload = row.payload or {}
            observed = payload.get("observed") or {}
            explanation = payload.get("model_explanation") or {}
            kp_id = (q.main_kp_id if q else "") or observed.get("kp_id") or ""
            question_layer.append({
                "question_id": qid,
                "question_type": (q.question_type if q else observed.get("question_type") or ""),
                "stem": (q.stem if q else ""),
                "prompt_prefix": display_prompt_prefix(
                    (q.question_type if q else "") or "", (q.stem if q else "") or ""),
                "kp_id": kp_id,
                **_labels_for(kp_labels, kp_id),
                "score": observed.get("awarded_score"),
                "max_score": observed.get("max_score"),
                "correct": observed.get("correct"),
                "independent": observed.get("independent_evidence"),
                "hints_used": observed.get("hints_used"),
                "response": observed.get("response"),
                "candidate_causes": (payload.get("candidate_causes")
                                     or explanation.get("candidate_causes") or []),
                "evidence": payload.get("evidence") or [],
                "next_verification": (payload.get("next_verification")
                                      or explanation.get("next_verification") or []),
                "explanation": explanation,
                "uncertainties": row.uncertainties or [],
                "next_actions": row.next_actions or [],
                "engine": row.engine, "skill_version": row.skill_version,
                "created_at": row.created_at,
            })

    return {
        "attempts": attempts,
        "selected_attempt_id": selected,
        "diagnosis_missing": diagnosis_missing,
        "diagnosis_missing_note": (
            "这次作答还没有跑过诊断：点『运行诊断』会执行 diagnose → 三层次 → "
            "推荐 → 验证，结果写入诊断记录；未测到的层次显示『未测』，不记为 0 分。"
            if diagnosis_missing else ""),
        "diagnosed_attempt_ids": sorted(diagnosed),
        "question": question_layer,
        "paper": _diag("paper", selected) if selected else None,
        "history": _diag("history", USER_ID),
        "knowledge_points": kp_labels,
        "criteria": {
            "min_evidence": cfg.STATE_MIN_EVIDENCE,
            "min_families": cfg.STATE_MIN_FAMILIES,
            "stable_days": cfg.STATE_STABLE_MIN_DAYS,
            "retest_intervals_days": list(cfg.RETEST_INTERVALS_DAYS),
            "states": ["未测", "证据不足", "需加强", "初步通过", "阶段稳定"],
            "rule": ("状态按有效证据的时间顺序计算：提示、已见答案、同题族重复"
                     "与待核查成绩不参与状态提升；未测不等于不会。"),
        },
    }


@router.get("/knowledge-states")
def knowledge_states(db: Session = Depends(get_session)) -> dict:
    rows = db.execute(select(E.KnowledgeState).where(
        E.KnowledgeState.user_id == USER_ID)).scalars().all()
    kp_labels = _knowledge_point_labels(db)
    return {"total": len(rows), "items": [
        {"kp_id": r.kp_id, "state": r.state,
         "state_label": KNOWLEDGE_STATE_LABELS.get(r.state, r.state),
         "status_label": KNOWLEDGE_STATE_LABELS.get(r.state, r.state),
         "evidence_count": r.evidence_count,
         "independent_family_count": r.independent_family_count,
         "time_span_days": r.time_span_days, "hint_count": r.hint_count,
         "last_evidence_at": r.last_evidence_at, "explanation": r.explanation,
         **_labels_for(kp_labels, r.kp_id)}
        for r in rows],
        # 掌握判定阈值随接口返回，界面不必自己写死一套数字。
        "criteria": {
            "min_evidence": cfg.STATE_MIN_EVIDENCE,
            "min_families": cfg.STATE_MIN_FAMILIES,
            "stable_days": cfg.STATE_STABLE_MIN_DAYS,
            "retest_intervals_days": list(cfg.RETEST_INTERVALS_DAYS),
        }}


@router.get("/retest-tasks")
def retest_tasks(db: Session = Depends(get_session)) -> dict:
    rows = db.execute(select(E.RetestTask).where(
        E.RetestTask.user_id == USER_ID).order_by(
        desc(E.RetestTask.created_at))).scalars().all()
    kp_labels = _knowledge_point_labels(db)
    return {"total": len(rows), "items": [
        {"task_id": r.task_id, "kp_id": r.kp_id, "from_wrong_id": r.from_wrong_id,
         "family_id": r.family_id, "kind": r.kind,
         "kind_label": RETEST_KIND_LABELS.get(r.kind, r.kind),
         "due_at": r.due_at, "status": r.status,
         "status_label": RETEST_STATUS_LABELS.get(r.status, r.status),
         "paper_id": r.paper_id, "result": r.result,
         "created_at": r.created_at,
         **_labels_for(kp_labels, r.kp_id)} for r in rows]}


@router.get("/study-plan")
def study_plan(db: Session = Depends(get_session)) -> dict:
    plan = db.execute(select(E.StudyPlan).where(
        E.StudyPlan.user_id == USER_ID, E.StudyPlan.active.is_(True))
        .order_by(desc(E.StudyPlan.created_at))).scalars().first()
    tasks: list[dict] = []
    if plan:
        kp_labels = _knowledge_point_labels(db)
        rows = db.execute(select(E.PlanTask).where(
            E.PlanTask.plan_id == plan.plan_id).order_by(
            E.PlanTask.due_date)).scalars().all()
        tasks = [{"task_id": t.task_id, "kp_id": t.kp_id, "title": t.title,
                  "kind": t.kind,
                  "kind_label": PLAN_KIND_LABELS.get(t.kind, t.kind),
                  "material_ref": t.material_ref,
                  "question_ids": t.question_ids, "minutes": t.minutes,
                  "due_date": t.due_date,
                  "completion_criteria": t.completion_criteria,
                  "rationale": t.rationale, "status": t.status,
                  "status_label": PLAN_STATUS_LABELS.get(t.status, t.status),
                  "completed_at": t.completed_at,
                  **_labels_for(kp_labels, t.kp_id)} for t in rows]
    return {"plan": ({"plan_id": plan.plan_id, "exam_date": plan.exam_date,
                      "daily_minutes": plan.daily_minutes,
                      "created_at": plan.created_at, "payload": plan.payload}
                     if plan else None),
            "tasks": tasks,
            "total_minutes": sum(t["minutes"] for t in tasks if t["status"] == "pending")}


@router.get("/jobs")
def jobs(limit: int = Query(50, ge=1, le=500),
         offset: int = Query(0, ge=0),
         job_id: str | None = None,
         db: Session = Depends(get_session)) -> dict:
    """Return jobs newest-first. `total` is the real row count (not the page
    size); use job_id / offset to reach older jobs that fall outside the page.
    """
    stmt = select(E.Job)
    count_stmt = select(func.count()).select_from(E.Job)
    if job_id:
        stmt = stmt.where(E.Job.job_id == job_id)
        count_stmt = count_stmt.where(E.Job.job_id == job_id)
    total = int(db.execute(count_stmt).scalar_one() or 0)
    rows = db.execute(stmt.order_by(desc(E.Job.created_at))
                      .offset(offset).limit(limit)).scalars().all()
    return {"total": total, "returned": len(rows),
            "limit": limit, "offset": offset, "items": [
        {"job_id": j.job_id, "job_type": j.job_type, "status": j.status,
         "stage": j.stage, "progress": j.progress, "error": j.error,
         "idempotency_key": j.idempotency_key, "attempts": j.attempts,
         "created_at": j.created_at, "updated_at": j.updated_at} for j in rows]}


@router.get("/skill-runs")
def skill_runs(limit: int = Query(50, ge=1, le=500),
               offset: int = Query(0, ge=0),
               db: Session = Depends(get_session)) -> dict:
    total = int(db.execute(
        select(func.count()).select_from(E.SkillRun)).scalar_one() or 0)
    rows = db.execute(select(E.SkillRun).order_by(desc(E.SkillRun.started_at))
                      .offset(offset).limit(limit)).scalars().all()
    return {"total": total, "returned": len(rows),
            "limit": limit, "offset": offset, "items": [
        {"run_id": r.run_id, "skill_name": r.skill_name,
         "skill_version": r.skill_version, "status": r.status,
         "engine": r.engine, "thread_id": r.thread_id, "job_id": r.job_id,
         "prompt_version": r.prompt_version, "model": r.model,
         "error": r.error, "duration_ms": r.duration_ms,
         "started_at": r.started_at, "finished_at": r.finished_at,
         "output_keys": sorted((r.output_payload or {}).keys())} for r in rows]}


@router.get("/skills")
def skills() -> dict:
    try:
        from backend.app.skills.registry import REGISTRY
    except ImportError:
        return {"total": 0, "items": [], "status": "stage2_pending",
                "note": "六个业务 skill 与 LangGraph 尚未接入（阶段2）"}
    return {"total": len(REGISTRY), "status": "active", "items": [
        {"name": name, "version": spec.version,
         "requires_model": spec.requires_model,
         "description": spec.description,
         "input_schema": spec.input_schema,
         "output_schema": spec.output_schema,
         "prompt_version": spec.prompt_version}
        for name, spec in REGISTRY.items()]}


@router.post("/skills/learning_diagnosis/run")
def run_learning_diagnosis(body: dict, db: Session = Depends(get_session)) -> dict:
    """学习闭环入口：诊断 → 三层 → 推荐 → （可选）出题 → 复测状态。"""
    attempt_id = str((body or {}).get("attempt_id") or "")
    if not attempt_id:
        # 「一键诊断」不要求页面先选答卷：默认取最近一次已交卷的记录
        latest = db.execute(select(E.Attempt).where(
            E.Attempt.user_id == USER_ID,
            E.Attempt.status == "submitted").order_by(
            desc(E.Attempt.submitted_at))).scalars().first()
        if latest is None:
            raise HTTPException(400, "还没有已交卷的作答记录，先做一套卷再来诊断")
        attempt_id = latest.attempt_id
    try:
        return skill_runtime.run_skill(
            db, "learning_diagnosis",
            {"attempt_id": attempt_id,
             "generate_questions": bool((body or {}).get("generate_questions")),
             "question_count": int((body or {}).get("question_count") or 2),
             "use_model": (body or {}).get("use_model", True)},
            user_id=USER_ID)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/skills/learning_diagnosis/graph")
def run_learning_graph(body: dict, db: Session = Depends(get_session)) -> dict:
    """LangGraph 版学习闭环：同一套 skill，按图执行并返回节点轨迹。"""
    from backend.app.skills.graphs.learning_diagnosis_graph import run_graph
    attempt_id = str((body or {}).get("attempt_id") or "")
    if not attempt_id:
        latest = db.execute(select(E.Attempt).where(
            E.Attempt.user_id == USER_ID,
            E.Attempt.status == "submitted").order_by(
            desc(E.Attempt.submitted_at))).scalars().first()
        if latest is None:
            raise HTTPException(400, "还没有已交卷的作答记录")
        attempt_id = latest.attempt_id
    run_id = E.new_id("run")
    out = run_graph(
        db, attempt_id, user_id=USER_ID,
        want_questions=bool((body or {}).get("generate_questions")),
        question_count=int((body or {}).get("question_count") or 2),
        use_model=(body or {}).get("use_model", True), run_id=run_id)
    # 执行记录落库，便于追溯"这张图什么时候跑过、结果如何"
    db.add(E.SkillRun(
        run_id=run_id, skill_name="learning_diagnosis_graph",
        skill_version="v3.0/graph-1.0", thread_id=f"graph:{run_id}",
        user_id=USER_ID, status=out.get("status", "succeeded"),
        engine="langgraph", input_payload={"attempt_id": attempt_id},
        output_payload={"trace": out.get("trace"),
                        "errors": out.get("errors")},
        prompt_version="", model="", started_at=E.utcnow(),
        finished_at=E.utcnow()))
    db.commit()
    return out


@router.get("/skills/runs")
def skill_runs(name: str = "", limit: int = Query(20, ge=1, le=100),
               db: Session = Depends(get_session)) -> dict:
    """每次 skill 调用的可追溯记录。"""
    stmt = select(E.SkillRun).where(E.SkillRun.user_id == USER_ID)
    if name:
        stmt = stmt.where(E.SkillRun.skill_name == name)
    rows = db.execute(stmt.order_by(desc(E.SkillRun.started_at))
                      .limit(limit)).scalars().all()
    return {"total": len(rows), "items": [
        {"run_id": r.run_id, "skill": r.skill_name, "status": r.status,
         "engine": r.engine, "model": r.model,
         "skill_version": r.skill_version, "started_at": r.started_at,
         "finished_at": r.finished_at}
        for r in rows]}


# ------------------------------------------------------------------ admin / verification helpers
@router.post("/admin/regrade")
def regrade(body: dto.RegradeIn, db: Session = Depends(get_session)) -> dict:
    return attempt_service.regrade_question(
        db, body.question_id, body.new_answer_payload, body.reason,
        new_rubric=body.new_rubric)


@router.post("/admin/questions/{question_id}/revise")
def revise_question(question_id: str, body: dto.ReviseQuestionIn,
                    db: Session = Depends(get_session)) -> dict:
    try:
        return bank_service.revise_question(db, question_id, body.patch)
    except KeyError:
        raise HTTPException(404, "题目不存在")


@router.post("/admin/questions/{question_id}/verify")
def verify_question(question_id: str, body: dto.VerifyQuestionIn,
                    db: Session = Depends(get_session)) -> dict:
    """OPTIONAL human cross-check / correction channel.

    This is NOT a prerequisite for using a question: AI reference answers
    that pass the automatic checks are published without human sign-off.
    This endpoint exists for the '答案有疑问' workflow and for source
    statements a human wants to certify. It creates a new question version
    so published papers keep their immutable snapshots.
    """
    q = db.execute(select(E.Question).where(
        E.Question.question_id == question_id).order_by(
        E.Question.question_version.desc())).scalars().first()
    if not q:
        raise HTTPException(404, "题目不存在")
    if not q.answer_payload and not body.answer_payload:
        raise HTTPException(400, "该题没有答案内容，不能标为已核验")
    patch = {
        "answer_status": "verified",
        "review_status": "verified",
        "answer_origin": "human_cross_check",
        "notes": f"人工复核记录：{body.cross_check.strip()}",
    }
    if body.answer_payload is not None:
        patch["answer_payload"] = body.answer_payload
    if body.explanation is not None:
        patch["explanation"] = body.explanation
    out = bank_service.revise_question(db, question_id, patch)
    db.add(E.LearningEvent(
        event_id=E.new_id("evt"), user_id=USER_ID,
        event_type="question_verified", target_id=question_id,
        payload={"version": out["new_version"],
                 "cross_check": body.cross_check.strip()}))
    db.commit()
    return {**out, "cross_check_recorded": True,
            "human_confirmation_required": False,
            "note": "已生成新版本并停用旧版本；已发布试卷仍使用原快照。"
                    "人工复核是可选通道，不是题目可用的前置条件"}


# ------------------------------------------------------------------ home / about
@router.get("/home/overview")
def home_overview(sort: str = "exam", db: Session = Depends(get_session)) -> dict:
    """High-frequency points, today's tasks and recent activity.

    Every number is computed from the project database; the rules are returned
    with the payload so the UI can explain what "已复习次数" means instead of
    showing an unexplained figure.
    """
    out = home_service.overview(db, sort=sort)
    out["model_configured"] = model_provider.chat_status()["configured"]
    return out


@router.get("/home/daily-three")
def home_daily_three(date: str = "", db: Session = Depends(get_session)) -> dict:
    """V2.2 每日 3 题：选择题 / 名词解释 / 公式类简单题，各一道。"""
    return daily_service.daily_three(db, on_date=date or None,
                                     user_id=USER_ID)


@router.post("/home/daily-three/start")
def home_daily_three_start(body: dict | None = None,
                           db: Session = Depends(get_session)) -> dict:
    """兼容旧入口：生成试卷并跳转到答题页。首页现在走 /submit 就地交卷。"""
    try:
        return daily_service.start_daily(
            db, on_date=(body or {}).get("date") or None, user_id=USER_ID)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/home/daily-three/refresh")
def home_daily_three_refresh(body: dict | None = None,
                             db: Session = Depends(get_session)) -> dict:
    """「换一组」：重新从题库随机抽取三题，旧题组及其作答记录保留。"""
    return daily_service.refresh_daily(
        db, on_date=(body or {}).get("date") or None, user_id=USER_ID)


@router.post("/home/daily-three/submit")
def home_daily_three_submit(body: dict,
                            db: Session = Depends(get_session)) -> dict:
    """首页就地交卷：真实评分后返回正确答案与解析，不需要跳转页面。

    提交前 `/home/daily-three` 不返回任何答案内容；只有这里交卷之后才揭示。
    """
    set_id = str((body or {}).get("set_id") or "").strip()
    answers = (body or {}).get("answers") or {}
    if not set_id:
        raise HTTPException(400, "缺少 set_id")
    if not isinstance(answers, dict):
        raise HTTPException(400, "answers 必须是 题目id -> 作答内容 的对象")
    try:
        return daily_service.submit_daily(db, set_id=set_id, answers=answers,
                                          user_id=USER_ID)
    except KeyError:
        raise HTTPException(404, "这一组题不存在或已失效，请刷新后重试")
    except ValueError as e:
        raise HTTPException(409, str(e))
    except attempt_service.ConflictError as e:
        raise HTTPException(409, str(e))


@router.get("/search/all")
def search_all(q: str = "", db: Session = Depends(get_session)) -> dict:
    """Global search grouped as 考点 / 试卷 / 资料 / 题目 (no internal ids shown)."""
    return home_service.search_all(db, q)


@router.get("/meta/about")
def meta_about(db: Session = Depends(get_session)) -> dict:
    """Facts for 关于我们: real version, real storage, real model configuration."""
    return home_service.about_facts(db)


# ------------------------------------------------------------------ 个人中心
@router.get("/me/papers")
def me_papers(keyword: str = "", status: str = "", source: str = "",
              include_archived: bool = False,
              db: Session = Depends(get_session)) -> dict:
    return home_service.my_papers(db, keyword=keyword, status=status,
                                  source=source,
                                  include_archived=include_archived)


@router.get("/me/attempts")
def me_attempts(keyword: str = "",
                db: Session = Depends(get_session)) -> dict:
    return home_service.my_attempts(db, keyword=keyword)


@router.get("/me/materials")
def me_materials(db: Session = Depends(get_session)) -> dict:
    return home_service.my_materials(db)


@router.get("/me/favorites")
def me_favorites(db: Session = Depends(get_session)) -> dict:
    return home_service.list_favorites(db)


@router.post("/me/favorites")
def create_favorite(body: dict, db: Session = Depends(get_session)) -> dict:
    try:
        return home_service.add_favorite(
            db, kind=str(body.get("kind", "")),
            target_id=str(body.get("target_id", "")),
            title=str(body.get("title", "")), note=str(body.get("note", "")))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/me/favorites/{entry_id}")
def delete_favorite(entry_id: str, db: Session = Depends(get_session)) -> dict:
    try:
        return home_service.remove_favorite(db, entry_id)
    except KeyError:
        raise HTTPException(404, "收藏不存在")


@router.get("/me/settings")
def me_settings(db: Session = Depends(get_session)) -> dict:
    return home_service.get_settings(db)


@router.patch("/me/settings")
def patch_me_settings(body: dict, db: Session = Depends(get_session)) -> dict:
    try:
        return home_service.save_settings(db, body or {})
    except ValueError as e:
        raise HTTPException(400, str(e))
