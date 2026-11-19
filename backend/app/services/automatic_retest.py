"""Conservative automatic retest checks; no teacher approval or live-data migration."""
from __future__ import annotations
import json
import re
import unicodedata
from difflib import SequenceMatcher
from sqlalchemy import select
from backend.app import config as cfg
from backend.app.models import entities as E
from backend.app.services import (answer_pipeline, bank_service, model_provider,
                                  release_service, targeted_retest)


def _validate_candidate(db, *, qid: str, prior_stems, chapter_ids, kp_ids,
                        thread_id: str):
    q = answer_pipeline.latest_question(db, qid)
    if q is None:
        raise ValueError("复测候选题不存在")
    if q.origin not in {"ai", "ai_generated"}:
        raise ValueError("AI复测必须使用本次生成的候选题，不能用题库原题冒充")
    if chapter_ids and (not q.chapter_id or q.chapter_id not in chapter_ids):
        raise ValueError("新题章节与复测目标不匹配")
    if kp_ids and (not q.main_kp_id or q.main_kp_id not in kp_ids):
        raise ValueError("新题知识点与复测目标不匹配")
    if q.question_type not in {"single_choice", "multi_choice", "true_false"}:
        raise ValueError("当前自动复测仅支持可确定评分的单选题、多选题和判断题")
    valid, reason = answer_pipeline.answer_structurally_valid(q)
    if not valid or not q.source_refs or q.scope_class != "in_scope":
        raise ValueError("题目结构、来源或课程范围检查未通过：" + reason)
    for ref in q.source_refs:
        chunk = db.get(E.Chunk, ref.get("chunk_id")) if isinstance(ref, dict) and ref.get("chunk_id") else None
        if chunk is None or not str(chunk.content_normalized or chunk.content_raw or "").strip():
            raise ValueError("题目引用不存在或引用内容为空")
        doc = db.get(E.Document, chunk.document_id)
        version = db.get(E.DocumentVersion, chunk.document_version_id)
        if doc is None or version is None or version.document_id != doc.document_id:
            raise ValueError("题目引用缺少可追溯的文档版本")
    if q.question_type in {"single_choice", "multi_choice"}:
        keys = answer_pipeline.option_keys_of(q)
        if len(keys) != len(set(str(k).strip().upper() for k in keys)):
            raise ValueError("选择题选项标识重复")
        texts = [re.sub(r"\s+", "", unicodedata.normalize("NFC", str(o.get("text") or "")))
                 if isinstance(o, dict) else "" for o in q.options]
        if not all(texts) or len(texts) != len(set(texts)):
            raise ValueError("选择题选项正文为空或重复")
    normalize = lambda text: re.sub(r"\W+", "", text or "").lower()
    for other_id, stem in prior_stems:
        if other_id != qid and SequenceMatcher(None, normalize(q.stem), normalize(stem)).ratio() >= .78:
            raise ValueError("复测题与题库已有题目过于相似")
    kp = db.get(E.KnowledgePoint, q.main_kp_id)
    if kp is None:
        raise ValueError("知识点不存在")
    chapter = db.get(E.Chapter, q.chapter_id) if q.chapter_id else None
    response = model_provider.chat(db, messages=[
        {"role": "system", "content": "独立解答机器学习题，检查歧义与指定知识点匹配。只输出 JSON：answer_payload（单选为 key，多选为 keys 数组，判断为布尔 value）、explanation、unambiguous（布尔）、kp_matches（布尔）、assessment_angle（简短的考查方式类别，例如定义辨析、情境诊断、公式应用、反例判断；不得用题目编号或复述题干冒充类别）。不要猜测，无法唯一解答时 unambiguous=false。"},
        {"role": "user", "content": json.dumps({"stem": q.stem, "options": q.options,
            "question_type": q.question_type, "knowledge_point": kp.name,
            "chapter": chapter.title if chapter else q.chapter_id}, ensure_ascii=False)}],
        skill_name="automatic_retest_verification", run_id=thread_id, max_tokens=1600)
    check = model_provider.parse_json_content(response.get("content", ""))
    if response.get("finish_reason") == "length" or not isinstance(check, dict):
        raise ValueError("独立解答未返回完整校验结果")
    raw_answer = check.get("answer_payload")
    if isinstance(raw_answer, dict):
        answer = raw_answer
    elif q.question_type == "single_choice" and isinstance(raw_answer, str):
        answer = {"key": raw_answer}
    elif q.question_type == "multi_choice" and isinstance(raw_answer, list):
        answer = {"keys": raw_answer}
    elif q.question_type == "true_false" and isinstance(raw_answer, bool):
        answer = {"value": raw_answer}
    else:
        answer = {}
    expected = q.answer_payload or {}
    if q.question_type == "single_choice":
        same = (str(answer.get("key", "")).strip().upper() ==
                str(expected.get("key", "")).strip().upper())
    elif q.question_type == "multi_choice":
        same = ({str(value).strip().upper() for value in answer.get("keys", [])} ==
                {str(value).strip().upper() for value in expected.get("keys", [])})
    else:
        same = (isinstance(answer.get("value"), bool) and
                answer.get("value") == expected.get("value"))
    if (not same or check.get("unambiguous") is not True or
            check.get("kp_matches") is not True or
            not str(check.get("explanation") or "").strip()):
        raise ValueError("独立解答与候选答案不一致，或题目有歧义/知识点不匹配")
    return q, check, response.get("call_id")


def validate_and_publish(db, *, candidate_ids, kp_ids=None, chapter_ids=None,
                         actor_user_id, student_id="", student_ids=None, classroom_id,
                         source_attempt_id, diagnosis_id, thread_id,
                         source_assessment_version_id: str = "",
                         untested_kp_ids: list[str] | None = None,
                         round_no: int = 1, required_count: int | None = None):
    kp_ids = [str(value) for value in (kp_ids or []) if value]
    chapter_ids = [str(value) for value in (chapter_ids or []) if value]
    candidate_ids = list(dict.fromkeys(candidate_ids))
    if not candidate_ids or not (chapter_ids or kp_ids):
        raise ValueError("没有可自动校验的新题或可靠的目标章节")
    # Legacy/internal callers validate a deliberately small batch.  The
    # targeted-retest workflow passes ``required_count=5`` explicitly.
    expected_count = int(required_count or len(candidate_ids))
    if len(set(kp_ids)) > expected_count:
        raise ValueError("本轮题量不足以覆盖目标知识点，请分轮复测")
    recipients = list(dict.fromkeys(
        [str(value) for value in (student_ids or [student_id]) if value]))
    anchors = {}
    for sid in recipients:
        source = db.get(E.Attempt, source_attempt_id) if source_attempt_id else None
        if source and source.student_id != sid:
            raise ValueError("原答卷与复测学生不匹配")
        if not source and source_assessment_version_id:
            source = db.execute(select(E.Attempt).where(
                E.Attempt.student_id == sid,
                E.Attempt.assessment_version_id == source_assessment_version_id,
                E.Attempt.status == "submitted", E.Attempt.is_effective.is_(True),
                E.Attempt.attendance_status == "attended",
            ).order_by(E.Attempt.submitted_at.desc())).scalars().first()
        if source:
            anchors[sid] = (source, targeted_retest.next_round(
                db, source_attempt_id=source.attempt_id, student_id=sid))
    if len(candidate_ids) < expected_count:
        raise ValueError(
            f"需要{expected_count}道合格题，自动生成与筛选后仅有{len(candidate_ids)}道；本轮未发布"
        )
    questions = []
    rejected = []
    # Compare with the existing bank and already accepted candidates. Two
    # similar unvalidated candidates must not reject each other symmetrically.
    prior_stems = list(db.execute(select(E.Question.question_id, E.Question.stem).where(
        E.Question.question_id.not_in(candidate_ids))).all())
    for qid in candidate_ids:
        try:
            questions.append(_validate_candidate(
                db, qid=qid, prior_stems=prior_stems,
                chapter_ids=chapter_ids, kp_ids=kp_ids, thread_id=thread_id))
            accepted = questions[-1][0]
            prior_stems.append((accepted.question_id, accepted.stem))
        except ValueError as exc:
            rejected.append({"question_id": qid, "reason": str(exc)})
        covered = {str(q.main_kp_id or "") for q, _, _ in questions}
        if len(questions) >= expected_count and (len(kp_ids) <= 1 or set(kp_ids) <= covered):
            break
    if len(questions) < expected_count:
        reasons = "；".join(dict.fromkeys(row["reason"] for row in rejected))
        raise ValueError(
            f"需要{expected_count}道自动校验通过的新题，候选池中仅{len(questions)}道通过"
            + (f"（{reasons}）" if reasons else "")
        )
    covered_kps = {str(q.main_kp_id or "") for q, _, _ in questions}
    missing_kps = [kp_id for kp_id in kp_ids if kp_id not in covered_kps]
    if len(kp_ids) > 1 and missing_kps:
        rejected_reasons = "；".join(dict.fromkeys(row["reason"] for row in rejected))
        raise ValueError(
            "复测题未覆盖全部原始薄弱概念，本轮未发布"
            + (f"（候选淘汰原因：{rejected_reasons}）" if rejected_reasons else "")
        )
    if len(questions) > expected_count:
        selected = []
        selected_ids = set()
        for kp_id in kp_ids:
            match = next((row for row in questions
                          if row[0].main_kp_id == kp_id and
                          row[0].question_id not in selected_ids), None)
            if match:
                selected.append(match)
                selected_ids.add(match[0].question_id)
        for row in questions:
            if len(selected) >= expected_count:
                break
            if row[0].question_id not in selected_ids:
                selected.append(row)
                selected_ids.add(row[0].question_id)
        questions = selected[:expected_count]
    try:
        # Do not activate any candidate until the entire batch passes.
        for q, check, call_id in questions:
            q.review_status = "auto_checked"
            q.answer_status = "ai_auto_checked"
            q.answer_origin = "deepseek_generated"
            q.explanation = check["explanation"]
            try:
                provenance = json.loads(q.notes or "{}").get("generation", {})
            except (ValueError, AttributeError):
                provenance = {}
            q.notes = json.dumps({"generation": provenance,
                "validation": "automatic_retest_v2_chapter", "call_id": call_id,
                "checked_at": E.utcnow(), "independent_solution": check,
                "target_chapter_ids": chapter_ids,
                "limitation": "独立模型解答一致不等于教师权威答案；保留 AI 参考答案标签"}, ensure_ascii=False)
            q.active = True
        db.flush()
        chapter_names = [row.title for row in db.execute(select(E.Chapter).where(
            E.Chapter.chapter_id.in_(chapter_ids))).scalars().all()] if chapter_ids else []
        concept_names = [row.name for row in db.execute(select(E.KnowledgePoint).where(
            E.KnowledgePoint.kp_id.in_(kp_ids))).scalars().all()] if kp_ids else []
        base_title = "、".join(chapter_names) or "当前错题概念"
        release_title = f"{base_title} · 针对性复测（第{round_no}轮）"
        paper = bank_service.create_paper_from_questions(db, [q for q, _, _ in questions],
            title=release_title, mode="retest", source="teacher_retest", user_id=actor_user_id,
            scores=[float(q.score or 2) for q, _, _ in questions],
            blueprint={"source": "automatic_retest_v2_chapter",
                       "question_source": "ai_generated",
                       "source_counts": {"ai_generated": len(questions), "bank_selected": 0, "original_reused": 0},
                       "chapter_ids": chapter_ids, "kp_ids": kp_ids}, commit=False)
        release = release_service.publish_release(db, teacher_user_id=actor_user_id,
            student_ids=recipients, title=("班级针对性复测" if len(recipients) > 1 else release_title), paper_id=paper["paper_id"],
            classroom_id=classroom_id, release_type="retest", source_attempt_id=source_attempt_id,
            source_diagnosis_id=diagnosis_id, workflow_run_id=thread_id,
            idempotency_key=(f"targeted-retest:{source_attempt_id}:{recipients[0]}:{round_no}"
                             if len(recipients) == 1 and source_attempt_id else
                             "automatic-retest:" + thread_id), commit=False)
        cycle = None
        cycle_ids = []
        for sid, (source_attempt, student_round) in anchors.items():
            cycle = targeted_retest.create_cycle(
                db, student_id=sid, source_attempt_id=source_attempt.attempt_id,
                source_assessment_version_id=source_attempt.assessment_version_id,
                diagnosis_id=diagnosis_id, release_id=release["release_id"],
                workflow_run_id=thread_id, round_no=student_round,
                question_ids=[q.question_id for q, _, _ in questions],
                chapter_ids=chapter_ids, kp_ids=kp_ids,
                concept_names=concept_names, untested_kp_ids=untested_kp_ids)
            cycle_ids.append(cycle.cycle_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    selected_ids = [q.question_id for q, _, _ in questions]
    return {"status": "published", "paper_id": paper["paper_id"], "release": release,
            "question_source": "ai_generated",
            "source_counts": {"ai_generated": len(questions), "bank_selected": 0, "original_reused": 0},
            "candidate_question_ids": selected_ids, "rejected_candidates": rejected,
            "chapter_ids": chapter_ids,
            "round": round_no,
            "cycle_id": cycle.cycle_id if cycle else "",
            "cycle_ids": cycle_ids,
            "threshold": cfg.TARGETED_RETEST_ACCURACY_THRESHOLD,
            "validation": "automatic_retest_v2_chapter"}
