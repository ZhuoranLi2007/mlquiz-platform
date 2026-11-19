# Skill: verify_learning

- **版本**：`skills-1.0/verify_learning-1.0`
- **执行函数**：`backend/app/skills/implementations.py::verify_learning`
- **调用方**：LangGraph `retest_graph` 与 `classroom_workflow`；复测交卷后
- **是否需要模型**：否

## 用途
复测检测：判定本次是否为独立新题证据、更新错题状态、安排延迟复测、刷新知识状态。

## 输入 schema
```json
{"type":"object","properties":{"retest_task_id":{"type":"string"},
 "attempt_id":{"type":"string"}}}
```

## 输出 schema
教师发布与学生自主发起的复测：先按 `attempt_id` 查找 `TargetedRetestCycle`，返回
`cycle_id`、`round`、`status`、`passed`、`result_determinable`、`accuracy`、
`threshold`、`remaining_kp_ids`。只有全部题目具备有效评分时才判定，默认正确率
达到 0.8 为本轮通过；尚未评分完整时状态为 `scoring_pending`。同班发布按学生
分别关联原卷和复测轮次，不共享其他学生的通过结果。本轮通过不等于知识点达标。

`learning_loop` 使用共享服务 `learning_evidence.py` 回算，分开返回
`task_state`、`round_result`、`concept_results`、`available_actions`。
当前规则版本为 `learning-loop-v3`：正式复测 5 题、最多两个主知识点，按 3+2 分配；
知识点同轮至少两道独立新题、不同题族与考查方式、全部可靠评分且全对，才达标。
达标至少 24 小时后开始另一组新题且再次满足条件，才显示延后确认。
新发布、待评分、未覆盖不撤销已有达标；后来可靠错答更新为需加强，但保留历史通过。
重复题、使用提示或不足量题目只计练习；缺少独立性证据的旧轮次不升级知识点状态。
无历史答卷的基线自测不计算“前后提升”。

以下为原有个人错题复测 `RetestTask` 路径的输出：
`result`：`scored_items`、`independent`、`all_correct`、`new_question_family`、
`passed`、`state_after`。`passed` 必须三项全为真（独立 + 全对 + 不同题族）。

## 规则
- 教师针对性复测按上面的正确率规则处理；下面的全对和延迟任务规则属于个人错题复测路径。
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
