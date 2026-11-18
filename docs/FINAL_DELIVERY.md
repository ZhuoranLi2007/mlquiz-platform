# 本轮交付与使用说明

> **2026-11-18最新交付**：当前结果以 [WORKBENCH_ACCEPTANCE.md](WORKBENCH_ACCEPTANCE.md) 为准，后端189项、前端33项，累计116条真实提示词，102组尺寸与33组真实缩放检查。8页报告/PDF和8页PPT已同步。本文件以下为此前阶段的实现与验证记录，旧测试数量和日期保留历史含义，不作为当前最终数量。

首轮验收日期：2026-11-16；AI出题复测专项交付：2026-11-17。保留原Vue、FastAPI、SQLite与LangGraph架构及页面布局。本轮所有新考试、作答和真实模型验证在从原包恢复的独立副本中完成，未连接或修改桌面上的旧平台数据库。

新增AI出题复测专项说明见`AI_RETEST_REVIEW.md`。常规组卷保留题库优先、缺题AI补的原有策略；最终后端182项、前端30项通过，真实模型累计85次调用。新增诊断与资源专项说明见`DIAGNOSIS_RESOURCE_REVIEW.md`，35个课程知识点对应80张唯一原课件页，支持离线精读。

## 启动

安装Python 3.13和带npm的Node.js 20+；运行前端单元测试建议Node 24。解压后在项目根目录打开PowerShell：

```powershell
.\start.ps1 -Port 8001
# 默认端口空闲时也可直接 .\start.ps1
.\start.ps1 -Port 8001 -Check
.\start.ps1 -Stop
```

首次启动创建虚拟环境、按锁文件安装依赖、恢复原始匿名快照和教学素材、生成本机演示凭据，启动后端与worker。前端生产构建由同一后端服务提供。登录页选择演示教师或STU001—STU016，每个标签页身份独立。后台生成任务需要worker持续运行。停止脚本只处理本目录记录的进程。

Python未在PATH时，可传入`-PythonExe 'Python3.13解释器的绝对路径'`。系统也会尝试发现本机WorkBuddy附带的Python 3.13与npm。此发现机制只为兼容运行环境，不能证明项目过去使用了WorkBuddy。

AI调用需在本机`.env`填写模型配置，参考`.env.example`。本轮使用已有配置真实验证，交付包按要求不包含该密钥。已有基础题、考试和确定性分析可在无模型时使用。缺模型生成任务会如实报告失败/待配置，不会提供模拟题冒充真实生成。

开发前端可在独立终端运行：

```powershell
$env:MLQ_DEV_API_URL='http://127.0.0.1:8001'
cd frontend
npm ci
npm run dev
```

## 重现本轮页面结果

默认启动使用未覆盖的原始演示快照。`data/acceptance/mlquiz-verified.sqlite3.gz`另存本轮脱敏验收快照，可恢复到新的独立目录：

```powershell
.venv\Scripts\python.exe -X utf8 backend/scripts/restore_acceptance.py --output data/acceptance-view
$env:MLQ_DATA_DIR=(Resolve-Path data/acceptance-view).Path
.venv\Scripts\python.exe -X utf8 backend/scripts/bootstrap_demo.py
.venv\Scripts\python.exe -X utf8 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8002
# 另开终端，设置同一个MLQ_DATA_DIR后启动worker
.venv\Scripts\python.exe -X utf8 -m backend.app.workers.worker --interval 2
```

恢复脚本拒绝覆盖已存在的数据库。验收快照只供复查，基础卷标题为“验收20261116-基础双知识点-A”。教师单人多卷选择STU002，日期设为2026-11-16至2026-11-16。

## 真实结果

| 流程 | 页面观察与持久化结果 |
|---|---|
| 基础卷 | 两题各50分；STU001为0/100，STU002为50/100 |
| 单人单卷复测 | STU001第1轮5题全对，10/10；verify_learning执行并更新诊断 |
| 多人单卷复测 | 同一次发布，STU001第2轮10/10，STU002第1轮0/10；各自关联本人的原卷 |
| 单人多卷复测 | STU002两次考试平均25%，根据诊断发布第2轮复测，10/10；更新为3次考试平均50% |
| 前后对比 | STU002原卷50%、第1轮0%、第2轮100%，历史记录同时可见 |
| 无答案题 | 隔离复制题删除答案后，真实solve_answer解答成功，再独立验答一致 |
| worker失败恢复 | 不存在答卷的负例失败3次后保留failed；后一正常检测任务成功，worker继续运行 |
| 数据保留 | 原有122份答卷、717条作答、719条评分逐行一致，未删除或覆盖 |

完整证据和实现路径见`REQUIREMENTS_AUDIT.md`。首轮61次、出题专项新增19次、诊断专项新增5次，共85次真实模型响应均有对应提示词，响应模型为`deepseek-flash`；配置模型名与服务商实际返回名可能不同。自动化模型替身记录不混入这85条。

## 修改与学习说明

先读源码和数据库，再建立副本和基线测试。沿教师与学生页面实际操作，出现故障后追到控制条件、统计转换或数据库约束，修复后重复受影响流程。最后对照页面、API、数据库三种证据。

- 发布按钮：空字符串可能作为HTML布尔属性使按钮禁用，改成显式Boolean，重新发布同一草稿验证。
- 判断题统计：False是一个有效答案，不能用“真假判断”代替“是否提供答案”的判断。
- 复测覆盖：本轮目标与未覆盖知识点分别保存；全部题评分齐全才可判断通过。
- 多人记录：同一发布对每名学生创建独立周期，使用发布ID与学生ID组合唯一约束。
- 后台任务：SQLite写锁保证查重与创建顺序；心跳区分慢任务与中断；已发布任务有提交标记，恢复时不盲目重复发布。
- 迁移：先做SQLite在线备份并检查完整性，再增加字段，避免WAL中的已提交答卷在普通文件复制时遗漏。
- 提示词：在真实模型适配器处记录messages和响应，脱敏后由教师页面下载，不能事后编造过去记录。

## 验证边界和未完成材料

本轮功能验收范围内未留已知阻断问题。下列边界明确保留：

1. 指定豆包Work或WorkBuddy的项目使用历史、早期开发提示词、真实成员信息未获可靠原始材料，未补造。需本人补充实际存在的记录；无记录就如实说明。
2. 一次复测达标表示本轮表现，不代表长期掌握。部分历史题没有可靠章节映射时显示“未映射章节”，仍用明确知识点ID诊断，不猜章节。
3. 部分AI评语未通过内容校验，页面明确回退为依据真实作答的分析；不把模型HTTP成功当作内容全部通过。
4. 外链本轮抽验了B站播放器、CSDN正文和新抖音视频页。资源总数不等于所有视频逐帧审核或所有外链永久可达；旧搜索入口不算已核验内容。
5. 班级复测发布至16名演示学生，本轮只要求并实际让两名有基线的学生提交；无原卷者不能生成伪造前后对比。

## 交付内容

报告DOCX及PDF、8页可编辑PPT含讲解备注位于`docs/submission`。截图、测试、运行JSON位于`docs/verification`。原始匿名快照和素材在`data/demo`，本轮快照在`data/acceptance`。不包含`.env`、虚拟环境、node_modules、本机密码文件或登录会话。初次安装依赖需要网络。

上一专项压缩包已在没有.env的全新目录解压启动，复用本轮按锁文件新装的Python环境。292个素材恢复、前端构建资源、API、worker、演示登录和独立验收快照复查均通过。首轮记录见`verification/current-package-smoke.json`；专项更新包记录见`verification/ai-retest-package-smoke.json`。文件变更清单见`verification/DELIVERY_CHANGES.json`；脱敏和包检查见`verification/PACKAGE_VALIDATION.json`。ZIP旁的`.sha256.txt`用于检查下载文件是否完整。


## 本次诊断资源交付

三范围诊断和学生课程阅读已重新做真实浏览器验证。恢复本次结果请使用`restore_acceptance.py --snapshot diagnosis-review --output data/diagnosis-view`，其余环境和启动步骤与上文相同。详细证据、已知边界、学习说明见`DIAGNOSIS_RESOURCE_REVIEW.md`。最终包的独立解压启动结果记录在`verification/diagnosis-package-smoke.json`。

最终包已完成该独立解压检查：恢复354个素材文件，后端、worker、前端、演示登录及本次诊断快照复查均通过，35个知识点材料与80张唯一图片可用。

## 2026-11-18完整工作台交付

最新ZIP为mlquiz-platform-workbench-verified-20261118.zip；报告和PDF均8页，PPT8页。独立解压检查通过，恢复354个素材，实际启动API/worker/前端，并在新目录再次通过学生30组和教师66组浏览器检查。文本预览与PDF按类型显示，文本追加六尺寸验证。当前完整说明、所有截图和实际结果见WORKBENCH_ACCEPTANCE.md，压缩包检查见verification/workbench/package-smoke-final.json。
