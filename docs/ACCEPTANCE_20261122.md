# 2026年11月22日课程验收

基准为 GitHub main 的 c8e973c211f6c3a83156cebb3f35d40b664fc86f。优化分支为 course/acceptance-polish。同步前 main 与缓存 origin/main 均为 ca689b6，本地有 .env.example 删除。通过 git fetch 获取远程更新后，在优化分支快进至最新 main。本地删除保留，旧基准上的试作代码保存在 Git stash，记录名为 Codex provisional audit on ca689b6 before remote synchronization。

## 课程要求与证据

|课程要求|远程已有实现|本轮发现与处理|验证证据|
|---|---|---|---|
|基础题库管理与标签|题库检索、筛选、导入、章节调整|补充教师新增、版本化编辑、停用、主知识点和关联标签维护|test_teacher_paper_builder.py，review1122/browser.json|
|AI出题并校验入库|独立出题工作台、异步任务、来源核验、修订及审核|补充重复编号、空选项、无效数值与容差校验|完整后端回归，原真实AI记录见 demo-polish-20261119|
|试卷、考试、提交与评分|试卷快照、指定学生发布、交卷幂等、评分持久化|复核正式入口与学生权限|2026-11-22-api.json，10/10读回|
|题目级诊断|题目错误分布、知识点、难度、错因假设|复核真实题项与答卷关联|API question-analysis，test_classroom_diagnosis.py|
|试卷级诊断|得分、薄弱点、优势及建议|复核有效评分分母|API student-attempt，test_grading_denominators.py|
|个人多卷诊断|纵向知识点证据及趋势|保留小样本证据不足结论|API student-history，test_learning_loop.py|
|同场多人诊断|固定试卷版本班级统计与学生差异|复核同卷比较口径|API class-assessment，test_course_acceptance.py|
|推荐学习资源|课程原页、B站、抖音、帖子与来源说明|补充按学生保存和取消收藏，刷新可读回|review1122/browser.json，crud-tests.txt|
|新题检测闭环|未见题检查、题族与角度校验、复测与延后验证|复核学生、教师三种视角共享结果|test_learning_loop.py，test_targeted_retest.py|
|LangGraph与检测Skill|learning-loop图与verify_learning实际调用，Skill输入输出、运行记录|保留已有业务编排并回归|test_course_acceptance.py，skills/verify_learning/SKILL.md|
|异常与数据一致性|重复提交、AI失败、权限、部分评分测试|增加旧版本拒绝更新和重复收藏测试|221项后端通过，7项专项通过|
|界面与现场运行|统一工作台、响应式布局和历史截图|复用组件加入维护与收藏入口，修复刷新竞态与npm启动失败|浏览器6类检查无页面错误，构建及类型检查通过|
|报告与PPT|已有详细报告及11页答辩稿|保留原案例，追加工程复核附录并同步PPT测试数|submission/course_report_reviewed_1122.docx及PDF，course_demo_reviewed_1122.pptx|
|真实提示词历史|原始开发记录摘录、源码模板及运行日志导出|追加本聊天已收到的实际指令摘录与迭代记录|PROMPT_HISTORY_20261122.md|
|两人以内及12月2日汇报|模板保留成员栏|人数、姓名、学号与真实分工由成员填写|原报告封面与待本人补充事项|

## 本轮运行记录

后端最终执行 python -m pytest -q，共221项通过。前端45项单测、vue-tsc检查、vite生产构建通过。记录位于 docs/acceptance/review1122。隔离API验证恢复16名学生、782条题目记录、122份历史答卷与37项资源，完成组卷、发布、学生答题和评分保存。

浏览器实际完成新增、编辑、停用，收藏、刷新、取消收藏，以及教师首页、学情诊断、教学资源、在线资源访问。新接口使用教师权限或绑定学生权限。编辑时先锁定SQLite写事务并检查版本，旧试卷使用原快照。静态文件回退入口增加目录包含校验，两个编码目录穿越请求均返回404，新增文本交付件通过本机模型密钥扫描，见security.json。

两名学生的历史真实复测已在本轮实例读回，每名学生的单卷、历史、班级三个教师视角与学生端结论一致。交叉访问答卷返回403，访问他人的任务返回404。35个知识点的82个课程原页引用均可读取，历史23条真实模型运行提示词经脱敏导出至review1122/learning-loop。

本轮模型生成仅验证现有受控测试与历史真实证据。未重新发起付费模型生成。外部平台的正文、视频和登录可用性需要演示当天检查。

## 启动与课堂路径

普通运行在项目根目录执行 .\start.ps1。需要构建时执行 .\start.ps1 -NoBrowser -RebuildFrontend。默认地址为 http://127.0.0.1:8000。

本轮隔离实例使用8022端口，数据位于data/course-review-1122。重启时在同一PowerShell会话设置 $env:MLQ_DATA_DIR 为该目录的绝对路径，再执行 .\start.ps1 -NoBrowser -Port 8022。检查和停止也使用相同环境变量与端口。bootstrap不会覆盖现有数据库。全新复现可使用restore_acceptance.py的learning-loop-review快照，输出选择新的目录，再启动。

演示顺序为教师首页、题库维护、AI候选审核、组卷发布、两名学生答题、题目与单卷反馈、班级横向诊断、个人纵向诊断、课程原页与收藏、新题复测、知识点结论。整轮80%通过、知识点达标和延后复测确认分别展示。

最终报告为docs/submission/course_report_reviewed_1122.docx及同名PDF，共65页。保留原报告内容并追加工程复核，末尾分页已调整。11页PPT为course_demo_reviewed_1122.pptx，保留原稿结构、截图与案例，在测试页及演讲备注更新本轮结果。打包渲染器缺少LibreOffice，报告使用本机Word导出PDF，Poppler渲染后检查。PPT使用Artifact Tool导入、编辑、渲染和导出，最终结构与布局检查无发现项。

## 仍需准备

成员身份与早期开发对话原件仍需实际作者补齐。部分历史章节映射尚需人工确认。模型实时生成依赖有效配置、网络和额度，外部资源可能要求登录。前端主包及诊断包仍较大，建议演示前打开常用页面完成预加载。题库手工编辑支持单选、多选、判断，其余题型继续沿用导入与AI候选审核入口。
