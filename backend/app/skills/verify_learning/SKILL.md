# Skill: verify_learning

- **版本**：`skills-1.0/verify_learning-1.0`
- **执行函数**：`backend/app/skills/implementations.py::verify_learning`
- **调用方**：LangGraph `retest_graph` 的 `verify` 节点；复测交卷后
- **是否需要模型**：否

## 用途
复测检测：判定本次是否为独立新题证据、更新错题状态、安排延迟复测、刷新知识状态。

## 输入 schema
```json
{"type":"object","properties":{"retest_task_id":{"type":"string"},
 "attempt_id":{"type":"string"}}}
```

## 输出 schema
`result`：`scored_items`、`independent`、`all_correct`、`new_question_family`、
`passed`、`state_after`。`passed` 必须三项全为真（独立 + 全对 + 不同题族）。

## 规则
- 做对一次只更新状态，**不删除**旧错题记录。
- 通过后把 `WrongBookEntry.status` 改为 `verified`，`RetestTask.status` 改为 `completed`。
- 按项目规则 1 / 3 / 7 天安排下一次 `kind=delayed` 复测（可修改，不声称最优）。
- 刷新 `knowledge_states`（规则见 diagnose_history）。

## 有效样例
输入：`{"retest_task_id":"<task_id>","attempt_id":"<复测 attempt_id>"}`
期望：若作答使用提示或与原题族相同，`passed=false` 且 `uncertainties` 说明原因；
通过后产生一条新的延迟复测任务与 `learning_verified` 事件。

## 边界
- 原题订正、新题迁移验证、延迟复测三种任务用 `kind` 区分。
- 未作答或全为待评分题时不得判定通过。
