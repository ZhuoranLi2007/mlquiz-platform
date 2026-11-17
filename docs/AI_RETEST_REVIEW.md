# AI出题与复测专项复查

任务日期：2026-11-16。保留用户确认的“题库优先，缺题时由AI补题”策略。题库组卷页面已逐字节对照上一交付包恢复一致，未增加来源选择项，未改变默认组卷来源。

## 现在到底怎样出题

| 入口 | 实际题目来源 | 执行位置 |
|---|---|---|
| 题库页“AI自动组卷” | 先按条件选已有合格题；题量不够才调用AI补题 | TeacherWorkspaceView.vue发送source_policy=mixed；paper_builder_service.compose_draft |
| 学情诊断“生成并发布复测” | 根据实际薄弱知识点与课程资料调用AI生成新题，经独立验答后发布5题 | classroom_workflow → generate_questions → automatic_retest.validate_and_publish |
| 普通学习计划复测 | 从题库选同知识点、未做过、不同题族且答案就绪的题 | retest_service.choose_new_questions |
| 旧错题“生成相似题”服务 | AI依据原错题生成变式，独立求解后用于本次练习 | retest_service.generate_similar_questions |
| 旧错题混合复测服务 | 原题优先，缺额由AI生成并核验后补入 | retest_service.build_retest_paper |

后两项是保留的旧服务/API，当前教师—学生主导航没有对应独立页面。本轮对它们做了真实模型与服务检查，不冒充浏览器入口验收。候选题先保存进questions表是持久化步骤，并不意味着从已有题库抽到了旧题；可通过生成调用ID、创建记录和原快照中不存在的question_id核查。

## 修复内容

1. 旧相似题把skill返回的80字预览当作完整题目，丢失选项和真实引用，却标为已核验。现在按question_id读取完整候选，不再复制摘要；独立求解成功才建立复测卷。自动化使用超过80字的真实结构夹具覆盖截断问题。
2. 旧混合复测直接使用未核验候选。现在复用独立解题检查，失败候选不进入试卷；只按实际数量报告原题与AI题，不把失败当成功。未核验原题也不进入自动评分复测。
3. 私人相似练习的自动验答不再把题目激活到正式抽题池；完整题目、选项、来源、评分快照均可追溯。
4. 普通复测缺题时曾放宽知识点限制，用无关题凑数。现在报告缺额；verify_learning额外检查是否实际覆盖目标知识点，未覆盖不能判通过。
5. “迁移应用”仅是出题请求，不能证明独立性。相似题先保留原题族，不凭新ID把一次答对算成独立迁移通过。
6. 教师复测候选之间曾相互判重，可能两道都被淘汰。现在对照既有题库与已经接受的候选，至少保留一道合格题；重复候选ID不能凑题量。
7. 生成器校验题型和知识点是否属于本次请求。每道新候选保存生成call_id、run_id、模型与目标，发布时继续保留生成与独立验答两组调用记录。
8. 复核发现Vite代理配置直接使用process，但项目没有对应Node全局类型。改用Vite的loadEnv，保留可配置代理，类型检查通过；首次失败日志保留。

## 实际验证结果

- 后端完整167项通过；前端29项通过，类型检查与生产构建通过。见ai-retest-backend-tests.txt、ai-retest-frontend-*.txt。
- 真实模型新增19次调用，包含出题、独立验答与诊断；加上首轮61次，共80条提示词。见ai-retest-runtime-prompts.json。自动化模拟不混入此数据库。
- 教师在浏览器点击再次复测，后台生成12道候选，筛选并发布5道AI新题。发布结果记录AI生成5、题库抽取0、原题重用0；每题在本轮前的快照里都不存在，并有独立验答调用。
- STU001在浏览器实际完成第3轮，刻意答错一道LASSO判断题，得到8/10、4/5题正确。本轮达到80%阈值，原卷关联正确，L1/L2仍保留在remaining_kp_ids，不把达标误解为全部掌握。截图在ai-retest-browser。
- 旧相似题入口真实生成1道单选题，4个选项和真实引用完整，独立验答通过，服务交卷正确评分；仍保留同题族，不判为独立迁移通过。见ai-retest-real-similar.json。
- 页面、API、SQLite分数一致，verify_learning成功执行，诊断与3轮记录可回读。见ai-retest-persistence-and-api.json。
- 相比首轮快照，原128份答卷、741条作答、743条评分逐行未变。新验证在单独数据目录完成，原运行数据库未写入本轮答卷。

浏览器中途因用户消息结束了临时标签页，后台生成任务仍完成；重开页面后继续作答。前端重建时旧标签页曾引用旧文件名，刷新后加载新构建正常。没有把这些中间状态记录为业务成功。

## 恢复专项验收数据

正常启动仍恢复原始教学快照。复查本次记录可执行：

```powershell
.venv\Scripts\python.exe -X utf8 backend/scripts/restore_acceptance.py --snapshot ai-review --output data/ai-review-view
$env:MLQ_DATA_DIR=(Resolve-Path data/ai-review-view).Path
.venv\Scripts\python.exe -X utf8 backend/scripts/bootstrap_demo.py
.venv\Scripts\python.exe -X utf8 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8002
# 另一个终端设置同样的MLQ_DATA_DIR，再启动worker
.venv\Scripts\python.exe -X utf8 -m backend.app.workers.worker --interval 2
# API运行后，可重新核对本轮记录
.venv\Scripts\python.exe -X utf8 backend/scripts/verify_ai_retest_review.py --url http://127.0.0.1:8002
```

本轮无已知阻断项。真实模型仍可能生成不合格题，这些题应被淘汰或重新生成，不能承诺每次调用都发布成功。既往缺失的指定开发平台历史、成员信息和早期提示词仍未补造。
