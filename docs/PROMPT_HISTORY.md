# 真实提示词与历史边界

## 本轮实际用户指令

2026-11-16，Codex桌面应用中的实际请求开头如下：

> 请以“完成老师要求，并验证真实使用闭环”为 Goal，对我提供的机器学习小测平台持续检查、修复和验证，最后交付完整项目压缩包。

用户进一步要求保留框架和布局，独立复查旧记录，在浏览器跑通单人单卷、单人多卷和多人单卷，使用本机模型配置、隔离数据、迁移前备份，保留真实失败与缺失历史。本段是要求摘要，不冒充完整逐字开发对话导出。

## 可核验的实际运行

教师浏览器“题库与组卷 → 导出运行提示词”实际下载文件：verification/runtime-prompts-browser-export.json。

其中61条真实请求与响应含call_id、run_id、skill、messages、response、created_at、status与request_sha256，响应模型为deepseek-flash。它们与SQLite的prompt_records和model_calls一一对应。自动化测试的TEST_DOUBLE_NOT_LIVE没有混入此数据库。

记录覆盖诊断评语、generate_questions、automatic_retest_verification、solve_answer等。确定性诊断、推荐等skill无需强制调用模型，其真实输入输出见current-persistence-and-api.json的skill_runs，不能包装成AI对话。

模板位于backend/app/skills/prompts.py及服务实际调用处；模板不是过去执行证据。部分AI评语未通过内容校验，页面使用真实答卷的确定性分析，原响应仍保留。

## 脱敏与不可补造内容

模型适配器在实际调用处记录请求和响应。已知API密钥、authorization和password字段脱敏，图片载荷用摘要代替。交付排除.env、本机凭据、密码哈希和会话，并进行密钥扫描；独立验收快照保留业务记录。

原包PROMPT_HISTORY.md另存verification/previous-prompt-history.md，其中自述来自原包，本轮不将其当作核验过的平台使用证据。

未获得豆包Work或WorkBuddy中与项目对应的可靠完整开发对话。机器上存在WorkBuddy的Python目录不能证明本项目由它开发。早期缺失提示词、指定平台记录、成员信息仍需本人提供真实材料；没有原件就如实说明，不生成替代历史。

## AI出题复测专项补充

用户本轮明确要求重点检查AI出题复测，并确认“题库优先、缺题才让 AI 补，这个可以哈，请你不要改了”。因此保持原有组卷策略及页面，修复完整候选读取、独立验答和复测判定。专项真实调用新增19条，累计80条导出到verification/ai-retest-runtime-prompts.json。该文件由与导出接口相同的export_history函数生成；不声称专项80条再次通过浏览器下载。首轮61条浏览器下载文件继续保留。

## 2026-11-18工作台与学习反馈补充

最新真实导出是 `verification/workbench/runtime-prompts-final.json`，累计116条，相对上一诊断资源阶段85条新增31条。闭环开始前备份已有86条，因此 `snapshot-and-preservation-final.json` 中列出备份后30个新增call_id。导出使用运行数据库的export_history和redact；本次116条未声称重新通过浏览器下载。首轮61条浏览器下载原件仍保留。

本轮实际用户要求完整统一固定工作台布局并改进学情诊断，原文保存于WORKBENCH_REQUIREMENTS.md；要求保留技术栈和真实功能，不用模拟数据冒充真实操作。GitHub备份请求实际完成。上述是当前指令与执行证据，不是过去指定开发平台的历史。

普通组卷保留题库优先、缺题AI补；诊断专项复测现有入口使用AI生成新题并独立验答。本轮改正过一处把专项复测说明误写为题库优先的文案，没有改变原策略。
