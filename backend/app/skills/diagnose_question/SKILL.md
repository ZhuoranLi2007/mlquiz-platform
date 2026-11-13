# Skill: diagnose_question

- **版本**：`skills-1.0/diagnose_question-1.0`
- **执行函数**：`backend/app/skills/implementations.py::diagnose_question`
- **注册表**：`backend/app/skills/registry.py::REGISTRY["diagnose_question"]`
- **调用方**：LangGraph `diagnosis_graph` 的 `question_nodes`；API `POST /api/diagnoses/run`
- **是否需要模型**：否（模型只提供额外解释，失败不影响结论）

## 用途
题目层诊断：观察事实 → 可能原因（候选）→ 支持证据 → 下一步验证。

## 输入 schema
```json
{"type":"object","required":["attempt_id","question_id"],
 "properties":{"attempt_id":{"type":"string"},"question_id":{"type":"string"},
               "use_model":{"type":"boolean","default":true}}}
```

## 输出 schema（统一信封由 skill_runtime.envelope 生成）
```json
{"result":{"observed":{},"candidate_causes":[],"evidence":[],
           "next_verification":[],"model_explanation":null},
 "evidence_refs":[],"uncertainties":[],"next_actions":[],
 "skill_version":"...","run_id":"...","status":"succeeded",
 "engine":"deterministic|deterministic+model"}
```

## 提示词
`skills/prompts.py::DIAGNOSE_QUESTION_SYSTEM/USER`，版本 `prompt-1.0`。
约束：单题错误只能给候选误区；不推断心理原因；不使用非独立证据；证据不足不给概率。

## 有效样例
输入：`{"attempt_id":"<已交卷>","question_id":"seed_human_tf_logistic"}`
期望：`result.observed.correct` 为布尔，`hints_used`/`seen_before`/`independent_evidence` 存在；
`candidate_causes` 为候选措辞；`evidence_refs` 含 `score_id` 与题目 `source_refs`；
模型未配置时 `engine="deterministic"` 且 `uncertainties` 明确说明未调用模型。

## 边界
- 不把"看过答案/使用提示/同题族重复"计为独立新题证据。
- 文本题或答案为未知的题不产生"错误"结论。
- 结果写入 `diagnoses(level=question, target_id=attempt_id:question_id)`，可重复计算。
