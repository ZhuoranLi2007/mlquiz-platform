from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import config as cfg
from backend.app.graphs import classroom_workflow, workflows
from backend.app.models import entities as E
from backend.app.models.base import get_session
from backend.app.services import (
    answer_pipeline,
    attempt_service,
    auth_service,
    bank_service,
    classroom_diagnosis,
    classroom_import,
    paper_builder_service,
    release_service,
    teacher_attempt_review,
)

router = APIRouter(prefix="/api", tags=["teacher-student"])


class PublishIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    student_ids: list[str] = Field(min_length=1)
    assessment_version_id: str = ""
    paper_id: str = ""
    classroom_id: str = ""
    instructions: str = ""
    due_at: str = ""
    release_type: str = "exam"
    source_diagnosis_id: str = ""
    source_attempt_id: str = ""
    workflow_run_id: str = ""
    idempotency_key: str | None = None


class AnswerIn(BaseModel):
    response: dict = Field(default_factory=dict)
    hints_used: int = Field(default=0, ge=0)


class SubmitIn(BaseModel):
    request_key: str | None = None


class InterventionIn(BaseModel):
    student_id: str = Field(default="", max_length=64)
    assessment_version_id: str = Field(default="", max_length=64)
    classroom_id: str = Field(default="", max_length=64)
    scope_type: str = Field(default="student_attempt", max_length=32)
    attempt_id: str = Field(default="", max_length=64)
    count: int = Field(default=cfg.TARGETED_RETEST_DEFAULT_QUESTION_COUNT, ge=1, le=5)
    date_from: str = Field(default="", max_length=10)
    date_to: str = Field(default="", max_length=10)
    kp_ids: list[str] = Field(default_factory=list, max_length=2)
    practice: bool = False
    prefer_bank: bool = False
    retry_generation: bool = False


class SelfRetestIn(BaseModel):
    attempt_id: str = Field(default='', max_length=64)
    kp_ids: list[str] = Field(default_factory=list, max_length=2)
    practice: bool = False
    prefer_bank: bool = False
    retry_generation: bool = False


class ApproveCandidateIn(BaseModel):
    review_note: str = Field(default="", max_length=500)


class TeacherPaperIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    question_ids: list[str] = Field(min_length=1, max_length=100)
    scores: list[float] = Field(default_factory=list)


class GenerateQuestionsIn(BaseModel):
    kp_ids: list[str] = Field(default_factory=list, max_length=8)
    count: int = Field(default=3, ge=1, le=5)
    question_types: list[str] = Field(default_factory=list, max_length=5)
    instruction: str = Field(default="", max_length=1000)


class PaperDraftItemIn(BaseModel):
    question_id: str = Field(min_length=1, max_length=64)
    question_version: int | None = Field(default=None, ge=1)
    score: float = Field(default=1, gt=0, le=1000)


class PaperDraftComposeIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    instructions: str = Field(default="", max_length=3000)
    mode: str = Field(default="manual", pattern="^(manual|ai|mixed)$")
    selected_items: list[PaperDraftItemIn] = Field(default_factory=list,
                                                    max_length=100)
    requirements: dict = Field(default_factory=dict)


class PaperDraftUpdateIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    instructions: str = Field(default="", max_length=3000)
    items: list[PaperDraftItemIn] = Field(min_length=1, max_length=100)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc).strip("'"))
    if isinstance(exc, (ValueError, attempt_service.ConflictError)):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail="操作失败")


@router.get("/teacher/question-bank")
def teacher_question_bank(
    chapter: str = "", kp_id: str = "", qtype: str = "",
    difficulty: str = "", source: str = "", keyword: str = "",
    availability: str = "", scope: str = "",
    include_inactive: bool = True,
    limit: int = Query(default=1200, ge=1, le=2000),
    _: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    """Teacher-only bank view, including answer/rubric and quality gates.

    Student-facing question endpoints still pass through the no-answer DTO;
    this route is role-protected because a teacher needs the answer and
    explanation when reviewing a card or composing a fixed paper.
    """
    return paper_builder_service.question_bank(
        db, chapter=chapter, kp_id=kp_id, qtype=qtype,
        difficulty=difficulty, source=source, keyword=keyword,
        availability=availability, scope=scope,
        include_inactive=include_inactive, limit=limit)


@router.get("/teacher/paper-drafts")
def teacher_paper_drafts(
    principal: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    items = paper_builder_service.list_drafts(
        db, teacher_user_id=principal.user_id)
    return {"total": len(items), "items": items}


@router.post("/teacher/paper-drafts/compose")
def compose_teacher_paper(
    body: PaperDraftComposeIn,
    principal: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    try:
        return paper_builder_service.compose_draft(
            db, teacher_user_id=principal.user_id, title=body.title,
            instructions=body.instructions, mode=body.mode,
            selected_items=[item.model_dump() for item in body.selected_items],
            requirements=body.requirements)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/teacher/paper-drafts/{paper_id}")
def teacher_paper_draft(
    paper_id: str,
    principal: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    try:
        return paper_builder_service.draft_detail(
            db, paper_id, teacher_user_id=principal.user_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.put("/teacher/paper-drafts/{paper_id}")
def save_teacher_paper_draft(
    paper_id: str, body: PaperDraftUpdateIn,
    principal: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    try:
        return paper_builder_service.update_draft(
            db, paper_id, teacher_user_id=principal.user_id,
            title=body.title, instructions=body.instructions,
            items=[item.model_dump() for item in body.items])
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/teacher/attempts/{attempt_id}/review")
def teacher_review_attempt(
    attempt_id: str,
    principal: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    attempt = auth_service.assert_attempt_access(db, principal, attempt_id)
    if attempt.status != "submitted":
        raise HTTPException(409, "答卷尚未提交")
    return teacher_attempt_review.teacher_attempt_review(db, attempt)


@router.get("/teacher/releases")
def teacher_releases(
    classroom_id: str = Query(default="", max_length=64),
    _: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    items = release_service.list_releases(db, classroom_id=classroom_id)
    return {"total": len(items), "items": items}


@router.get("/teacher/classrooms")
def teacher_classrooms(
    _: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    rows = db.execute(select(E.Classroom).order_by(
        E.Classroom.created_at)).scalars().all()
    items = []
    for row in rows:
        try:
            summary = classroom_diagnosis.overview(db, row.classroom_id)
        except Exception:
            summary = {}
        counts = summary.get("counts") or {}
        classroom_students = classroom_diagnosis.list_students(
            db, row.classroom_id).get("items", [])
        online_count = sum(1 for item in classroom_students
                           if item.get("online_receivable"))
        items.append({
            "classroom_id": row.classroom_id,
            "course_key": row.course_key,
            "name": row.name,
            "term": row.term,
            "source": row.source,
            "student_count": counts.get("students", 0),
            "assessment_count": counts.get("assessments", 0),
            "attempt_count": counts.get("submitted_attempts", 0),
            "online_student_count": online_count,
            "historical_only_student_count": (
                len(classroom_students) - online_count),
        })
    return {"total": len(items), "items": items}


@router.get("/teacher/official-classroom")
def teacher_official_classroom(
    _: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    row = classroom_diagnosis.official_classroom(db)
    summary = classroom_diagnosis.overview(db, row.classroom_id)
    students = classroom_diagnosis.list_students(
        db, row.classroom_id).get("items", [])
    return {
        "classroom_id": row.classroom_id,
        "course_key": row.course_key,
        "name": row.name,
        "term": row.term,
        "source": row.source,
        "student_count": len(students),
        "online_student_count": sum(
            1 for item in students if item.get("online_receivable")),
        "counts": summary.get("counts", {}),
        "data_rule": summary.get("data_rule", ""),
    }


@router.post("/teacher/classroom-import")
async def upload_classroom_workbook(
    file: UploadFile = File(...),
    course_key: str = Form("ml-2025-fall"),
    classroom_name: str = Form("2025秋机器学习"),
    term: str = Form("2025秋"),
    _: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    if Path(file.filename or "").suffix.lower() != ".xlsx":
        raise HTTPException(400, "只支持 .xlsx 课堂汇总文件")
    data = await file.read(20 * 1024 * 1024 + 1)
    if not data or len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "文件为空或超过 20 MB")
    digest = hashlib.sha256(data).hexdigest()
    target_dir = cfg.PROJECT_ROOT / "data" / "classroom_imports"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{digest}.xlsx"
    if not target.exists():
        target.write_bytes(data)
    try:
        result = classroom_import.import_teacher_classroom(
            db, target, course_key=course_key.strip() or "ml-2025-fall",
            classroom_name=classroom_name.strip() or "机器学习课堂",
            term=term.strip())
    except (classroom_import.ClassroomImportError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    result["source_sha256"] = digest
    result["privacy"] = "只导入匿名学生代码；不读取说明页实名和文档作者邮箱。"
    return result


@router.get("/teacher/classroom-workflows")
def classroom_workflows(
    _: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    return classroom_workflow.graph_status(db)


@router.post("/teacher/workflows/intervention")
def teacher_intervention(
    body: InterventionIn,
    principal: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    try:
        return classroom_workflow.run_teacher_intervention(
            db, actor_user_id=principal.user_id,
            student_id=body.student_id,
            assessment_version_id=body.assessment_version_id,
            classroom_id=body.classroom_id,
            scope_type=body.scope_type,
            attempt_id=body.attempt_id,
            date_from=body.date_from, date_to=body.date_to,
            count=body.count, kp_ids=body.kp_ids, practice=body.practice, prefer_bank=body.prefer_bank, retry_generation=body.retry_generation)
    except (classroom_diagnosis.ClassroomDiagnosisError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _queue_teacher_job(db: Session, principal, kind: str, payload: dict) -> dict:
    from backend.app.workers.worker import create_job
    from sqlalchemy import text
    # Reserve SQLite's writer before reading active jobs. Two tabs/processes
    # must not both observe an empty result and enqueue the same paid workflow.
    db.execute(text("BEGIN IMMEDIATE"))
    # Reopening the page or double clicking joins an existing active request.
    active = db.execute(select(E.Job).where(
        E.Job.user_id == principal.user_id, E.Job.job_type == kind,
        E.Job.status.in_(["queued", "running"]))).scalars().all()
    for job in active:
        if job.payload == payload:
            db.commit()
            return {"job_id": job.job_id, "status": job.status, "reused": True}
    return create_job(db, kind, payload, user_id=principal.user_id)


@router.post("/teacher/workflows/intervention/jobs")
def queue_intervention(body: InterventionIn,
                       principal=Depends(auth_service.require_teacher),
                       db: Session = Depends(get_session)) -> dict:
    from backend.app.services import learning_loop
    try:
        return learning_loop.queue(db, principal, body.model_dump())
    except Exception as exc:
        db.rollback()
        raise _http_error(exc) from exc


@router.post('/student/retests/jobs')
def queue_self_retest(body: SelfRetestIn, principal=Depends(auth_service.require_student),
                      db: Session = Depends(get_session)):
    from backend.app.services import learning_loop
    if body.attempt_id:
        auth_service.assert_attempt_access(db, principal, body.attempt_id)
    try:
        return learning_loop.queue(db, principal, body.model_dump())
    except Exception as exc:
        db.rollback()
        raise _http_error(exc) from exc


@router.get('/student/retests/options')
def self_retest_options(principal=Depends(auth_service.require_student), db: Session=Depends(get_session)):
    from backend.app.services.learning_resources import material_catalog
    return {'items':[{'kp_id':kp,'name':row['name']} for kp,row in material_catalog().items()]}


@router.get('/student/retests/state')
def self_retest_state(attempt_id: str='', principal=Depends(auth_service.require_student), db: Session=Depends(get_session)):
    from backend.app.services import learning_loop
    if attempt_id:
        auth_service.assert_attempt_access(db,principal,attempt_id)
    history=learning_loop.history_for(db,principal.student_id,attempt_id)
    result=learning_loop.public_summary(history)
    jobs=db.execute(select(E.Job).where(E.Job.job_type=='learning_retest',E.Job.status.in_(['queued','running']))).scalars().all()
    key=learning_loop._scope_key(principal.student_id,attempt_id)
    result['active_job_id']=next((j.job_id for j in jobs if key in (j.payload or {}).get('_scope_keys',[])), '')
    return result


@router.get('/student/retests/jobs/{job_id}')
def self_retest_job(job_id: str, principal=Depends(auth_service.require_student), db: Session=Depends(get_session)):
    job=db.get(E.Job,job_id)
    if not job or job.job_type!='learning_retest' or not any(
            s.get('student_id')==principal.student_id for s in (job.payload or {}).get('_scopes',[])):
        raise HTTPException(404,'任务不存在')
    out=((job.result or {}).get('outputs') or {}).get('retest') or {}
    own=next((r for r in out.get('items',[]) if r.get('student_id')==principal.student_id),None)
    # Never expose another student's diagnosis, answer, prompt or job payload.
    return {'job_id':job.job_id,'status':job.status,'stage':job.stage,
        'result':own,'error':'任务中断，可重试或使用题库。' if job.status=='failed' else ''}


@router.post("/teacher/paper-drafts/compose/jobs")
def queue_composition(body: PaperDraftComposeIn,
                      principal=Depends(auth_service.require_teacher),
                      db: Session = Depends(get_session)) -> dict:
    return _queue_teacher_job(db, principal, "teacher_compose", body.model_dump())


@router.get("/teacher/jobs/{job_id}")
def teacher_job(job_id: str, principal=Depends(auth_service.require_teacher),
                db: Session = Depends(get_session)) -> dict:
    job = db.get(E.Job, job_id)
    if not job or (job.user_id != principal.user_id and job.job_type != 'learning_retest'):
        raise HTTPException(404, "任务不存在")
    return {"job_id": job.job_id, "status": job.status, "stage": job.stage,
            "result": job.result, "error": job.error, "progress": job.progress}


@router.get("/teacher/prompt-history/export")
def export_prompts(_principal=Depends(auth_service.require_teacher),
                   db: Session = Depends(get_session)):
    import json
    from fastapi.responses import Response
    from backend.app.services.prompt_history import export_history
    return Response(json.dumps(export_history(db), ensure_ascii=False, indent=2),
                    media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="runtime-prompts.json"'})


@router.post("/teacher/ai-questions/generate")
def generate_teacher_questions(
    body: GenerateQuestionsIn,
    principal: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    return workflows.run_generation(db, {
        "kp_ids": body.kp_ids,
        "count": body.count,
        "question_types": body.question_types or ["single_choice", "true_false"],
        "mode": "teacher_question_bank",
        "instruction": body.instruction or (
            "为机器学习课程生成可诊断的新题；提供答案、解析和真实资料引用。"
        ),
    }, user_id=principal.user_id)


@router.post("/teacher/ai-candidates/{question_id}/approve")
def approve_ai_candidate(
    question_id: str, body: ApproveCandidateIn,
    principal: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    question = db.execute(select(E.Question).where(
        E.Question.question_id == question_id).order_by(
        E.Question.question_version.desc())).scalars().first()
    if not question:
        raise HTTPException(404, "候选题不存在")
    if not str(question.origin or "").startswith("ai"):
        raise HTTPException(409, "该题不是 AI 候选题")
    valid, reason = answer_pipeline.answer_structurally_valid(question)
    if not valid:
        raise HTTPException(409, f"答案结构尚不可发布：{reason}")
    question.review_status = "verified"
    question.answer_status = "verified"
    question.answer_origin = "teacher_verified_ai"
    question.active = True
    note = body.review_note.strip() or "教师已核对题干、答案与来源，可用于指定复测。"
    question.notes = ((question.notes or "") + "\n" +
                      f"[{E.utcnow()}] {principal.user_id}: {note}").strip()
    db.commit()
    return {"question_id": question.question_id,
            "question_version": question.question_version,
            "review_status": question.review_status, "active": question.active,
            "approved_by": principal.user_id}


@router.post("/teacher/papers/from-questions")
def teacher_paper_from_questions(
    body: TeacherPaperIn,
    principal: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    ids = list(dict.fromkeys(qid.strip() for qid in body.question_ids if qid.strip()))
    if len(ids) != len(body.question_ids):
        raise HTTPException(400, "题目 ID 不能为空或重复")
    questions = []
    for question_id in ids:
        question = db.execute(select(E.Question).where(
            E.Question.question_id == question_id,
            E.Question.active.is_(True),
        ).order_by(E.Question.question_version.desc())).scalars().first()
        if not question:
            raise HTTPException(409, f"题目不存在或已停用：{question_id}")
        ready, reason = paper_builder_service.quiz_gate(question)
        if not ready:
            raise HTTPException(409, f"题目不可组卷：{question_id}（{reason}）")
        questions.append(question)
    scores = body.scores or [float(q.score or 1) for q in questions]
    if len(scores) != len(questions) or any(float(x) <= 0 for x in scores):
        raise HTTPException(400, "分值必须与题数一致且大于 0")
    try:
        result = bank_service.create_paper_from_questions(
            db, questions, title=body.title.strip(), mode="quiz",
            source="teacher_retest", scores=[float(x) for x in scores],
            user_id=principal.user_id,
            blueprint={"source": "validated_question_bank",
                       "question_ids": ids})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    result["immutable_snapshot"] = True
    result["next_action"] = "通过发布接口选择学生；发布后固定为 AssessmentVersion。"
    return result


@router.post("/teacher/releases")
def publish(
    body: PublishIn,
    principal: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    try:
        return release_service.publish_release(
            db, teacher_user_id=principal.user_id,
            student_ids=body.student_ids, title=body.title,
            assessment_version_id=body.assessment_version_id,
            paper_id=body.paper_id, classroom_id=body.classroom_id,
            instructions=body.instructions, due_at=body.due_at,
            release_type=body.release_type,
            source_diagnosis_id=body.source_diagnosis_id,
            source_attempt_id=body.source_attempt_id,
            workflow_run_id=body.workflow_run_id,
            idempotency_key=body.idempotency_key,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/teacher/releases/{release_id}")
def teacher_release(
    release_id: str,
    _: auth_service.Principal = Depends(auth_service.require_teacher),
    db: Session = Depends(get_session),
) -> dict:
    try:
        return release_service.release_detail(db, release_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/student/assignments")
def student_assignments(
    principal: auth_service.Principal = Depends(auth_service.require_student),
    db: Session = Depends(get_session),
) -> dict:
    items = release_service.list_student_assignments(db, principal.student_id)
    from backend.app.services.learning_loop import self_assignments
    items = sorted(items + self_assignments(db, principal.student_id), key=lambda r:r.get('published_at') or '', reverse=True)
    return {"total": len(items), "items": items}


@router.post("/student/assignments/{recipient_id}/start")
def start_student_assignment(
    recipient_id: str,
    principal: auth_service.Principal = Depends(auth_service.require_student),
    db: Session = Depends(get_session),
) -> dict:
    try:
        return release_service.start_assignment(
            db, recipient_id, student_id=principal.student_id,
            user_id=principal.user_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/student/attempts/{attempt_id}")
def student_attempt(
    attempt_id: str,
    principal: auth_service.Principal = Depends(auth_service.require_student),
    db: Session = Depends(get_session),
) -> dict:
    auth_service.assert_attempt_access(db, principal, attempt_id)
    return attempt_service.attempt_state(db, attempt_id)


@router.get("/student/attempts/{attempt_id}/review")
def student_review_attempt(
    attempt_id: str,
    principal: auth_service.Principal = Depends(auth_service.require_student),
    db: Session = Depends(get_session),
) -> dict:
    attempt = auth_service.assert_attempt_access(db, principal, attempt_id)
    if attempt.status != "submitted":
        raise HTTPException(409, "答卷尚未提交")
    return teacher_attempt_review.teacher_attempt_review(db, attempt)


@router.put("/student/attempts/{attempt_id}/answers/{question_id}")
def save_student_answer(
    attempt_id: str, question_id: str, body: AnswerIn,
    principal: auth_service.Principal = Depends(auth_service.require_student),
    db: Session = Depends(get_session),
) -> dict:
    auth_service.assert_attempt_access(db, principal, attempt_id)
    try:
        return attempt_service.save_answer(
            db, attempt_id, question_id, body.response,
            hints_used=body.hints_used)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/student/assignments/{recipient_id}/submit")
def submit_student_assignment(
    recipient_id: str, body: SubmitIn,
    principal: auth_service.Principal = Depends(auth_service.require_student),
    db: Session = Depends(get_session),
) -> dict:
    try:
        return release_service.submit_assignment(
            db, recipient_id, student_id=principal.student_id,
            request_key=body.request_key)
    except Exception as exc:
        raise _http_error(exc) from exc


def _exam_chapters(db: Session, version: E.AssessmentVersion | None,
                   assessment: E.Assessment | None) -> list[dict]:
    chapter_ids: list[str] = []
    if assessment and assessment.chapter_id:
        chapter_ids.append(assessment.chapter_id)
    if version:
        chapter_ids.extend(db.execute(select(E.PaperItem.chapter_id).where(
            E.PaperItem.paper_id == version.paper_id,
            E.PaperItem.chapter_id != "",
        )).scalars().all())
    unique = list(dict.fromkeys(chapter_ids))
    labels = {row.chapter_id: row.title for row in db.execute(
        select(E.Chapter).where(E.Chapter.chapter_id.in_(unique))
    ).scalars().all()} if unique else {}
    return [{"chapter_id": chapter_id,
             "title": labels.get(chapter_id, chapter_id)}
            for chapter_id in unique]


def _regrade_for_attempt(db: Session, attempt_id: str) -> dict | None:
    if not attempt_id:
        return None
    row = db.execute(select(E.AttemptRegradeSummary).join(
        E.ScoringRun,
        E.ScoringRun.scoring_run_id == E.AttemptRegradeSummary.scoring_run_id,
    ).where(
        E.AttemptRegradeSummary.attempt_id == attempt_id,
        E.ScoringRun.status == "completed",
    ).order_by(E.ScoringRun.completed_at.desc(),
               E.ScoringRun.scoring_run_id.desc())).scalars().first()
    if row is None:
        return None
    return {
        "status": row.status,
        "awarded_score": row.awarded_score,
        "max_score": row.max_score,
        "coverage_rate": row.coverage_rate,
        "scored_items": row.scored_items,
        "computed_at": row.computed_at,
    }


def _student_exam_records(db: Session, student_id: str) -> list[dict]:
    """One chronological record per historical quiz plus online attempts."""
    records: list[dict] = []
    imported = db.execute(select(E.ImportedSubmission).where(
        E.ImportedSubmission.student_id == student_id,
        E.ImportedSubmission.status.in_(["canonical", "absent"]),
    ).order_by(E.ImportedSubmission.source_row,
               E.ImportedSubmission.created_at)).scalars().all()
    seen_assessments: set[str] = set()
    for source in imported:
        version = db.get(E.AssessmentVersion, source.assessment_version_id)
        assessment = db.get(E.Assessment, version.assessment_id) if version else None
        if not version or not assessment or assessment.assessment_id in seen_assessments:
            continue
        seen_assessments.add(assessment.assessment_id)
        attempt = (db.get(E.Attempt, source.canonical_attempt_id)
                   if source.canonical_attempt_id else None)
        absent = source.status == "absent" or attempt is None
        records.append({
            "record_id": source.submission_id,
            "source_type": "historical_import",
            "source_label": "Excel历史小测",
            "read_only": True,
            "absent": absent,
            "attendance_status": "absent" if absent else "attended",
            "assessment_id": assessment.assessment_id,
            "assessment_version_id": version.assessment_version_id,
            "attempt_id": attempt.attempt_id if attempt else "",
            "quiz_code": assessment.quiz_code,
            "title": assessment.title,
            "scheduled_at": assessment.scheduled_at,
            "submitted_at": attempt.submitted_at if attempt else "",
            "reported_score": (
                attempt.source_reported_total if attempt else None),
            "reported_max_score": (
                attempt.max_score if attempt and attempt.max_score_reliable
                else None),
            "platform_score": None,
            "platform_max_score": None,
            "grading_status": attempt.grading_status if attempt else "absent",
            "regrade": _regrade_for_attempt(
                db, attempt.attempt_id if attempt else ""),
            "answer_count": (len(db.execute(select(E.Answer.answer_id).where(
                E.Answer.attempt_id == attempt.attempt_id)).scalars().all())
                if attempt else 0),
            "chapters": _exam_chapters(db, version, assessment),
            "feedback_available": bool(attempt),
        })

    online_attempts = db.execute(select(E.Attempt).where(
        E.Attempt.student_id == student_id,
        E.Attempt.status == "submitted",
        E.Attempt.is_effective.is_(True),
        E.Attempt.source_import_run_id == "",
    ).order_by(E.Attempt.submitted_at.desc())).scalars().all()
    for attempt in online_attempts:
        current = attempt_service.grading_summary(db, attempt)
        version = (db.get(E.AssessmentVersion, attempt.assessment_version_id)
                   if attempt.assessment_version_id else None)
        assessment = (db.get(E.Assessment, version.assessment_id)
                      if version else None)
        records.append({
            "record_id": attempt.attempt_id,
            "source_type": ("online_retest" if attempt.mode == "retest"
                            else "online_exam"),
            "source_label": ("在线针对性复测" if attempt.mode == "retest"
                             else "在线考试"),
            "read_only": True,
            "absent": False,
            "attendance_status": attempt.attendance_status,
            "assessment_id": assessment.assessment_id if assessment else "",
            "assessment_version_id": attempt.assessment_version_id,
            "attempt_id": attempt.attempt_id,
            "quiz_code": assessment.quiz_code if assessment else "",
            "title": assessment.title if assessment else "在线考试",
            "scheduled_at": assessment.scheduled_at if assessment else "",
            "submitted_at": attempt.submitted_at,
            "reported_score": None,
            "reported_max_score": None,
            "platform_score": current["score"],
            "platform_max_score": current["max_score"],
            "grading_status": current["grading_status"],
            "scored_item_count": current["scored_item_count"],
            "ungraded_item_count": current["ungraded_item_count"],
            "regrade": None,
            "answer_count": len(db.execute(select(E.Answer.answer_id).where(
                E.Answer.attempt_id == attempt.attempt_id)).scalars().all()),
            "chapters": _exam_chapters(db, version, assessment),
            "feedback_available": True,
        })
    return sorted(records, key=lambda item: (
        str(item.get("scheduled_at") or item.get("submitted_at") or ""),
        str(item.get("quiz_code") or ""),
    ), reverse=True)


def _feedback_options(item: dict[str, Any]) -> list[dict[str, str]]:
    raw = item.get("options") or item.get("source_options") or []
    if isinstance(raw, dict):
        raw = [{"key": key, "text": value} for key, value in raw.items()]
    result = []
    for index, option in enumerate(raw if isinstance(raw, list) else []):
        if isinstance(option, dict):
            key = str(option.get("key") or chr(65 + index))
            text = str(option.get("text") or option.get("value") or "")
        else:
            key, text = chr(65 + index), str(option)
        result.append({"key": key, "text": text})
    return result


def _student_question_view(item: dict[str, Any]) -> dict[str, Any]:
    from backend.app.services.feedback_workbench import kp_ids
    reference = item.get("answer_reference") or {}
    comparison = item.get("class_comparison") or {}
    revised = bool(item.get("is_scoring_revision"))
    original = item.get("original_question") or {}
    return {
        "paper_item_id": item.get("paper_item_id") or item.get("item_id"),
        "question_id": item.get("question_id"),
        "kp_ids": kp_ids(item),
        "position": item.get("position"),
        "question_type": item.get("question_type") or item.get("type") or "",
        "stem": item.get("stem") or "",
        "options": _feedback_options(item),
        "shared_materials": [] if revised else item.get("shared_materials") or [],
        "source_blocks": [] if revised else item.get("source_blocks") or [],
        "source_options": [] if revised else item.get("source_options") or [],
        "content_warning": item.get("content_warning") or "",
        "is_scoring_revision": revised,
        "original_question": ({
            "stem": original.get("stem") or "",
            "options": _feedback_options(original),
        } if revised else None),
        "chapter_id": item.get("chapter_id") or (item.get("chapter") or {}).get("chapter_id") or "",
        "chapter": {
            "title": (item.get("chapter") or {}).get("title") or "未映射章节",
        },
        "knowledge_point": {
            "kp_id": (kp_ids(item) or [""])[0],
            "name": (item.get("knowledge_point") or {}).get("name")
                    or item.get("concept_name") or "本题对应概念",
        },
        "response_status": item.get("response_status") or "",
        "response": item.get("response"),
        "evidence_status": item.get("evidence_status") or "ungraded",
        "correct": item.get("correct"),
        "awarded_score": item.get("awarded_score"),
        "max_score": item.get("max_score"),
        "answer_reference": {
            "available": bool(reference.get("available")),
            "payload": reference.get("payload"),
            "explanation": reference.get("explanation") or "",
            "rubric": reference.get("rubric") or {},
        },
        "error_analysis": {
            "label": (item.get("error_analysis") or {}).get("label") or "",
        },
        "class_comparison": {
            "denominator": comparison.get("denominator"),
            "correct_count": comparison.get("correct_count"),
            "correct_rate": comparison.get("correct_rate"),
        },
    }


def _student_analysis_view(payload: dict[str, Any]) -> dict[str, Any]:
    analysis = payload.get("ai_analysis") or {}
    if analysis.get("analysis_version") in {2, 3}:
        return {
            "analysis_version": analysis.get("analysis_version"),
            "context": analysis.get("context"),
            "learning_plan": analysis.get("learning_plan") or [],
            "source": analysis.get("source"),
            "status": analysis.get("status"),
            "model_status": analysis.get("model_status"),
            "summary": analysis.get("summary") or "",
            "concepts": [{
                "kp_id": row.get("kp_id") or "",
                "name": row.get("name") or "",
                "chapter": row.get("chapter") or "",
                "question_positions": row.get("question_positions") or [],
                "question_refs": row.get("question_refs") or [],
                "observation": row.get("observation") or "",
                "analysis": row.get("analysis") or "",
                "priority": row.get("priority"),
                "priority_reason": row.get("priority_reason"),
                "analysis_source": row.get("analysis_source"),
            } for row in (analysis.get("concept_hypotheses") or [])
                if isinstance(row, dict)],
            "recommendations": (analysis.get("recommendations") or [])[:3],
            "retest_summary": analysis.get("retest_summary"),
            "evidence_details": (analysis.get("evidence_details") or [])[:6],
        }
    wrong = [row for row in payload.get("items") or []
             if row.get("evidence_status") == "scored"
             and row.get("correct") is False]
    concepts: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates = analysis.get("concept_hypotheses") or []
    if not candidates:
        candidates = [{
            "name": (row.get("knowledge_point") or {}).get("name"),
            "chapter": (row.get("chapter") or {}).get("title"),
            "question_positions": [row.get("position")],
        } for row in wrong]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        name = str(candidate.get("name") or candidate.get("concept") or "").strip()
        if not name or name in seen:
            continue
        positions = [int(value) for value in candidate.get("question_positions") or []
                     if str(value).isdigit()]
        related = [row for row in wrong if row.get("position") in positions]
        label = next((str((row.get("error_analysis") or {}).get("label") or "")
                      for row in related
                      if (row.get("error_analysis") or {}).get("label")), "")
        concepts.append({
            "name": name,
            "chapter": str(candidate.get("chapter") or
                           ((related[0].get("chapter") or {}).get("title")
                            if related else "")),
            "question_positions": positions,
            "description": (f"{label}，建议结合正确答案重新梳理解题依据。"
                            if label else "结合对应错题复习定义、条件和解题步骤。"),
        })
        seen.add(name)
        if len(concepts) >= 4:
            break
    recommendations = []
    for row in analysis.get("recommendations") or []:
        value = (row if isinstance(row, str) else
                 row.get("message") or row.get("advice") or row.get("action") or "")
        value = str(value).strip()
        if value and value not in recommendations:
            recommendations.append(value)
        if len(recommendations) >= 3:
            break
    return {
        "summary": str(analysis.get("summary") or "").strip(),
        "concepts": concepts,
        "recommendations": recommendations,
        "assisted": analysis.get("source") == "deepseek",
    }


@router.get("/student/feedback")
def student_feedback(
    principal: auth_service.Principal = Depends(auth_service.require_student),
    db: Session = Depends(get_session),
) -> dict:
    """Return only the authenticated learner's evidence and recommendations."""
    attempts = db.execute(select(E.Attempt).where(
        E.Attempt.student_id == principal.student_id,
        E.Attempt.status == "submitted",
        E.Attempt.is_effective.is_(True)).order_by(
        E.Attempt.submitted_at.desc())).scalars().all()
    items = []
    for attempt in attempts:
        # Targeted retests are presented under their source exam instead of as
        # unrelated papers in the selector.
        if attempt.mode == "retest" and attempt.retest_of_attempt_id:
            continue
        version = (db.get(E.AssessmentVersion, attempt.assessment_version_id)
                   if attempt.assessment_version_id else None)
        assessment = (db.get(E.Assessment, version.assessment_id)
                      if version else None)
        diagnosis = db.execute(select(E.Diagnosis).where(
            E.Diagnosis.student_id == principal.student_id,
            E.Diagnosis.assessment_version_id == attempt.assessment_version_id,
            E.Diagnosis.scope_type == "student_attempt",
            # Direct service calls retain the domain version while the Skill
            # adapter records its registry version.  Both represent the same
            # chapter diagnosis contract; selecting the newest scoped row
            # keeps feedback visible without rewriting audit provenance.
        ).order_by(E.Diagnosis.created_at.desc())).scalars().first()
        payload = (diagnosis.payload or {}) if diagnosis else {}
        grading = attempt_service.grading_summary(db, attempt)
        score = grading["score"]
        max_score = grading["max_score"]
        quiz_code = assessment.quiz_code if assessment else ""
        if str(quiz_code).startswith("paper_"):
            quiz_code = ""
        items.append({
            "attempt_id": attempt.attempt_id,
            "assessment_version_id": attempt.assessment_version_id,
            "title": assessment.title if assessment else "课堂测验",
            "quiz_code": quiz_code,
            "scheduled_at": assessment.scheduled_at if assessment else "",
            "submitted_at": attempt.submitted_at,
            "source_type": ("historical_import" if attempt.source_import_run_id
                            else "online_exam"),
            "source_label": ("历史课堂小测" if attempt.source_import_run_id
                             else "在线考试"),
            **grading,
            "score": score,
            "max_score": max_score,
            "score_rate": (round(float(score) / float(max_score), 6)
                           if score is not None and max_score and max_score > 0
                           else None),
            "grading_status": grading["grading_status"],
            "score_summary": payload.get("score_summary"),
            "chapters": payload.get("chapters", []),
            "knowledge_points": payload.get("knowledge_points", []),
            "resources": payload.get("resources", []),
        })
    states = db.execute(select(E.StudentKnowledgeState).where(
        E.StudentKnowledgeState.student_id == principal.student_id).order_by(
        E.StudentKnowledgeState.state, E.StudentKnowledgeState.kp_id)
    ).scalars().all()
    classroom_ids = sorted({
        assessment.classroom_id
        for attempt in attempts
        for version in [db.get(E.AssessmentVersion, attempt.assessment_version_id)]
        if version is not None
        for assessment in [db.get(E.Assessment, version.assessment_id)]
        if assessment is not None and assessment.classroom_id
    })
    chapter_states = []
    for classroom_id in classroom_ids:
        try:
            history = classroom_diagnosis.student_history(
                db, principal.student_id, classroom_id)
        except classroom_diagnosis.ClassroomDiagnosisError:
            continue
        for chapter in history.get("chapters") or []:
            latest = (chapter.get("series") or [{}])[-1]
            chapter_states.append({
                "classroom_id": classroom_id,
                "chapter_id": chapter.get("chapter_id"),
                "title": chapter.get("title"),
                "assessment_count": chapter.get("assessment_count"),
                "latest_score_rate": latest.get("score_rate"),
                "comparison_status": chapter.get("comparison_status"),
                "observation": (
                    "只描述现有考试与复测表现，不代表长期稳定掌握程度。"),
            })
    return {
        "student_id": principal.student_id,
        "exam_records": _student_exam_records(db, principal.student_id),
        "attempts": items,
        "chapter_states": chapter_states,
        "knowledge_states": [{
            "kp_id": row.kp_id, "state": row.state,
            "evidence_count": row.evidence_count,
            "correct_count": row.correct_count,
            "scored_assessment_count": row.scored_assessment_count,
            "explanation": row.explanation,
        } for row in states],
        "privacy": "仅返回当前登录账号显式绑定的学生档案数据。",
    }


@router.get("/student/feedback/{attempt_id}")
def student_feedback_detail(
    attempt_id: str,
    principal: auth_service.Principal = Depends(auth_service.require_student),
    db: Session = Depends(get_session),
) -> dict:
    """Student-friendly detail for one owned, completed source exam."""
    auth_service.assert_attempt_access(db, principal, attempt_id)
    attempt = db.get(E.Attempt, attempt_id)
    if (attempt is None or attempt.status != "submitted"
            or not attempt.is_effective or (attempt.mode == "retest" and attempt.retest_of_attempt_id)):
        raise HTTPException(404, "未找到可查看的已完成试卷")
    try:
        payload = classroom_diagnosis.student_attempt(
            db, principal.student_id, attempt.assessment_version_id,
            attempt_id=attempt.attempt_id)
    except Exception as exc:
        raise _http_error(exc) from exc

    # Prefer the latest saved analysis for this exact attempt, then attach the
    # current deterministic retest outcome so an old diagnosis is never shown
    # unchanged after a new retest submission.
    diagnosis = db.execute(select(E.Diagnosis).where(
        E.Diagnosis.student_id == principal.student_id,
        E.Diagnosis.assessment_version_id == attempt.assessment_version_id,
        E.Diagnosis.scope_type == "student_attempt",
        E.Diagnosis.skill_version == classroom_diagnosis.SKILL_VERSION,
    ).order_by(E.Diagnosis.created_at.desc())).scalars().first()
    saved = (diagnosis.payload or {}) if diagnosis else {}
    if (saved.get("attempt_id") in {None, "", attempt.attempt_id}
            and saved.get("ai_analysis")):
        from backend.app.services import targeted_retest
        payload["ai_analysis"] = targeted_retest.enrich_analysis(
            saved["ai_analysis"], payload.get("retest_history") or [])

    comparison = payload.get("class_comparison") or {}
    score_summary = payload.get("score_summary") or {}
    resources = [{
        **{key:row.get(key) for key in ('resource_id','kp_id','kp_ids','kp_name','location',
                                      'study_focus','success_check','suggested_minutes','platform',
                                      'content_type','source_type','checked_date','access_status','content_readable')},
        "title": row.get("title") or "学习资源",
        "type": row.get("platform") or row.get("source_type") or "资源",
        "reason": row.get("reason") or "与本次错题相关",
        "url": row.get("url") or "",
    } for row in (payload.get("resources") or [])
       if isinstance(row, dict) and row.get("url")]
    retest_history = []
    for round_row in payload.get("retest_history") or []:
        retest_history.append({
            **{key: round_row.get(key) for key in (
                "cycle_id", "kp_ids", "remaining_kp_ids", "untested_kp_ids",
                "release_id", "release_title", "retest_attempt_id", "result_determinable")},
            "round": round_row.get("round"),
            "status": round_row.get("status"),
            "threshold": round_row.get("threshold"),
            "question_count": round_row.get("question_count"),
            "correct_count": round_row.get("correct_count"),
            "accuracy": round_row.get("accuracy"),
            "published_at": round_row.get("published_at"),
            "submitted_at": round_row.get("submitted_at"),
            "wrong_items": [_student_question_view(row)
                            for row in round_row.get("wrong_items") or []],
        })
    from backend.app.services.feedback_workbench import build as feedback_workbench
    return {
        "diagnosis_workbench": feedback_workbench(payload, _student_question_view),
        "learning_loop": payload.get('learning_loop'),
        "attempt_id": attempt.attempt_id,
        "assessment": {
            "title": (payload.get("assessment") or {}).get("title") or "课堂测验",
            "quiz_code": (payload.get("assessment") or {}).get("quiz_code") or "",
            "topic": (payload.get("assessment") or {}).get("topic") or "",
            "scheduled_at": (payload.get("assessment") or {}).get("scheduled_at") or "",
        },
        "submitted_at": payload.get("submitted_at") or "",
        "score_summary": score_summary,
        "class_comparison": {
            "status": comparison.get("status"),
            "rank": comparison.get("rank"),
            "participant_count": comparison.get("participant_count"),
            "reason": comparison.get("reason") or "",
        },
        "items": [_student_question_view(row)
                  for row in payload.get("items") or []],
        "analysis": _student_analysis_view(payload),
        "resources": resources,
        "retest_history": retest_history,
        "retest_threshold": (payload.get("retest_plan") or {}).get(
            "accuracy_threshold", cfg.TARGETED_RETEST_ACCURACY_THRESHOLD),
    }



@router.get("/classroom-media/{source_sha}/{asset_id}")
def classroom_media(source_sha: str, asset_id: str,
                    _: auth_service.Principal = Depends(auth_service.get_principal)):
    from fastapi.responses import FileResponse, Response
    from backend.app.services.classroom_content import asset_path, render_emf_png
    try:
        path = asset_path(source_sha, asset_id)
        if path.suffix.lower() == ".emf":
            return Response(render_emf_png(path), media_type="image/png",
                            headers={"Cache-Control": "private, max-age=3600"})
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})
