"""Teacher publication and student assignment lifecycle."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import entities as E
from backend.app.services import attempt_service


LOCAL_TIMEZONE = timezone(timedelta(hours=8))

TASK_MINUTES_BY_TYPE = {
    "true_false": 1.5, "single_choice": 1.5,
    "multiple_choice": 2.5, "multi_choice": 2.5,
    "fill_blank": 2.5, "formula_fill": 2.5, "matching": 3,
    "numeric": 4, "calculation": 5, "short_answer": 5,
    "term": 3, "derivation": 6, "comprehensive": 6,
}


def _task_paper_facts(db: Session, paper_id: str) -> tuple[int, int | None]:
    """Count fixed paper items and estimate reading/work time from their types."""
    if not paper_id:
        return 0, None
    items = db.execute(select(E.PaperItem).where(
        E.PaperItem.paper_id == paper_id).order_by(
        E.PaperItem.position)).scalars().all()
    if not items:
        return 0, None
    minutes = 0.0
    for item in items:
        snapshot = item.snapshot or {}
        kind = str(snapshot.get("question_type") or "")
        if (kind not in TASK_MINUTES_BY_TYPE
                or not (snapshot.get("stem") or snapshot.get("stem_zh"))
                or (kind in {"single_choice", "multiple_choice", "multi_choice", "matching"}
                    and not snapshot.get("options"))):
            return len(items), None
        minutes += TASK_MINUTES_BY_TYPE[kind]
    return len(items), math.ceil(minutes)


def _parse_due_at(value: str) -> datetime | None:
    """Parse the browser's datetime-local value and compare it in UTC.

    The platform is a local classroom app in Asia/Shanghai.  Values with an
    explicit offset keep that offset; values from ``datetime-local`` are
    interpreted as China Standard Time instead of accidentally as UTC.
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("截止时间格式无效") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def _is_expired(due_at: str) -> bool:
    due = _parse_due_at(due_at)
    return bool(due and due <= datetime.now(timezone.utc))


def _online_account(db: Session, student: E.Student) -> E.User | None:
    if not student.active or not student.linked_user_id:
        return None
    user = db.get(E.User, student.linked_user_id)
    if not user or not user.active or user.role != "student":
        return None
    return user


def _stable(prefix: str, value: str, length: int = 24) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def _paper_hash(db: Session, paper_id: str) -> str:
    items = db.execute(select(E.PaperItem).where(
        E.PaperItem.paper_id == paper_id).order_by(
        E.PaperItem.position)).scalars().all()
    payload = [{
        "position": item.position,
        "question_id": item.question_id,
        "question_version": item.question_version,
        "score": item.score,
        "snapshot": item.snapshot,
    } for item in items]
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def ensure_version_for_paper(db: Session, paper_id: str,
                             classroom_id: str = "", *, commit: bool = True) -> E.AssessmentVersion:
    paper = db.get(E.Paper, paper_id)
    if not paper:
        raise KeyError(f"试卷不存在: {paper_id}")
    existing = db.execute(select(E.AssessmentVersion).where(
        E.AssessmentVersion.paper_id == paper_id)).scalars().first()
    if existing:
        return existing

    if classroom_id:
        classroom = db.get(E.Classroom, classroom_id)
        if not classroom:
            raise KeyError(f"课堂不存在: {classroom_id}")
    else:
        classroom = db.execute(select(E.Classroom).order_by(
            E.Classroom.created_at)).scalars().first()
        if not classroom:
            classroom = E.Classroom(
                classroom_id="classroom_demo",
                course_key="teacher-demo",
                name="机器学习课程演示班",
                term="演示",
                source="teacher_created",
            )
            db.add(classroom)
            db.flush()

    external_key = f"{classroom.course_key}:paper:{paper_id}"
    assessment = db.execute(select(E.Assessment).where(
        E.Assessment.external_key == external_key)).scalars().first()
    if not assessment:
        assessment = E.Assessment(
            assessment_id=_stable("assessment", external_key),
            classroom_id=classroom.classroom_id,
            external_key=external_key,
            quiz_code=paper_id,
            title=paper.title or "教师发布试卷",
            topic=paper.title or "",
            source="teacher_created",
            details={"legacy_paper_id": paper_id},
        )
        db.add(assessment)
        db.flush()

    content_hash = _paper_hash(db, paper_id)
    version = E.AssessmentVersion(
        assessment_version_id=_stable("av", f"{external_key}:1"),
        assessment_id=assessment.assessment_id,
        version_no=1,
        version_key=f"{external_key}:v1",
        paper_id=paper_id,
        content_hash=content_hash,
        scoring_status="graded",
        answer_key_source="paper_snapshot",
        teacher_verified=False,
        scoring_policy={"source": "immutable_paper_item_snapshot"},
    )
    db.add(version)
    db.commit() if commit else db.flush()
    return version


def publish_release(db: Session, *, teacher_user_id: str,
                    student_ids: list[str], title: str,
                    assessment_version_id: str = "", paper_id: str = "",
                    classroom_id: str = "", instructions: str = "",
                    due_at: str = "", release_type: str = "exam",
                    source_diagnosis_id: str = "",
                    source_attempt_id: str = "",
                    workflow_run_id: str = "",
                    idempotency_key: str | None = None, commit: bool = True) -> dict:
    if release_type not in {"exam", "retest"}:
        raise ValueError("release_type 必须是 exam 或 retest")
    unique_students = list(dict.fromkeys(s for s in student_ids if s))
    if not unique_students:
        raise ValueError("至少选择一名学生")
    teacher = db.get(E.User, teacher_user_id)
    if not teacher or teacher.role != "teacher":
        raise PermissionError("发布者不是教师账号")
    students = [db.get(E.Student, sid) for sid in unique_students]
    missing = [sid for sid, student in zip(unique_students, students)
               if student is None]
    if missing:
        raise KeyError(f"学生不存在: {', '.join(missing)}")
    offline = [student.display_code for student in students
               if student is not None and _online_account(db, student) is None]
    if offline:
        raise ValueError(
            "以下学生档案没有可登录的在线账号，不能接收在线试卷："
            + "、".join(offline)
        )
    due = _parse_due_at(due_at)
    if due and due <= datetime.now(timezone.utc):
        raise ValueError("截止时间必须晚于当前时间")

    version = (db.get(E.AssessmentVersion, assessment_version_id)
               if assessment_version_id else None)
    if not version and paper_id:
        version = ensure_version_for_paper(db, paper_id, classroom_id, commit=False)
    if not version:
        raise KeyError("固定试卷版本不存在")
    assessment = db.get(E.Assessment, version.assessment_id)
    if not assessment:
        raise KeyError("试卷版本缺少所属课堂")
    member_ids = set(db.execute(select(E.ClassroomStudent.student_id).where(
        E.ClassroomStudent.classroom_id == assessment.classroom_id,
        E.ClassroomStudent.status == "active",
    )).scalars().all())
    outsiders = [sid for sid in unique_students if sid not in member_ids]
    if outsiders:
        raise ValueError(
            "发布对象不属于该试卷所在课堂：" + ", ".join(outsiders))

    if idempotency_key:
        old = db.execute(select(E.AssessmentRelease).where(
            E.AssessmentRelease.idempotency_key == idempotency_key
        )).scalars().first()
        if old:
            old_students = set(db.execute(select(
                E.ReleaseRecipient.student_id).where(
                E.ReleaseRecipient.release_id == old.release_id
            )).scalars().all())
            same_request = (
                old.assessment_version_id == version.assessment_version_id
                and old.teacher_user_id == teacher_user_id
                and old.title == (title.strip() or "课堂测验")
                and old.instructions == instructions.strip()
                and old.release_type == release_type
                and old.due_at == due_at
                and old_students == set(unique_students)
            )
            if not same_request:
                raise ValueError("发布请求标识已用于另一份发布，请刷新后重试")
            return release_detail(db, old.release_id)

    release = E.AssessmentRelease(
        release_id=E.new_id("release"),
        assessment_version_id=version.assessment_version_id,
        teacher_user_id=teacher_user_id,
        title=title.strip() or "课堂测验",
        instructions=instructions.strip(),
        release_type=release_type,
        status="published",
        due_at=due_at,
        source_diagnosis_id=source_diagnosis_id,
        source_attempt_id=source_attempt_id,
        workflow_run_id=workflow_run_id,
        idempotency_key=idempotency_key,
        published_at=E.utcnow(),
    )
    db.add(release)
    db.flush()
    for student_id in unique_students:
        db.add(E.ReleaseRecipient(
            recipient_id=E.new_id("recipient"),
            release_id=release.release_id,
            student_id=student_id,
            status="assigned",
        ))
    db.commit() if commit else db.flush()
    return release_detail(db, release.release_id)


def release_detail(db: Session, release_id: str) -> dict:
    release = db.get(E.AssessmentRelease, release_id)
    if not release:
        raise KeyError(release_id)
    version = db.get(E.AssessmentVersion, release.assessment_version_id)
    assessment = db.get(E.Assessment, version.assessment_id) if version else None
    paper = db.get(E.Paper, version.paper_id) if version else None
    item_count, estimated_minutes = _task_paper_facts(
        db, version.paper_id if version else "")
    recipients = db.execute(select(E.ReleaseRecipient).where(
        E.ReleaseRecipient.release_id == release_id).order_by(
        E.ReleaseRecipient.assigned_at)).scalars().all()
    items = []
    for recipient in recipients:
        student = db.get(E.Student, recipient.student_id)
        account = _online_account(db, student) if student else None
        attempt = db.get(E.Attempt, recipient.attempt_id) if recipient.attempt_id else None
        effective_status = recipient.status
        if (effective_status in {"assigned", "opened"}
                and (release.status != "published" or _is_expired(release.due_at))):
            effective_status = "expired"
        items.append({
            "recipient_id": recipient.recipient_id,
            "student_id": recipient.student_id,
            "student_code": student.display_code if student else "未知学生",
            "linked_account": account.user_id if account else "",
            "linked_display_name": account.display_name if account else "",
            "online_receivable": account is not None,
            "status": effective_status,
            "attempt_id": recipient.attempt_id,
            "score": attempt.score if attempt and attempt.status == "submitted" else None,
            "max_score": attempt.max_score if attempt and attempt.status == "submitted" else None,
            "grading_status": attempt.grading_status if attempt else "not_started",
            **(attempt_service.grading_summary(db, attempt) if attempt and attempt.status == "submitted" else {}),
            "submitted_at": recipient.submitted_at,
        })
    targeted_cycle = db.execute(select(E.TargetedRetestCycle).where(
        E.TargetedRetestCycle.release_id == release_id
    )).scalars().first()
    return {
        "release_id": release.release_id,
        "title": release.title,
        "instructions": release.instructions,
        "release_type": release.release_type,
        "status": release.status,
        "due_at": release.due_at,
        "published_at": release.published_at,
        "assessment_version_id": release.assessment_version_id,
        "assessment_id": assessment.assessment_id if assessment else "",
        "paper_id": version.paper_id if version else "",
        "paper_title": paper.title if paper else "",
        "item_count": item_count,
        "estimated_minutes": estimated_minutes,
        "recipient_count": len(items),
        "submitted_count": sum(1 for item in items if item["status"] == "submitted"),
        "pending_count": sum(1 for item in items
                             if item["status"] in {"assigned", "opened"}),
        "expired_count": sum(1 for item in items if item["status"] == "expired"),
        "recipients": items,
        "source_diagnosis_id": release.source_diagnosis_id,
        "source_attempt_id": release.source_attempt_id,
        "workflow_run_id": release.workflow_run_id,
        "targeted_retest": ({
            "cycle_id": targeted_cycle.cycle_id,
            "round": targeted_cycle.round_no,
            "status": targeted_cycle.status,
            "passed": targeted_cycle.status == "passed",
            "threshold": targeted_cycle.threshold,
            "question_count": targeted_cycle.question_count,
            "correct_count": targeted_cycle.correct_count,
            "accuracy": targeted_cycle.accuracy,
        } if targeted_cycle else None),
    }


def list_releases(db: Session, classroom_id: str = "") -> list[dict]:
    stmt = select(E.AssessmentRelease)
    if classroom_id:
        stmt = (stmt
                .join(E.AssessmentVersion,
                      E.AssessmentVersion.assessment_version_id ==
                      E.AssessmentRelease.assessment_version_id)
                .join(E.Assessment,
                      E.Assessment.assessment_id ==
                      E.AssessmentVersion.assessment_id)
                .where(E.Assessment.classroom_id == classroom_id))
    rows = db.execute(stmt.order_by(
        E.AssessmentRelease.created_at.desc())).scalars().all()
    return [release_detail(db, row.release_id) for row in rows]


def list_student_assignments(db: Session, student_id: str) -> list[dict]:
    recipients = db.execute(select(E.ReleaseRecipient).where(
        E.ReleaseRecipient.student_id == student_id).order_by(
        E.ReleaseRecipient.assigned_at.desc())).scalars().all()
    result = []
    for recipient in recipients:
        detail = release_detail(db, recipient.release_id)
        release_status = detail["status"]
        own = next(item for item in detail["recipients"]
                   if item["recipient_id"] == recipient.recipient_id)
        detail.pop("recipients", None)
        detail["release_status"] = release_status
        detail.update(own)
        cycle = db.execute(select(E.TargetedRetestCycle).where(
            E.TargetedRetestCycle.release_id == recipient.release_id,
            E.TargetedRetestCycle.student_id == student_id)).scalars().first()
        if cycle:
            detail["targeted_retest"] = {
                "cycle_id": cycle.cycle_id, "round": cycle.round_no,
                "status": cycle.status, "question_count": cycle.question_count,
                "correct_count": cycle.correct_count, "accuracy": cycle.accuracy}
        else:
            detail["targeted_retest"] = None
        # A submitted answer sheet remains visible even if the release is
        # subsequently closed.  Unsubmitted drafts/closed releases are not
        # actionable; expired assignments are returned explicitly so the UI
        # never counts them as pending.
        if own["status"] == "submitted" or release_status == "published":
            result.append(detail)
    return result


def start_assignment(db: Session, recipient_id: str, *, student_id: str,
                     user_id: str) -> dict:
    recipient = db.get(E.ReleaseRecipient, recipient_id)
    if not recipient or recipient.student_id != student_id:
        raise PermissionError("不能打开其他学生的任务")
    release = db.get(E.AssessmentRelease, recipient.release_id)
    if not release or release.status != "published":
        raise ValueError("任务当前不可作答")
    if _is_expired(release.due_at):
        recipient.status = "expired"
        db.commit()
        raise ValueError("任务已超过截止时间，不能开始作答")
    if recipient.attempt_id:
        return attempt_service.attempt_state(db, recipient.attempt_id)
    version = db.get(E.AssessmentVersion, release.assessment_version_id)
    if not version:
        raise KeyError("试卷版本不存在")
    created = attempt_service.create_attempt(
        db, version.paper_id, user_id=user_id,
        mode="retest" if release.release_type == "retest" else "quiz",
        student_id=student_id,
        assessment_version_id=version.assessment_version_id,
    )
    attempt = db.get(E.Attempt, created["attempt_id"])
    attempt.assignment_recipient_id = recipient.recipient_id
    attempt.retest_of_attempt_id = release.source_attempt_id
    cycle = db.execute(select(E.TargetedRetestCycle).where(
        E.TargetedRetestCycle.release_id == release.release_id,
        E.TargetedRetestCycle.student_id == student_id)).scalars().first()
    if cycle:
        attempt.retest_of_attempt_id = cycle.source_attempt_id
    attempt.diagnosis_cycle_id = release.source_diagnosis_id
    recipient.attempt_id = attempt.attempt_id
    recipient.status = "opened"
    recipient.opened_at = E.utcnow()
    db.commit()
    return attempt_service.attempt_state(db, attempt.attempt_id)


def submit_assignment(db: Session, recipient_id: str, *, student_id: str,
                      request_key: str | None = None) -> dict:
    recipient = db.get(E.ReleaseRecipient, recipient_id)
    if (not recipient or recipient.student_id != student_id or
            not recipient.attempt_id):
        raise PermissionError("不能提交其他学生的任务")
    release = db.get(E.AssessmentRelease, recipient.release_id)
    if recipient.status != "submitted":
        if not release or release.status != "published":
            raise ValueError("任务当前不可提交")
        if _is_expired(release.due_at):
            recipient.status = "expired"
            db.commit()
            raise ValueError("任务已超过截止时间，不能提交")
    result, created = attempt_service.submit_attempt(
        db, recipient.attempt_id, request_key=request_key)
    recipient.status = "submitted"
    recipient.submitted_at = result.get("submitted_at") or E.utcnow()
    db.commit()
    result["newly_submitted"] = created
    result["release_id"] = recipient.release_id
    result["recipient_id"] = recipient.recipient_id
    if release and release.release_type == "retest":
        from backend.app.services import targeted_retest
        outcome = targeted_retest.record_submission(
            db, release_id=release.release_id,
            attempt_id=recipient.attempt_id)
        if outcome is not None:
            result["targeted_retest"] = outcome
    # The score is already committed by attempt_service.  Classroom diagnosis
    # is a follow-up workflow: a failure must never roll back or hide the
    # student's submitted answer sheet.
    if created:
        try:
            from backend.app.graphs import classroom_workflow
            workflow = classroom_workflow.run_post_submission(
                db,
                actor_user_id=(release.teacher_user_id if release else
                               db.get(E.Attempt, recipient.attempt_id).user_id),
                attempt_id=recipient.attempt_id,
            )
            result["classroom_workflow"] = workflow
        except Exception as exc:  # score/submission remains authoritative
            result["diagnosis_warning"] = (
                "答卷和成绩已保存，但课堂诊断暂未完成："
                f"{type(exc).__name__}: {exc}"
            )
    return result
