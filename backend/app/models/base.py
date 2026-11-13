"""SQLite engine/session. All business facts live here; the vector/keyword
index is only a retrieval aid and never the source of truth."""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.app import config as cfg


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{cfg.DB_PATH.as_posix()}",
    connect_args={"check_same_thread": False, "timeout": 30},
    echo=False,
    future=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                            future=True)


def get_session():
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_columns() -> None:
    """Additive-only migrations for an EXISTING database.

    `create_all` never alters a table that already exists, so a new nullable
    column would be missing on the live database. These statements only ever ADD
    columns (never drop/rewrite data), which keeps existing question banks,
    attempts and learning records untouched. Each one is idempotent.
    """
    from sqlalchemy import text
    wanted = [
        ("users", "role", "VARCHAR(24) DEFAULT 'student'"),
        ("users", "password_hash", "TEXT DEFAULT ''"),
        ("users", "active", "BOOLEAN DEFAULT 1"),
        ("users", "last_login_at", "VARCHAR(32) DEFAULT ''"),
        ("model_calls", "finish_reason", "VARCHAR(32) DEFAULT ''"),
        ("papers", "archived", "BOOLEAN DEFAULT 0"),
        ("answer_booklets", "version", "INTEGER DEFAULT 1"),
        ("answer_booklets", "generated_at", "VARCHAR(32) DEFAULT ''"),
        # V2.1 课程范围：默认值保证历史题目仍然按「课程范围内」处理
        ("questions", "scope_class", "VARCHAR(32) DEFAULT 'in_scope'"),
        ("questions", "external_item_id", "VARCHAR(64) DEFAULT ''"),
        ("papers", "scope", "VARCHAR(32) DEFAULT 'course_only'"),
        # V2.2 内容级资料分类 + 课件后台出题（additive-only，可回滚）
        ("material_uploads", "classify_source", "VARCHAR(16) DEFAULT ''"),
        ("material_uploads", "classify_confidence", "FLOAT DEFAULT 0.0"),
        ("material_uploads", "classify_needs_review", "BOOLEAN DEFAULT 0"),
        ("material_uploads", "slides_question_count", "INTEGER DEFAULT 0"),
        ("material_uploads", "slides_job_id", "VARCHAR(64) DEFAULT ''"),
        # V2.2 扩展学习：来源地区台账（additive-only，默认 unknown 不影响历史条目）
        ("external_sources", "region", "VARCHAR(16) DEFAULT 'unknown'"),
        # V2.3 试卷库：条目归属试卷与原卷顺序（additive-only，历史条目默认无归属）
        ("external_items", "paper_id", "VARCHAR(64) DEFAULT ''"),
        ("external_items", "position", "INTEGER DEFAULT 0"),
        # V2.3 缩略图：预览授权、缓存路径、缓存失效依据（默认不授权预览）
        ("external_papers", "preview_allowed", "BOOLEAN DEFAULT 0"),
        ("external_papers", "thumb_rel_path", "VARCHAR(512) DEFAULT ''"),
        ("external_papers", "thumb_generated_at", "VARCHAR(32) DEFAULT ''"),
        ("external_papers", "pdf_sha256", "VARCHAR(64) DEFAULT ''"),
        # V2.3 中文版：双语题目与试卷级本地化状态（additive-only）
        ("questions", "stem_zh", "TEXT DEFAULT ''"),
        ("questions", "original_language", "VARCHAR(16) DEFAULT ''"),
        ("questions", "translation_status", "VARCHAR(32) DEFAULT ''"),
        ("questions", "translation_model", "VARCHAR(64) DEFAULT ''"),
        ("questions", "translation_version", "VARCHAR(32) DEFAULT ''"),
        ("questions", "translated_at", "VARCHAR(32) DEFAULT ''"),
        ("questions", "extraction_status", "VARCHAR(32) DEFAULT ''"),
        ("questions", "source_page", "INTEGER DEFAULT 0"),
        ("questions", "source_quote", "TEXT DEFAULT ''"),
        ("external_papers", "zh_status", "VARCHAR(32) DEFAULT ''"),
        ("external_papers", "zh_pdf_rel_path", "VARCHAR(512) DEFAULT ''"),
        ("external_papers", "zh_generated_at", "VARCHAR(32) DEFAULT ''"),
        ("external_papers", "zh_missing_note", "TEXT DEFAULT ''"),
        ("external_papers", "localize_version", "VARCHAR(64) DEFAULT ''"),
        ("external_papers", "localize_model", "VARCHAR(64) DEFAULT ''"),
        ("external_papers", "localized_at", "VARCHAR(32) DEFAULT ''"),
        ("external_papers", "localize_note", "TEXT DEFAULT ''"),
        # V3.0 在线资源：作者/类型/简介/匹配度/范围/访问状态/封面/核验深度
        ("resources", "author", "VARCHAR(128) DEFAULT ''"),
        ("resources", "content_type", "VARCHAR(32) DEFAULT ''"),
        ("resources", "intro", "TEXT DEFAULT ''"),
        ("resources", "match_level", "VARCHAR(32) DEFAULT ''"),
        ("resources", "scope", "VARCHAR(32) DEFAULT 'unconfirmed'"),
        ("resources", "access_status", "VARCHAR(32) DEFAULT ''"),
        ("resources", "thumbnail", "TEXT DEFAULT ''"),
        ("resources", "verified_scope", "VARCHAR(32) DEFAULT ''"),
        # V3.0 第二轮：封面来源 / 双语标题 / 内容核验 / 测试夹具标记
        ("resources", "cover_source", "VARCHAR(32) DEFAULT ''"),
        ("resources", "original_title", "VARCHAR(256) DEFAULT ''"),
        ("resources", "title_zh", "VARCHAR(256) DEFAULT ''"),
        ("resources", "content_status", "VARCHAR(32) DEFAULT ''"),
        ("resources", "is_test_fixture", "BOOLEAN DEFAULT 0"),
        ("material_uploads", "is_test_fixture", "BOOLEAN DEFAULT 0"),
        ("papers", "is_test_fixture", "BOOLEAN DEFAULT 0"),
        ("questions", "is_test_fixture", "BOOLEAN DEFAULT 0"),
        # V5 章节诊断：章节是独立维度，不借用知识点 ID。
        ("questions", "chapter_id", "VARCHAR(64) DEFAULT ''"),
        ("paper_items", "chapter_id", "VARCHAR(64) DEFAULT ''"),
        ("assessments", "chapter_id", "VARCHAR(64) DEFAULT ''"),
        ("answer_scores", "chapter_id", "VARCHAR(64) DEFAULT ''"),
        ("retest_tasks", "chapter_id", "VARCHAR(64) DEFAULT ''"),
        # V3.3 错题去重：只 ADD 列，绝不改写或删除已有错题记录。
        # fingerprint 由题目内容算出；canonical_* 只作「关联」，不改原值。
        ("wrong_book", "question_fingerprint", "VARCHAR(64) DEFAULT ''"),
        ("wrong_book", "canonical_question_id", "VARCHAR(64) DEFAULT ''"),
        ("wrong_book", "canonical_entry_id", "VARCHAR(64) DEFAULT ''"),
        # V4 课堂学情：学生与登录账号分离，旧 user_id 原样保留。
        ("attempts", "student_id", "VARCHAR(64) DEFAULT ''"),
        ("attempts", "assessment_version_id", "VARCHAR(64) DEFAULT ''"),
        ("attempts", "attendance_status", "VARCHAR(32) DEFAULT 'attended'"),
        ("attempts", "grading_status", "VARCHAR(32) DEFAULT 'unknown'"),
        ("attempts", "source_reported_total", "FLOAT"),
        ("attempts", "max_score_reliable", "BOOLEAN DEFAULT 1"),
        ("attempts", "source_submission_key", "VARCHAR(192) DEFAULT ''"),
        ("attempts", "source_import_run_id", "VARCHAR(64) DEFAULT ''"),
        ("attempts", "is_effective", "BOOLEAN DEFAULT 1"),
        ("attempts", "duplicate_of_attempt_id", "VARCHAR(64) DEFAULT ''"),
        ("attempts", "assignment_recipient_id", "VARCHAR(64) DEFAULT ''"),
        ("attempts", "retest_of_attempt_id", "VARCHAR(64) DEFAULT ''"),
        ("attempts", "diagnosis_cycle_id", "VARCHAR(64) DEFAULT ''"),
        ("answers", "response_status", "VARCHAR(32) DEFAULT 'answered'"),
        ("answers", "paper_item_id", "VARCHAR(64)"),
        ("answers", "grading_status", "VARCHAR(32) DEFAULT 'unknown'"),
        ("answers", "source_ref", "VARCHAR(256) DEFAULT ''"),
        ("answers", "raw_response", "TEXT DEFAULT ''"),
        ("answer_scores", "score_provenance", "VARCHAR(48) DEFAULT 'platform_scored'"),
        ("answer_scores", "paper_item_id", "VARCHAR(64)"),
        ("answer_scores", "authority_status", "VARCHAR(48) DEFAULT 'snapshot_rule'"),
        ("answer_scores", "teacher_verified", "BOOLEAN DEFAULT 0"),
        ("answer_scores", "source_ref", "VARCHAR(256) DEFAULT ''"),
        ("retest_tasks", "student_id", "VARCHAR(64) DEFAULT ''"),
        ("diagnoses", "student_id", "VARCHAR(64) DEFAULT ''"),
        ("diagnoses", "classroom_id", "VARCHAR(64) DEFAULT ''"),
        ("diagnoses", "assessment_version_id", "VARCHAR(64) DEFAULT ''"),
        ("diagnoses", "scope_type", "VARCHAR(32) DEFAULT 'personal'"),
    ]
    with engine.begin() as conn:
        for table, column, ddl in wanted:
            cols = {row[1] for row in conn.execute(
                text(f"PRAGMA table_info({table})"))}
            if not cols or column in cols:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            print(f"[db] added column {table}.{column}", flush=True)

        # SQLAlchemy create_all does not add indexes for columns introduced by
        # ALTER TABLE.  These are non-unique, additive and safe to re-run.
        indexes = [
            ("ix_attempts_student_id", "attempts", "student_id"),
            ("ix_attempts_assessment_version_id", "attempts", "assessment_version_id"),
            ("ix_attempts_source_submission_key", "attempts", "source_submission_key"),
            ("ix_attempts_assignment_recipient_id", "attempts", "assignment_recipient_id"),
            ("ix_attempts_retest_of_attempt_id", "attempts", "retest_of_attempt_id"),
            ("ix_answers_response_status", "answers", "response_status"),
            ("ix_answers_paper_item_id", "answers", "paper_item_id"),
            ("ix_answer_scores_score_provenance", "answer_scores", "score_provenance"),
            ("ix_answer_scores_paper_item_id", "answer_scores", "paper_item_id"),
            ("ix_questions_chapter_id", "questions", "chapter_id"),
            ("ix_paper_items_chapter_id", "paper_items", "chapter_id"),
            ("ix_assessments_chapter_id", "assessments", "chapter_id"),
            ("ix_answer_scores_chapter_id", "answer_scores", "chapter_id"),
            ("ix_retest_tasks_chapter_id", "retest_tasks", "chapter_id"),
            ("ix_diagnoses_student_id", "diagnoses", "student_id"),
            ("ix_diagnoses_classroom_id", "diagnoses", "classroom_id"),
            ("ix_diagnoses_assessment_version_id", "diagnoses", "assessment_version_id"),
            ("ix_diagnoses_scope_type", "diagnoses", "scope_type"),
        ]
        for name, table, column in indexes:
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"))


def init_db() -> None:
    from backend.app.models import entities  # noqa: F401  (register mappers)
    Base.metadata.create_all(engine)
    _ensure_columns()
