# Skill: solve_answer

- **版本**：`skills-1.0/solve_answer-1.0`
- **执行函数**：`backend/app/services/answer_pipeline.py::skill_solve_answer`
  （核心逻辑在同文件 `build_request` / `structure_check` / `auto_check` / `publish_answer` / `ensure_answer_ready`）
- **调用方**：后台任务 `answer_generation`（worker）、`POST /api/answers/generate-batch`、
  `POST /api/answers/generate`、正式组卷前的 `bank_service.assemble_paper()` → `ensure_answer_ready()`
- **是否需要模型**：**是**（未配置时如实 `pending_model_configuration`，不生成答案、不写题库、无 mock）

## 用途

无答案原题 → DeepSeek 生成参考答案 / 解析 / 评分要点 →
**结构检查 + 可执行的自动检查** → 自动进入可用题库（**无需逐题人工确认**）→
保存来源、模型、生成时间与答案版本 → 界面标注「AI参考答案」。

## 输入 schema
```json
{"type":"object","properties":{
  "generation_id":{"type":"string"},
  "question_id":{"type":"string"},
  "trigger":{"type":"string","enum":["import_batch","on_demand","pre_exam","dispute_regen"]}}}
```

**请求白名单**（`build_request`）：只允许 `question_type / score / stem / options / context`。
显式禁止 `student_response / response / attempt_id / answer_score / awarded_score / given / student_answer`。
保存 `request_fingerprint`（SHA256）与 `student_answer_included=False` 供审计。

## 输出 schema
统一信封：`result / evidence_refs / uncertainties / next_actions / skill_version / run_id / status / engine`。
`result.status` 取值：
- `succeeded`：检查通过，题目已 `answer_status=ai_auto_checked`、
  `answer_origin=deepseek_generated`、`review_status=auto_checked`、`active=True`；
- `model_not_configured`：未配置，队列条目标记 `pending_model_configuration`；
- `needs_attention`：具体异常（`missing_figure_or_formula` / `insufficient_conditions` /
  `contradictory_result`）；
- `retrying` / `failed`：结构检查失败或模型调用异常，按有限重试处理；
- `cached`：同一 `(question_id, question_version, answer_version)` 已有成功记录，直接复用。

## 提示词
`skills/prompts.py::ANSWER_SOLVE_SYSTEM/USER`（`answer-solve-1.0`）。
主观题评阅另用 `RUBRIC_SCORING_SYSTEM/USER`（`rubric-score-1.0`），实现在
`services/subjective_scoring.py`。

约束：输入不含任何学生作答；`answer_payload` 按题型输出；数值题 `calculation`
必须能被 Python 独立求值；`rubric.points` 总分等于题目分值；`accept` 必须非空以接受
等价表达与其他正确解法；`source` 固定 `ai_generated`；只有缺图/公式、条件不足、
结果矛盾允许 `insufficient=true`。

## 校验规则
- **结构检查**：JSON 对象形状、`answer_payload` 存在且题型匹配、`rubric.points` 列表、
  `explanation` 非空、选项 key 合法、`accept` 等价表达声明。
- **可执行自动检查**：AST 白名单沙箱 `safe_eval()` 独立复算数值题（拒绝注入），
  核对 `rubric.points` 总分 == 题目分值、单选答案与解析一致、具体异常分类。
- **发布**：不设人工门禁；检查通过即自动入库。人工修正走**可选**通道
  `POST /api/admin/questions/{id}/verify` 或「答案有疑问」复核，并产生新题目/答案版本。
- **缓存**：`AnswerGeneration(question_id, question_version, answer_version)` 唯一键；
  成功记录即同题版本缓存，正式组卷只复用不重复调用模型。
- **固定答案**：正式测验在任何 `Attempt` 存在之前，把答案、`rubric`、`answer_meta`、
  `answer_fixed_at` 写入 `PaperItem.snapshot`。

## 有效样例（当前环境）
输入：`{"question_id":"<无答案原题>","trigger":"import_batch"}`
期望（DeepSeek 未配置）：`status="model_not_configured"`、
`uncertainties=["模型未配置，答案未生成"]`、题库不变、`model_calls` 增量为 0。
配置后检查通过：`answer_status="ai_auto_checked"`、`answer_label="AI参考答案"`。

## 边界
- 「AI参考答案」**不是**教师或原试卷提供的标准答案；界面与 `answer_meta` 必须标注免责声明。
- 结构/自动检查只能证明输出**自洽可复算**，不能证明学术上绝对正确；
  有异议时走「答案有疑问」复核、版本修正、相关作答重评与诊断更新。
- 答案生成请求**永远不含学生回答**；学生端 `student_view()` 交卷前不返回答案内容，
  只返回来源标签与安全元数据。
- 模型未配置时，主观题评分如实 `needs_model_scoring`，不用关键词猜测冒充给分。
