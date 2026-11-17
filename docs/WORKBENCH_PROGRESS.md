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
