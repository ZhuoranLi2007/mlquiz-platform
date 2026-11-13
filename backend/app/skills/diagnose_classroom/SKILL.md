# Skill: diagnose_classroom

- **版本**：`skills-1.0/diagnose_classroom-1.0`
- **执行函数**：`backend/app/skills/implementations.py::diagnose_classroom`
- **调用方**：课堂教师干预与学生交卷后的 LangGraph 工作流
- **是否需要模型**：否

## 用途

用统一 Skill 信封执行四种课堂诊断：`question_analysis`、
`student_attempt`、`student_history`、`class_assessment`。调用会保存可审计的
`SkillRun`；课堂诊断结果仍由唯一的确定性服务计算并保存。

## 输入

```json
{
  "scope_type": "question_analysis | student_attempt | student_history | class_assessment",
  "classroom_id": "按 scope 需要",
  "student_id": "按 scope 需要",
  "assessment_version_id": "按 scope 需要的固定试卷版本 ID",
  "paper_item_id": "question_analysis 必需的卷内题项 ID"
}
```

执行者身份取自 Skill 运行上下文，不接受 payload 冒充身份。

## 严格证据规则

- 缺考、无效作答、`pending_key`、待评分及 `correct=NULL` 不进入分母；真实
  0 分必须保留。
- 题干、题型、分值和知识点只读取固定版本的 `PaperItem.snapshot`，不得用题库
  最新版本替换历史试卷内容。
- 结构化得分只能证明作答表现；没有学生解释、人工阅卷证据时，错因必须标为
  `not_inferred`，不可编造心理或知识性原因。
- 班级结果只返回匿名代码与评分事实，不泄露其他学生答案、身份或原始导入行。
- 不同满分、难度或题型的试卷不得直接比较原始总分并解释为能力变化。

## 输出

统一信封包含 `result`、`evidence_refs`、`uncertainties`、`next_actions`、
`skill_version`、`run_id`、`status`。`extra.diagnosis_id` 指向持久化课堂诊断。
