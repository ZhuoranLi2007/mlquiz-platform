"""Import the anonymized teacher classroom workbook into normalized tables.

The workbook is a *canonical snapshot*, not the raw Questionnaire Star export.
This importer therefore persists all 160 student/quiz blocks as
``ImportedSubmission`` rows, creates ``Attempt`` rows only for the 118 attended
blocks, and marks ``raw_preserved=False``.  It never reads the workbook's
``说明`` sheet or document properties because both may contain personal data.

Parsing and persistence are intentionally separate.  ``parse_*`` is a pure,
read-only validation step; ``import_*`` performs idempotent UPSERTs using
stable IDs derived from the course key and source natural keys.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable

from sqlalchemy.orm import Session

from backend.app.models import entities as E


REQUIRED_SHEETS = ("答题明细", "小测概览", "学生汇总", "题目清单")
ANONYMOUS_CODE_RE = re.compile(r"^STU\d{3,}$", re.IGNORECASE)
QUIZ_TITLE_RE = re.compile(r"^小测(?P<code>\S+)\s")
QUESTION_HEADER_RE = re.compile(r"^Q(?P<number>\d+)\s*(?P<kind>答案|得分)$")
PENDING_MARKERS = {"", "待补", "待补标准答案", "未提供", "无"}


class ClassroomImportError(ValueError):
    """Raised when workbook facts are ambiguous or internally inconsistent."""


@dataclass(frozen=True)
class QuizRecord:
    code: str
    topic: str
    scheduled_at: str
    question_count: int
    judged_question_count: int
    participant_count: int
    overview_row: int
    start_col: int = 0
    end_col: int = 0


@dataclass(frozen=True)
class QuestionRecord:
    quiz_code: str
    number: str
    detail_key: str
    question_type: str
    raw_type: str
    stem: str
    standard_answer: str | None
    points: float | None
    scoring_status: str
    source_row: int


@dataclass(frozen=True)
class ResponseRecord:
    question_number: str
    raw_answer: str | None
    item_score: float | None
    answer_cell: str
    score_cell: str


@dataclass(frozen=True)
class SubmissionRecord:
    student_code: str
    quiz_code: str
    source_row: int
    status: str
    source_reported_total: float | None
    responses: tuple[ResponseRecord, ...] = ()


@dataclass
class ParsedClassroomWorkbook:
    path: Path
    file_sha256: str
    quizzes: list[QuizRecord]
    questions: list[QuestionRecord]
    student_codes: list[str]
    submissions: list[SubmissionRecord]
    reconciliation: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        attempts = sum(row.status == "canonical" for row in self.submissions)
        absences = sum(row.status == "absent" for row in self.submissions)
        scored = sum(
            response.item_score is not None
            for row in self.submissions
            for response in row.responses
        )
        answers = sum(len(row.responses) for row in self.submissions)
        return {
            "file_sha256": self.file_sha256,
            "students": len(self.student_codes),
            "assessments": len(self.quizzes),
            "questions": len(self.questions),
            "attempts": attempts,
            "absences": absences,
            "submission_blocks": len(self.submissions),
            "answers": answers,
            "scored_answers": scored,
            "pending_answers": answers - scored,
            "reconciliation": self.reconciliation,
        }


def _load_workbook(path: Path):
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError(
            "课堂 Excel 导入需要 openpyxl；请先安装项目运行依赖。"
        ) from exc
    # Required contract: never calculate formulas and never mutate the file.
    return load_workbook(path, read_only=True, data_only=True)


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{sha256(payload).hexdigest()[:40]}"


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _quiz_code(value: object) -> str:
    code = _clean_text(value)
    if not code:
        raise ClassroomImportError("小测编号不能为空")
    return code


def _iso_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _clean_text(value)


def _number(value: object, *, field_name: str, allow_none: bool = False) -> float | None:
    if value is None or _clean_text(value) in PENDING_MARKERS:
        if allow_none:
            return None
        raise ClassroomImportError(f"{field_name} 不能为空")
    if isinstance(value, bool):
        raise ClassroomImportError(f"{field_name} 不是数值")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ClassroomImportError(f"{field_name} 不是数值：{value!r}") from exc
    if not math.isfinite(result):
        raise ClassroomImportError(f"{field_name} 不是有限数值")
    return result


def _integer(value: object, *, field_name: str) -> int:
    number = _number(value, field_name=field_name)
    assert number is not None
    if not number.is_integer():
        raise ClassroomImportError(f"{field_name} 必须是整数")
    return int(number)


def _header_map(ws, row: int) -> dict[str, int]:
    headers: dict[str, int] = {}
    for column in range(1, ws.max_column + 1):
        label = _clean_text(ws.cell(row=row, column=column).value)
        if label:
            headers[label] = column
    return headers


def _require_headers(headers: dict[str, int], required: Iterable[str], sheet: str) -> None:
    missing = [label for label in required if label not in headers]
    if missing:
        raise ClassroomImportError(f"{sheet} 缺少列：{', '.join(missing)}")


def _question_type(raw: str) -> str:
    if "判断" in raw:
        return "true_false"
    if "多选" in raw:
        return "multi_choice"
    if "连线" in raw:
        return "matching"
    if "填空" in raw:
        return "fill_blank"
    if "简答" in raw:
        return "short_answer"
    return "unknown"


def _parse_overview(ws) -> list[QuizRecord]:
    headers = _header_map(ws, 2)
    required = (
        "序号", "小测编号", "小测主题", "小测日期", "题数", "已判分题数", "参考人数",
    )
    _require_headers(headers, required, ws.title)
    quizzes: list[QuizRecord] = []
    for row in range(3, ws.max_row + 1):
        sequence = ws.cell(row=row, column=headers["序号"]).value
        if not isinstance(sequence, (int, float)) or isinstance(sequence, bool):
            continue
        code = _quiz_code(ws.cell(row=row, column=headers["小测编号"]).value)
        quizzes.append(QuizRecord(
            code=code,
            topic=_clean_text(ws.cell(row=row, column=headers["小测主题"]).value),
            scheduled_at=_iso_date(ws.cell(row=row, column=headers["小测日期"]).value),
            question_count=_integer(
                ws.cell(row=row, column=headers["题数"]).value,
                field_name=f"{ws.title}!题数{row}",
            ),
            judged_question_count=_integer(
                ws.cell(row=row, column=headers["已判分题数"]).value,
                field_name=f"{ws.title}!已判分题数{row}",
            ),
            participant_count=_integer(
                ws.cell(row=row, column=headers["参考人数"]).value,
                field_name=f"{ws.title}!参考人数{row}",
            ),
            overview_row=row,
        ))
    if not quizzes:
        raise ClassroomImportError("小测概览没有数据行")
    codes = [quiz.code for quiz in quizzes]
    if len(codes) != len(set(codes)):
        raise ClassroomImportError("小测概览包含重复编号")
    return quizzes


def _parse_questions(ws) -> list[QuestionRecord]:
    headers = _header_map(ws, 2)
    required = (
        "序号", "小测编号", "题号", "答题明细列", "题型（推断）", "标准答案",
        "每题分值", "判分状态", "题干（原文）",
    )
    _require_headers(headers, required, ws.title)
    questions: list[QuestionRecord] = []
    for row in range(3, ws.max_row + 1):
        sequence = ws.cell(row=row, column=headers["序号"]).value
        if not isinstance(sequence, (int, float)) or isinstance(sequence, bool):
            continue
        code = _quiz_code(ws.cell(row=row, column=headers["小测编号"]).value)
        number = _clean_text(ws.cell(row=row, column=headers["题号"]).value).upper()
        if not re.fullmatch(r"Q\d+", number):
            raise ClassroomImportError(f"{ws.title}!{row} 的题号无效：{number!r}")
        detail_key = _clean_text(ws.cell(row=row, column=headers["答题明细列"]).value)
        expected_key = f"{code}-{number}"
        if detail_key != expected_key:
            raise ClassroomImportError(
                f"{ws.title}!{row} 的答题明细列应为 {expected_key}，实际为 {detail_key}"
            )
        raw_status = _clean_text(ws.cell(row=row, column=headers["判分状态"]).value)
        inferred = raw_status == "已判分（反解）"
        answer_text = _clean_text(ws.cell(row=row, column=headers["标准答案"]).value)
        points_value = ws.cell(row=row, column=headers["每题分值"]).value
        if inferred:
            if not answer_text or answer_text in PENDING_MARKERS:
                raise ClassroomImportError(f"{detail_key} 标为已判分但缺少标准答案")
            points = _number(points_value, field_name=f"{detail_key} 每题分值")
            scoring_status = "reverse_inferred"
            standard_answer: str | None = answer_text
        else:
            # Never turn the literal placeholder into an authoritative key.
            standard_answer = None
            points = None
            scoring_status = "pending_key"
        raw_type = _clean_text(ws.cell(row=row, column=headers["题型（推断）"]).value)
        questions.append(QuestionRecord(
            quiz_code=code,
            number=number,
            detail_key=detail_key,
            question_type=_question_type(raw_type),
            raw_type=raw_type,
            stem=_clean_text(ws.cell(row=row, column=headers["题干（原文）"]).value),
            standard_answer=standard_answer,
            points=points,
            scoring_status=scoring_status,
            source_row=row,
        ))
    keys = [row.detail_key for row in questions]
    if len(keys) != len(set(keys)):
        raise ClassroomImportError("题目清单中的答题明细列不唯一")
    return questions


def _discover_blocks(ws, quizzes: list[QuizRecord]) -> list[QuizRecord]:
    starts = [
        column for column in range(3, ws.max_column + 1)
        if ws.cell(row=3, column=column).value not in (None, "")
    ]
    if not starts:
        raise ClassroomImportError("答题明细第3行没有小测列块")
    overview = {quiz.code: quiz for quiz in quizzes}
    blocks: list[QuizRecord] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] - 1 if index + 1 < len(starts) else ws.max_column
        title = _clean_text(ws.cell(row=3, column=start).value)
        matched = QUIZ_TITLE_RE.match(title)
        if not matched:
            raise ClassroomImportError(f"无法从答题明细标题识别小测编号：{title!r}")
        code = _quiz_code(matched.group("code"))
        if code not in overview:
            raise ClassroomImportError(f"答题明细包含概览中不存在的小测 {code}")
        width = end - start + 1
        if width < 3 or (width - 1) % 2:
            raise ClassroomImportError(f"小测 {code} 列块宽度无效：{width}")
        question_count = (width - 1) // 2
        if _clean_text(ws.cell(row=4, column=start).value) != "总分":
            raise ClassroomImportError(f"小测 {code} 首列不是总分")
        for offset in range(question_count):
            expected = offset + 1
            for column, kind in ((start + 1 + 2 * offset, "答案"),
                                 (start + 2 + 2 * offset, "得分")):
                label = re.sub(r"\s+", "", _clean_text(ws.cell(row=4, column=column).value))
                header = QUESTION_HEADER_RE.match(label)
                if not header or int(header.group("number")) != expected or header.group("kind") != kind:
                    raise ClassroomImportError(
                        f"小测 {code} 的第 {column} 列表头无效：{label!r}"
                    )
        meta = overview[code]
        if question_count != meta.question_count:
            raise ClassroomImportError(
                f"小测 {code} 概览题数 {meta.question_count} 与明细 {question_count} 不一致"
            )
        blocks.append(QuizRecord(
            code=meta.code, topic=meta.topic, scheduled_at=meta.scheduled_at,
            question_count=meta.question_count,
            judged_question_count=meta.judged_question_count,
            participant_count=meta.participant_count,
            overview_row=meta.overview_row, start_col=start, end_col=end,
        ))
    if [block.code for block in blocks] != [quiz.code for quiz in quizzes]:
        raise ClassroomImportError("答题明细列块顺序/集合与小测概览不一致")
    return blocks


def _column_letter(column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _student_code_from_row(ws, row: int, *, strict: bool = True) -> str | None:
    # The supplied workbook labels column B as 姓名 but contains only STUxxx.
    # Accept an anonymized code in either A or B; reject names instead of
    # copying them into the database or error logs.
    candidates = [_clean_text(ws.cell(row=row, column=1).value),
                  _clean_text(ws.cell(row=row, column=2).value)]
    for candidate in candidates:
        if ANONYMOUS_CODE_RE.fullmatch(candidate):
            return candidate.upper()
    if not any(candidates) or not strict:
        return None
    raise ClassroomImportError(f"{ws.title}第 {row} 行没有合法匿名学号")


def _parse_submissions(ws, blocks: list[QuizRecord],
                       questions: list[QuestionRecord]) -> tuple[list[str], list[SubmissionRecord]]:
    by_quiz_number = {(q.quiz_code, q.number): q for q in questions}
    students: list[str] = []
    submissions: list[SubmissionRecord] = []
    for row in range(5, ws.max_row + 1):
        student_code = _student_code_from_row(ws, row)
        if student_code is None:
            continue
        if student_code in students:
            raise ClassroomImportError("答题明细包含重复匿名学号")
        students.append(student_code)
        for block in blocks:
            total_cell = ws.cell(row=row, column=block.start_col).value
            values = [
                ws.cell(row=row, column=column).value
                for column in range(block.start_col + 1, block.end_col + 1)
            ]
            if total_cell in (None, ""):
                if any(value not in (None, "") for value in values):
                    raise ClassroomImportError(
                        f"{student_code} 小测 {block.code} 总分为空但题目列不为空"
                    )
                submissions.append(SubmissionRecord(
                    student_code=student_code, quiz_code=block.code,
                    source_row=row, status="absent", source_reported_total=None,
                ))
                continue
            total = _number(
                total_cell,
                field_name=f"答题明细!{_column_letter(block.start_col)}{row}",
            )
            assert total is not None
            responses: list[ResponseRecord] = []
            known_scores: list[float] = []
            all_questions_scored = True
            for offset in range(block.question_count):
                number = f"Q{offset + 1}"
                question = by_quiz_number.get((block.code, number))
                if question is None:
                    raise ClassroomImportError(f"题目清单缺少 {block.code}-{number}")
                answer_col = block.start_col + 1 + 2 * offset
                score_col = answer_col + 1
                raw_answer = _clean_text(ws.cell(row=row, column=answer_col).value) or None
                score_raw = ws.cell(row=row, column=score_col).value
                item_score = _number(
                    score_raw, field_name=f"{block.code}-{number} 得分",
                    allow_none=True,
                )
                if question.scoring_status == "reverse_inferred":
                    if item_score is None:
                        raise ClassroomImportError(
                            f"{student_code} 的 {block.code}-{number} 应有反解题分"
                        )
                    assert question.points is not None
                    if item_score < 0 or item_score > question.points:
                        raise ClassroomImportError(
                            f"{student_code} 的 {block.code}-{number} 题分越界"
                        )
                    known_scores.append(item_score)
                else:
                    if item_score is not None:
                        raise ClassroomImportError(
                            f"{student_code} 的 {block.code}-{number} 待补答案却已有题分"
                        )
                    all_questions_scored = False
                responses.append(ResponseRecord(
                    question_number=number, raw_answer=raw_answer,
                    item_score=item_score,
                    answer_cell=f"{_column_letter(answer_col)}{row}",
                    score_cell=f"{_column_letter(score_col)}{row}",
                ))
            if all_questions_scored and not math.isclose(
                    sum(known_scores), total, rel_tol=0.0, abs_tol=1e-8):
                raise ClassroomImportError(
                    f"{student_code} 小测 {block.code} 题分和与总分不一致"
                )
            submissions.append(SubmissionRecord(
                student_code=student_code, quiz_code=block.code,
                source_row=row, status="canonical",
                source_reported_total=total, responses=tuple(responses),
            ))
    if not students:
        raise ClassroomImportError("答题明细没有匿名学生行")
    return students, submissions


def _parse_student_summary(ws) -> dict[str, int]:
    headers = _header_map(ws, 2)
    _require_headers(headers, ("参考次数",), ws.title)
    counts: dict[str, int] = {}
    for row in range(3, ws.max_row + 1):
        code = _student_code_from_row(ws, row, strict=False)
        if code is None:
            continue
        counts[code] = _integer(
            ws.cell(row=row, column=headers["参考次数"]).value,
            field_name=f"学生汇总!参考次数{row}",
        )
    return counts


def parse_teacher_classroom_workbook(path: str | Path) -> ParsedClassroomWorkbook:
    """Read and validate the teacher workbook without mutating it or the DB."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    file_hash = _sha256(source)
    workbook = _load_workbook(source)
    try:
        missing = [sheet for sheet in REQUIRED_SHEETS if sheet not in workbook.sheetnames]
        if missing:
            raise ClassroomImportError(f"工作簿缺少工作表：{', '.join(missing)}")
        # Deliberately do not access workbook.properties or workbook["说明"].
        quizzes = _parse_overview(workbook["小测概览"])
        questions = _parse_questions(workbook["题目清单"])
        blocks = _discover_blocks(workbook["答题明细"], quizzes)
        student_codes, submissions = _parse_submissions(
            workbook["答题明细"], blocks, questions,
        )
        summary_counts = _parse_student_summary(workbook["学生汇总"])
    finally:
        workbook.close()

    question_counts = {
        quiz.code: sum(question.quiz_code == quiz.code for question in questions)
        for quiz in quizzes
    }
    participant_counts = {
        quiz.code: sum(
            row.quiz_code == quiz.code and row.status == "canonical"
            for row in submissions
        )
        for quiz in quizzes
    }
    student_attempt_counts = {
        student: sum(
            row.student_code == student and row.status == "canonical"
            for row in submissions
        )
        for student in student_codes
    }
    question_check = all(
        question_counts[quiz.code] == quiz.question_count for quiz in quizzes
    )
    participant_check = all(
        participant_counts[quiz.code] == quiz.participant_count for quiz in quizzes
    )
    student_check = summary_counts == student_attempt_counts
    block_check = len(submissions) == len(student_codes) * len(quizzes)
    reconciliation = {
        "question_counts_match": question_check,
        "participant_counts_match": participant_check,
        "student_attempt_counts_match": student_check,
        "complete_student_quiz_matrix": block_check,
        "all_pass": question_check and participant_check and student_check and block_check,
        "participant_total": sum(participant_counts.values()),
        "overview_participant_total": sum(quiz.participant_count for quiz in quizzes),
    }
    if not reconciliation["all_pass"]:
        raise ClassroomImportError("工作簿明细与汇总页核对失败")
    return ParsedClassroomWorkbook(
        path=source, file_sha256=file_hash, quizzes=blocks,
        questions=questions, student_codes=student_codes,
        submissions=submissions, reconciliation=reconciliation,
    )


def _upsert(db: Session, model, primary_key: str, values: dict[str, Any]):
    identity = values[primary_key]
    row = db.get(model, identity)
    if row is None:
        row = model(**values)
        db.add(row)
    else:
        for key, value in values.items():
            if key != primary_key:
                setattr(row, key, value)
    return row


def _question_ids(course_key: str, question: QuestionRecord) -> tuple[str, str]:
    logical = _stable_id("cq", course_key, question.quiz_code, question.number)
    return logical, _stable_id("cqrow", logical, 1)


def _normalized_response(question: QuestionRecord, raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {"answered": False}
    if question.question_type == "true_false":
        if raw in {"对", "是", "正确", "true", "True"}:
            return {"answered": True, "value": True, "raw_text": raw}
        if raw in {"错", "否", "错误", "false", "False"}:
            return {"answered": True, "value": False, "raw_text": raw}
    if question.question_type == "multi_choice":
        return {
            "answered": True,
            "texts": [part.strip() for part in raw.split("┋") if part.strip()],
            "raw_text": raw,
        }
    return {"answered": True, "text": raw, "raw_text": raw}


def _answer_payload(question: QuestionRecord) -> dict[str, Any]:
    raw = question.standard_answer
    if raw is None:
        return {}
    if question.question_type == "true_false":
        if raw in {"对", "是", "正确"}:
            return {"value": True, "raw_text": raw}
        if raw in {"错", "否", "错误"}:
            return {"value": False, "raw_text": raw}
    return {"raw_text": raw}


def _quiz_scoring(questions: list[QuestionRecord]) -> tuple[str, str, bool]:
    inferred = sum(q.scoring_status == "reverse_inferred" for q in questions)
    if inferred == len(questions):
        return "graded", "reverse_inferred_totals", False
    if inferred:
        return "partial", "mixed", False
    return "pending_key", "missing", False


def import_teacher_classroom(
    db: Session,
    workbook_path: str | Path,
    *,
    course_key: str = "ml-2025-fall",
    classroom_name: str = "2025秋机器学习",
    term: str = "2025秋",
    is_test_fixture: bool = False,
    commit: bool = True,
) -> dict[str, Any]:
    """Parse and idempotently persist one anonymized classroom workbook."""
    parsed = parse_teacher_classroom_workbook(workbook_path)
    course_key = course_key.strip()
    if not course_key:
        raise ValueError("course_key 不能为空")

    run_id = _stable_id("classrun", course_key, parsed.file_sha256)
    dataset_id = _stable_id("dataset", course_key, parsed.file_sha256)
    classroom_id = _stable_id("class", course_key)
    summary = parsed.summary()
    question_by_key = {(q.quiz_code, q.number): q for q in parsed.questions}
    questions_by_quiz = {
        quiz.code: [q for q in parsed.questions if q.quiz_code == quiz.code]
        for quiz in parsed.quizzes
    }

    try:
        run = _upsert(db, E.ImportRun, "run_id", {
            "run_id": run_id,
            "source_kind": "teacher_classroom_xlsx",
            "source_id": dataset_id,
            "file_sha256": parsed.file_sha256,
            # Store only the basename; never persist a user directory path.
            "file_rel_path": parsed.path.name,
            "language": "zh",
            "status": "running",
            "stage": "persisting",
            "steps": [{"stage": "validated", "status": "completed"}],
            "result": {},
            "error": "",
            "attempts": 1,
            "job_id": "",
            "is_test_fixture": is_test_fixture,
        })
        _upsert(db, E.Classroom, "classroom_id", {
            "classroom_id": classroom_id,
            "course_key": course_key,
            "name": classroom_name,
            "term": term,
            "source": "teacher_workbook",
            "details": {
                "anonymous_students": True,
                "student_login_accounts_created": False,
                "source_file_sha256": parsed.file_sha256,
            },
        })
        # The live database enforces foreign keys.  Flush each parent layer
        # explicitly because these additive models intentionally do not carry
        # ORM relationships that would otherwise guide unit-of-work ordering.
        db.flush()

        student_ids: dict[str, str] = {}
        for code in parsed.student_codes:
            student_id = _stable_id("student", course_key, code)
            student_ids[code] = student_id
            _upsert(db, E.Student, "student_id", {
                "student_id": student_id,
                "external_key": f"{course_key}:{code}",
                "display_code": code,
                "linked_user_id": None,
                "anonymized": True,
                "source": "classroom_import",
                "active": True,
                "profile": {"login_account_created": False},
            })
            db.flush()
            membership_id = _stable_id("member", classroom_id, student_id)
            _upsert(db, E.ClassroomStudent, "membership_id", {
                "membership_id": membership_id,
                "classroom_id": classroom_id,
                "student_id": student_id,
                "status": "active",
            })

        version_ids: dict[str, str] = {}
        paper_ids: dict[str, str] = {}
        paper_item_ids: dict[tuple[str, str], str] = {}
        logical_question_ids: dict[tuple[str, str], str] = {}
        for quiz in parsed.quizzes:
            quiz_questions = questions_by_quiz[quiz.code]
            assessment_id = _stable_id("assessment", course_key, quiz.code)
            version_id = _stable_id("assessv", assessment_id, 1)
            paper_id = _stable_id("cpaper", version_id)
            version_ids[quiz.code] = version_id
            paper_ids[quiz.code] = paper_id
            grading_status, key_source, verified = _quiz_scoring(quiz_questions)
            content_payload = [
                {
                    "number": q.number, "stem": q.stem, "type": q.question_type,
                    "answer": q.standard_answer, "points": q.points,
                }
                for q in quiz_questions
            ]
            content_hash = sha256(json.dumps(
                content_payload, ensure_ascii=False, sort_keys=True,
            ).encode("utf-8")).hexdigest()
            _upsert(db, E.Assessment, "assessment_id", {
                "assessment_id": assessment_id,
                "classroom_id": classroom_id,
                "external_key": f"{course_key}:quiz:{quiz.code}",
                "quiz_code": quiz.code,
                "title": f"小测{quiz.code} {quiz.topic}".strip(),
                "topic": quiz.topic,
                "scheduled_at": quiz.scheduled_at,
                "source": "teacher_workbook",
                "details": {
                    "question_count": quiz.question_count,
                    "source_participant_count": quiz.participant_count,
                },
            })
            reliable_max = all(q.points is not None for q in quiz_questions)
            total_score = sum(float(q.points or 0.0) for q in quiz_questions)
            _upsert(db, E.Paper, "paper_id", {
                "paper_id": paper_id,
                "user_id": classroom_id,
                "title": f"小测{quiz.code} {quiz.topic}".strip(),
                "mode": "quiz",
                "blueprint": {
                    "source": "teacher_classroom_xlsx",
                    "quiz_code": quiz.code,
                    "answer_key_source": key_source,
                    "teacher_verified": False,
                },
                "total_score": total_score if reliable_max else 0.0,
                "item_count": len(quiz_questions),
                "estimated_minutes": 0.0,
                "coverage_report": {
                    "max_score_reliable": reliable_max,
                    "grading_status": grading_status,
                },
                "source": "teacher_workbook",
                "is_test_fixture": is_test_fixture,
                "archived": False,
                "scope": "course_only",
            })
            db.flush()
            _upsert(db, E.AssessmentVersion, "assessment_version_id", {
                "assessment_version_id": version_id,
                "assessment_id": assessment_id,
                "version_no": 1,
                "version_key": f"{course_key}:quiz:{quiz.code}:v1",
                "paper_id": paper_id,
                "content_hash": content_hash,
                "scoring_status": grading_status,
                "answer_key_source": key_source,
                "teacher_verified": verified,
                "source_file_sha256": parsed.file_sha256,
                "scoring_policy": {
                    "pending_items_are_evidence": False,
                    "source_reported_total_is_item_score_authority": False,
                },
            })
            for position, question in enumerate(quiz_questions, start=1):
                question_id, row_id = _question_ids(course_key, question)
                logical_question_ids[(quiz.code, question.number)] = question_id
                inferred = question.scoring_status == "reverse_inferred"
                source_ref = {
                    "kind": "teacher_classroom_xlsx",
                    "file_sha256": parsed.file_sha256,
                    "sheet": "题目清单",
                    "row": question.source_row,
                }
                _upsert(db, E.Question, "row_id", {
                    "row_id": row_id,
                    "question_id": question_id,
                    "question_version": 1,
                    "origin": "original",
                    "original_exam_id": assessment_id,
                    "original_question_no": question.number,
                    "question_type": question.question_type,
                    "stem": question.stem,
                    "options": [],
                    "stem_images": [],
                    "score": float(question.points or 0.0),
                    "main_kp_id": "",
                    "aux_kp_ids": [],
                    "cognitive_level": "理解",
                    "difficulty": "",
                    "estimated_minutes": 0.0,
                    "source_refs": [source_ref],
                    "question_family_id": question_id,
                    "parent_question_id": "",
                    "review_status": "pending",
                    "answer_status": "candidate" if inferred else "unknown",
                    "answer_origin": "computed" if inferred else "none",
                    "active": True,
                    "created_by": "teacher_classroom_import",
                    "notes": (
                        "标准答案由班级总分唯一反解，未经教师确认。"
                        if inferred else "缺少权威标准答案与题目分值，不得自动评分。"
                    ),
                    "scope_class": "in_scope",
                    "external_item_id": question.detail_key,
                    "answer_payload": _answer_payload(question),
                    "rubric": {},
                    "explanation": "",
                    "original_language": "zh",
                    "translation_status": "none",
                    "extraction_status": "text_layer",
                    "source_quote": question.stem,
                    "is_test_fixture": is_test_fixture,
                })
                item_id = _stable_id("cpitem", paper_id, position)
                paper_item_ids[(quiz.code, question.number)] = item_id
                _upsert(db, E.PaperItem, "item_id", {
                    "item_id": item_id,
                    "paper_id": paper_id,
                    "position": position,
                    "question_id": question_id,
                    "question_version": 1,
                    "score": float(question.points or 0.0),
                    "snapshot": {
                        "stem": question.stem,
                        "question_type": question.question_type,
                        "raw_type": question.raw_type,
                        "answer_payload": _answer_payload(question),
                        "answer_key_source": (
                            "reverse_inferred_totals" if inferred else "missing"
                        ),
                        "teacher_verified": False,
                        "scoring_status": question.scoring_status,
                    },
                })

        # Submissions reference run/student/version and optionally attempt.
        db.flush()

        for submission in parsed.submissions:
            student_id = student_ids[submission.student_code]
            version_id = version_ids[submission.quiz_code]
            paper_id = paper_ids[submission.quiz_code]
            source_key = (
                f"{course_key}|{submission.student_code}|"
                f"{submission.quiz_code}|canonical_snapshot"
            )
            submission_id = _stable_id("submission", dataset_id, source_key)
            attempt_id: str | None = None
            if submission.status == "canonical":
                attempt_id = _stable_id(
                    "cattempt", course_key, submission.student_code,
                    submission.quiz_code, "canonical",
                )
                quiz_questions = questions_by_quiz[submission.quiz_code]
                grading_status, _, _ = _quiz_scoring(quiz_questions)
                reliable_max = all(q.points is not None for q in quiz_questions)
                known_max = sum(float(q.points or 0.0) for q in quiz_questions)
                known_awarded = sum(
                    float(response.item_score or 0.0)
                    for response in submission.responses
                    if response.item_score is not None
                )
                _upsert(db, E.Attempt, "attempt_id", {
                    "attempt_id": attempt_id,
                    "paper_id": paper_id,
                    # Compatibility owner only; no User/login row is created.
                    "user_id": student_id,
                    "mode": "quiz",
                    "status": "submitted",
                    "started_at": "",
                    "server_started_ms": 0,
                    "submitted_at": "",
                    "score": float(submission.source_reported_total or 0.0),
                    "max_score": known_max if reliable_max else 0.0,
                    "objective_score": known_awarded,
                    "manual_pending_count": sum(
                        q.scoring_status == "pending_key" for q in quiz_questions
                    ),
                    "submit_request_key": _stable_id("submitkey", source_key),
                    "diagnosis_status": "not_requested",
                    "student_id": student_id,
                    "assessment_version_id": version_id,
                    "attendance_status": "attended",
                    "grading_status": grading_status,
                    "source_reported_total": submission.source_reported_total,
                    "max_score_reliable": reliable_max,
                    "source_submission_key": source_key,
                    "source_import_run_id": run_id,
                    "is_effective": True,
                    "duplicate_of_attempt_id": "",
                })
                for response in submission.responses:
                    question = question_by_key[
                        (submission.quiz_code, response.question_number)
                    ]
                    question_id = logical_question_ids[
                        (submission.quiz_code, response.question_number)
                    ]
                    answer_id = _stable_id("canswer", attempt_id, question_id)
                    source_ref = (
                        f"sha256:{parsed.file_sha256}:答题明细!{response.answer_cell}"
                    )
                    _upsert(db, E.Answer, "answer_id", {
                        "answer_id": answer_id,
                        "attempt_id": attempt_id,
                        "question_id": question_id,
                        "question_version": 1,
                        "paper_item_id": paper_item_ids[
                            (submission.quiz_code, response.question_number)],
                        "response": _normalized_response(question, response.raw_answer),
                        "saved_at": "",
                        "updated_at": "",
                        "save_count": 1,
                        "hints_used": 0,
                        "solution_viewed": False,
                        "seen_before": False,
                        "is_independent_evidence": True,
                        "response_status": (
                            "answered" if response.raw_answer is not None else "unanswered"
                        ),
                        "grading_status": (
                            "graded_inferred"
                            if question.scoring_status == "reverse_inferred"
                            else "pending_key"
                        ),
                        "source_ref": source_ref,
                        "raw_response": response.raw_answer or "",
                    })
                    inferred = question.scoring_status == "reverse_inferred"
                    if inferred:
                        assert question.points is not None
                        assert response.item_score is not None
                        correct: bool | None = math.isclose(
                            response.item_score, question.points,
                            rel_tol=0.0, abs_tol=1e-8,
                        )
                        awarded = response.item_score
                        max_score = question.points
                        scoring_status = "auto"
                        provenance = "reverse_inferred_totals"
                        authority = "inferred_unverified"
                    else:
                        correct = None
                        awarded = 0.0
                        max_score = 0.0
                        scoring_status = "pending_key"
                        provenance = "unavailable_missing_key"
                        authority = "not_scored"
                    score_id = _stable_id("cscore", attempt_id, question_id)
                    _upsert(db, E.AnswerScore, "score_id", {
                        "score_id": score_id,
                        "attempt_id": attempt_id,
                        "question_id": question_id,
                        "question_version": 1,
                        "paper_item_id": paper_item_ids[
                            (submission.quiz_code, response.question_number)],
                        "kp_id": "",
                        "family_id": question_id,
                        "correct": correct,
                        "awarded_score": awarded,
                        "max_score": max_score,
                        "scoring_status": scoring_status,
                        "score_provenance": provenance,
                        "authority_status": authority,
                        "teacher_verified": False,
                        "source_ref": (
                            f"sha256:{parsed.file_sha256}:答题明细!{response.score_cell}"
                        ),
                        "detail": {
                            "source_item_score": response.item_score,
                            "score_available": inferred,
                            "answer_key_source": (
                                "reverse_inferred_totals" if inferred else "missing"
                            ),
                            "teacher_verified": False,
                        },
                    })
                # ImportedSubmission.canonical_attempt_id is a real FK.
                db.flush()
            _upsert(db, E.ImportedSubmission, "submission_id", {
                "submission_id": submission_id,
                "import_run_id": run_id,
                "dataset_id": dataset_id,
                "source_sheet": "答题明细",
                "source_row": submission.source_row,
                "source_key": source_key,
                "student_id": student_id,
                "assessment_version_id": version_id,
                "status": submission.status,
                "canonical_attempt_id": attempt_id,
                "duplicate_of_submission_id": "",
                "selection_rule": "workbook_canonical_snapshot",
                "dedupe_reason": (
                    "Source workbook contains only the selected canonical block; "
                    "raw duplicate rows are unavailable."
                ),
                "raw_preserved": False,
                "raw_payload": {
                    "attendance": (
                        "attended" if submission.status == "canonical" else "absent"
                    ),
                    "source_reported_total": submission.source_reported_total,
                    "answer_count": len(submission.responses),
                },
            })

        result = {
            **summary,
            "import_run_id": run_id,
            "dataset_id": dataset_id,
            "classroom_id": classroom_id,
            "raw_submission_rows_preserved": False,
            "student_login_accounts_created": False,
        }
        run.status = "completed"
        run.stage = "completed"
        run.steps = [
            {"stage": "validated", "status": "completed"},
            {"stage": "persisted", "status": "completed"},
            {"stage": "reconciled", "status": "completed"},
        ]
        run.result = result
        run.error = ""
        db.flush()
        if commit:
            db.commit()
        return result
    except Exception:
        db.rollback()
        raise


__all__ = [
    "ClassroomImportError",
    "ParsedClassroomWorkbook",
    "parse_teacher_classroom_workbook",
    "import_teacher_classroom",
]
