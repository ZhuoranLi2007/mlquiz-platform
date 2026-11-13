# Skill: build_study_plan

- **版本**：`skills-1.0/build_study_plan-1.0`
- **执行函数**：`backend/app/skills/implementations.py::build_study_plan`
- **调用方**：LangGraph `diagnosis_graph` 的 `update_plan` 节点；API `POST /api/study-plan/generate`
- **是否需要模型**：否

## 用途
按考试日期、每日可用时间、教师重点依据（来源陈述）、近期错误、前置知识和到期复测
排序生成任务，任务总时长不超过每日预算。

## 输入 schema
```json
{"type":"object","properties":{"exam_date":{"type":"string","example":"2026-12-02"},
 "daily_minutes":{"type":"integer","example":60}}}
```

## 输出 schema
`result`：`plan_id`、`total_minutes`、`dropped_by_budget`、`tasks[]`
（task_id / title / minutes / material_ref / completion_criteria）。

## 规则
- 排序：到期复测 > 错题订正 > 需加强知识点 > 证据不足知识点。
- 每个任务绑定真实材料（检索命中的 `rel_path + page`）或真实题目 ID。
- 超出预算的任务不静默塞入，`dropped_by_budget` 明确记录数量。
- 「老师重点」「标准答案」是资料中的来源陈述，未经核查不升级为已确认教师要求。
- 初始提醒只在应用内展示。

## 有效样例
输入：`{"exam_date":"2026-12-02","daily_minutes":60}`
期望：`total_minutes <= 60`；无错误/复测时任务可为空且说明原因；
有错题时 `material_ref` 指向真实资料页或题目 ID。

## 边界
- 分钟数为项目估算，允许用户在界面调整。
- 不基于伪造记录生成"学习效果"结论。
