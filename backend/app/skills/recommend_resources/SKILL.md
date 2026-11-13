# Skill: recommend_resources

- **版本**：`skills-1.0/recommend_resources-1.0`
- **执行函数**：`backend/app/skills/implementations.py::recommend_resources`
- **调用方**：LangGraph `diagnosis_graph` 的 `recommend` 节点；资料页检索
- **是否需要模型**：否（真实关键词检索）

## 用途
按目标知识点推荐可打开的学习内容：本地资料片段 + 原页图 API + 外部资源入口。

## 输入 schema
```json
{"type":"object","properties":{"kp_ids":{"type":"array","items":{"type":"string"}},
 "attempt_id":{"type":"string"}}}
```
`kp_ids` 为空时：优先取 `attempt_id` 覆盖的知识点，其次取状态为需加强/证据不足的知识点。

## 输出 schema
`result.recommendations[]`：`type=local_chunk|external`，
含 `title`、`snippet`、`source_refs`、`url`、`reason`、`review_status`、`quality_note`；
另含 `search_summary[]` 与 `retrieval_mode`。

## 检索说明
当前为**真实关键词检索**（`services/retrieval.py`，中英文术语 + NFKC 兼容汉字归一）。
向量/语义索引未构建时不宣称可用。标记为 `needs_formula_review` 的片段会提示查看原页图。

## 有效样例
输入：`{"kp_ids":["kp.regularization.l1l2"]}`
期望：返回命中片段的 `chunk_id` 与 `document_id/page`；外部资源带 `verified=false` 与
"未读取正文/字幕"说明。

## 边界
- 不编造视频内容、时间点或教师要求。
- 测验提交前不提供可揭示当前题答案的资料入口（由 question_dto 白名单保证）。
