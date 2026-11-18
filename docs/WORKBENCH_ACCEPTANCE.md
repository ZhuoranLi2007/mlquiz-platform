# 工作台与学习反馈最终验收（2026-11-18）

本轮需求原文见 [WORKBENCH_REQUIREMENTS.md](WORKBENCH_REQUIREMENTS.md)。保留 Vue、FastAPI、SQLite、LangGraph 和既有业务，在独立数据目录完成修改及真实运行。下文区分自动化、真实浏览器、真实模型和持久化证据；过去阶段的日志没有改写为本轮结果。

## 结果与复查入口

- 后端 **189 项**、前端 **33 项**自动化测试通过；类型检查与生产构建通过。
- 真实 Chromium：学生页面 30 组、教师页面 66 组、答题页面 6 组尺寸检查；另有 11 页 × 125%/150%/200% 的 **33 组真实浏览器缩放**。检查主页面尺寸、标题位置、局部滚动末端及操作可达性，最终记录均无失败。
- 两名学生使用同一份班级复测试卷，分别得到 **10/10、6/10**。页面、接口和 SQLite 的学生、原卷、轮次、评分及 verify_learning 调用相符，当前待复习概念分别为 **0、2**。
- 单人单卷、单人多卷、多人单卷均实际打开验证；单人多卷与班级诊断执行真实 DeepSeek 调用后重新打开页面，保存的 AI 解释能够正确恢复。
- 累计 **116 条真实模型请求和响应**；相对上个交付阶段新增 31 条，其中本轮双学生闭环开始前备份之后新增 30 条。它们包括失败或内容校验未通过的记录，不能全部称为成功模型结果。

当前开发机入口是 http://127.0.0.1:8004/ 。选择演示教师，或学生 STU001、STU002。学生进入“学习反馈”，选择 **工作台验收20261118-双知识点双学生**，可查看原卷及第 1 轮复测。交付包默认使用原始匿名快照；恢复本轮结果的方法见后文。

## 老师要求逐项对照

| 要求 | 实现位置 | 实际验证与结果 |
|---|---|---|
| 基础题和 AI 题均可组卷、发布、作答、评分 | `services/bank_service.py`、`automatic_retest.py`、`targeted_retest.py`；教师组卷和发布页面 | 本轮教师从题库选 LASSO/过拟合题发布，两名学生提交 0/100、50/100；班级 AI 复测生成 11 道候选，独立验答后发布 5 道新题，两人提交 10/10、6/10。见 cycle-record、class-workflow-result、browser-persistence-final |
| 题目层、单卷、多卷诊断；多卷含一人多次和同卷多人 | `classroom_diagnosis.py`、`feedback_workbench.py`；三个教师 Workbench 组件 | 教师三范围真实浏览器通过；单人多卷读取 STU001 的 19 次考试；班级按同一 assessment_version 读取两份有效答卷；题目答案分布与实际评分核对。见 teacher-workbench-final |
| 实际作答→诊断→推荐→针对性复测→更新 | `graphs/classroom_workflow.py`、`skills/implementations.py`、`targeted_retest.py` | 两名学生由各自原卷反馈进入复测并返回；两个独立 cycle 的 verify_learning 均成功，前后对比及当前弱点更新。见下表和持久化记录 |
| B站、帖子、抖音与知识点相关；资源入口可用 | `resource_updates.py`、`learning_catalog.py`、`learning_resources.py`、`LearningMaterialView.vue` | 前一专项实际打开 B站播放器、CSDN 正文、抖音具体视频，原始证据保留。本轮重新验证知识点课程原页、页码跳转、返回和教师 PDF 第 2 页真实渲染；35 个知识点对应 80 张唯一原课件页。外链不是本轮全部重新播放，不能承诺永久可达 |
| 实际 LangGraph/Pi 与可执行 skill | `graphs/`、`skills/registry.py`、`skills/*/SKILL.md`、`services/skill_runtime.py` | 实际 worker 执行 LangGraph 的诊断、推荐、生成、验答和发布；学生提交后执行 verify_learning。snapshot 中保留 188 条 skill_runs；确定性 skill 不冒充模型调用 |
| 报告/PPT/提示词与实现一致 | `docs/submission/`、`verification/workbench/runtime-prompts-final.json` | 8 页 DOCX/PDF、8 页可编辑 PPT（含备注）已更新并逐页渲染检查；116 条实际请求响应由运行记录导出。指定开发平台及过去缺失的开发对话没有补造 |

后端实现路径均相对于 `backend/app/`。原有重点修复及旧库迁移、任务失败恢复、并发重复点击、无答案题真实解答和独立验答证据，见 [REQUIREMENTS_AUDIT.md](REQUIREMENTS_AUDIT.md) 和 [AI_RETEST_REVIEW.md](AI_RETEST_REVIEW.md)；本轮完整回归再次覆盖这些逻辑，不把它们全部描述成本轮重新执行的现场操作。

## 出题来源，保持原策略

普通组卷 `bank_service.py` 先选题库，数量不足才调用 AI 补题。诊断页“专项复测”现有 `automatic_retest.py` 则要求 AI 新题，独立验答通过后发布。这是两条不同入口，本轮没有改动来源策略。

中间阶段曾将专项复测说明误写成“题库优先”，现已改正三个教师组件及最终文档。`cycle-record.json` 保留采集当时的页面文字，不改写历史证据。其 `ai_generated=5, bank_selected=0` 只能证明此次发布的是 5 道 AI 题，不能证明“题库缺题才触发”发生在该专项流程。

## 工作台需求逐项验收

| 需求组 | 实现及检查 | 结论与证据 |
|---|---|---|
| 一.1 固定侧栏/顶部、剩余空间、无整页滚动、局部滚动 | App 根容器 100dvh；WorkbenchPage 的 header/body/footer；Flex/Grid 的 min-height/min-width:0；长区 overflow:auto | 102 组尺寸检查 document=viewport；标题保持原位置，长区滚动到末端且在可见范围内。四指定尺寸全部包含，另测 640×720、910×512 和答题页 683×384 |
| 一.1 小窗与缩放操作可达 | 短窗口限制 header 最大占比；任务页必要时滚动主体，内部列表有可用最小空间 | Chrome tabs.setZoom/getZoom 实际设置 125/150/200%，33 组通过；没有用 CSS 缩放或缩小字号代替。发现的任务记录区被压扁问题已修复 |
| 一.2 标题右侧选择与操作、紧凑卡片 | 反馈标题 flex-wrap；指标约 86px、最大 260px，字体正常 | 1366 截图中选择/刷新/复测记录与标题同行；较窄时换行。分数、错题、弱点、复测来自真实答卷，不补造空指标 |
| 一.3 统一设计语言 | 共用 workbench.css、WorkbenchPage；沿用浅蓝/白/深蓝、原卡片和按钮 | 最终各教师/学生截图人工核查；移除题库卡片过渡导致的暂时重叠，未引入大面积渐变或装饰性重构 |
| 二.1 双视图、试卷切换同步 | StudentFeedbackView 请求序号防止旧响应覆盖；每卷记忆当前概念 | 真实切换到另一份卷，标题、统计、错题同步更新，再回原卷保留概念选择；browser-persistence-final 的 exam_switch |
| 二.2 两列错题、独立滚动、解析、换行、题号定位 | 复用 QuestionCard/MathText；独立 question-scroll；定位题号 | cycle-record 的 feedback_interactions；最终错题数量与 API 去重数量相同；1366 截图与真实操作通过 |
| 二.3 35/65 诊断、真实错因/纠正/资源/复测 | 概念列表与详情独立滚动；事实和“可能的错因”分离；解释引用真实题号，课程索引提供资料和页码 | 两学生逐概念切换及复测错误卡核对；打开课程图片成功，跳页后保持登录；实际复测任务可进入，未复测显示真实状态 |
| 三.1 可靠计算与细化知识点 | feedback_workbench 按 paper_item_id 去重；每概念统计；题干细化精确率/召回率/F1/交叉验证 | 7 项新增后端测试及完整回归；不修改原题库标签；统计与实际答卷交叉验证 |
| 三.2 AI 只解释、结构校验 | diagnosis_coaching coaching-v4；classroom_diagnosis 校验证据 ID、假设和概念纠正 | 真实调用和保存后重载；数值/排名/页码来自程序；内容无效时保留真实失败并回退规则分析 |
| 三.3 可操作建议与取消统一分钟数 | 概念纠正、复习重点、资源入口、自测标准由真实知识点组织；删除无依据 suggested_minutes 展示 | 资源/诊断代码和最终页面复查；保留原课程精读和自测内容 |
| 三.4 范围与复测更新 | 学生+原答卷+知识点+轮次关联；区分原卷弱点和当前弱点；缺评分/未覆盖不通过 | STU001 当前弱点 0；STU002 原卷 1 个、当前 2 个；两周期不串用；测试覆盖待评分和未覆盖状态 |
| 四.1 我的任务 | 待完成、最近提交、考试记录分区滚动，标题/卡片固定 | 6 个尺寸及真实缩放通过；小窗允许必要主体滚动，内容可到达 |
| 四.2 答题 | 题干内滚动、题号/交卷可达、最后一题、确认弹窗 | STU003 隔离草稿检查 6 尺寸，最后一题交卷按钮及 Tab/Esc 通过；该草稿未提交，不当作学习结果 |
| 四.3 在线资源/PDF | 搜索筛选在标题区；资源列表滚动；按真实文件类型预览：PDF 页码、文本原文、图片；独立阅读弹窗和返回 | 实际 PDF 签名有效，原生阅读器完成第 2 页渲染；返回/Esc/焦点通过；课程原页另验证 |
| 四.4 教师现有页面 | 首页、题库、试卷/发布、答卷、三范围诊断、教学材料、在线资源接入公共壳层 | 66 组教师尺寸检查；真实选题发布及复测发布保留原业务 |
| 四.5 弹窗/抽屉 | 公共最大高度、内容内部滚动、按钮保留；Element Plus 焦点管理 | 复测记录 15 次 Tab、PDF 12 次 Tab 保持焦点容器；Esc、返回和提交取消有效 |
| 五.1–10 技术/质量约束 | 先检查旧根容器 min-height 与嵌套内容高度；优先公共壳层，保留接口/路由/数据库，无模拟页面 | 新投影不迁移原题库；旧库兼容迁移测试保留。使用真实账号、服务、worker 和持久化数据；189/33 回归、构建和浏览器证据分别保留 |
| 六 最终交付 | 修改清单、说明、测试、1366 截图、未完成原因 | 本文及下列证据索引；报告/PPT 和完整项目压缩包一并提供 |

## 双学生真实闭环与持久化

基础卷 `paper_8d6f24ce3c3249f4`，发布 `release_a8e07f1f0cb94900`；AI 复测任务 `job_bb8032dfde1541e8`，复测卷 `paper_fa221e34bb1f4575`，发布 `release_d3d22142b98246b0`。

| 学生 | 原卷答卷/分数 | 第1轮复测答卷/分数 | 周期与检测 |
|---|---|---|---|
| STU001 | attempt_d660ce810c684cfd；0/100 | attempt_edc64a9bc77b4fbc；10/10，100% | retestcycle_0275845651a74ffa；run_718d2a3915fd4ed4；passed |
| STU002 | attempt_7bc74a1a495e4925；50/100 | attempt_b2c33f0d57b24fc1；6/10，60% | retestcycle_3cdc5a3111e84cc2；run_666c9efb4f1c4c93；not_passed |

原卷与复测满分不同，比较使用正确率及每知识点证据，不直接用“增加多少原始分”表示学习进步。当前弱点 0 不代表长期全部掌握。班级发布覆盖 16 人，只让上述两名有原卷的学生正式作答。STU003 的 `attempt_e64e64a2853d418c` 只用于布局/取消交卷检查，未作答未评分。

单人多卷真实模型任务 `job_c8866d3d3af9445e`，班级诊断任务 `job_b8a72af570fa4297`。教师页面原来按过时版本号拒绝重用已保存 AI 结果，现按客观证据一致性决定重用；重评分、增加答卷或同均分下答案分布变化均不复用旧解释。对应 `savedDiagnosis.test.ts` 三项回归和 teacher-workbench-final 的 model_reload。

## 修改页面、组件及逻辑

- 公共：`App.vue`、`main.ts`、`styles/workbench.css`、`WorkbenchPage.vue`、`MasonryGrid.vue`。
- 学生：`StudentPortalView.vue`、`StudentFeedbackView.vue`、`ExamView.vue`、`StudentAttemptResult.vue`。
- 教师：`TeacherDashboardView.vue`、`TeacherWorkspaceView.vue`、`PaperBuilderView.vue`、`TeacherAttemptReviewView.vue`、`TeacherAnalysisView.vue`、`SingleAttemptWorkbench.vue`、`MultiAttemptWorkbench.vue`、`ClassAssessmentWorkbench.vue`、`AttemptEvidenceTable.vue`。
- 资源与诊断共用：`OnlineResourcesView.vue`、`MaterialsView.vue`、`LearningMaterialView.vue`、`LearningResourceList.vue`、`DiagnosisAnalysisContent.vue`、`utils/savedDiagnosis.ts`。
- 后端：`api/role_routes.py`、`services/feedback_workbench.py`、`classroom_diagnosis.py`、`diagnosis_coaching.py`、`learning_catalog.py`、`learning_resources.py`。另有对应测试、快照恢复/导出及浏览器验证脚本。

学习要点：仅给父容器加 `overflow:hidden` 会把内容截掉。这里先让根容器确定高度，再给可收缩的子层设置 `min-height:0`，最后只让真正的长内容层滚动。诊断也采用类似的职责分离：程序计算事实，模型解释有证据的问题，复测结果按学生和原卷更新，最后对照页面、API、数据库，避免“页面看起来成功”但数据错误。

## 证据索引与1366截图

以下路径均位于 [verification/workbench](verification/workbench/)。

- [学生页面/接口/数据库](verification/workbench/browser-persistence-final.json)、[教师三范围/PDF](verification/workbench/teacher-workbench-final.json)、[真实缩放](verification/workbench/browser-zoom-final.json)、[答题最后一题与取消交卷](verification/workbench/active-exam-final.json)。
- [真实操作闭环](verification/workbench/cycle-record.json)、[LangGraph 任务](verification/workbench/class-workflow-result.json)、[真实提示词](verification/workbench/runtime-prompts-final.json)。
- [后端测试](verification/workbench/backend-tests-final.txt)、[前端测试](verification/workbench/frontend-tests-final.txt)、[类型检查](verification/workbench/frontend-typecheck-final.txt)、[生产构建](verification/workbench/frontend-build-final.txt)。
- [原数据保留及脱敏快照](verification/workbench/snapshot-and-preservation-final.json)：832 条题目版本、130 份原答卷、747 条作答、749 条评分逐行无改动或缺失；新快照清除会话、密码哈希，integrity_check=ok。
- 1366×768：[我的任务](verification/workbench/tasks-final-1366.png)、[学习反馈](verification/workbench/stu002-feedback-final-1366.png)、[答题](verification/workbench/active-exam-final-1366.png)、[课程精读](verification/workbench/course-final-1366.png)、[教师首页](verification/workbench/teacher-home-final-1366.png)、[题库](verification/workbench/teacher-bank-final-1366.png)、[试卷](verification/workbench/teacher-paper-final-1366.png)、[单人单卷](verification/workbench/teacher-student_attempt-final-1366.png)、[单人多卷](verification/workbench/teacher-student_history-final-1366.png)、[多人单卷](verification/workbench/teacher-class_assessment-final-1366.png)、[PDF](verification/workbench/teacher-pdf-final-1366.png)。
- 文件名含 `failure`、`initial`、不含 final 的早期截图及旧测试日志保留排错过程，不是最终通过证据；例如小窗压扁、读取旧诊断、PDF 等待条件和动画时序问题，以对应 final 记录为准。

## 本地启动与恢复本轮结果

安装 Python 3.13、Node.js 20+（运行前端测试建议 Node 24），解压后在项目根目录执行 `./start.ps1 -Port 8001`。首次安装依赖需要网络；ZIP 附生产构建，API 同时提供前端，另启动 worker。模型密钥仅在本机 `.env` 配置，包内不含密钥。默认数据恢复从不覆盖已有数据库。

要查看上述验收数据，先正常启动完成依赖安装，再停止该目录服务。在新目录恢复验收快照：

```powershell
.\start.ps1 -Stop
.venv\Scripts\python.exe -X utf8 backend/scripts/restore_acceptance.py --snapshot workbench-review --output data/workbench-view
$env:MLQ_DATA_DIR=(Resolve-Path data/workbench-view).Path
.venv\Scripts\python.exe -X utf8 backend/scripts/bootstrap_demo.py
.venv\Scripts\python.exe -X utf8 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001
# 另开终端、进入同一根目录，并设置相同 MLQ_DATA_DIR 后：
$env:MLQ_DATA_DIR=(Resolve-Path data/workbench-view).Path
.venv\Scripts\python.exe -X utf8 -m backend.app.workers.worker --interval 2
```

浏览器复查脚本是 `backend/scripts/verify_workbench_browser.py`、`verify_teacher_workbench_browser.py`、`verify_workbench_zoom.py`、`verify_exam_workbench_browser.py`。运行前单独安装 Playwright 和 Chromium（`python -m pip install playwright`、`python -m playwright install chromium`），各脚本 `--help` 列出参数。读验收快照的脚本不替代从头操作；教师 `--run-model` 才发起真实新调用，答题检查要求显式 `--allow-open-draft`。只对独立演示目录运行。

## 独立解压复查中追加修复

全新快照中的资料排序不同，首次检查错把 Markdown 当作 PDF；同时发现文本预览也显示 PDF 页码控件。现按真实类型渲染：PDF 使用阅读器和页码，Markdown/文本以可滚动原文显示，图片单独显示；脚本按类型选取实际 PDF，并检查文本内容、无 PDF 页码控件和返回。原始失败保留，最后结果以 package-smoke-final.json 为准。初始化脚本前置日志造成验收脚本 JSON 解析失败也已修正，属于验收工具问题。

## 最终压缩包复查

[package-smoke-final.json](verification/workbench/package-smoke-final.json) 记录独立解压结果：未复制.env，复用已按锁文件安装的Python 3.13环境，默认快照及workbench-review快照均恢复354个素材，API、worker、前端资源及教师登录通过。新目录再次执行学生30组、教师66组浏览器检查，两个学生当前弱点0/2一致；PDF真实渲染第2页，文本原文55字完整显示。恢复脚本拒绝覆盖已有数据库。未声称重新安装了一套Python依赖，未在缺密钥的包内伪造AI新调用。

文本预览追加[六个尺寸及键盘验证](verification/workbench/material-preview-final.json)和[1366截图](verification/workbench/teacher-text-preview-final-1366.png)均通过，单独列出，不混入原102组数量。开发副本中缺失的测试笔记也从随包同源文件恢复，只新增缺失文件、未改数据库。

交付文件为 `mlquiz-platform-workbench-verified-20261118.zip`，相邻 `.sha256.txt` 可校验完整性。ZIP含源码、生产前端、依赖锁、启动脚本、原始匿名快照、各阶段验收快照、实际素材、报告/PPT与验证记录；排除模型密钥、本机凭据、运行数据库和依赖环境。最终包只追加文档/验收工具，应用源码和前端构建与独立解压验证的版本逐文件比对相同。

## 尚未完成材料与边界

已验证的软件范围内未留已知阻断问题。仍缺指定开发平台历史、早期开发提示词原件与真实成员信息，无法由代码修复补齐，未伪造；不影响平台功能交付。

部分历史题没有可靠章节映射时如实显示“未映射章节”。外部站点可随时间改变，历史抽验不保证所有链接永久可用；离线课件提供稳定复习入口。实际验收覆盖本文列出的窗口、缩放、账号和流程，不宣称穷举所有浏览器或所有题目。自动化测试可用模型替身，真实运行证据与替身严格分开。重新生成 AI 新题仍需本地有效模型配置。
