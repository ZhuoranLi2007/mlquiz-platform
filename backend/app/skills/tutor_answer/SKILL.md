# Skill: tutor_answer

- **版本**：`skills-1.0/tutor_answer-1.0`
- **执行函数**：`backend/app/skills/implementations.py::tutor_answer`
- **调用方**：全局助手侧栏 `POST /api/assistant/chat`；LangGraph `tutor_graph`（可选）
- **是否需要模型**：否（配置后用于生成解释，未配置时给出真实检索结果）

## 用途
理解当前题目/资料/报告与模式；检索少量相关内容，按需逐步提示，并给出出处。

## 输入 schema
```json
{"type":"object","required":["message"],
 "properties":{"message":{"type":"string"},"mode":{"enum":["practice","quiz"]},
 "session_id":{"type":"string"},"context":{"type":"object"}}}
```

## 输出 schema
`result.answer`、`result.source_refs[]`、`result.retrieval_mode`、
`result.model_used`、`engine`。

## 提示词
`skills/prompts.py::TUTOR_SYSTEM/USER`（`prompt-1.0`）：
每个结论标注 `chunk_id`；无依据时写「课程资料依据不足」；不编造公式/页码/视频内容；
学生说「我懂了」不算掌握证据。

## 模式规则
- `quiz`：**明确禁用助手**（简单可靠的答案隔离实现），返回 `engine="blocked_by_quiz_mode"`。
- `practice` 且模型未配置：返回本地关键词检索命中的原文片段与出处，
  `engine="deterministic_retrieval"`，**不生成模拟回答**。
- `practice` 且模型可用：调用真实模型并附出处；失败则降级并说明。

## 有效样例
输入：`{"message":"Logistic 代价函数是什么","mode":"practice"}`
期望（模型未配置）：answer 含"模型 API 未配置"与真实片段 `chunk_id`；
`source_refs` 可回到 `document_id/page`。

## 边界
- 聊天消息写入 `chat_messages`；不直接改写 `knowledge_states`。
- 不把对话中的自我声明当作学习效果证据。
