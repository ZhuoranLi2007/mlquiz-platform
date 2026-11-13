"""Static skill registry. Loaded by the application at import time and called
by LangGraph nodes (see graphs/workflows.py) and the API layer."""
from __future__ import annotations

from backend.app import config as cfg
from backend.app.services import answer_pipeline
from backend.app.services.skill_runtime import SkillSpec
from backend.app.skills import implementations as impl
from backend.app.skills import prompts

VERSION = cfg.SKILL_REGISTRY_VERSION

OUT_SCHEMA = {
    "type": "object",
    "required": ["result", "evidence_refs", "uncertainties",
                 "next_actions", "skill_version", "run_id"],
    "properties": {
        "result": {"type": "object"},
        "evidence_refs": {"type": "array"},
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
        "skill_version": {"type": "string"},
        "run_id": {"type": "string"},
        "status": {"type": "string"},
        "engine": {"type": "string"},
    },
}


def _in(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


REGISTRY: dict[str, SkillSpec] = {
    "diagnose_question": SkillSpec(
        name="diagnose_question", version=f"{VERSION}/diagnose_question-1.0",
        description="题目层诊断：观察事实、候选误区、支持证据、下一步验证",
        requires_model=False, prompt_version=prompts.PROMPT_VERSION,
        input_schema=_in({"attempt_id": {"type": "string"},
                          "question_id": {"type": "string"},
                          "use_model": {"type": "boolean"}},
                         ["attempt_id", "question_id"]),
        output_schema=OUT_SCHEMA, execute=impl.diagnose_question,
        examples=[{"input": {"attempt_id": "<交卷后的 attempt_id>",
                             "question_id": "<question_id>"},
                   "expect": "result.observed 含 correct/hints_used/independent_evidence；"
                             "candidate_causes 明确为候选而非确定结论"}]),
    "diagnose_paper": SkillSpec(
        name="diagnose_paper", version=f"{VERSION}/diagnose_paper-1.0",
        description="试卷层诊断：知识点覆盖、证据数量、薄弱点、复习优先级",
        requires_model=False, prompt_version=prompts.PROMPT_VERSION,
        input_schema=_in({"attempt_id": {"type": "string"}}, ["attempt_id"]),
        output_schema=OUT_SCHEMA, execute=impl.diagnose_paper,
        examples=[{"input": {"attempt_id": "<attempt_id>"},
                   "expect": "result.covered_knowledge_points 每项含 items/evidence_ids"}]),
    "diagnose_history": SkillSpec(
        name="diagnose_history", version=f"{VERSION}/diagnose_history-1.0",
        description="历次试卷层诊断：同知识点独立作答比较与知识状态计算",
        requires_model=False, prompt_version=prompts.PROMPT_VERSION,
        input_schema=_in({"user_id": {"type": "string"}}),
        output_schema=OUT_SCHEMA, execute=impl.diagnose_history,
        examples=[{"input": {"user_id": "local_user"},
                   "expect": "per_knowledge_point 含题量/题族/时间跨度/辅助情况；"
                             "总分差不被解释为能力变化"}]),
    "diagnose_classroom": SkillSpec(
        name="diagnose_classroom",
        version=f"{VERSION}/diagnose_classroom-1.0",
        description=("课堂诊断：按固定试卷版本执行题目、学生单卷、学生历史或"
                     "班级聚合诊断，严格保留证据边界"),
        requires_model=False, prompt_version=prompts.PROMPT_VERSION,
        input_schema=_in({
            "scope_type": {
                "type": "string",
                "enum": ["question_analysis", "student_attempt",
                         "student_history", "class_assessment"],
            },
            "classroom_id": {"type": "string"},
            "student_id": {"type": "string"},
            "assessment_version_id": {"type": "string"},
            "paper_item_id": {"type": "string"},
            "attempt_id": {"type": "string"},
        }, ["scope_type"]),
        output_schema=OUT_SCHEMA, execute=impl.diagnose_classroom,
        examples=[{
            "input": {"scope_type": "question_analysis",
                      "assessment_version_id": "<固定版本 ID>",
                      "paper_item_id": "<卷内题项 ID>"},
            "expect": ("缺考、pending_key、correct=NULL 不入分母；题干来自"
                       "PaperItem.snapshot；cause_analysis 不编造心理错因"),
        }]),
    "recommend_resources": SkillSpec(
        name="recommend_resources", version=f"{VERSION}/recommend_resources-1.0",
        description="按知识点检索本地资料片段与外部资源入口，给出可打开出处",
        requires_model=False, prompt_version=prompts.PROMPT_VERSION,
        input_schema=_in({"kp_ids": {"type": "array"},
                          "attempt_id": {"type": "string"}}),
        output_schema=OUT_SCHEMA, execute=impl.recommend_resources,
        examples=[{"input": {"kp_ids": ["kp.regularization.l1l2"]},
                   "expect": "recommendations 含 source_refs 与原页 API 链接"}]),
    "generate_questions": SkillSpec(
        name="generate_questions", version=f"{VERSION}/generate_questions-1.0",
        description="出卷蓝图→检索依据→生成候选→结构检查→答案核验→去重→审核队列",
        requires_model=True, prompt_version=prompts.PROMPT_VERSION,
        input_schema=_in({"blueprint": {"type": "object"}}, ["blueprint"]),
        output_schema=OUT_SCHEMA, execute=impl.generate_questions,
        examples=[{"input": {"blueprint": {"count": 3, "kp_ids": ["kp.linear.gradient"]}},
                   "expect": "模型未配置时 status=model_not_configured 且 candidates=[]；"
                             "配置后候选 active=False、answer_status=candidate"}]),
    "verify_learning": SkillSpec(
        name="verify_learning", version=f"{VERSION}/verify_learning-1.0",
        description="复测验证：独立新题证据判定、错题状态更新、延迟复测排期",
        requires_model=False, prompt_version=prompts.PROMPT_VERSION,
        input_schema=_in({"retest_task_id": {"type": "string"},
                          "attempt_id": {"type": "string"}}),
        output_schema=OUT_SCHEMA, execute=impl.verify_learning,
        examples=[{"input": {"retest_task_id": "<task_id>"},
                   "expect": "passed 需同时满足 independent/all_correct/new_question_family"}]),
    "build_study_plan": SkillSpec(
        name="build_study_plan", version=f"{VERSION}/build_study_plan-1.0",
        description="按考试日期、每日预算、错误与到期复测生成任务",
        requires_model=False, prompt_version=prompts.PROMPT_VERSION,
        input_schema=_in({"exam_date": {"type": "string"},
                          "daily_minutes": {"type": "integer"}}),
        output_schema=OUT_SCHEMA, execute=impl.build_study_plan,
        examples=[{"input": {"exam_date": cfg.EXAM_DATE, "daily_minutes": 60},
                   "expect": "total_minutes <= daily_minutes，任务绑定真实材料/题目"}]),
    "tutor_answer": SkillSpec(
        name="tutor_answer", version=f"{VERSION}/tutor_answer-1.0",
        description="课程助手：少量检索、逐步提示、给出出处；测验模式明确禁用",
        requires_model=False, prompt_version=prompts.PROMPT_VERSION,
        input_schema=_in({"message": {"type": "string"},
                          "mode": {"type": "string"},
                          "session_id": {"type": "string"}}, ["message"]),
        output_schema=OUT_SCHEMA, execute=impl.tutor_answer,
        examples=[{"input": {"message": "Logistic 代价函数是什么", "mode": "practice"},
                   "expect": "answer 附 source_refs；模型未配置时明确说明是本地检索结果"}]),
    "solve_answer": SkillSpec(
        name="solve_answer", version=f"{VERSION}/solve_answer-1.0",
        description=("无答案原题 → DeepSeek 生成参考答案/解析/评分要点 → "
                     "结构检查 + 可执行自动检查 → 自动进入可用题库（无需逐题人工确认）；"
                     "请求不含学生回答，按题目版本缓存复用"),
        requires_model=True, prompt_version=prompts.ANSWER_PROMPT_VERSION,
        input_schema=_in({"generation_id": {"type": "string"},
                          "question_id": {"type": "string"},
                          "trigger": {"type": "string"}}),
        output_schema=OUT_SCHEMA, execute=answer_pipeline.skill_solve_answer,
        examples=[{"input": {"question_id": "<无答案原题>", "trigger": "import_batch"},
                   "expect": "模型未配置时 status=model_not_configured 且不写题库；"
                             "配置后检查通过即 answer_status=ai_auto_checked、"
                             "answer_label=AI参考答案"}]),
    "learning_diagnosis": SkillSpec(
        name="learning_diagnosis", version=f"{VERSION}/learning_diagnosis-1.0",
        description=("学习闭环编排：试卷诊断 → 三层认知（基础/应用/综合）→ "
                     "资源推荐 → 新题生成 → 复测验证；每一步调用已有 skill，"
                     "失败项如实记录不伪造"),
        requires_model=False, prompt_version=prompts.PROMPT_VERSION,
        input_schema=_in({"attempt_id": {"type": "string"},
                          "generate_questions": {"type": "boolean"},
                          "question_count": {"type": "integer"},
                          "use_model": {"type": "boolean"}}, ["attempt_id"]),
        output_schema=OUT_SCHEMA, execute=impl.learning_diagnosis,
        examples=[{"input": {"attempt_id": "<已交卷的 attempt_id>"},
                   "expect": "返回三层得分率、薄弱知识点与推荐资源；"
                             "generate_questions=false 时不调用模型出题"}]),
}

REQUIRED_SKILLS = ("diagnose_question", "diagnose_paper", "diagnose_history",
                   "diagnose_classroom", "recommend_resources",
                   "generate_questions", "verify_learning")


def assert_registry() -> None:
    missing = [s for s in REQUIRED_SKILLS if s not in REGISTRY]
    if missing:
        raise RuntimeError(f"缺少必须 skill: {missing}")
    for name, spec in REGISTRY.items():
        if not callable(spec.execute):
            raise RuntimeError(f"skill {name} 没有可执行函数")
