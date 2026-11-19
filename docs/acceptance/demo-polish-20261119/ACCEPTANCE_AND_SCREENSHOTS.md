# 界面优化与真实演示补图（2026-11-19）

本轮在隔离数据 `data/demo-polish-20261119` 中完成。原始 `data/report-ui-20261119` 与 `data/final-acceptance` 数据库哈希与操作前一致。截图均为 1366×768 的真实浏览器页面；共 38 张，保存在同目录 `screenshots/`。

## 改动与验收结果

| 环节 | 实际结果与依据 |
| --- | --- |
| 在线资源 | 教师、学生资源卡片统一浅蓝白视觉；站内预览显示真实课件页和原站入口。`resource-checks.json` 记录三个页面无全页滚动、三张课件图加载成功、scikit-learn 正文在新窗口可读。 |
| AI 出新题 | 教师端配置后真实任务 `job_d0fc4d78486548da` 生成 3 道候选。教师发现温度题选项标号与计算不符，并补足“固定输入、变更训练集”的条件；3 题修订为 v2 后审核入库，组成 3 题草稿 `paper_d41ce0e1db5c4b97`。原候选与修订记录见 `generation-result.json`、`candidate-review.json`、`new-case.json`。 |
| 学生 A 复测 | 旧轮 5/5 含 4 道重复题，保持“练习完成”。新轮 `job_a8c0877a0951418a` 发布 2 道 AI 新题＋3 道未见过的题库题，0 道重复题；答卷 `attempt_bb7f13bb13cd4ece` 得 10/10，本轮通过，偏差—方差知识点达标，当前薄弱点从 1 个变为 0。 |
| 学生 B 复测 | `job_3b6baeedd6884aa6` 同样形成 2 道 AI 新题＋3 道题库题、0 道重复题；答卷 `attempt_393a14e38d6e42e9` 得 8/10。4/5 满足整轮通过，但 L1/L2 正则化有错答，仍显示“需加强”，当前薄弱点为 1。此例说明“整轮通过”不能直接等同“知识点达标”。 |
| 教师端 | 单人单卷、单人多卷、多人单卷均读到真实结果。班级抽屉同时显示 A 达标、B 需加强，见 `teacher-retest-readback.json`。四个教师页面均为 1366×768，无全页溢出或页面脚本错误。 |
| 无答案题 | 当前题库存在 `acceptance_noanswer_20261116`，来源为 `deepseek_generated`，答案状态为 `ai_auto_checked`；教师端 `AnswersView.vue` 保留批量生成入口。此前真实求解与独立验答记录见 `docs/FINAL_DELIVERY.md`。本轮未重新导入无答案试卷，故不将旧记录计为本轮新截图。 |

复测过程使用浏览器点击开始、逐题作答、保存、交卷及刷新。`retest-start-*.json`、`retest-complete-*.json` 同时保存任务、接口结果与 SQLite 答卷核对；`settled-screenshots.json` 保存重载后的页面文字。操作前以 SQLite 在线备份保存隔离库。两名学生的最终页面均无 JavaScript 异常。操作前后模型调用及对应提示记录由 179 条变为 197 条。

应用代码在本轮前半段完成。测试记录：后端 218 项、前端 45 项通过，类型检查和生产构建通过；文件见 `backend-tests.txt`、`frontend-tests.txt`、`typecheck.txt`、`frontend-build.txt`。本次接续仅增加验收脚本、运行记录与截图，没有再改评分或复测算法。

## 截图清单

| 分类 | 文件 | 画面 | 建议用途 |
| --- | --- | --- | --- |
| AI 出题 | [A01_ai_configuration.png](screenshots/A01_ai_configuration.png) | 知识点、题型、数量及出题要求 | 正文 |
| AI 出题 | [A02_ai_running.png](screenshots/A02_ai_running.png) | 后台生成中 | PPT 过程页 |
| AI 出题 | [A03_ai_candidates.png](screenshots/A03_ai_candidates.png) | 原始候选与解析，供展示审核必要性 | 正文或 PPT |
| AI 出题 | [A04_candidate_revision.png](screenshots/A04_candidate_revision.png) | 教师修订错误答案 | PPT |
| AI 出题 | [A05_candidate_review_sources.png](screenshots/A05_candidate_review_sources.png) | 审核时查看评分要点和来源 | 备选，内容区域已滚动 |
| AI 出题 | [A06_ai_reviewed_results.png](screenshots/A06_ai_reviewed_results.png) | 3 道题审核入库 | 备选，与 A08 相近 |
| AI 出题 | [A07_ai_paper_preview.png](screenshots/A07_ai_paper_preview.png) | 已审核题进入组卷草稿 | 正文 |
| AI 出题 | [A08_approved_question_detail.png](screenshots/A08_approved_question_detail.png) | 修订后完整题干、答案和解析 | 正文 |
| 资源 | [R01_teacher_online.png](screenshots/R01_teacher_online.png) | 教师在线资源列表；封面恢复后重拍 | 备选 |
| 资源 | [R02_teacher_resource_preview.png](screenshots/R02_teacher_resource_preview.png) | 教师预览真实 L1/L2 课件页 | 正文、PPT |
| 资源 | [R03_student_online.png](screenshots/R03_student_online.png) | 学生在线资源列表；封面恢复后重拍 | 备选 |
| 资源 | [R04_recommended_resource_preview.png](screenshots/R04_recommended_resource_preview.png) | 从薄弱点推荐打开站内预览 | 正文 |
| 资源 | [R05_course_page_reader.png](screenshots/R05_course_page_reader.png) | 点击课程原页后的阅读器 | PPT |
| 资源 | [R06_external_resource_open.png](screenshots/R06_external_resource_open.png) | scikit-learn 原站正文真实打开 | 备选证据 |
| 学生复测 | [B_stu001_01_before.png](screenshots/B_stu001_01_before.png) | A 的原卷弱点与旧“练习完成” | 正文 |
| 学生复测 | [B_stu001_02_preparing.png](screenshots/B_stu001_02_preparing.png) | A 的新题准备中 | 备选 |
| 学生复测 | [B_stu001_03_new_questions.png](screenshots/B_stu001_03_new_questions.png) | A 的复测题已出现 | PPT |
| 学生复测 | [B_stu001_04_answered.png](screenshots/B_stu001_04_answered.png) | A 作答 5/5 | 备选 |
| 学生复测 | [B_stu001_05_submit_confirmation.png](screenshots/B_stu001_05_submit_confirmation.png) | A 交卷确认 | 备选 |
| 学生复测 | [B_stu001_06_score.png](screenshots/B_stu001_06_score.png) | A 10/10 与本轮通过 | 正文、PPT |
| 学生复测 | [B_stu001_07_feedback_updated.png](screenshots/B_stu001_07_feedback_updated.png) | A 知识点达标、薄弱点归零、仍可再次复测 | 正文、PPT |
| 学生复测 | [B_stu001_08_retest_history.png](screenshots/B_stu001_08_retest_history.png) | 旧练习与新正式轮次并列 | 正文 |
| 学生复测 | [B_stu002_01_before.png](screenshots/B_stu002_01_before.png) | B 的复测前反馈 | 备选 |
| 学生复测 | [B_stu002_02_preparing.png](screenshots/B_stu002_02_preparing.png) | B 的新题准备中 | 备选 |
| 学生复测 | [B_stu002_03_new_questions.png](screenshots/B_stu002_03_new_questions.png) | B 的复测题 | 备选 |
| 学生复测 | [B_stu002_04_answered.png](screenshots/B_stu002_04_answered.png) | B 作答 5/5 | 备选 |
| 学生复测 | [B_stu002_05_submit_confirmation.png](screenshots/B_stu002_05_submit_confirmation.png) | B 交卷确认 | 备选 |
| 学生复测 | [B_stu002_06_score.png](screenshots/B_stu002_06_score.png) | B 8/10 与整轮通过 | PPT，与下一图配对 |
| 学生复测 | [B_stu002_07_feedback_updated.png](screenshots/B_stu002_07_feedback_updated.png) | B 的知识点仍需加强、资料与入口仍在 | 正文、PPT |
| 学生复测 | [B_stu002_08_retest_history.png](screenshots/B_stu002_08_retest_history.png) | B 的轮次记录 | 备选 |
| 教师复测 | [T21_student_a_retest.png](screenshots/T21_student_a_retest.png) | A 单人单卷诊断 | 备选 |
| 教师复测 | [T21_student_a_retest_progress.png](screenshots/T21_student_a_retest_progress.png) | A 的复测证据卡 | PPT |
| 教师复测 | [T22_student_b_retest.png](screenshots/T22_student_b_retest.png) | B 单人单卷诊断 | 备选 |
| 教师复测 | [T22_student_b_retest_progress.png](screenshots/T22_student_b_retest_progress.png) | B 的复测证据卡 | 备选 |
| 教师复测 | [T23_student_history_retest.png](screenshots/T23_student_history_retest.png) | 单人多卷诊断 | 备选 |
| 教师复测 | [T23_student_history_retest_progress.png](screenshots/T23_student_history_retest_progress.png) | 多卷复测按原卷分列 | 备选 |
| 教师复测 | [T24_class_retest.png](screenshots/T24_class_retest.png) | 多人单卷诊断 | 备选 |
| 教师复测 | [T24_class_retest_progress.png](screenshots/T24_class_retest_progress.png) | 同卷双学生的不同知识点结论 | 正文、PPT |

资源列表 R01、R03 已按[封面恢复验收](../cover-restore-20261119/README.md)替换。新增教师和学生的视频预览、缺图蓝色占位截图及接口验证记录均在该目录；上表其余资源预览图为此前真实课程页与推荐入口证据。

## 报告与答辩选图

主报告已选入 8 张本目录原图：**R02、R04、A01、A03、A08、A07、B_stu001_07、T24_class_retest_progress**，另选封面恢复目录的教师视频预览图。资源预览和题目详情单幅放大；B 的 `B_stu002_07` 保留为答辩对照，讲解“4/5 整轮通过，知识点仍需加强”。全部本轮原图及用途见[最终截图总清单](../FINAL_SCREENSHOT_CATALOG.md)。

答辩 PPT 用 A02、A04、R02、B_stu001_06、B_stu002_07、T24_class_retest_progress，形成“生成—审核—学习—复测—判定”六个状态。其余图作为备选或原始过程证据。截图不要单独用于证明算法正确；正文应同时引用上述 JSON 的来源计数、成绩和知识点状态。

判定口径：本轮至少 4/5 可判整轮通过；同一知识点至少两道独立新题、不同考查角度且全部答对，才判该知识点达标。达标只说明本轮表现，延后确认需 24 小时后再次通过。当前截图不声称已完成延后确认。
