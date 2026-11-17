# 老师要求逐项验收

日期：2026-11-16。以本轮真实源码、浏览器、模型、接口及数据库为依据。原包结论保存在 verification/previous-requirements-audit.md，仅供历史参考。

核心软件闭环已通过：教师组卷发布、两名学生作答、评分保存、三种范围诊断、推荐、真实AI复测、前后对比和诊断更新。保留原Vue、FastAPI、SQLite和LangGraph及页面布局。仍缺真实成员信息、指定豆包Work或WorkBuddy项目使用历史和早期提示词原件；没有补造。完整启动方法与学习说明见 FINAL_DELIVERY.md。

AI出题复测专项复查已完成，详见 [专项记录](AI_RETEST_REVIEW.md)。上一专项后端167项通过，真实调用累计80条；常规组卷策略保持题库优先、缺题AI补。首轮截图和日志继续保留，专项新增证据以ai-retest前缀命名。

2026-11-17新增学情诊断与资源专项复查：最终后端182项、前端30项通过，累计85条真实提示词。三范围真实模型及学生资源阅读重新验证，35个课程知识点对应80张唯一原课件页。实现与证据见[诊断资源专项记录](DIAGNOSIS_RESOURCE_REVIEW.md)。

## 要求对照

实现路径相对项目根目录，backend/app/为后端目录。截图位于docs/verification/browser-current，其他证据位于docs/verification。

| 要求 | 实现位置 | 验证方式 | 实际结果及证据 |
|---|---|---|---|
| 基础题组卷、发布、作答、评分 | frontend/src/views/PaperBuilderView.vue；services/paper_builder_service.py、release_service.py、attempt_service.py | 浏览器两题组卷发布，两学生分别交卷，API和DB回读 | 通过：STU001为0/100，STU002为50/100；截图01—03，current-persistence-and-api.json |
| AI题可用于同样流程 | graphs/classroom_workflow.py、skills/implementations.py、services/automatic_retest.py | 真模型生成候选，独立验答后发布3份5题复测卷，完成4份复测答卷 | 通过：截图05—14；runtime-prompts-browser-export.json |
| 题目层诊断 | services/classroom_diagnosis.py::question_analysis | 班级页面题目错误分布、作答依据与API回读 | 通过：False有效作答不再当作缺失；截图07，JSON中question_analysis |
| 单人单卷诊断 | classroom_diagnosis.py::student_attempt | STU001原卷诊断、推荐、个人复测 | 通过：0%到第1轮100%，反馈更新；截图05、06 |
| 单人多卷诊断 | classroom_diagnosis.py::student_history；MultiAttemptWorkbench.vue | STU002日期范围两次考试诊断，再复测并刷新 | 通过：2次平均25%更新为3次平均50%；截图11—14 |
| 多人同卷诊断 | classroom_diagnosis.py::class_assessment；ClassAssessmentWorkbench.vue | 两人同卷不同答案，查看平均、最高、最低和共性错误 | 通过：基线平均25、最高50、最低0；截图04、07 |
| 实际答卷推荐、新题检测、复测更新 | recommend_resources、generate_questions、verify_learning；graphs/classroom_workflow.py | 三范围实际执行完整图，回读SkillRun、周期、比较及诊断 | 通过：每个复测答卷有成功verify_learning；current-persistence-and-api.json |
| 班级复测多人独立 | models/entities.py::TargetedRetestCycle、models/base.py、services/targeted_retest.py | 同一发布两人分别全对、全错，对照原卷、轮次、答卷ID | 通过：STU001第2轮10/10，STU002第1轮0/10；截图08—10 |
| B站、帖子、抖音资源与相关入口 | services/resource_updates.py；skills/implementations.py::recommend_resources；OnlineResourcesView.vue | 真实打开B站播放器、CSDN正文、抖音具体视频；按知识点运行推荐 | 本轮抽验通过；新增抖音L1/L2视频入库并被推荐；截图15—18，current-resource-recommendation.json |
| 实际LangGraph或Pi | graphs/workflows.py、graphs/classroom_workflow.py | 真实worker执行StateGraph，核对运行日志 | 通过：teacher_intervention_graph和post_submission_graph实际参与；无需另加Pi |
| 诊断、推荐、生成、检测做成skill | skills/registry.py、skills/*/SKILL.md、services/skill_runtime.py | 注册调用及实际输入、输出、状态入库 | 通过：current-persistence-and-api.json中skill_runs；非模型skill不冒充AI调用 |
| 报告、PPT与最终实现一致 | docs/submission/course_report.docx、course_report.pdf、course_demo.pptx | 更新正文、表格、讲解备注，渲染检查 | 6页报告（含两次专项补充）、8页可编辑PPT已交付，包含本轮真实分数及边界 |
| 真实提示词保存和导出 | services/model_provider.py、prompt_history.py；teacher/prompt-history/export | 教师页面点击导出，核对PromptRecord和ModelCall | 首轮61条由浏览器下载；AI出题专项后80条、诊断资源专项后85条已导出，返回模型deepseek-flash；runtime-prompts-browser-export.json；旧缺失历史仍缺失 |

## 重点修复与复验

| 问题 | 修复与验证 |
|---|---|
| 新草稿已选学生仍不能发布 | 将空字符串条件转为显式Boolean；同份草稿在浏览器重新发布成功，截图01 |
| 判断题False误作作答缺失 | 布尔答案单独归一化，选择题漏选逻辑限于选择题；截图04与07前后对照，test_classroom_diagnosis.py |
| 满分复测可能丢掉未覆盖弱点 | 增加untested_kp_ids持久字段，待评分和满分后均保留remaining；test_targeted_retest.py |
| 同一发布多人约束及旧库迁移 | 发布ID+学生ID组合唯一，复测答卷非空时唯一；旧索引迁移保留数据，并检查新增字段 |
| 并发点击重复入队 | SQLite BEGIN IMMEDIATE使查重与创建按顺序执行；两个并发Session只创建一个任务 |
| 旧复测路径部分评分也可能全对 | 检查全部PaperItem均有有效评分且所属用户正确，缺评分不能通过 |
| 迁移前缺自动备份 | SQLite在线备份和integrity_check后迁移；显式关闭Windows文件句柄，原有答卷不变 |
| 缺模型任务仍显示成功 | failed + pending_model_configuration，不自动反复重试；自动化负例通过 |
| 慢任务和中断无法区分 | 增加心跳；失联发布任务只恢复已提交发布标记，未确认提交的不盲目重放；实际worker负例失败后继续正常任务 |
| 模型异常可能回显密钥 | 存储与抛出前脱敏；含测试secret的异常回归通过 |
| 候选题数量易误认为发布题数 | 页面明确“候选题，筛选并核验后发布”；每轮固定5道 |
| 抖音只有被过滤的搜索入口 | 增量添加真实具体视频及核验范围，原资源保留；截图18 |
| 停止脚本可能误关其他项目 | 限定本checkout记录PID且命令行匹配；新虚拟环境一键启动通过 |

## 测试和持久化证据

- 后端167 passed：ai-retest-backend-tests.txt；首轮156 passed记录为current-backend-final-clean-env.txt，Python 3.13新虚拟环境按锁文件安装后重跑。
- 前端29 passed：current-frontend-tests-final.txt；类型检查和构建通过，见current-frontend-typecheck-final.txt、current-startup.txt。
- 真实浏览器：截图01—14，三范围闭环、不同学生结果与前后对比。
- 页面、API和DB：current-persistence-and-api.json、current-persistence-check.txt；6份新答卷、4个周期分数一致。
- 无答案题真实AI生成并独立验答：current-real-answerless.json。
- 实际worker失败恢复：current-live-worker-recovery.json；不存在答卷的受控负例失败3次，后续正常任务成功。失败记录保留，不冒充正常成功。
- 原122份答卷、717条作答、719条评分逐行未变；数据库integrity_check=ok。
- 61条真实运行提示词由浏览器下载，自动化模型替身不混入该数据库。
- 原有失败日志保留用于追溯。例如current-backend-tests.txt曾记录已修复的Windows备份句柄失败，当前结论以final-clean-env日志为准。
- 压缩包另解压到全新目录，无.env，实际启动前端静态资源、API和worker；292个素材恢复、演示登录、验收快照恢复及6份答卷再次回读全部通过，见current-package-smoke.json。该检查复用已按锁文件全新安装并测试过的Python 3.13环境，不冒充第二次独立安装。
- 停止隔离实测：current-stop-isolation.txt；本项目关闭后原8000端口仍返回200，再启动成功见current-restart.txt。

## 边界与未完成材料

本轮功能验收范围内无已知阻断缺陷。外链抽验不等于所有链接永久可达，视频标题与章节核对不等于逐帧审核；旧搜索入口不算已核验内容。部分历史题缺章节映射时明确显示“未映射章节”，仍按有效知识点ID诊断，不猜章节。一次复测达标不证明长期掌握。

部分AI评语未通过内容校验，页面明确回退为依据真实答卷的分析；不以HTTP成功冒充内容通过。班级复测发布至16人，本轮实际要求并完成两名有原卷基线学生的答题，其余学生未被代答。

尚缺真实成员信息、指定平台项目使用证据与早期提示词原件。它们不能由软件修改或新建日志补造。详见PROMPT_HISTORY.md。
