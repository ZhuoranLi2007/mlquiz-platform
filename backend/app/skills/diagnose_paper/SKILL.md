# Skill: diagnose_paper

- **版本**：`skills-1.0/diagnose_paper-1.0`
- **执行函数**：`backend/app/skills/implementations.py::diagnose_paper`
- **调用方**：LangGraph `diagnosis_graph` 的 `paper_report` 节点
- **是否需要模型**：否（模型仅生成文字摘要）

## 用途
试卷层诊断：覆盖的知识点、各知识点证据数量、薄弱点、复习优先级。

## 输入 schema
```json
{"type":"object","required":["attempt_id"],
 "properties":{"attempt_id":{"type":"string"},"use_model":{"type":"boolean"}}}
```

## 输出 schema
`result` 含 `covered_knowledge_points[]`（kp_id / items / auto_scored_items /
correct_items / manual_pending / lost_points / independent_families / evidence_ids /
accuracy_if_scored）、`weak_points[]`、`review_priority[]`、`score`、`max_score`。
统一信封字段见 `diagnose_question/SKILL.md`。

## 提示词
`skills/prompts.py::PAPER_SUMMARY_SYSTEM/USER`（`prompt-1.0`）：只依据统计事实写摘要，
不新增事实、不推断能力变化。

## 有效样例
输入：`{"attempt_id":"<phase1 verification attempt>"}`
期望：`covered_knowledge_points` 覆盖该卷全部 `AnswerScore` 的知识点；
待评分题计入 `manual_pending` 而不计入 `accuracy_if_scored`；
`uncertainties` 明确"未测不等于不会"与"总分差不能解释能力变化"。

## 边界
- 不把未出现在卷中的知识点判为不会。
- 不把不同卷的总分差直接解释为能力变化（由 diagnose_history 处理）。
