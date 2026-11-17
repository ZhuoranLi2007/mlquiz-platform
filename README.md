# 机器学习学情诊断平台

一个面向机器学习课程的教师—学生双端教学平台。教师可以管理题库、组卷发布、查看课堂与个人诊断，并生成针对性复测；学生可以完成考试、查看完整错题反馈、复测结果和学习资源。

项目使用 **Vue 3 + TypeScript + FastAPI + SQLite + LangGraph**。仓库内附带一份脱敏课程快照，包含16名匿名学生、历史课堂考试、题库、评分、诊断、课程检索片段及页面实际使用的教学资源，可以在全新克隆后直接体验主要功能。

学情诊断与资源专项修复见 [诊断资源核查说明](docs/DIAGNOSIS_RESOURCE_REVIEW.md)：35个课程知识点对应80张原课件页，可离线阅读。

AI出题复测专项修复见 [专项核查说明](docs/AI_RETEST_REVIEW.md)。常规组卷保留“题库优先、缺题AI补”的原有策略。

## 主要功能

- 教师端：教学总览、题库与组卷、试卷发布、三种学情诊断、教学资源和在线资源。
- 学生端：考试任务、学习反馈、完整错题、针对性复测和在线资源。
- 评分与诊断：客观题自动评分、追加式重评分、章节诊断和跨考试趋势分析。
- AI工作流：可选接入DeepSeek，完成诊断、资源推荐、复测出题与自动校验。
- 数据安全：学生使用匿名编号；演示密码只在首次运行时生成到本机，不进入Git。

最终自动化结果：后端182项、前端30项通过，类型检查与构建通过。真实浏览器与模型结果单独记录，不能用自动化测试代替。

## 环境要求

本次功能核查版的逐条结论见 [课程要求核查表](docs/REQUIREMENTS_AUDIT.md)，报告和PPT位于 `docs/submission/`，验证日志位于 `docs/verification/`。本轮已完成三种真实浏览器闭环、累计85次真实模型调用（首轮61次、出题专项19次、诊断专项5次，含如实保留的失败记录）和持久化核对。完整启动、验证边界、独立验收快照的复查方法见 [本轮交付说明](docs/FINAL_DELIVERY.md)。指定开发平台历史材料仍需本人提供，不能补造。

- Windows 10/11与PowerShell
- Python 3.13（本轮已在全新Windows虚拟环境按锁文件安装并实际启动）
- Node.js 20或更高版本（附带npm）

## 快速开始

克隆本分支后，在项目根目录打开PowerShell运行（也可使用已交付ZIP）：

```powershell
.\start.ps1
```

首次运行会自动完成以下工作：

1. 校验Python 3.13与Node.js 20+，创建 `.venv` 并按锁文件安装依赖。
2. 从脱敏快照恢复本地SQLite数据库。
3. 在 `data/private/` 生成教师和16名学生的本地演示凭据。
4. Git版本首次启动按锁文件安装前端依赖并构建；ZIP版本可使用随包的生产构建。
5. 启动FastAPI、后台worker并打开浏览器。

本版教师AI干预与组卷使用后台任务，必须保持worker运行。教师端新增“导出运行提示词”，保存从本版开始产生的实际模型请求和响应；旧调用不会自动补造正文。

访问地址：<http://127.0.0.1:8000/>

登录页直接提供“演示教师”和 `STU001`—`STU016` 身份选择，选中后自动登录；旧账号仍可通过“手动输入其他账号”进入。会话保存在当前浏览器标签页，教师和学生可以分别在两个标签页登录。教师发布试卷后，学生在自己的标签页刷新“我的任务”即可开始答题。

常用启动命令：

```powershell
.\start.ps1 -NoBrowser        # 启动但不自动打开浏览器
.\start.ps1 -Port 8001        # 默认端口被其他项目占用时使用
.\start.ps1 -RebuildFrontend  # 强制重新构建前端
.\start.ps1 -Check            # 检查服务状态
.\start.ps1 -Stop             # 停止服务，不删除数据
```

## 可选：配置DeepSeek

不配置模型也能使用题库、考试、评分和已有诊断。需要刷新AI分析或生成新复测题时：

```powershell
Copy-Item .env.example .env
```

然后在 `.env` 中填写：

```text
MLQ_MODEL_PROVIDER=deepseek
MLQ_MODEL_BASE_URL=https://api.deepseek.com
MLQ_MODEL_CHAT_MODEL=deepseek-chat
MLQ_MODEL_API_KEY=你的密钥
```

`.env` 已被Git忽略，不要把密钥写入源码或提交记录。若将系统部署到共享网络，请关闭快速登录：

```text
MLQ_QUICK_LOGIN_ENABLED=false
```

## 开发与测试

后端测试：

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest -q
```

前端开发与检查：

```powershell
cd frontend
npm ci
npm run dev
npm run test:unit
npm run typecheck
npm run build
```

前端开发服务器地址为 <http://127.0.0.1:5173/>，`/api` 请求代理到8000端口。

## 目录结构

```text
backend/app/       FastAPI接口、评分、诊断、工作流与数据模型
backend/tests/     后端自动化测试
backend/scripts/   演示数据恢复和发布快照工具
frontend/src/      Vue教师端与学生端
frontend/tests/    前端展示与交互测试
data/demo/         脱敏压缩快照和页面实际引用的资料、封面与页图
start.ps1          Windows一键启动脚本
```

## 演示数据说明

仓库没有上传原始数据库、完整267MB课程资料目录、约100MB源DOCX、学生姓名、密码哈希、登录会话、API密钥、未脱敏运行日志或学生手写附件。`docs/verification`保留核验所需的脱敏提示词、任务结果和测试证据，`data/acceptance`仅包含清除了密码哈希和会话的验收快照。

`data/demo/mlquiz-course-demo.sqlite3.gz` 保留演示主要业务所需的匿名课程数据，包括60份文档元数据、4612个检索片段、4份教师教学资源记录、已生成答案册和在线资源封面信息。`data/demo/assets/` 保存这些活跃页面真正请求的PDF、文本、封面、页图和经哈希核对的历史课堂题目清单；发布脚本会在生成时检查所有文件引用，任何缺失都会直接失败。首次启动会把数据恢复到被Git忽略的 `data/db/`，且从不覆盖已存在的数据库。

如需从维护者自己的数据库重新生成发布快照：

```powershell
.venv\Scripts\python.exe -X utf8 backend/scripts/create_release_snapshot.py
```

该命令通过SQLite在线备份API读取源库，生成脱敏副本并执行完整性与敏感信息检查，不会修改源数据库。

## 安全提示

本项目的快速登录面向本地课程演示。不要直接把默认配置暴露到公网；正式部署应关闭快速登录、设置HTTPS、使用独立密钥并重新设计账户初始化流程。
