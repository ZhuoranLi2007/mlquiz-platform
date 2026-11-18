# 工作台改造执行记录

任务原文保存在 WORKBENCH_REQUIREMENTS.md。全部要求仍是验收范围，以下仅记录进度，不是验收结论。

## 当前工作副本

- Git分支：codex/workbench-layout-20261117，基于已推送的d213227。
- 开发目录：D:/文档/机器学习复习平台搭建/github-publish-verified-20261117。
- 新服务：127.0.0.1:8004；隔离数据data/workbench-review，由diagnosis-review脱敏快照恢复。后台PID在.artifact-build/workbench-pids.json。
- 原修复版8003不变；桌面旧工程及其未提交改动不变。
- Python复用../delivery-work/mlquiz-platform-main/.venv/Scripts/python.exe。npm CLI路径为C:/Users/lizhi/.workbuddy/binaries/node/versions/22.22.2-6/node_modules/npm/bin/npm-cli.js。
- 本机.env仅复制到新副本供真实模型验证，仍被Git忽略，不得输出或上传。

## 已实施但尚未浏览器验收

- App.vue公共壳层从min-height改为100dvh，角色主体采用Flex并设置min-height/min-width为0，保留内容区域滚动的兼容路径，避免直接裁切旧页面。
- 新WorkbenchPage.vue支持固定header/footer和独立body滚动。极小窗口时header自身可滚动，不永久遮挡操作。
- 新workbench.css统一视口、工具栏、统计卡、弹窗/抽屉内部滚动约定。尚需实际检查Element Plus焦点与Esc行为。
- OnlineResourcesView.vue首先复用WorkbenchPage，搜索筛选固定，资源列表在body滚动。
- npm ci成功；第一次vue-tsc和vite build成功。没有用编译通过代替布局验收。

## 接下来必须完成

1. 学习反馈双视图：标题工具栏、80–90px统计卡、错题两列及定位、35/65知识点诊断、分别滚动，试卷切换避免并发请求覆盖。
2. 新诊断数据契约：程序统计每知识点的题目/错误/比例，跨知识点按题去重；细化指标概念；AI明确输出有证据关联的错因与概念纠正，资源/客观数值不由AI生成。
3. 取消无估算依据的8/10分钟建议（包括旧展示和课程精读）；复测状态按学生、原卷及知识点对齐，真实入口与历史对比。
4. 我的任务、答题/结果、教师首页、题库/组卷/发布、教师诊断、资源/PDF、课程精读全部按固定工作台逐页接入。当前公共兼容滚动不等于已满足分区滚动。
5. 浏览器1366×768、1280×720、1440×900、1920×1080及较小/缩放场景；检查document高度、可见性、滚动到末尾、关键操作、弹窗键盘。保存主要页面截图。
6. 在8004隔离副本完成真实学生反馈选择/知识点/题号/资源、教师发布、学生复测、评分/数据库/诊断更新闭环，小批真实AI调用并保留提示词，不修改8003数据。
7. 按改动范围运行测试，更新交付清单、布局说明、诊断说明、实际结果和未解决项；所有证据不足项目仍未完成。

既有182后端/30前端结果属于上个版本，本次不能直接复用为最终验收结论。题库优先、缺题AI补的业务策略保持。

## 2026-11-18 继续实施（尚未最终验收）

- GitHub 基础提交657d988已推送；以下改动纳入本次工作进度备份，具体提交号以Git历史为准。
- 学生反馈新增feedback_workbench确定性投影，保留题目/知识点ID，按题去重，区分待评分、未覆盖、当前概念仍错、当前轮相关题全对。精确率/召回率/F1/交叉验证按题干补充展示分类，不迁移原题库。
- AI coaching-v4保留每条解释的真实证据ID/题号，新增概念纠正字段；取消统一8/10分钟建议。新旧报告兼容，版本字段仍为3，提示词版本独立升级。
- 学习反馈改为双视图，35/65两栏独立滚动、两列错题、题号定位、真实复测入口、加载请求序号避免旧请求覆盖。
- 教师首页、题库、试卷预览/发布、答卷、三种诊断、材料页、学生任务、答题/结果、课程原页已接入WorkbenchPage。PDF预览放入可返回的阅读弹窗。
- 真实浏览器工具：Playwright Python1.65.2安装在.artifact-build/browser-tools，使用现有LOCALAPPDATA/ms-playwright/chromium-1248/chrome.exe，无模拟接口返回。临时验收脚本位于.artifact-build/browser_*.py。后续需整理可复用脚本。
- 188项后端测试、30项前端测试通过；结果在docs/verification/workbench。旧布局源码断言已按新要求更新，实际几何仍用浏览器验收。
- 第一轮12条路由×5尺寸（四指定分辨率+640×720）60条浏览器几何记录完成，无页面JS错误，document均等于viewport。但这不是最终验收：检查截图发现缩放过渡导致题库卡片暂时重叠、窄屏结果页底部超出24px，已修复，待最终复查。教师诊断随后进一步改为列内滚动，也需复查。
- 真实模型诊断：UI教师选择原验收A卷，点击运行诊断，job_5a4c60c9f39b40a3完成，coaching-v4解释显示且持久化；截图/文本在real-model-ui-diagnosis.json。
- 新完整闭环正在验证：教师真实UI从题库选择LASSO判断题与过拟合判断题，创建工作台验收20261118-双知识点双学生，paper_8d6f24ce3c3249f4，release_a8e07f1f0cb94900，version av_7367b55548402028827c56c3。
- STU001和STU002已真实UI提交，分别0/100和50/100；确认交卷弹窗Esc取消后再次确认有效。具体attemptID见cycle-record.json。
- 教师多人单卷页面显示平均25、参考2/16。真实UI运行班级复测job_bb8032dfde1541e8成功，LangGraph诊断/推荐/生成/验答/发布完成，11候选筛成5题，最终source_counts ai_generated=5, bank_selected=0（未更改题库优先策略）。发布班级16人，已有原卷答卷的两名学生分别建立cycle，round1；release_d3d22142b98246b0，paper_fa221e34bb1f4575，完整结果见class-workflow-result.json。
- 下一步：两名学生从对应原卷反馈入口打开并提交复测，分别全对和部分错；核对cycle/attempt/source/评分/verify_learning持久化。再验证反馈知识点/题号/原页跳转、单人多卷、所有页面最终尺寸/小窗/缩放/弹窗焦点/列表末端，更新真实提示词、交付说明并打包。
- 8004当前进程信息以.artifact-build/workbench-pids.json为准。原始数据/8003仍保留。开始新闭环前另备份data/workbench-review/private/before-workbench-ui-cycle-20261118.sqlite3（不得上传）。

## 2026-11-18 GitHub进度备份补记

本节更新上述阶段记录，不将进度备份视为最终验收。

- 两名学生已从各自原卷反馈入口完成真实浏览器复测：STU001为10/10（100%），STU002为6/10（60%），返回各自原卷。实际页面文字和答卷标识见verification/workbench/cycle-record.json；该文件保留采集当时的原始结果。
- 已验证知识点切换、定位第2题、视图切换保留选择、打开真实课程原页、页码跳转及返回资源列表保留登录。
- 此后修复“当前薄弱知识点”随最新有效复测更新，保留原卷错误数量，并展示复测错题。相关后端回归26项通过（diagnosis-regression-final.txt）。现有截图及cycle-record.json早于此最后修复，不能证明最后修复的浏览器表现。
- 仍待完成：最后修复的浏览器复查；两个学生的页面、接口及数据库逐项对照和verify_learning调用核对；单人多卷复核；各局部滚动末端、缩放、弹窗焦点完整验收；最终提示词导出、报告/PPT同步与压缩包交付。
- 本次推送包含源码、测试和已采集的隔离演示数据验证证据；本机.env、私人备份、运行数据库、依赖目录及临时生成工具不上传。已有脱敏交付快照不等同于本次8004运行数据库。
