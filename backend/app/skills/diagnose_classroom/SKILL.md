# Skill: diagnose_classroom

- **版本**：`skills-1.0/diagnose_classroom-1.0`
- **执行函数**：`backend/app/skills/implementations.py::diagnose_classroom`
- **调用方**：教师诊断后台任务、课堂教师干预与学生交卷后的 LangGraph 工作流
- **是否需要模型**：基础统计不需要；include_ai=true时使用本地已配置模型生成证据约束的解读

## 用途

用统一 Skill 信封执行四种课堂诊断：`question_analysis`、
`student_attempt`、`student_history`、`class_assessment`。调用会保存可审计的
`SkillRun`；评分与统计仍由确定性服务计算；AI解读不能改变分数。诊断数据版本为classroom-chapter-diagnosis-v6，展示分析版本为3；它与注册器的skill版本分别保存。

## 输入

```json
{
  "scope_type": "question_analysis | student_attempt | student_history | class_assessment",
  "classroom_id": "按 scope 需要",
  "student_id": "按 scope 需要",
  "assessment_version_id": "按 scope 需要的固定试卷版本 ID",
  "paper_item_id": "question_analysis 必需的卷内题项 ID",
  "include_ai": true
}
```

执行者身份取自 Skill 运行上下文，不接受 payload 冒充身份。

## 严格证据规则

- 缺考、无效作答、`pending_key`、待评分及 `correct=NULL` 不进入分母；真实
  0 分必须保留。
- 题干、题型、分值和知识点只读取固定版本的 `PaperItem.snapshot`，不得用题库
  最新版本替换历史试卷内容。
- 结构化得分只能证明作答表现；没有学生解释、人工阅卷证据时，错因必须标为
  `not_inferred`，不可编造心理原因。AI可以解释错答涉及的概念差异，必须关联实际证据并使用有限结论。
- 班级结果只返回匿名代码与评分事实，不泄露其他学生答案、身份或原始导入行。
- 不同满分、难度或题型的试卷不得直接比较原始总分并解释为能力变化。

## 输出

统一信封包含 `result`、`evidence_refs`、`uncertainties`、`next_actions`、
`skill_version`、`run_id`、`status`。`extra.diagnosis_id` 指向持久化课堂诊断。

## 后台运行与质量边界

教师三范围按钮提交teacher_diagnosis任务，worker执行该skill并调用recommend_resources。模型真实messages和原始响应以call_id关联run_id保存；JSON数学转义修复会标记，校验不通过的条目回退为具体复习指引。页面明确区分AI解读与依据规则的复习重点。重复在途请求复用任务，付费诊断中断后不自动盲目重跑。
