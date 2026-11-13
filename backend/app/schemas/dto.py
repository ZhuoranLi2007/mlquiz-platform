"""Request DTOs. Keep them small; responses use explicit dicts so the
student-facing whitelist in services/question_dto.py is the only gate."""
from __future__ import annotations

from pydantic import BaseModel, Field


class BlueprintIn(BaseModel):
    title: str = "自动组卷"
    kp_ids: list[str] = Field(default_factory=list)
    question_types: list[str] = Field(default_factory=list)
    count: int = 5
    total_score: float | None = None
    estimated_minutes: float | None = None
    cognitive_levels: list[str] = Field(default_factory=list)
    difficulty: list[str] = Field(default_factory=list)
    exclude_seen: bool = True
    family_spread: bool = True
    allow_unverified: bool = False
    mode: str = "quiz"
    # V2.1 考查范围三档：course_only（默认）| course_plus_advanced | include_extension
    scope: str = "course_only"
    # V3.1：题库不够时用 DeepSeek 补足缺口（默认开，显式关掉才不补）
    ai_fill: bool = True


class AnswerIn(BaseModel):
    response: dict = Field(default_factory=dict)
    hints_used: int = 0
    solution_viewed: bool = False


class ManualPaperIn(BaseModel):
    """V3.3 手动选题组卷：题目顺序 = 用户勾选顺序，分值逐题可改。

    V3.4（RC-3）：新增 `ai_fill` / `target_count`。任务书要求
    「题库题目不足时，由用户明确选择是否允许 AI 补题」——默认关闭，
    只有用户显式勾选才会调用 DeepSeek 补足缺口。
    """
    title: str = ""
    mode: str = "practice"            # practice | quiz
    question_ids: list[str] = Field(default_factory=list)
    scores: list[float] = Field(default_factory=list)
    estimated_minutes: float | None = None
    # 只有验收脚本会显式置 True；页面手动组卷产生的都是真实用户试卷。
    mark_as_test: bool = False
    # --- V3.4 AI 补题（默认关闭，必须由用户显式开启）---
    ai_fill: bool = False
    target_count: int | None = None    # 目标题量；缺省=已选题量
    ai_kp_ids: list[str] = Field(default_factory=list)
    ai_question_types: list[str] = Field(default_factory=list)


class SubmitIn(BaseModel):
    request_key: str | None = None


class WrongPatchIn(BaseModel):
    correction: str | None = None
    self_report: str | None = None
    status: str | None = None


class PlanTaskPatchIn(BaseModel):
    status: str | None = None
    minutes: int | None = None


class ChatIn(BaseModel):
    message: str
    session_id: str = "default"
    mode: str = "practice"
    context: dict = Field(default_factory=dict)


class JobIn(BaseModel):
    job_type: str
    payload: dict = Field(default_factory=dict)
    idempotency_key: str | None = None


class VerifyQuestionIn(BaseModel):
    cross_check: str = Field(
        ..., min_length=8,
        description="可选人工复核/修正依据：写明在哪个文件/页/独立计算中核对过；"
                    "不是题目可用的前置条件，AI 参考答案经自动检查即可入库")
    answer_payload: dict | None = None
    explanation: str | None = None


class ReviseQuestionIn(BaseModel):
    patch: dict = Field(default_factory=dict)


class AnswerBatchIn(BaseModel):
    """Enqueue every question still missing a usable answer (import batch),
    or generate one on demand when it is first selected for a paper."""
    trigger: str = "import_batch"
    limit: int = 0                 # 0 = all
    force: bool = False            # create a new answer version even if cached
    process_now: bool = False      # run synchronously when the model is ready


class AnswerGenerateIn(BaseModel):
    question_id: str | None = None
    generation_id: str | None = None
    trigger: str = "on_demand"


class AnswerDisputeIn(BaseModel):
    reason: str = Field(..., min_length=4,
                        description="答案有疑问的具体理由")
    evidence: str = ""
    suggested_answer: dict = Field(default_factory=dict)


class AnswerDisputeResolveIn(BaseModel):
    resolution: str = "corrected"   # corrected|regenerated|confirmed|rejected
    resolution_note: str = ""
    answer_payload: dict | None = None
    rubric: dict | None = None
    explanation: str | None = None


class RegradeIn(BaseModel):
    question_id: str
    new_answer_payload: dict
    reason: str = "答案更正后重评"
    new_rubric: dict | None = None
