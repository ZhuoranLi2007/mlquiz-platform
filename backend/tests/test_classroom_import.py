from __future__ import annotations

from datetime import datetime
import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

openpyxl = pytest.importorskip("openpyxl")
from openpyxl import Workbook  # noqa: E402

from backend.app.models import entities as E  # noqa: E402
from backend.app.models.base import Base  # noqa: E402
from backend.app.services.classroom_import import (  # noqa: E402
    import_teacher_classroom,
    parse_teacher_classroom_workbook,
)


PRIVACY_SENTINEL = "PRIVATE_VALUE_MUST_NOT_BE_IMPORTED"


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _teacher_workbook(path) -> None:
    """Build two dynamic quiz blocks covering every required state."""
    workbook = Workbook()
    detail = workbook.active
    detail.title = "答题明细"
    overview = workbook.create_sheet("小测概览")
    students = workbook.create_sheet("学生汇总")
    questions = workbook.create_sheet("题目清单")
    notes = workbook.create_sheet("说明")

    # A privacy value exists in both places the importer is forbidden to read.
    workbook.properties.lastModifiedBy = PRIVACY_SENTINEL
    notes.append(["数据整合说明", None])
    notes.append(["项目", "内容"])
    notes.append(["重复提交", PRIVACY_SENTINEL])

    detail.append(["合成课堂数据"])
    detail.append(["第2行说明"])
    detail.cell(3, 1, "学号")
    detail.cell(3, 2, "姓名")
    detail.merge_cells("A3:A4")
    detail.merge_cells("B3:B4")
    detail.cell(3, 3, "小测1.1 线性回归　09-22　· 已判分")
    detail.merge_cells("C3:G3")
    detail.cell(3, 8, "小测3 神经网络　10-20　· 得分待补")
    detail.merge_cells("H3:J3")
    for column, value in enumerate(
        ["总分", "Q1\n答案", "Q1\n得分", "Q2\n答案", "Q2\n得分"],
        start=3,
    ):
        detail.cell(4, column, value)
    for column, value in enumerate(["总分", "Q1\n答案", "Q1\n得分"], start=8):
        detail.cell(4, column, value)

    # STU001 attended 1.1 and was absent from quiz 3.
    detail.cell(5, 2, "STU001")
    for column, value in enumerate([10, "对", 5, "错", 5], start=3):
        detail.cell(5, column, value)
    # STU002 really scored zero in 1.1, then submitted an ungraded quiz 3.
    detail.cell(6, 2, "STU002")
    for column, value in enumerate([0, "错", 0, "对", 0], start=3):
        detail.cell(6, column, value)
    for column, value in enumerate([5, "选项甲┋选项丙", None], start=8):
        detail.cell(6, column, value)

    overview.append(["课堂概览"])
    overview.append([
        "序号", "小测编号", "小测主题", "小测日期", "题数", "已判分题数",
        "参考人数", "平均分", "最高分", "最低分", "标准差",
    ])
    overview.append([1, "1.1", "线性回归", datetime(2025, 9, 22), 2, 2,
                     2, 5, 10, 0, 7.07])
    overview.append([2, "3", "神经网络", datetime(2025, 10, 20), 1, 0,
                     1, 5, 5, 5, 0])

    students.append(["学生汇总"])
    students.append([
        "学号", "姓名", "参考次数", "缺考次数", "参考率", "累计总分",
        "平均分", "累计排名", "缺考的小测",
    ])
    students.append([None, "STU001", 1, 1, 0.5, 10, 10, 1, "3"])
    students.append([None, "STU002", 2, 0, 1.0, 5, 2.5, 2, "—"])

    questions.append(["题目清单"])
    questions.append([
        "序号", "小测编号", "小测主题", "小测日期", "题号", "答题明细列",
        "题型（推断）", "标准答案", "每题分值", "判分状态", "题干（原文）",
    ])
    questions.append([
        1, "1.1", "线性回归", datetime(2025, 9, 22), "Q1", "1.1-Q1",
        "判断题", "对", 5, "已判分（反解）", "线性回归输出连续值。",
    ])
    questions.append([
        2, "1.1", "线性回归", datetime(2025, 9, 22), "Q2", "1.1-Q2",
        "判断题", "错", 5, "已判分（反解）", "梯度下降只需一步。",
    ])
    questions.append([
        3, "3", "神经网络", datetime(2025, 10, 20), "Q1", "3-Q1",
        "多选题（选项文本）", "待补", "待补", "待补标准答案", "选择激活函数。",
    ])
    workbook.save(path)


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def test_parser_discovers_blocks_and_reconciles_states(tmp_path):
    path = tmp_path / "teacher.xlsx"
    _teacher_workbook(path)

    parsed = parse_teacher_classroom_workbook(path)
    summary = parsed.summary()

    assert summary == {
        "file_sha256": parsed.file_sha256,
        "students": 2,
        "assessments": 2,
        "questions": 3,
        "attempts": 3,
        "absences": 1,
        "submission_blocks": 4,
        "answers": 5,
        "scored_answers": 4,
        "pending_answers": 1,
        "reconciliation": {
            "question_counts_match": True,
            "participant_counts_match": True,
            "student_attempt_counts_match": True,
            "complete_student_quiz_matrix": True,
            "all_pass": True,
            "participant_total": 3,
            "overview_participant_total": 3,
        },
    }
    zero = next(
        item for item in parsed.submissions
        if item.student_code == "STU002" and item.quiz_code == "1.1"
    )
    absent = next(
        item for item in parsed.submissions
        if item.student_code == "STU001" and item.quiz_code == "3"
    )
    pending = next(
        item for item in parsed.submissions
        if item.student_code == "STU002" and item.quiz_code == "3"
    )
    assert zero.status == "canonical" and zero.source_reported_total == 0
    assert absent.status == "absent" and absent.responses == ()
    assert pending.responses[0].item_score is None


def test_import_is_idempotent_and_preserves_zero_absent_pending(
        db, tmp_path):
    path = tmp_path / "teacher.xlsx"
    _teacher_workbook(path)

    first = import_teacher_classroom(
        db, path, course_key="test-course", classroom_name="测试班",
        term="2025秋", is_test_fixture=True,
    )
    second = import_teacher_classroom(
        db, path, course_key="test-course", classroom_name="测试班",
        term="2025秋", is_test_fixture=True,
    )

    assert first == second
    assert first["students"] == 2
    assert first["assessments"] == 2
    assert first["questions"] == 3
    assert first["attempts"] == 3
    assert first["absences"] == 1
    assert first["reconciliation"]["all_pass"] is True
    assert first["student_login_accounts_created"] is False

    assert _count(db, E.Student) == 2
    assert _count(db, E.Classroom) == 1
    assert _count(db, E.ClassroomStudent) == 2
    assert _count(db, E.Assessment) == 2
    assert _count(db, E.AssessmentVersion) == 2
    assert _count(db, E.Paper) == 2
    assert _count(db, E.PaperItem) == 3
    assert _count(db, E.Question) == 3
    assert _count(db, E.Attempt) == 3
    assert _count(db, E.ImportedSubmission) == 4
    assert _count(db, E.Answer) == 5
    assert _count(db, E.AnswerScore) == 5
    assert _count(db, E.ImportRun) == 1
    assert _count(db, E.User) == 0

    stu2 = db.scalar(select(E.Student).where(E.Student.display_code == "STU002"))
    assert stu2 is not None and stu2.linked_user_id is None
    zero_attempt = db.scalar(select(E.Attempt).where(
        E.Attempt.student_id == stu2.student_id,
        E.Attempt.source_reported_total == 0,
    ))
    assert zero_attempt is not None
    assert zero_attempt.attendance_status == "attended"
    assert zero_attempt.score == 0

    absent = db.scalar(select(E.ImportedSubmission).where(
        E.ImportedSubmission.status == "absent",
    ))
    assert absent is not None
    assert absent.canonical_attempt_id is None
    assert absent.raw_preserved is False

    pending = db.scalar(select(E.AnswerScore).where(
        E.AnswerScore.scoring_status == "pending_key",
    ))
    assert pending is not None
    assert pending.correct is None
    assert pending.score_provenance == "unavailable_missing_key"
    assert pending.authority_status == "not_scored"

    inferred = db.scalars(select(E.AnswerScore).where(
        E.AnswerScore.score_provenance == "reverse_inferred_totals",
    )).all()
    assert len(inferred) == 4
    assert all(row.teacher_verified is False for row in inferred)

    # The notes sheet and workbook properties contain the sentinel.  A raw DB
    # dump proves that neither source was copied into any persisted field.
    driver = db.connection().connection.driver_connection
    dump = "\n".join(driver.iterdump())
    assert PRIVACY_SENTINEL not in dump
    assert PRIVACY_SENTINEL not in json.dumps(first, ensure_ascii=False)
