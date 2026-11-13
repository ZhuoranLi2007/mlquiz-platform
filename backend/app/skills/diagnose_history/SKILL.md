# Skill: diagnose_history

- **版本**：`skills-1.0/diagnose_history-1.0`
- **执行函数**：`backend/app/skills/implementations.py::diagnose_history`
- **调用方**：LangGraph `diagnosis_graph` 的 `history_report` 节点
- **是否需要模型**：否（状态由代码按规则计算）

## 用途
历次试卷层诊断：比较同一知识点、相近认知要求的**独立作答**，显示题量、题族、
辅助情况和时间跨度；同时刷新 `knowledge_states`。

## 输入 schema
```json
{"type":"object","properties":{"user_id":{"type":"string","default":"local_user"}}}
```

## 输出 schema
`result.per_knowledge_point[]` 含 `total_attempt_items`、`independent_items`、
`independent_families`、`time_span_days`、`assisted_items`、`series[]`；
另含 `attempt_totals[]` 与 `state_summary{}`。

## 状态规则（项目规则，见 `backend/app/config.py`）
`未测 → 证据不足 → 需加强 → 初步通过 → 阶段稳定`
- 有效证据 = 自动评分、独立作答、且题目答案已就绪（`answer_status` 为 `verified`
  或 `ai_auto_checked`；见 `answer_pipeline.is_answer_ready()`）。AI 参考答案经自动
  检查后同样计入，不再要求逐题人工确认。
- 使用提示 / 看过答案 / 已见题 / 同题族重复 / 待核查成绩 **不参与状态提升**。
- 最近一次独立作答错误 → 先回到「需加强」。
- 「初步通过」需 ≥3 条有效证据、≥2 个独立题族且近期连续正确。
- 「阶段稳定」另外需要跨 ≥7 天与延迟复测通过证据。
- 未测不等于不会；证据不足不显示精确掌握概率。

## 有效样例
输入：`{"user_id":"local_user"}`
期望：同知识点多次测验被聚合；`series` 按时间排序；
`uncertainties` 明确总分差不可解释为能力变化。

## 边界
- 不基于极少量或伪造记录训练所谓精准知识追踪模型。
