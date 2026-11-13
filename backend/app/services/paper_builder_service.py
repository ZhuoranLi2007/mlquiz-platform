"""Teacher-facing question bank and paper draft builder.

This module deliberately reuses ``Question -> PaperItem.snapshot -> Paper ->
AssessmentVersion``.  A paper is editable only before an AssessmentVersion or
Attempt references it.  Publishing therefore remains the single immutable
boundary used by the classroom workflow.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services import answer_pipeline, bank_service
from backend.app.services.question_dto import student_view
from backend.app.services.scoring import effective_type


QUIZ_SCOPE = {"in_scope", "in_scope_advanced"}
REVIEW_READY = {"verified", "auto_checked"}
SOURCE_LABELS = {
    "teacher": "教师原题",
    "ai": "AI 生成题",
    "imported": "其他导入题目",
}


def _latest_questions(db: Session, *, include_inactive: bool = True) -> list[E.Question]:
    rows = db.execute(select(E.Question).order_by(
        E.Question.question_id, E.Question.question_version.desc()
    )).scalars().all()
    latest: dict[str, E.Question] = {}
    for row in rows:
        latest.setdefault(row.question_id, row)
    result = list(latest.values())
    if not include_inactive:
        result = [q for q in result if q.active]
    return result


def source_category(question: E.Question | dict) -> str:
    origin = str(question.get("origin", "") if isinstance(question, dict)
                 else question.origin or "").lower()
    if origin.startswith("ai") or origin in {
            "deepseek_generated", "openai_generated", "generated"}:
        return "ai"
    if origin in {"human", "original", "teacher", "manual"}:
        return "teacher"
    return "imported"


def difficulty_band(value: str) -> str:
    text = (value or "").strip().lower()
    if text in {"easy", "simple", "基础", "简单"}:
        return "easy"
    if text in {"medium", "same", "中等", "一般"}:
        return "medium"
    if text in {"hard", "difficult", "困难", "较难"}:
        return "hard"
    return "unknown"


def _normalized_stem(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\W_]+", "", value)


def _quiz_gate(question: E.Question, *, require_active: bool = True) -> tuple[bool, str]:
    reasons: list[str] = []
    if require_active and not question.active:
        reasons.append("题目已停用")
    if (question.scope_class or "in_scope") not in QUIZ_SCOPE:
        reasons.append("不在默认课程范围")
    if not answer_pipeline.is_publishable(question):
        reasons.append("题目内容被标记为待修复")
    valid, detail = answer_pipeline.answer_structurally_valid(question)
    if not valid:
        reasons.append(detail or "答案结构不可评分")
    if not answer_pipeline.is_answer_ready(question):
        reasons.append("缺少已核验或自动校验的答案")
    if question.review_status not in REVIEW_READY:
        reasons.append("题目尚未完成审核")
    return not reasons, "；".join(dict.fromkeys(reasons))


def quiz_gate(question: E.Question, *, require_active: bool = True) -> tuple[bool, str]:
    """Public publication gate shared by the bank UI and direct paper API.

    An independently validated AI answer is deliberately accepted through the
    same structural checks as a teacher-verified key.  The status is preserved
    as ``auto_checked``/``ai_auto_checked``; callers must not rewrite it to a
    fictitious teacher approval merely to pass this gate.
    """
    return _quiz_gate(question, require_active=require_active)


def _duplicate_map(questions: list[E.Question]) -> dict[str, dict]:
    """Mark exact, family and near duplicates for teacher review.

    The bank is small enough (hundreds, not millions) for a deterministic
    pairwise comparison.  We compare latest question versions only.
    """
    result = {q.question_id: {"kind": "", "of": "", "similarity": 0.0}
              for q in questions}
    normalized = {q.question_id: _normalized_stem(q.stem_zh or q.stem)
                  for q in questions}
    for index, left in enumerate(questions):
        for right in questions[index + 1:]:
            ltxt, rtxt = normalized[left.question_id], normalized[right.question_id]
            if not ltxt or not rtxt:
                continue
            same_family = bool(left.question_family_id and
                               left.question_family_id == right.question_family_id)
            if ltxt == rtxt:
                kind, ratio = "exact", 1.0
            else:
                ratio = SequenceMatcher(None, ltxt, rtxt).ratio()
                if same_family and ratio >= 0.60:
                    kind = "family"
                elif ratio >= 0.82:
                    kind = "near"
                else:
                    continue
            for current, other in ((left, right), (right, left)):
                old = result[current.question_id]
                if ratio > float(old["similarity"]):
                    result[current.question_id] = {
                        "kind": kind, "of": other.question_id,
                        "similarity": round(ratio, 3),
                    }
    return result


def _teacher_question(question: E.Question, duplicate: dict | None = None,
                      chapter_title: str = "") -> dict:
    payload = student_view(question, reveal=True, mode="quiz")
    ready, blocked_reason = _quiz_gate(question)
    payload.update({
        "source_category": source_category(question),
        "source_category_label": SOURCE_LABELS[source_category(question)],
        "difficulty_band": difficulty_band(question.difficulty),
        "usable_for_quiz": ready,
        "blocked_reason": blocked_reason,
        "scope_class": question.scope_class or "unconfirmed",
        "created_at": question.created_at,
        "created_by": question.created_by,
        "duplicate": duplicate or {"kind": "", "of": "", "similarity": 0.0},
        "chapter_title": chapter_title,
    })
    return payload


def question_bank(db: Session, *, chapter: str = "", kp_id: str = "",
                  qtype: str = "", difficulty: str = "", source: str = "",
                  keyword: str = "", availability: str = "",
                  scope: str = "", include_inactive: bool = True,
                  limit: int = 1200) -> dict:
    questions = _latest_questions(db, include_inactive=include_inactive)
    duplicates = _duplicate_map(questions)
    kp_rows = db.execute(select(E.KnowledgePoint).where(
        E.KnowledgePoint.status == "active").order_by(
        E.KnowledgePoint.chapter, E.KnowledgePoint.name)).scalars().all()
    kp_map = {row.kp_id: row for row in kp_rows}
    chapter_rows = db.execute(select(E.Chapter).order_by(
        E.Chapter.sort_order, E.Chapter.chapter_id)).scalars().all()
    chapter_map = {row.chapter_id: row for row in chapter_rows}
    items = [_teacher_question(
        q, duplicates.get(q.question_id),
        chapter_map[q.chapter_id].title
        if q.chapter_id and q.chapter_id in chapter_map else "")
        for q in questions]

    term = keyword.strip().lower()
    filtered: list[dict] = []
    for item in items:
        kp_ids = [item.get("main_kp_id", ""), *(item.get("aux_kp_ids") or [])]
        main = kp_map.get(item.get("main_kp_id", ""))
        names = [kp_map[x].name for x in kp_ids if x in kp_map]
        direct_chapter = str(item.get("chapter_title") or item.get("chapter_id") or "")
        if chapter:
            chapter_match = (direct_chapter == chapter if direct_chapter else
                             any(kp_map[x].chapter == chapter
                                 for x in kp_ids if x in kp_map))
            if not chapter_match:
                continue
        if kp_id and kp_id not in kp_ids:
            continue
        if qtype and item.get("question_type_effective") != qtype:
            continue
        if difficulty and item.get("difficulty_band") != difficulty:
            continue
        if source and item.get("source_category") != source:
            continue
        if availability == "usable" and not item.get("usable_for_quiz"):
            continue
        if availability == "needs_review" and item.get("usable_for_quiz"):
            continue
        if scope == "course" and item.get("scope_class") not in QUIZ_SCOPE:
            continue
        if scope == "extension" and item.get("scope_class") in QUIZ_SCOPE:
            continue
        haystack = " ".join([
            str(item.get("stem") or ""), str(item.get("stem_zh") or ""),
            *names, direct_chapter, str(main.chapter if main else ""),
        ]).lower()
        if term and term not in haystack:
            continue
        filtered.append(item)

    filtered.sort(key=lambda item: (
        not bool(item.get("usable_for_quiz")),
        item.get("scope_class") not in QUIZ_SCOPE,
        str(item.get("created_at") or ""),
        str(item.get("question_id") or ""),
    ))
    filtered = filtered[:max(1, min(limit, 2000))]
    chapter_counts = Counter()
    for item in items:
        main = kp_map.get(item.get("main_kp_id", ""))
        label = str(item.get("chapter_title") or (main.chapter if main else "") or "")
        if label:
            chapter_counts[label] += 1
    type_counts = Counter(item.get("question_type_effective") or "unknown" for item in items)
    source_counts = Counter(item.get("source_category") or "imported" for item in items)
    difficulty_counts = Counter(item.get("difficulty_band") or "unknown" for item in items)
    return {
        "total": len(items), "returned": len(filtered), "items": filtered,
        "metadata": {
            "chapters": [{
                "value": key,
                "chapter_id": next((row.chapter_id for row in chapter_rows
                                    if row.title == key), ""),
                "count": value,
            } for key, value in sorted(chapter_counts.items())],
            "knowledge_points": [{
                "kp_id": row.kp_id, "chapter": row.chapter, "name": row.name,
            } for row in kp_rows],
            "question_types": [{"value": key, "count": value}
                               for key, value in sorted(type_counts.items())],
            "sources": [{"value": key, "label": SOURCE_LABELS.get(key, key),
                         "count": value} for key, value in source_counts.items()],
            "difficulties": [{"value": key, "count": value}
                             for key, value in difficulty_counts.items()],
            "usable": sum(1 for item in items if item["usable_for_quiz"]),
            "needs_review": sum(1 for item in items if not item["usable_for_quiz"]),
            "duplicates": sum(1 for item in items if item["duplicate"]["kind"]),
        },
    }


def _is_frozen(db: Session, paper_id: str) -> bool:
    version = db.execute(select(E.AssessmentVersion).where(
        E.AssessmentVersion.paper_id == paper_id)).scalars().first()
    attempt = db.execute(select(E.Attempt).where(
        E.Attempt.paper_id == paper_id)).scalars().first()
    return bool(version or attempt)


def _resolve_question(db: Session, question_id: str,
                      question_version: int | None = None) -> E.Question:
    stmt = select(E.Question).where(E.Question.question_id == question_id)
    if question_version:
        stmt = stmt.where(E.Question.question_version == question_version)
    question = db.execute(stmt.order_by(
        E.Question.question_version.desc())).scalars().first()
    if not question:
        raise KeyError(f"题目不存在：{question_id}")
    ready, reason = _quiz_gate(question, require_active=not bool(question_version))
    if not ready:
        raise ValueError(f"题目不可进入正式试卷：{question_id}（{reason}）")
    return question


def _coverage(questions: list[E.Question]) -> dict:
    return {
        "question_types": dict(Counter(
            effective_type({"question_type": q.question_type,
                            "answer_payload": q.answer_payload or {}})
            for q in questions)),
        "knowledge_points": dict(Counter(q.main_kp_id or "未标注" for q in questions)),
        "sources": dict(Counter(source_category(q) for q in questions)),
        "difficulties": dict(Counter(difficulty_band(q.difficulty) for q in questions)),
    }


def create_draft(db: Session, *, teacher_user_id: str, title: str,
                 instructions: str, items: list[dict],
                 build_mode: str = "manual", build_report: dict | None = None,
                 total_score: float | None = None) -> dict:
    if not items:
        raise ValueError("请至少选择一道可评分题目")
    questions = [_resolve_question(
        db, str(item.get("question_id") or "").strip(),
        int(item["question_version"]) if item.get("question_version") else None,
    ) for item in items]
    if len({q.question_id for q in questions}) != len(questions):
        raise ValueError("同一道题不能重复加入试卷")
    if total_score is not None:
        if float(total_score) <= 0:
            raise ValueError("试卷总分必须大于 0")
        each = round(float(total_score) / len(questions), 2)
        scores = [each] * len(questions)
        scores[-1] = round(float(total_score) - each * (len(questions) - 1), 2)
    else:
        scores = [float(item.get("score") or question.score or 1)
                  for item, question in zip(items, questions)]
    if any(score <= 0 for score in scores):
        raise ValueError("每道题分值必须大于 0")
    blueprint = {
        "builder_status": "draft", "builder_version": 2,
        "build_mode": build_mode, "instructions": instructions.strip(),
        "question_ids": [q.question_id for q in questions],
        "build_report": build_report or {},
    }
    result = bank_service.create_paper_from_questions(
        db, questions, title=title.strip() or "未命名试卷", mode="quiz",
        source="teacher_builder", blueprint=blueprint, scores=scores,
        user_id=teacher_user_id,
    )
    paper = db.get(E.Paper, result["paper_id"])
    paper.coverage_report = {
        **(paper.coverage_report or {}), **_coverage(questions),
        "build_report": build_report or {},
    }
    db.commit()
    return draft_detail(db, paper.paper_id, teacher_user_id=teacher_user_id)


def update_draft(db: Session, paper_id: str, *, teacher_user_id: str,
                 title: str, instructions: str, items: list[dict]) -> dict:
    paper = db.get(E.Paper, paper_id)
    if not paper:
        raise KeyError("试卷草稿不存在")
    if paper.user_id != teacher_user_id:
        raise PermissionError("不能修改其他教师的试卷")
    if _is_frozen(db, paper_id):
        raise ValueError("该试卷已发布或已产生答卷，固定版本不能原地修改；请另建草稿")
    if not items:
        raise ValueError("试卷至少保留一道题")
    questions = [_resolve_question(
        db, str(item.get("question_id") or "").strip(),
        int(item["question_version"]) if item.get("question_version") else None,
    ) for item in items]
    if len({q.question_id for q in questions}) != len(questions):
        raise ValueError("同一道题不能重复加入试卷")
    scores = [float(item.get("score") or 0) for item in items]
    if any(score <= 0 for score in scores):
        raise ValueError("每道题分值必须大于 0")
    old_blueprint = dict(paper.blueprint or {})
    paper.title = title.strip() or "未命名试卷"
    paper.blueprint = {
        **old_blueprint, "builder_status": "draft",
        "instructions": instructions.strip(),
        "question_ids": [q.question_id for q in questions],
        "last_saved_at": E.utcnow(),
    }
    paper.total_score = round(sum(scores), 2)
    paper.item_count = len(questions)
    paper.estimated_minutes = round(sum(
        float(q.estimated_minutes or 0) for q in questions), 1)
    paper.coverage_report = {
        **(paper.coverage_report or {}), **_coverage(questions),
        "message": "教师草稿已保存；发布时会生成不可变 AssessmentVersion",
    }
    db.execute(delete(E.PaperItem).where(E.PaperItem.paper_id == paper_id))
    fixed_at = E.utcnow()
    for position, (question, score) in enumerate(zip(questions, scores), start=1):
        meta = answer_pipeline.answer_meta_for(
            db, question.question_id, question.question_version) or {
                "label": answer_pipeline.answer_label(question.answer_origin),
                "source": question.answer_origin or "none",
                "question_version": question.question_version,
            }
        snapshot = bank_service.question_snapshot(question, answer_meta=meta)
        snapshot.update({"score": score, "position": position,
                         "answer_fixed_at": fixed_at})
        db.add(E.PaperItem(
            item_id=E.new_id("item"), paper_id=paper_id, position=position,
            question_id=question.question_id,
            question_version=question.question_version,
            score=score, snapshot=snapshot,
        ))
    db.commit()
    return draft_detail(db, paper_id, teacher_user_id=teacher_user_id)


def draft_detail(db: Session, paper_id: str, *, teacher_user_id: str) -> dict:
    paper = db.get(E.Paper, paper_id)
    if not paper:
        raise KeyError("试卷不存在")
    if paper.user_id != teacher_user_id:
        raise PermissionError("不能查看其他教师的草稿")
    items = bank_service.get_paper_items(db, paper_id)
    questions = []
    for item in items:
        question = student_view(item.snapshot, reveal=True, mode="quiz")
        question.update({
            "item_id": item.item_id, "position": item.position,
            "score": item.score, "question_version": item.question_version,
            "source_category": source_category(item.snapshot),
        })
        questions.append(question)
    frozen = _is_frozen(db, paper_id)
    version = db.execute(select(E.AssessmentVersion).where(
        E.AssessmentVersion.paper_id == paper_id)).scalars().first()
    assessment = db.get(E.Assessment, version.assessment_id) if version else None
    return {
        "paper_id": paper.paper_id, "title": paper.title,
        "instructions": (paper.blueprint or {}).get("instructions", ""),
        "mode": paper.mode, "build_mode": (paper.blueprint or {}).get("build_mode", "manual"),
        "builder_status": "published" if frozen else "draft",
        "editable": not frozen, "assessment_version_id": (
            version.assessment_version_id if version else ""),
        "assessment_classroom_id": (
            assessment.classroom_id if assessment else ""),
        "item_count": len(items), "total_score": paper.total_score,
        "estimated_minutes": paper.estimated_minutes,
        "coverage_report": paper.coverage_report or {},
        "created_at": paper.created_at, "questions": questions,
        "immutable_snapshot": True,
    }


def list_drafts(db: Session, *, teacher_user_id: str) -> list[dict]:
    rows = db.execute(select(E.Paper).where(
        E.Paper.user_id == teacher_user_id,
        E.Paper.source == "teacher_builder",
        E.Paper.archived.is_(False),
    ).order_by(E.Paper.created_at.desc())).scalars().all()
    result = []
    for paper in rows:
        frozen = _is_frozen(db, paper.paper_id)
        result.append({
            "paper_id": paper.paper_id, "title": paper.title,
            "item_count": paper.item_count, "total_score": paper.total_score,
            "estimated_minutes": paper.estimated_minutes,
            "status": "published" if frozen else "draft",
            "build_mode": (paper.blueprint or {}).get("build_mode", "manual"),
            "created_at": paper.created_at,
        })
    return result


def _matches_requirements(question: E.Question, *, kp_ids: list[str],
                          chapter_ids: list[str],
                          chapter_kp_ids: list[str],
                          difficulties: list[str]) -> bool:
    if chapter_ids or chapter_kp_ids:
        question_kps = {question.main_kp_id, *(question.aux_kp_ids or [])}
        chapter_match = (question.chapter_id in chapter_ids
                         if question.chapter_id
                         else bool(question_kps & set(chapter_kp_ids)))
        if not chapter_match:
            return False
    if kp_ids and question.main_kp_id not in kp_ids and not (
            set(question.aux_kp_ids or []) & set(kp_ids)):
        return False
    if difficulties and difficulty_band(question.difficulty) not in difficulties:
        return False
    return True


def _auto_validate_candidates(db: Session, candidates: list[E.Question], *,
                              kp_ids: list[str], requested_types: set[str],
                              already_selected: list[E.Question]) -> list[E.Question]:
    existing = [q for q in _latest_questions(db, include_inactive=True)
                if q.question_id not in {c.question_id for c in candidates}]
    accepted: list[E.Question] = []
    comparison = [*existing, *already_selected]
    for question in candidates:
        qtype = effective_type({"question_type": question.question_type,
                                "answer_payload": question.answer_payload or {}})
        refs_valid = bool(question.source_refs)
        for ref in question.source_refs or []:
            chunk_id = ref.get("chunk_id") if isinstance(ref, dict) else ""
            chunk = db.get(E.Chunk, chunk_id) if chunk_id else None
            if chunk is None or not str(
                    chunk.content_normalized or chunk.content_raw or "").strip():
                refs_valid = False
                break
        kp_valid = (not kp_ids or question.main_kp_id in kp_ids or bool(
            set(question.aux_kp_ids or []) & set(kp_ids)))
        type_valid = not requested_types or qtype in requested_types
        scope_valid = (question.scope_class or "") == "in_scope"
        normalized = _normalized_stem(question.stem_zh or question.stem)
        max_similarity = max((SequenceMatcher(
            None, normalized, _normalized_stem(other.stem_zh or other.stem)
        ).ratio() for other in comparison if other.question_id != question.question_id),
            default=0.0)
        unique = max_similarity < 0.78
        # The generation workflow intentionally creates *candidates*: its answer
        # payload has only passed a shape check and must not be laundered into a
        # formal auto-scored paper.  First reject scope/type/reference/duplicate
        # problems, then ask the independent answer solver to solve the item
        # again.  That second workflow validates the rubric, numeric calculation
        # (when applicable), option/explanation consistency and input sufficiency.
        preliminary_ok = all((refs_valid, kp_valid, type_valid, scope_valid, unique))
        answer_check: dict[str, Any] = {"ready": False, "status": "not_run"}
        if preliminary_ok:
            answer_check = answer_pipeline.ensure_answer_ready(
                db, question, trigger="teacher_builder_auto_check",
                allow_sync=True, sync_timeout_s=60.0)
        valid, reason = answer_pipeline.answer_structurally_valid(question)
        verification_ok = bool(
            answer_check.get("ready")
            and answer_pipeline.is_answer_ready(question)
            and question.answer_status == "ai_auto_checked"
            and question.review_status == "auto_checked"
            and question.explanation
            and question.rubric
        )
        if not all((valid, preliminary_ok, verification_ok)):
            question.notes = ((question.notes or "") + "\n" + json.dumps({
                "builder_validation": "rejected", "reason": reason,
                "refs_valid": refs_valid, "kp_valid": kp_valid,
                "type_valid": type_valid, "scope_valid": scope_valid,
                "max_similarity": round(max_similarity, 3),
                "verification_ok": verification_ok,
                "independent_answer_status": answer_check.get("status") or (
                    (answer_check.get("generation") or {}).get("status")),
                "answer_generation_id": answer_check.get("generation_id", ""),
            }, ensure_ascii=False)).strip()
            question.active = False
            continue
        question.notes = ((question.notes or "") + "\n" + json.dumps({
            "builder_validation": "automatic_builder_v2",
            "checked_at": E.utcnow(), "format_valid": valid,
            "references_valid": refs_valid, "knowledge_point_match": kp_valid,
            "type_match": type_valid, "scope_match": scope_valid,
            "max_duplicate_similarity": round(max_similarity, 3),
            "independent_answer_generation_id": answer_check.get(
                "generation_id", ""),
            "limitation": "两阶段自动校验保证结构、可评分性与独立求解一致性；不冒充教师或原卷答案",
        }, ensure_ascii=False)).strip()
        accepted.append(question)
        comparison.append(question)
    return accepted


def compose_draft(db: Session, *, teacher_user_id: str, title: str,
                  instructions: str, mode: str, selected_items: list[dict],
                  requirements: dict) -> dict:
    if mode not in {"manual", "ai", "mixed"}:
        raise ValueError("组卷方式必须是 manual、ai 或 mixed")
    if mode == "manual":
        return create_draft(
            db, teacher_user_id=teacher_user_id, title=title,
            instructions=instructions, items=selected_items,
            build_mode="manual",
            total_score=float(requirements.get("total_score") or 0) or None)

    locked = [_resolve_question(
        db, str(item.get("question_id") or "").strip(),
        int(item["question_version"]) if item.get("question_version") else None,
    ) for item in selected_items]
    if mode == "mixed" and not locked:
        raise ValueError("混合组卷需要先锁定至少一道人工选择的题目")
    kp_ids = list(dict.fromkeys(str(x) for x in requirements.get("kp_ids", []) if x))
    requested_chapters = list(dict.fromkeys(
        str(x) for x in requirements.get("chapters", []) if x))
    chapter_ids = list(dict.fromkeys(
        str(x) for x in requirements.get("chapter_ids", []) if x))
    if requested_chapters:
        chapter_ids.extend(row.chapter_id for row in db.execute(
            select(E.Chapter).where(E.Chapter.title.in_(requested_chapters))
        ).scalars().all())
        chapter_ids = list(dict.fromkeys(chapter_ids))
    chapter_titles = list(requested_chapters)
    if chapter_ids:
        chapter_titles.extend(row.title for row in db.execute(
            select(E.Chapter).where(E.Chapter.chapter_id.in_(chapter_ids))
        ).scalars().all())
    chapter_titles = list(dict.fromkeys(chapter_titles))
    chapter_kp_ids = [row.kp_id for row in db.execute(
        select(E.KnowledgePoint).where(
            E.KnowledgePoint.chapter.in_(chapter_titles),
            E.KnowledgePoint.status == "active",
        )).scalars().all()] if chapter_titles else []
    difficulties = list(dict.fromkeys(
        str(x) for x in requirements.get("difficulties", []) if x))
    targets = [{"question_type": str(item.get("question_type") or ""),
                "count": int(item.get("count") or 0)}
               for item in requirements.get("type_targets", [])
               if item.get("question_type") and int(item.get("count") or 0) > 0]
    requested_count = int(requirements.get("total_count") or sum(
        item["count"] for item in targets))
    # In mixed mode the teacher's locked questions are preserved verbatim and
    # the target rows describe the questions to ADD.  This avoids a numeric or
    # short-answer lock being silently displaced by a single-choice target.
    target_count = requested_count + (len(locked) if mode == "mixed" else 0)
    if target_count <= 0 or target_count > 100:
        raise ValueError("目标题数需在 1-100 之间")
    if len(locked) > target_count:
        raise ValueError("已锁定题数超过目标总题数，请调大目标题数")
    source_policy = str(requirements.get("source_policy") or "existing")
    if source_policy not in {"existing", "ai", "mixed"}:
        raise ValueError("题目来源设置无效")
    allow_ai = bool(requirements.get("allow_ai_fill"))
    if source_policy in {"ai", "mixed"} and not allow_ai:
        raise ValueError("选择 AI 新题或混合来源时，必须明确允许 AI 补题")

    selected = list(locked)
    selected_ids = {q.question_id for q in selected}
    selected_families = {q.question_family_id or q.question_id for q in selected}
    all_bank = _latest_questions(db, include_inactive=False)
    eligible = []
    for question in all_bank:
        ready, _ = _quiz_gate(question)
        family = question.question_family_id or question.question_id
        if (not ready or question.question_id in selected_ids or
                family in selected_families or
                not _matches_requirements(question, kp_ids=kp_ids,
                                          chapter_ids=chapter_ids,
                                          chapter_kp_ids=chapter_kp_ids,
                                          difficulties=difficulties)):
            continue
        eligible.append(question)
    eligible.sort(key=lambda q: (source_category(q) == "ai",
                                 q.question_family_id or q.question_id,
                                 q.question_id))

    if not targets and bool(requirements.get("auto_type_distribution", True)):
        # Allocate the requested supplement across familiar objective types.
        # The allocation is capped by the qualified pool so a chapter with a
        # different type mix is not rejected merely to satisfy a cosmetic ratio.
        availability = Counter(effective_type({
            "question_type": q.question_type,
            "answer_payload": q.answer_payload or {},
        }) for q in eligible)
        common = ["single_choice", "multi_choice", "true_false", "fill_blank"]
        weights = {"single_choice": 4, "multi_choice": 2,
                   "true_false": 2, "fill_blank": 2}
        desired = {key: (requested_count * weight) // 10
                   for key, weight in weights.items()}
        while sum(desired.values()) < requested_count:
            for key in common:
                desired[key] += 1
                if sum(desired.values()) >= requested_count:
                    break
        allocated: Counter[str] = Counter()
        for key in common:
            allocated[key] = min(desired[key], availability[key])
        remaining = requested_count - sum(allocated.values())
        for key in [*common, *sorted(k for k in availability if k not in common)]:
            if remaining <= 0:
                break
            room = max(0, availability[key] - allocated[key])
            take = min(room, remaining)
            allocated[key] += take
            remaining -= take
        # If the existing pool is short, preserve a deterministic type request
        # for the AI supplement instead of silently reducing the paper size.
        index = 0
        while remaining > 0:
            allocated[common[index % len(common)]] += 1
            remaining -= 1
            index += 1
        targets = [{"question_type": key, "count": allocated[key]}
                   for key in [*common, *sorted(k for k in allocated if k not in common)]
                   if allocated[key] > 0]

    locked_types = Counter(effective_type({
        "question_type": q.question_type, "answer_payload": q.answer_payload or {},
    }) for q in locked)
    required_by_type = {item["question_type"]: item["count"] for item in targets}
    if mode == "mixed":
        required_by_type = {key: value + locked_types.get(key, 0)
                            for key, value in required_by_type.items()}
    if not required_by_type:
        required_by_type = {"": target_count}
    existing_picked: list[E.Question] = []
    if source_policy in {"existing", "mixed"}:
        for qtype, desired in required_by_type.items():
            need = max(0, desired - (locked_types.get(qtype, 0) if qtype else len(locked)))
            if need <= 0:
                continue
            for question in eligible:
                actual_type = effective_type({
                    "question_type": question.question_type,
                    "answer_payload": question.answer_payload or {},
                })
                if question in existing_picked or (qtype and actual_type != qtype):
                    continue
                existing_picked.append(question)
                if len([q for q in existing_picked if not qtype or effective_type({
                        "question_type": q.question_type,
                        "answer_payload": q.answer_payload or {},
                }) == qtype]) >= need:
                    break
        selected.extend(existing_picked)

    missing = target_count - len(selected)
    ai_picked: list[E.Question] = []
    generation_attempted = False
    if missing > 0 and source_policy in {"ai", "mixed"} and allow_ai:
        generation_attempted = True
        selected_type_counts = Counter(effective_type({
            "question_type": q.question_type, "answer_payload": q.answer_payload or {},
        }) for q in selected)
        requested_groups: list[tuple[str, int]] = []
        if targets:
            for target in targets:
                count = max(0, required_by_type[target["question_type"]]
                            - selected_type_counts[target["question_type"]])
                if count:
                    requested_groups.append((target["question_type"], count))
        else:
            requested_groups.append(("", missing))
        candidates: list[E.Question] = []
        for qtype, count in requested_groups:
            generation_kp_ids = kp_ids
            if not generation_kp_ids and chapter_kp_ids:
                generation_kp_ids = chapter_kp_ids
            generated = bank_service.ai_fill_questions(db, {
                "kp_ids": generation_kp_ids, "chapter_ids": chapter_ids,
                "difficulty": difficulties,
                "question_types": [qtype] if qtype else [],
            }, generation_kp_ids, count, [qtype] if qtype else [])
            candidates.extend(generated)
        ai_picked = _auto_validate_candidates(
            db, candidates, kp_ids=kp_ids,
            requested_types={item["question_type"] for item in targets},
            already_selected=selected,
        )
        selected.extend(ai_picked)

    # Targets are strict: never return a silently shortened or type-shifted paper.
    type_counts = Counter(effective_type({
        "question_type": q.question_type, "answer_payload": q.answer_payload or {},
    }) for q in selected)
    shortages = {qtype: count - type_counts[qtype]
                 for qtype, count in required_by_type.items()
                 if qtype and type_counts[qtype] < count}
    if len(selected) < target_count or shortages:
        db.commit()  # preserve rejected AI candidates and their audit notes
        detail = "，".join(f"{key} 缺 {value} 题" for key, value in shortages.items())
        if len(selected) < target_count:
            detail = (detail + "；" if detail else "") + \
                f"总题数缺 {target_count - len(selected)} 题"
        if generation_attempted and not ai_picked:
            detail += "；AI 未返回通过自动校验的新题"
        raise ValueError("无法满足组卷设置：" + detail)
    selected = selected[:target_count]
    report = {
        "requested_count": target_count, "supplement_count": requested_count,
        "selected_count": len(selected),
        "locked_count": len(locked), "existing_selected": len(existing_picked),
        "ai_generated_and_validated": len(ai_picked),
        "source_policy": source_policy, "kp_ids": kp_ids,
        "chapter_ids": chapter_ids, "chapters": requested_chapters,
        "difficulties": difficulties, "type_targets": targets,
        "shortfall": 0, "automatic_validation": "automatic_builder_v2",
    }
    total_score = float(requirements.get("total_score") or 0) or None
    db.commit()
    return create_draft(
        db, teacher_user_id=teacher_user_id, title=title,
        instructions=instructions,
        items=[{"question_id": q.question_id,
                "question_version": q.question_version,
                "score": q.score} for q in selected],
        build_mode=mode, build_report=report, total_score=total_score,
    )
