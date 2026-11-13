# Skill: generate_questions

- **版本**：`skills-1.0/generate_questions-1.0`
- **执行函数**：`backend/app/skills/implementations.py::generate_questions`
- **调用方**：LangGraph `generation_graph`；API `POST /api/papers/generate`
- **是否需要模型**：**是**（未配置时不生成任何题，也不使用 mock）

## 用途
出卷蓝图 → 检索依据 → 生成候选 → 结构检查 → 答案核验 → 去重 → 审核队列。

## 输入 schema
```json
{"type":"object","required":["blueprint"],
 "properties":{"blueprint":{"type":"object","properties":{
   "count":{"type":"integer"},"kp_ids":{"type":"array"},
   "question_types":{"type":"array"}}}}}
```

## 输出 schema
`result.candidates[]`（question_id / stem / verification_note / review_status=pending /
active=false）、`result.pipeline[]`（各阶段状态）、`result.rejected[]`。
模型未配置时 `status="model_not_configured"`、`candidates=[]`、`engine="not_run"`。

## 提示词
`skills/prompts.py::QUESTION_GEN_SYSTEM/USER`（`prompt-1.0`）。
约束：只传少量检索片段（不塞整本 212 页 PDF）、严格 JSON、必须引用真实 `chunk_id`、
题干不含答案、公式用 LaTeX、候选答案不得声称已核验。

## 校验规则
- 结构：题型合法、题干长度、选项数量、答案存在、答案在选项内、来源引用真实。
- 答案：数值题用 `_safe_eval` **独立计算**复核；不一致标为待核查。
- 去重：与题库 stem 的字符 Jaccard ≥ 0.78 判为重复。
- 发布：候选 `active=False`、`answer_status="candidate"`、`answer_origin="ai_candidate"`，
  **不自动发布**；待核查成绩不用于提升阶段稳定状态。

## 有效样例（当前环境）
输入：`{"blueprint":{"count":3,"kp_ids":["kp.linear.gradient"]}}`
期望（模型未配置）：`status="model_not_configured"`、`candidates=[]`、
`pipeline[1].stage="retrieve_evidence"` 有真实 chunk_id、
`message` 明确"未使用模拟结果冒充真实调用"。

## 边界
- 模型自检只提供线索，不能独立认证答案正确。
- 只换数字或选项顺序仍属于相关题族，不能冒充首次独立测试。
