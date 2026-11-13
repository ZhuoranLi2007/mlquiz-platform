"""All ORM entities. See docs/DATA_MODEL.md for semantics.

Design rules enforced here:
* Answers / rubrics / scoring rules are server-side columns, never part of
  any student-facing DTO (see api/ schemas for the whitelist).
* A paper item stores a full immutable snapshot of the question VERSION used,
  so editing the question later cannot change a published paper.
* Every AI/skill invocation records its own run with explicit status; a
  failed model call must never delete a saved answer or a deterministic score.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, Float, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def jdefault(factory=dict):
    return mapped_column(JSON, default=factory, nullable=False)


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), default="本地学习者")
    role: Mapped[str] = mapped_column(String(24), default="student", index=True)
    password_hash: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_login_at: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"
    kp_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chapter: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    prerequisites: Mapped[list] = jdefault(list)
    key_basis: Mapped[str] = mapped_column(Text, default="")   # 重点依据（来源陈述）
    material_refs: Mapped[list] = jdefault(list)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class Chapter(Base):
    """Course chapter used by classroom diagnosis.

    Chapters are deliberately independent from ``KnowledgePoint``.  A chapter
    can contain many knowledge points, while imported classroom evidence may be
    reliable only at chapter granularity.
    """

    __tablename__ = "chapters"
    chapter_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    source_ref: Mapped[str] = mapped_column(String(512), default="")
    details: Mapped[dict] = jdefault()
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class Document(Base):
    __tablename__ = "documents"
    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rel_path: Mapped[str] = mapped_column(String(512))
    source_category: Mapped[str] = mapped_column(String(64), index=True)
    current_version_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    document_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    rel_path: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    unit_label: Mapped[str] = mapped_column(String(32), default="")
    unit_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_category: Mapped[str] = mapped_column(String(64), default="")
    readability: Mapped[str] = mapped_column(String(32), default="")
    parse_status: Mapped[str] = mapped_column(String(48), default="pending")
    parser_config_version: Mapped[str] = mapped_column(String(32), default="")
    quality_summary: Mapped[dict] = jdefault()
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    __table_args__ = (UniqueConstraint("sha256", "parser_config_version",
                                       name="uq_doc_hash_parserversion"),)


class Chunk(Base):
    """Semantic unit with traceable source refs. Never overwrites raw text."""
    __tablename__ = "chunks"
    chunk_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), index=True)
    document_version_id: Mapped[str] = mapped_column(String(64), index=True)
    unit_refs: Mapped[list] = jdefault(list)       # [{"page":3},{"page":4}]
    printed_page: Mapped[str] = mapped_column(String(32), default="")
    kind: Mapped[str] = mapped_column(String(48), default="prose")  # definition|derivation|example|code|question|answer|prose
    title: Mapped[str] = mapped_column(String(256), default="")
    content_raw: Mapped[str] = mapped_column(Text, default="")
    content_normalized: Mapped[str] = mapped_column(Text, default="")
    assets: Mapped[list] = jdefault(list)          # 页图 / 图片定位
    quality: Mapped[dict] = jdefault()
    source_refs: Mapped[list] = jdefault(list)
    chapter: Mapped[str] = mapped_column(String(128), default="", index=True)
    kp_ids: Mapped[list] = jdefault(list)
    review_status: Mapped[str] = mapped_column(String(48), default="unreviewed")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class Question(Base):
    """One row = one immutable question version (question_id + question_version)."""
    __tablename__ = "questions"
    row_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    question_version: Mapped[int] = mapped_column(Integer, default=1)
    origin: Mapped[str] = mapped_column(String(32), default="human")  # original|human|ai
    original_exam_id: Mapped[str] = mapped_column(String(128), default="")
    original_question_no: Mapped[str] = mapped_column(String(32), default="")
    question_type: Mapped[str] = mapped_column(String(32), index=True)
    # single_choice|multi_choice|true_false|numeric|term|short_answer|derivation|comprehensive|unknown
    stem: Mapped[str] = mapped_column(Text, default="")
    options: Mapped[list] = jdefault(list)
    stem_images: Mapped[list] = jdefault(list)
    score: Mapped[float] = mapped_column(Float, default=1.0)
    chapter_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    main_kp_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    aux_kp_ids: Mapped[list] = jdefault(list)
    cognitive_level: Mapped[str] = mapped_column(String(32), default="理解")
    difficulty: Mapped[str] = mapped_column(String(32), default="中等")
    estimated_minutes: Mapped[float] = mapped_column(Float, default=2.0)
    source_refs: Mapped[list] = jdefault(list)
    question_family_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    parent_question_id: Mapped[str] = mapped_column(String(64), default="")
    review_status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|verified|rejected
    answer_status: Mapped[str] = mapped_column(String(32), default="unknown")  # unknown|candidate|to_check|verified
    answer_origin: Mapped[str] = mapped_column(String(32), default="none")     # source|human|ai_candidate|computed
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    notes: Mapped[str] = mapped_column(Text, default="")
    # --- V2.1 课程范围（默认值让历史题目行为完全不变）---
    # in_scope | in_scope_advanced | extension | unconfirmed
    scope_class: Mapped[str] = mapped_column(String(32), default="in_scope", index=True)
    external_item_id: Mapped[str] = mapped_column(String(64), default="", index=True)

    # --- SERVER SIDE ONLY. Never serialize to a student-facing DTO. ---
    answer_payload: Mapped[dict] = jdefault()   # {"key":"B"} / {"values":[...]} / {"numeric":1.0,"tol":0.01,"unit":""}
    rubric: Mapped[dict] = jdefault()           # 评分细则/要点
    explanation: Mapped[str] = mapped_column(Text, default="")

    # --- V2.3 中英双语（additive-only）---
    # 原卷原文保持不动（stem / options[].text），译文单独存；
    # 答题页默认中文，按 stem_zh -> stem 回退，绝不把英文原文冒充中文版。
    stem_zh: Mapped[str] = mapped_column(Text, default="")
    # en | zh | unknown：由原文语言检测/提取时标注
    original_language: Mapped[str] = mapped_column(String(16), default="")
    # none | done | failed | stale（原文或译文版本变化时置 stale 重译）
    translation_status: Mapped[str] = mapped_column(String(32), default="", index=True)
    translation_model: Mapped[str] = mapped_column(String(64), default="")
    translation_version: Mapped[str] = mapped_column(String(32), default="")
    translated_at: Mapped[str] = mapped_column(String(32), default="")
    # 提取来源：text_layer | page_image | manual
    extraction_status: Mapped[str] = mapped_column(String(32), default="")
    source_page: Mapped[int] = mapped_column(Integer, default=0)
    # 原文片段（供人工核对，不展示给学生作答时使用）
    source_quote: Mapped[str] = mapped_column(Text, default="")
    # 只有显式标为测试夹具的题目才允许被清理脚本删除
    is_test_fixture: Mapped[bool] = mapped_column(Boolean, default=False,
                                                   index=True)
    __table_args__ = (UniqueConstraint("question_id", "question_version",
                                       name="uq_question_version"),)


class CourseScopeUnit(Base):
    """课程考点范围表（V2.1）：以本地老师 PPT 为考试范围的主要依据。

    一行 = 一个考点在本课程里「教到什么程度」。页码来自真实 PPT 页级片段，
    不是按章节名猜的；`coverage=not_found` 表示 PPT 片段里没有直接命中，
    这只说明「没检索到」，不等同于「超纲」。
    """
    __tablename__ = "course_scope_units"
    unit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kp_id: Mapped[str] = mapped_column(String(64), index=True)
    chapter: Mapped[str] = mapped_column(String(128), default="")
    name: Mapped[str] = mapped_column(String(256), default="")
    depth: Mapped[str] = mapped_column(String(32), default="理解")
    # 识记 | 理解 | 计算 | 推导
    ppt_refs: Mapped[list] = jdefault(list)
    # [{"file":"5.支持向量机-2026.pdf","pages":[3,4,7],"chunk_ids":[...],"titles":[...]}]
    concepts: Mapped[list] = jdefault(list)
    formulas: Mapped[list] = jdefault(list)
    methods: Mapped[list] = jdefault(list)
    teacher_basis: Mapped[str] = mapped_column(Text, default="")
    page_hits: Mapped[int] = mapped_column(Integer, default=0)
    chunk_hits: Mapped[int] = mapped_column(Integer, default=0)
    coverage: Mapped[str] = mapped_column(String(32), default="not_found", index=True)
    # covered | partial | not_found
    evidence: Mapped[dict] = jdefault()
    built_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class ExternalSource(Base):
    """外部来源台账：身份、链接、授权与核验结果都要留痕。

    「公开可访问」不等于「允许批量转载」，所以这里单独记 license / allows_copy，
    正文一律以链接与元数据方式管理，只保存用于判断的短摘录。
    """
    __tablename__ = "external_sources"
    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    org: Mapped[str] = mapped_column(String(128), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(32), default="course_exam")
    # course_exam | interview | video | repo_index
    region: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    # overseas | domestic | mixed | unknown
    # 「扩展学习」按来源地区分组时用；逐条按来源机构登记，不从域名/标题猜测。
    license: Mapped[str] = mapped_column(String(128), default="unknown")
    license_note: Mapped[str] = mapped_column(Text, default="")
    allows_copy: Mapped[bool] = mapped_column(Boolean, default=False)
    identity_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verify_status: Mapped[str] = mapped_column(String(48), default="unverified")
    # ok | http_error | blocked | unreachable | unverified
    http_status: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[str] = mapped_column(String(32), default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class ExternalItem(Base):
    """外部候选题 / 视频（V2.1）。

    题目只在通过范围分类且答案为可核验来源时才进入题库；
    其余以「链接 + 元数据 + 短摘录」形式留在扩展性学习里。
    """
    __tablename__ = "external_items"
    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="question")  # question|video
    category: Mapped[str] = mapped_column(String(32), default="university_exam", index=True)
    # university_exam | interview | video
    title: Mapped[str] = mapped_column(String(512), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    url_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    content_excerpt: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    kp_ids: Mapped[list] = jdefault(list)
    aux_kp_ids: Mapped[list] = jdefault(list)
    scope_class: Mapped[str] = mapped_column(String(32), default="unconfirmed", index=True)
    # in_scope | in_scope_advanced | extension | unconfirmed
    scope_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ppt_refs: Mapped[list] = jdefault(list)
    out_of_scope_points: Mapped[list] = jdefault(list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    uncertainties: Mapped[list] = jdefault(list)
    answer_status: Mapped[str] = mapped_column(String(32), default="none")
    # official | unofficial | none
    answer_ref: Mapped[str] = mapped_column(Text, default="")
    question_type: Mapped[str] = mapped_column(String(32), default="")
    difficulty: Mapped[str] = mapped_column(String(32), default="")
    family_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    duplicate_of: Mapped[str] = mapped_column(String(64), default="")
    imported_question_id: Mapped[str] = mapped_column(String(64), default="")
    review_status: Mapped[str] = mapped_column(String(32), default="auto")
    # auto | confirmed | pending
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    reviewed_at: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # V2.3：归属的试卷/题集；为空表示无法确认属于哪一套，留在「专题练习」兼容区。
    paper_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # 原卷内的顺序（题目/子题），用于按原卷顺序作答，不做随机抽题。
    position: Mapped[int] = mapped_column(Integer, default=0)


class ExternalPaper(Base):
    """高校试卷 / 面试题集（V2.3）。

    与 `ExternalItem` 是 1:N：一套试卷下挂若干题目条目；只有 PDF、没有结构化
    题目的试卷同样允许存在（question_count=0）。零散题目不进这里，留在
    ExternalItem 且 paper_id 为空，避免把单题伪装成整套高校试卷。

    字段口径（缺数据一律留空/unknown，不用 0 或「简单」冒充）：
      * `year` / `duration_minutes` 允许为 NULL，前端显示「未记录」；
      * `has_answer` 三态：True 有、False 明确没有、None 未核实；
      * `course_match_level` 只由已判别的题目范围聚合，全部未判别就是 unassessed；
      * `pdf_rel_path` 是本地已授权文件，`pdf_url` 是外部原始链接，两者分开。
    """
    __tablename__ = "external_papers"
    epaper_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    institution: Mapped[str] = mapped_column(String(256), default="")
    # overseas | domestic | mixed | unknown（快照自来源台账，改台账不影响历史）
    country_or_region: Mapped[str] = mapped_column(String(16), default="unknown",
                                                   index=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    semester: Mapped[str] = mapped_column(String(32), default="")
    # midterm | final | mock | topic_set | interview | other
    paper_type: Mapped[str] = mapped_column(String(32), default="other", index=True)
    # easy | medium | hard | very_hard | unknown
    difficulty: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    difficulty_basis: Mapped[str] = mapped_column(Text, default="")
    # high | partial | challenge | unassessed
    course_match_level: Mapped[str] = mapped_column(String(16),
                                                    default="unassessed", index=True)
    match_basis: Mapped[str] = mapped_column(Text, default="")
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    # 已收录题目的分值合计，不是原卷总分（原卷可能未收全），展示时要说明口径。
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True,
                                                      default=None)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True,
                                                         default=None)
    has_answer: Mapped[bool | None] = mapped_column(Boolean, nullable=True,
                                                    default=None)
    answer_note: Mapped[str] = mapped_column(Text, default="")
    pdf_rel_path: Mapped[str] = mapped_column(String(512), default="")
    pdf_url: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    # local_pdf | external_pdf | web_page | none
    source_type: Mapped[str] = mapped_column(String(32), default="none")
    # V2.3：三种授权分开记录，不互相推定
    #   allows_copy      = 允许再分发原卷（才提供平台内下载）
    #   preview_allowed  = 允许公开展示第一页缩略图（仅用于识别资源）
    #   本地下载测试      见 external_pdfs/manifest.jsonl，不进学生端
    preview_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    thumb_rel_path: Mapped[str] = mapped_column(String(512), default="")
    thumb_generated_at: Mapped[str] = mapped_column(String(32), default="")
    # 缩略图缓存失效依据：PDF 内容变了，sha 变，缩略图文件名随之改变
    pdf_sha256: Mapped[str] = mapped_column(String(64), default="")

    # --- V2.3 中文版（additive-only）---
    # none | partial | done | failed | running
    zh_status: Mapped[str] = mapped_column(String(32), default="", index=True)
    zh_pdf_rel_path: Mapped[str] = mapped_column(String(512), default="")
    zh_generated_at: Mapped[str] = mapped_column(String(32), default="")
    # 缺题说明：不允许只翻 2 题却声称整卷
    zh_missing_note: Mapped[str] = mapped_column(Text, default="")
    # 缓存键：pdf_sha256 + PROMPT_VERSION，任一变化即需要重建
    localize_version: Mapped[str] = mapped_column(String(64), default="")
    localize_model: Mapped[str] = mapped_column(String(64), default="")
    localized_at: Mapped[str] = mapped_column(String(32), default="")
    localize_note: Mapped[str] = mapped_column(Text, default="")
    license_status: Mapped[str] = mapped_column(String(128), default="unknown")
    allows_copy: Mapped[bool] = mapped_column(Boolean, default=False)
    online_available: Mapped[bool] = mapped_column(Boolean, default=False)
    online_question_count: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[str] = mapped_column(String(32), default="unverified")
    origin_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class Resource(Base):
    __tablename__ = "resources"
    resource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(64), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(256), default="")
    kp_ids: Mapped[list] = jdefault(list)
    reason: Mapped[str] = mapped_column(Text, default="")
    checked_date: Mapped[str] = mapped_column(String(32), default="")
    verifiable_segment: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(32), default="external")  # local|external
    local_ref: Mapped[str] = mapped_column(Text, default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # --- V3.0 在线资源卡片（additive-only）---
    author: Mapped[str] = mapped_column(String(128), default="")            # 作者/UP 主/账号
    content_type: Mapped[str] = mapped_column(String(32), default="")       # video|article|topic
    intro: Mapped[str] = mapped_column(Text, default="")                    # 内容简介（不猜正文）
    match_level: Mapped[str] = mapped_column(String(32), default="")        # high|partial|extension
    scope: Mapped[str] = mapped_column(String(32), default="unconfirmed")   # in_scope|extension|unconfirmed
    access_status: Mapped[str] = mapped_column(String(32), default="")      # ok|unreachable|login_required
    thumbnail: Mapped[str] = mapped_column(Text, default="")                # 封面地址（平台原始图）
    verified_scope: Mapped[str] = mapped_column(String(32), default="")     # title_only|partial|full
    # --- V3.0 第二轮：封面来源、双语标题、内容核验、测试标记 ---
    cover_source: Mapped[str] = mapped_column(String(32), default="")       # bilibili_api|og_image|none
    original_title: Mapped[str] = mapped_column(String(256), default="")
    title_zh: Mapped[str] = mapped_column(String(256), default="")
    content_status: Mapped[str] = mapped_column(String(32), default="")     # title_only|intro_checked|full_text|subtitle_checked
    is_test_fixture: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Paper(Base):
    __tablename__ = "papers"
    paper_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    mode: Mapped[str] = mapped_column(String(32), default="quiz")  # quiz|practice|retest
    blueprint: Mapped[dict] = jdefault()
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    estimated_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    coverage_report: Mapped[dict] = jdefault()
    source: Mapped[str] = mapped_column(String(32), default="bank")
    is_test_fixture: Mapped[bool] = mapped_column(Boolean, default=False,
                                                   index=True)
    # 归档而不是删除：已有答卷仍要能回看原卷快照（V2 §11.3）
    archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)  # bank|ai
    # V2.1：本次组卷使用的考查范围，交卷结果按它区分「课程内」与「扩展」，不混算
    scope: Mapped[str] = mapped_column(String(32), default="course_only")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class PaperItem(Base):
    """Immutable snapshot: version, score, order and scoring rule are fixed."""
    __tablename__ = "paper_items"
    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(String(64), index=True)
    position: Mapped[int] = mapped_column(Integer)
    question_id: Mapped[str] = mapped_column(String(64))
    question_version: Mapped[int] = mapped_column(Integer)
    chapter_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    score: Mapped[float] = mapped_column(Float)
    snapshot: Mapped[dict] = jdefault()   # includes server-side answer_payload/rubric
    __table_args__ = (UniqueConstraint("paper_id", "position",
                                       name="uq_paper_position"),)


class Attempt(Base):
    __tablename__ = "attempts"
    attempt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(32), default="quiz")
    status: Mapped[str] = mapped_column(String(32), default="in_progress")  # in_progress|submitted
    started_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    server_started_ms: Mapped[int] = mapped_column(Integer, default=0)
    submitted_at: Mapped[str] = mapped_column(String(32), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, default=0.0)
    objective_score: Mapped[float] = mapped_column(Float, default=0.0)
    manual_pending_count: Mapped[int] = mapped_column(Integer, default=0)
    # NULL-able: SQLite UNIQUE allows many NULLs; a real key is written once on submit.
    submit_request_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None, index=True)
    diagnosis_status: Mapped[str] = mapped_column(String(32), default="not_requested")
    # Classroom identity is deliberately separate from the login/user owner.
    # Empty values are legacy-compatible and are backfilled by the classroom
    # migration without changing the original user_id.
    student_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    assessment_version_id: Mapped[str] = mapped_column(
        String(64), default="", index=True)
    attendance_status: Mapped[str] = mapped_column(
        String(32), default="attended", index=True)  # attended|absent|unknown
    grading_status: Mapped[str] = mapped_column(
        String(32), default="unknown", index=True)  # graded|partial|pending_key|unknown
    source_reported_total: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None)
    max_score_reliable: Mapped[bool] = mapped_column(Boolean, default=True)
    source_submission_key: Mapped[str] = mapped_column(
        String(192), default="", index=True)
    source_import_run_id: Mapped[str] = mapped_column(
        String(64), default="", index=True)
    is_effective: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    duplicate_of_attempt_id: Mapped[str] = mapped_column(
        String(64), default="")
    assignment_recipient_id: Mapped[str] = mapped_column(
        String(64), default="", index=True)
    retest_of_attempt_id: Mapped[str] = mapped_column(
        String(64), default="", index=True)
    diagnosis_cycle_id: Mapped[str] = mapped_column(
        String(64), default="", index=True)
    __table_args__ = (UniqueConstraint("submit_request_key",
                                       name="uq_submit_request_key"),)


class Answer(Base):
    __tablename__ = "answers"
    answer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(64), index=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    question_version: Mapped[int] = mapped_column(Integer)
    paper_item_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None, index=True)
    response: Mapped[dict] = jdefault()
    saved_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    save_count: Mapped[int] = mapped_column(Integer, default=1)
    hints_used: Mapped[int] = mapped_column(Integer, default=0)
    solution_viewed: Mapped[bool] = mapped_column(Boolean, default=False)
    seen_before: Mapped[bool] = mapped_column(Boolean, default=False)
    is_independent_evidence: Mapped[bool] = mapped_column(Boolean, default=True)
    response_status: Mapped[str] = mapped_column(
        String(32), default="answered", index=True)  # answered|unanswered|unavailable
    grading_status: Mapped[str] = mapped_column(
        String(32), default="unknown", index=True)
    source_ref: Mapped[str] = mapped_column(String(256), default="")
    raw_response: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("attempt_id", "question_id",
                                       name="uq_attempt_question"),)


class AnswerAttachment(Base):
    """学生上传的手写答案图片（V2.3）。

    原图必须保留：OCR 只产生可编辑文本，绝不覆盖或代替原图。
    文件只通过受控接口读取（校验 attempt 归属），不暴露静态目录、不允许猜 URL。
    """
    __tablename__ = "answer_attachments"
    attachment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(64), index=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    rel_path: Mapped[str] = mapped_column(String(512), default="")
    mime: Mapped[str] = mapped_column(String(32), default="image/png")
    bytes: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class OCRResult(Base):
    """一次 OCR 识别记录。保留历史，便于「重新识别」与人工修正对照。"""
    __tablename__ = "ocr_results"
    ocr_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attachment_id: Mapped[str] = mapped_column(String(64), index=True)
    attempt_id: Mapped[str] = mapped_column(String(64), index=True)
    question_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default="ok")  # ok|failed
    error: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[str] = mapped_column(String(32), default="")


class AnswerScore(Base):
    __tablename__ = "answer_scores"
    score_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(64), index=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    question_version: Mapped[int] = mapped_column(Integer)
    paper_item_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None, index=True)
    chapter_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    kp_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    family_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    awarded_score: Mapped[float] = mapped_column(Float, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, default=0.0)
    scoring_status: Mapped[str] = mapped_column(String(32), default="auto")  # auto|needs_manual|ai_review|rescored
    score_provenance: Mapped[str] = mapped_column(
        String(48), default="platform_scored", index=True)
    authority_status: Mapped[str] = mapped_column(
        String(48), default="snapshot_rule", index=True)
    teacher_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_ref: Mapped[str] = mapped_column(String(256), default="")
    detail: Mapped[dict] = jdefault()
    computed_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    __table_args__ = (UniqueConstraint("attempt_id", "question_id",
                                       name="uq_score_attempt_question"),)


class WrongBookEntry(Base):
    __tablename__ = "wrong_book"
    entry_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    attempt_id: Mapped[str] = mapped_column(String(64), index=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    question_version: Mapped[int] = mapped_column(Integer)
    kp_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    family_id: Mapped[str] = mapped_column(String(64), default="")
    original_response: Mapped[dict] = jdefault()
    lost_points: Mapped[float] = mapped_column(Float, default=0.0)
    misconception_candidates: Mapped[list] = jdefault(list)
    correction: Mapped[str] = mapped_column(Text, default="")
    self_report: Mapped[str] = mapped_column(String(64), default="")  # 不会|算错|读错|猜测|...
    material_refs: Mapped[list] = jdefault(list)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open|corrected|verified
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    # --- V3.3 additive-only：展示去重用的内容指纹与规范记录关联 ---
    # 历史记录一条不删；这三列为空时后端会在请求期实时计算指纹。
    question_fingerprint: Mapped[str] = mapped_column(String(64), default="", index=True)
    canonical_question_id: Mapped[str] = mapped_column(String(64), default="")
    canonical_entry_id: Mapped[str] = mapped_column(String(64), default="")
    __table_args__ = (UniqueConstraint("attempt_id", "question_id",
                                       name="uq_wrong_attempt_question"),)


class RetestTask(Base):
    __tablename__ = "retest_tasks"
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    chapter_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    kp_id: Mapped[str] = mapped_column(String(64), index=True)
    from_wrong_id: Mapped[str] = mapped_column(String(64), default="")
    family_id: Mapped[str] = mapped_column(String(64), default="")
    kind: Mapped[str] = mapped_column(String(32), default="new_transfer")  # original_correction|new_transfer|delayed
    due_at: Mapped[str] = mapped_column(String(32), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|completed|expired
    paper_id: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[dict] = jdefault()
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    student_id: Mapped[str] = mapped_column(String(64), default="", index=True)


class TargetedRetestCycle(Base):
    """One teacher-published retest round linked to its original diagnosis.

    This is deliberately separate from ``Attempt``: the immutable answer
    sheet remains the scoring source of truth, while this row records the
    learning-loop state and the deterministic 80% decision.
    """

    __tablename__ = "targeted_retest_cycles"
    cycle_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.student_id"), index=True)
    source_attempt_id: Mapped[str] = mapped_column(
        ForeignKey("attempts.attempt_id"), index=True)
    source_assessment_version_id: Mapped[str] = mapped_column(
        String(64), default="", index=True)
    diagnosis_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    release_id: Mapped[str] = mapped_column(
        String(64), default="", unique=True, index=True)
    retest_attempt_id: Mapped[str] = mapped_column(
        String(64), default="", unique=True, index=True)
    workflow_run_id: Mapped[str] = mapped_column(String(64), default="")
    round_no: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(
        String(32), default="published", index=True)
    threshold: Mapped[float] = mapped_column(Float, default=0.8)
    question_count: Mapped[int] = mapped_column(Integer, default=5)
    correct_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    chapter_ids: Mapped[list] = jdefault(list)
    kp_ids: Mapped[list] = jdefault(list)
    concept_names: Mapped[list] = jdefault(list)
    generated_question_ids: Mapped[list] = jdefault(list)
    remaining_kp_ids: Mapped[list] = jdefault(list)
    published_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    submitted_at: Mapped[str] = mapped_column(String(32), default="")
    completed_at: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    __table_args__ = (
        UniqueConstraint("source_attempt_id", "student_id", "round_no",
                         name="uq_targeted_retest_source_student_round"),
    )


class Diagnosis(Base):
    __tablename__ = "diagnoses"
    diagnosis_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(32), index=True)  # question|paper|history
    target_id: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict] = jdefault()
    evidence_refs: Mapped[list] = jdefault(list)
    uncertainties: Mapped[list] = jdefault(list)
    next_actions: Mapped[list] = jdefault(list)
    skill_version: Mapped[str] = mapped_column(String(64), default="")
    run_id: Mapped[str] = mapped_column(String(64), default="")
    engine: Mapped[str] = mapped_column(String(32), default="deterministic")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    # Explicit scope columns keep personal and classroom diagnoses in one
    # auditable table without pretending that an anonymous student is a login.
    student_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    classroom_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    assessment_version_id: Mapped[str] = mapped_column(
        String(64), default="", index=True)
    scope_type: Mapped[str] = mapped_column(
        String(32), default="personal", index=True)


class KnowledgeState(Base):
    """Derived from valid evidence only; recomputable from learning events."""
    __tablename__ = "knowledge_states"
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kp_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), default="未测")  # 未测|证据不足|需加强|初步通过|阶段稳定
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    independent_family_count: Mapped[int] = mapped_column(Integer, default=0)
    time_span_days: Mapped[float] = mapped_column(Float, default=0.0)
    hint_count: Mapped[int] = mapped_column(Integer, default=0)
    last_evidence_at: Mapped[str] = mapped_column(String(32), default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class LearningEvent(Base):
    __tablename__ = "learning_events"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(128), default="")
    payload: Mapped[dict] = jdefault()
    occurred_at: Mapped[str] = mapped_column(String(32), default=utcnow, index=True)


class StudyPlan(Base):
    __tablename__ = "study_plans"
    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    exam_date: Mapped[str] = mapped_column(String(32), default="")
    daily_minutes: Mapped[int] = mapped_column(Integer, default=60)
    payload: Mapped[dict] = jdefault()
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PlanTask(Base):
    __tablename__ = "plan_tasks"
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kp_id: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(256), default="")
    kind: Mapped[str] = mapped_column(String(32), default="review")
    material_ref: Mapped[str] = mapped_column(Text, default="")
    question_ids: Mapped[list] = jdefault(list)
    minutes: Mapped[int] = mapped_column(Integer, default=20)
    due_date: Mapped[str] = mapped_column(String(32), default="", index=True)
    completion_criteria: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    completed_at: Mapped[str] = mapped_column(String(32), default="")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(32), default="practice")
    context: Mapped[dict] = jdefault()
    source_refs: Mapped[list] = jdefault(list)
    engine: Mapped[str] = mapped_column(String(32), default="deterministic")
    run_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class SkillRun(Base):
    __tablename__ = "skill_runs"
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    skill_name: Mapped[str] = mapped_column(String(64), index=True)
    skill_version: Mapped[str] = mapped_column(String(64), default="")
    thread_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    job_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="running")  # running|succeeded|failed|model_unconfigured
    engine: Mapped[str] = mapped_column(String(32), default="deterministic")
    input_payload: Mapped[dict] = jdefault()
    output_payload: Mapped[dict] = jdefault()
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    finished_at: Mapped[str] = mapped_column(String(32), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class ModelCall(Base):
    __tablename__ = "model_calls"
    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    skill_name: Mapped[str] = mapped_column(String(64), default="")
    provider: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    usage: Mapped[dict] = jdefault()
    usage_missing: Mapped[bool] = mapped_column(Boolean, default=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    # "stop" = completed, "length" = output cut off by max_tokens. A truncated
    # JSON must never be accepted as a valid answer, so this is persisted.
    finish_reason: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default="ok")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class Job(Base):
    """Background work with restart-safe states and an idempotency key."""
    __tablename__ = "jobs"
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    # queued|running|succeeded|failed|cancelled
    stage: Mapped[str] = mapped_column(String(64), default="")
    progress: Mapped[dict] = jdefault()
    payload: Mapped[dict] = jdefault()
    result: Mapped[dict] = jdefault()
    error: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    claimed_at: Mapped[str] = mapped_column(String(32), default="")
    __table_args__ = (UniqueConstraint("idempotency_key", "job_type",
                                       name="uq_job_idempotency"),)


class AnswerGeneration(Base):
    """AI reference-answer generation record.

    One row per (question_id, question_version, answer_version). A succeeded
    row is the CACHE for that exact question version: paper assembly must reuse
    it and must never call the model again for the same version.

    `request_fingerprint` is the SHA256 of the exact prompt input. It exists so
    we can PROVE the generation request contained only stem/options/material
    snippets and no student response (see answer_pipeline.build_request).
    """
    __tablename__ = "answer_generations"
    generation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    question_version: Mapped[int] = mapped_column(Integer, default=1)
    answer_version: Mapped[int] = mapped_column(Integer, default=1)
    trigger: Mapped[str] = mapped_column(String(32), default="import_batch")
    # import_batch | on_demand | pre_exam | dispute_regen
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    # queued | running | succeeded | needs_attention |
    # pending_model_configuration | failed
    source: Mapped[str] = mapped_column(String(40), default="deepseek")
    # deepseek | human_correction | source | computed
    model_provider: Mapped[str] = mapped_column(String(64), default="")
    model_name: Mapped[str] = mapped_column(String(128), default="")
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    generated_at: Mapped[str] = mapped_column(String(32), default="")
    request_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    student_answer_included: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict] = jdefault()
    checks: Mapped[dict] = jdefault()
    exception_code: Mapped[str] = mapped_column(String(64), default="")
    exception_detail: Mapped[str] = mapped_column(Text, default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[str] = mapped_column(String(64), default="")
    call_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    __table_args__ = (
        UniqueConstraint("question_id", "question_version", "answer_version",
                         name="uq_answer_generation_version"),
    )


class AnswerDispute(Base):
    """'答案有疑问' ticket: review -> answer version correction -> regrade
    -> diagnosis/knowledge-state invalidation, all traceable in one record."""
    __tablename__ = "answer_disputes"
    dispute_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    question_version: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[str] = mapped_column(String(64), default="local_user")
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    suggested_answer: Mapped[dict] = jdefault()
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    # open | investigating | resolved | rejected
    resolution: Mapped[str] = mapped_column(String(48), default="")
    # corrected | regenerated | confirmed | rejected
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    from_generation_id: Mapped[str] = mapped_column(String(64), default="")
    to_generation_id: Mapped[str] = mapped_column(String(64), default="")
    new_question_version: Mapped[int] = mapped_column(Integer, default=0)
    rescored_rows: Mapped[int] = mapped_column(Integer, default=0)
    diagnoses_invalidated: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    resolved_at: Mapped[str] = mapped_column(String(32), default="")


class AnswerValidation(Base):
    """One append-only candidate-answer validation decision.

    The candidate supplied by a repair package and the independently solved
    answer are stored separately so an agreement can be audited later.  A row
    never claims teacher confirmation unless that actually happened.
    """

    __tablename__ = "answer_validations"
    validation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    question_version: Mapped[int] = mapped_column(Integer, default=0)
    candidate_source: Mapped[str] = mapped_column(String(64), default="")
    candidate_payload: Mapped[dict] = jdefault()
    independent_payload: Mapped[dict] = jdefault()
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    confidence: Mapped[str] = mapped_column(String(32), default="")
    method: Mapped[str] = mapped_column(String(64), default="")
    model_call_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    checks: Mapped[dict] = jdefault()
    source_refs: Mapped[list] = jdefault(list)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class ScoringRun(Base):
    """Immutable header for one auditable historical regrading run."""

    __tablename__ = "scoring_runs"
    scoring_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), default="", index=True)
    answer_key_version: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    source: Mapped[str] = mapped_column(String(64), default="")
    parameters: Mapped[dict] = jdefault()
    summary: Mapped[dict] = jdefault()
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    started_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    completed_at: Mapped[str] = mapped_column(String(32), default="")


class AnswerRegrade(Base):
    """Append-only score fact layered over, never replacing, AnswerScore."""

    __tablename__ = "answer_regrades"
    regrade_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scoring_run_id: Mapped[str] = mapped_column(
        ForeignKey("scoring_runs.scoring_run_id"), index=True)
    original_score_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    answer_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    attempt_id: Mapped[str] = mapped_column(String(64), index=True)
    paper_item_id: Mapped[str] = mapped_column(String(64), index=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    original_question_version: Mapped[int] = mapped_column(Integer, default=0)
    answer_key_question_version: Mapped[int] = mapped_column(Integer, default=0)
    chapter_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    awarded_score: Mapped[float] = mapped_column(Float, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, default=0.0)
    scoring_status: Mapped[str] = mapped_column(String(32), default="rescored")
    score_provenance: Mapped[str] = mapped_column(
        String(48), default="historical_regrade", index=True)
    authority_status: Mapped[str] = mapped_column(
        String(48), default="ai_auto_checked", index=True)
    teacher_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_ref: Mapped[str] = mapped_column(String(256), default="")
    detail: Mapped[dict] = jdefault()
    computed_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    # Compatibility with evidence helpers that expect AnswerScore.score_id.
    @property
    def score_id(self) -> str:
        return self.regrade_id

    @property
    def kp_id(self) -> str:
        return ""

    @property
    def family_id(self) -> str:
        return ""

    __table_args__ = (
        UniqueConstraint("scoring_run_id", "attempt_id", "paper_item_id",
                         name="uq_regrade_run_attempt_item"),
    )


class AttemptRegradeSummary(Base):
    """A recomputed total kept beside the source-reported historical total."""

    __tablename__ = "attempt_regrade_summaries"
    summary_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scoring_run_id: Mapped[str] = mapped_column(
        ForeignKey("scoring_runs.scoring_run_id"), index=True)
    attempt_id: Mapped[str] = mapped_column(String(64), index=True)
    scored_items: Mapped[int] = mapped_column(Integer, default=0)
    correct_items: Mapped[int] = mapped_column(Integer, default=0)
    awarded_score: Mapped[float] = mapped_column(Float, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, default=0.0)
    coverage_rate: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="partial", index=True)
    detail: Mapped[dict] = jdefault()
    computed_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    __table_args__ = (
        UniqueConstraint("scoring_run_id", "attempt_id",
                         name="uq_attempt_regrade_run"),
    )


class MaterialUpload(Base):
    """One user-uploaded material item.

    The ORIGINAL file, the extracted questions and the assembled answer booklet
    are all tied together here, so closing the browser or restarting the server
    never loses the task: every state below is a real DB row, not memory.

    status:
      queued | parsing | parsed | generating | partial | completed | failed
    `parsed` means the original was parsed and questions were recognised but no
    answer is usable yet (e.g. DeepSeek not configured). `partial` means some
    answers are usable and some are not. `completed` means every recognised
    question has a fixed, usable answer and the booklet/PDF were rebuilt.
    """
    __tablename__ = "material_uploads"
    upload_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default="local_user", index=True)
    filename: Mapped[str] = mapped_column(String(512))
    stored_rel_path: Mapped[str] = mapped_column(String(512))   # relative to PROJECT_ROOT
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mime: Mapped[str] = mapped_column(String(128), default="")

    declared_type: Mapped[str] = mapped_column(String(32), default="auto")
    resolved_type: Mapped[str] = mapped_column(String(32), default="other", index=True)
    detected_type: Mapped[str] = mapped_column(String(32), default="other")
    detect_reason: Mapped[str] = mapped_column(Text, default="")
    # V2.2 内容级分类：判定来源 rule|model|user、置信度、是否需要人工复核
    classify_source: Mapped[str] = mapped_column(String(16), default="")
    classify_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    classify_needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    # 课件后台静默出题：幂等记录，避免重复调用模型、重复入库
    slides_question_count: Mapped[int] = mapped_column(Integer, default=0)
    slides_job_id: Mapped[str] = mapped_column(String(64), default="")

    document_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    document_version_id: Mapped[str] = mapped_column(String(64), default="")
    parse_status: Mapped[str] = mapped_column(String(48), default="")

    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress: Mapped[dict] = jdefault()
    error: Mapped[str] = mapped_column(Text, default="")

    question_count: Mapped[int] = mapped_column(Integer, default=0)
    answer_ready_count: Mapped[int] = mapped_column(Integer, default=0)
    question_order: Mapped[list] = jdefault(list)   # question_ids in original order

    booklet_id: Mapped[str] = mapped_column(String(64), default="")
    paper_id: Mapped[str] = mapped_column(String(64), default="")

    deduped: Mapped[bool] = mapped_column(Boolean, default=False)
    reused_from_upload_id: Mapped[str] = mapped_column(String(64), default="")
    job_id: Mapped[str] = mapped_column(String(64), default="", index=True)

    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    # 只有显式标为测试夹具的记录才允许被清理脚本删除
    is_test_fixture: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class DailyThreeSet(Base):
    """V2.2：每日 3 题的「一组题」快照。

    题目从题库随机抽出后固定下来，页面上看到的三题与点击提交时实际评分的
    三题必须是同一组（否则刷新或换页就会答非所问）。提交结果与作答记录也挂在
    这里，刷新首页能直接看到已交卷的解析，不用跳转。
    """
    __tablename__ = "daily_three_sets"
    set_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default="local_user", index=True)
    day: Mapped[str] = mapped_column(String(16), default="", index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    items: Mapped[list] = jdefault(list)       # 完整题干/选项/题型/考点，绝不含答案
    pool_sizes: Mapped[dict] = jdefault()
    paper_id: Mapped[str] = mapped_column(String(64), default="")
    attempt_id: Mapped[str] = mapped_column(String(64), default="")
    submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict] = jdefault()          # 仅交卷后才写：正确答案/解析/评分
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class AnswerBooklet(Base):
    """Assembled whole-paper answer booklet for an uploaded exam paper.

    This is the SINGLE source that the web answer view, the downloadable PDF and
    the scoring path all read. `content_hash` covers every (question_id,
    answer_version, answer_payload, rubric) triple, so a corrected answer makes
    the booklet stale and it is rebuilt instead of silently diverging.
    """
    __tablename__ = "answer_booklets"
    booklet_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    upload_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending | partial | complete
    sections: Mapped[list] = jdefault(list)
    # [{"section":"一、单项选择题","section_index":1,
    #   "questions":[{"no":"一 第1题","order":1,"question_type":"single_choice",
    #                 "score":2,"answer_text":"B","explanation":"...",
    #                 "rubric_points":[...],"answer_version":1,
    #                 "answer_origin":"deepseek_generated","source_refs":[...]}]}]
    answer_version_map: Mapped[dict] = jdefault()   # question_id -> answer_version
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    answered_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    # 1 for the first assembly, +1 each time the user asks to regenerate.
    # Old attempts keep the score they were graded with; only the booklet the
    # web view / PDF / future scoring read moves forward.
    version: Mapped[int] = mapped_column(Integer, default=1)
    generated_at: Mapped[str] = mapped_column(String(32), default="")
    pdf_rel_path: Mapped[str] = mapped_column(String(512), default="")
    pdf_bytes: Mapped[int] = mapped_column(Integer, default=0)
    pdf_generated_at: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class AnswerReveal(Base):
    """'User actually saw the answer' evidence.

    Auto-generating an answer must NEVER count as the user having seen it.
    A row is written only when the user opens the answer view or downloads the
    answer PDF; later attempts on that question are then marked `seen_before`
    and excluded from independent-evidence state promotion.
    """
    __tablename__ = "answer_reveals"
    reveal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    question_version: Mapped[int] = mapped_column(Integer, default=1)
    channels: Mapped[list] = jdefault(list)   # web_answer | pdf_download
    reveal_count: Mapped[int] = mapped_column(Integer, default=1)
    upload_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    first_revealed_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    last_revealed_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "question_id",
                                       name="uq_reveal_user_question"),)


Index("ix_answers_attempt", Answer.attempt_id)
Index("ix_scores_kp", AnswerScore.kp_id)
Index("ix_events_time", LearningEvent.occurred_at)


class Favorite(Base):
    """去重收藏：题目 / 试卷 / 考点 / 资料片段。

    收藏只影响个人偏好与快捷入口，**不改变任何掌握状态**（V2 §11.6）。
    `title` 是保存时的展示快照，便于原对象被改名或停用后仍能解释这条收藏。
    """
    __tablename__ = "favorites"
    entry_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # question|paper|kp|chunk
    target_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "kind", "target_id",
                                       name="uq_favorite_target"),)


class UserSetting(Base):
    """个人设置（单用户本地版）。

    仅保存界面偏好与学习参数；API 密钥不在此存储，也不下发给前端
    （V2 §11.7-4：密钥只走后端环境变量，页面只读状态）。
    """
    __tablename__ = "user_settings"
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nickname: Mapped[str] = mapped_column(String(64), default="本地学习者")
    exam_date: Mapped[str] = mapped_column(String(32), default="")
    daily_minutes: Mapped[int] = mapped_column(Integer, default=60)
    default_mode: Mapped[str] = mapped_column(String(32), default="practice")
    default_difficulty: Mapped[str] = mapped_column(String(32), default="")
    payload: Mapped[dict] = jdefault()
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)

class ImportRun(Base):
    """V3.5 统一资料导入流水线的一次执行记录。

    为什么需要这张表：初始化批量导入（external_papers）和用户手动上传
    （material_uploads）原本各有一份状态字段，语义和阶段名都不一样。把
    "这一次处理跑到哪一步、每步结果如何"独立记在这里，两种入口就能共用
    同一套阶段词表、断点续跑与失败重试，而不必再造第二套题库。
    """
    __tablename__ = "import_runs"
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # external_paper | material_upload | url
    source_kind: Mapped[str] = mapped_column(String(32), index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    file_sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    file_rel_path: Mapped[str] = mapped_column(String(512), default="")
    language: Mapped[str] = mapped_column(String(16), default="")
    # queued|running|completed|partial|needs_review|failed
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(48), default="queued")
    steps: Mapped[list] = jdefault(list)
    result: Mapped[dict] = jdefault(dict)
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    job_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    is_test_fixture: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# Classroom assessment domain (V4, additive-only)


class Student(Base):
    """A learner identity independent from an authentication account.

    ``external_key`` is stable within its source dataset (for example
    ``ml-2025-fall:STU002``).  ``display_code`` is the only classroom label
    exposed by the API; names and source document properties are never copied.
    """

    __tablename__ = "students"
    student_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_key: Mapped[str] = mapped_column(String(192), unique=True,
                                               index=True)
    display_code: Mapped[str] = mapped_column(String(64), index=True)
    linked_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.user_id"), nullable=True, default=None, unique=True,
        index=True)
    anonymized: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(48), default="classroom_import")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    profile: Mapped[dict] = jdefault()
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class Classroom(Base):
    __tablename__ = "classrooms"
    classroom_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    course_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    term: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(48), default="teacher_workbook")
    details: Mapped[dict] = jdefault()
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class ClassroomStudent(Base):
    __tablename__ = "classroom_students"
    membership_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    classroom_id: Mapped[str] = mapped_column(
        ForeignKey("classrooms.classroom_id"), index=True)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.student_id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    joined_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    __table_args__ = (
        UniqueConstraint("classroom_id", "student_id",
                         name="uq_classroom_student"),
    )


class Assessment(Base):
    """A logical quiz, independent from any one published version."""

    __tablename__ = "assessments"
    assessment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    classroom_id: Mapped[str] = mapped_column(
        ForeignKey("classrooms.classroom_id"), index=True)
    external_key: Mapped[str] = mapped_column(String(192), unique=True,
                                               index=True)
    quiz_code: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    topic: Mapped[str] = mapped_column(String(256), default="")
    chapter_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    scheduled_at: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(48), default="teacher_workbook")
    details: Mapped[dict] = jdefault()
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class AssessmentVersion(Base):
    """Maps a logical assessment to one immutable Paper/PaperItem snapshot."""

    __tablename__ = "assessment_versions"
    assessment_version_id: Mapped[str] = mapped_column(
        String(64), primary_key=True)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.assessment_id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    version_key: Mapped[str] = mapped_column(String(192), unique=True,
                                             index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.paper_id"),
                                          unique=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    scoring_status: Mapped[str] = mapped_column(
        String(32), default="pending_key", index=True)
    answer_key_source: Mapped[str] = mapped_column(
        String(48), default="missing", index=True)
    teacher_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_file_sha256: Mapped[str] = mapped_column(
        String(64), default="", index=True)
    scoring_policy: Mapped[dict] = jdefault()
    published_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    __table_args__ = (
        UniqueConstraint("assessment_id", "version_no",
                         name="uq_assessment_version_number"),
    )


class ImportedSubmission(Base):
    """One source student/quiz block, including absences and duplicates.

    The normalized Attempt is only created for ``status=canonical``.  Keeping
    this row separate lets an import preserve all available source facts and
    explain why a record was selected or excluded.
    """

    __tablename__ = "imported_submissions"
    submission_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    import_run_id: Mapped[str] = mapped_column(
        ForeignKey("import_runs.run_id"), index=True)
    dataset_id: Mapped[str] = mapped_column(String(64), index=True)
    source_sheet: Mapped[str] = mapped_column(String(128), default="")
    source_row: Mapped[int] = mapped_column(Integer, default=0)
    source_key: Mapped[str] = mapped_column(String(256), index=True)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.student_id"), index=True)
    assessment_version_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_versions.assessment_version_id"), index=True)
    status: Mapped[str] = mapped_column(
        String(32), default="canonical", index=True)  # canonical|absent|duplicate
    canonical_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("attempts.attempt_id"), nullable=True, default=None,
        index=True)
    duplicate_of_submission_id: Mapped[str] = mapped_column(
        String(64), default="")
    selection_rule: Mapped[str] = mapped_column(String(64), default="")
    dedupe_reason: Mapped[str] = mapped_column(Text, default="")
    raw_preserved: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_payload: Mapped[dict] = jdefault()
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    __table_args__ = (
        UniqueConstraint("dataset_id", "source_key",
                         name="uq_imported_submission_source"),
    )


class StudentKnowledgeState(Base):
    """Derived classroom learner state; personal KnowledgeState is unchanged."""

    __tablename__ = "student_knowledge_states"
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.student_id"), primary_key=True)
    kp_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_points.kp_id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), default="未测")
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    independent_family_count: Mapped[int] = mapped_column(Integer, default=0)
    scored_assessment_count: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    last_evidence_at: Mapped[str] = mapped_column(String(32), default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class SessionToken(Base):
    """Opaque login session; only the SHA-256 token hash is stored."""

    __tablename__ = "session_tokens"
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    role_snapshot: Mapped[str] = mapped_column(String(24), default="student")
    expires_at: Mapped[str] = mapped_column(String(32), index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    last_seen_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class AssessmentRelease(Base):
    """A teacher publication of one immutable assessment version."""

    __tablename__ = "assessment_releases"
    release_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assessment_version_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_versions.assessment_version_id"), index=True)
    teacher_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id"), index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    release_type: Mapped[str] = mapped_column(
        String(24), default="exam", index=True)  # exam|retest
    status: Mapped[str] = mapped_column(
        String(24), default="draft", index=True)  # draft|published|closed
    due_at: Mapped[str] = mapped_column(String(32), default="", index=True)
    source_diagnosis_id: Mapped[str] = mapped_column(String(64), default="")
    source_attempt_id: Mapped[str] = mapped_column(String(64), default="")
    workflow_run_id: Mapped[str] = mapped_column(String(64), default="")
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None, unique=True)
    published_at: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class ReleaseRecipient(Base):
    """One assigned student and its submission state for a release."""

    __tablename__ = "release_recipients"
    recipient_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    release_id: Mapped[str] = mapped_column(
        ForeignKey("assessment_releases.release_id"), index=True)
    student_id: Mapped[str] = mapped_column(
        ForeignKey("students.student_id"), index=True)
    status: Mapped[str] = mapped_column(
        String(24), default="assigned", index=True)  # assigned|opened|submitted|expired
    attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("attempts.attempt_id"), nullable=True, default=None,
        unique=True, index=True)
    assigned_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    opened_at: Mapped[str] = mapped_column(String(32), default="")
    submitted_at: Mapped[str] = mapped_column(String(32), default="")
    __table_args__ = (
        UniqueConstraint("release_id", "student_id",
                         name="uq_release_recipient_student"),
    )
