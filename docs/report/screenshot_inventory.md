# 全界面截图清单

显示修复：T08/T20已替换为修复后实拍，旧问题截图保存在history目录；补充评分要点、试卷预览、作答前答案隔离和成绩反馈检查。前端45项测试、类型检查、生产构建通过；偏差—方差定义、相邻章节与两道L2公式选项已核对并创建新版本；仅展示库修订。浏览器核验见visible_content_verification.json，版本依据见visible_content_revision.json。

本轮使用真实Chromium页面与1366×768视口，在隔离实例http://127.0.0.1:8021采集。数据目录为data/report-ui-20261119，由final-acceptance复制；原验收数据库未写入。所有原图保留，未改数字或拼接。

封面恢复补拍：T05、S16已用data/demo-polish-20261119隔离实例（127.0.0.1:8023）重新拍摄，显示项目已有的视频封面。教师、学生资源预览及蓝色占位见[封面恢复验收](../acceptance/cover-restore-20261119/README.md)；其余本轮截图沿用上述实例。

主报告现包含50张不同原图；本表保留早期全界面截图，AI出新题、双学生正式复测及资源预览的新图请见[最终演示截图总清单](../acceptance/FINAL_SCREENSHOT_CATALOG.md)。

## 覆盖与操作

教师实际操作：自动组卷条件→生成4题100分→预览→选择STU001→确认发布（HTTP 200）→学生任务接收→逐题保存→交卷→75/100→任务进入已提交。原有双学生75分案例、英文导入与复测证据继续保留，未混为本轮新考试。

## 清单

| 文件 | 实际页面及功能 | 对应课程要求 | 报告位置 | 测试情况 |
|---|---|---|---|---|
| [T08_question_detail.png](screenshots_full/history/20261119-180455/T08_question_detail.png) | T08_question_detail | 题库与AI生成/组卷 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [T20_ai_question_review.png](screenshots_full/history/20261119-180455/T20_ai_question_review.png) | T20_ai_question_review | 题库与AI生成/组卷 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [S19_fixed_score_feedback.png](screenshots_full/history/20261119-184030/S19_fixed_score_feedback.png) | S19_fixed_score_feedback | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [T08_question_detail.png](screenshots_full/history/20261119-184030/T08_question_detail.png) | T08_question_detail | 题库与AI生成/组卷 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [T08b_question_rubric.png](screenshots_full/history/20261119-184030/T08b_question_rubric.png) | T08b_question_rubric | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [T10_ai_bank_results.png](screenshots_full/history/20261119-184030/T10_ai_bank_results.png) | T10_ai_bank_results | 题库与AI生成/组卷 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [T12_ai_paper_preview.png](screenshots_full/history/20261119-184030/T12_ai_paper_preview.png) | T12_ai_paper_preview | 题库与AI生成/组卷 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [T19_attempt_review.png](screenshots_full/history/20261119-184030/T19_attempt_review.png) | T19_attempt_review | 多层次诊断与新题验证 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [T20_ai_question_review.png](screenshots_full/history/20261119-184030/T20_ai_question_review.png) | T20_ai_question_review | 题库与AI生成/组卷 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [T21_fixed_paper_answer.png](screenshots_full/history/20261119-184030/T21_fixed_paper_answer.png) | T21_fixed_paper_answer | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_01_teacher_home.png](screenshots_full/other/previous_01_teacher_home.png) | previous_01_teacher_home | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_02_question_bank.png](screenshots_full/other/previous_02_question_bank.png) | previous_02_question_bank | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_03_exam_publish.png](screenshots_full/other/previous_03_exam_publish.png) | previous_03_exam_publish | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_04_student_exam.png](screenshots_full/other/previous_04_student_exam.png) | previous_04_student_exam | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_05_exam_result.png](screenshots_full/other/previous_05_exam_result.png) | previous_05_exam_result | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_06_single_diagnosis.png](screenshots_full/other/previous_06_single_diagnosis.png) | previous_06_single_diagnosis | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_07_history_diagnosis.png](screenshots_full/other/previous_07_history_diagnosis.png) | previous_07_history_diagnosis | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_08_group_diagnosis.png](screenshots_full/other/previous_08_group_diagnosis.png) | previous_08_group_diagnosis | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_09_resource_recommendation.png](screenshots_full/other/previous_09_resource_recommendation.png) | previous_09_resource_recommendation | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_10_retest.png](screenshots_full/other/previous_10_retest.png) | previous_10_retest | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_11_progress_comparison.png](screenshots_full/other/previous_11_progress_comparison.png) | previous_11_progress_comparison | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_13_imported_exam.png](screenshots_full/other/previous_13_imported_exam.png) | previous_13_imported_exam | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_14_course_resource.png](screenshots_full/other/previous_14_course_resource.png) | previous_14_course_resource | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_student1-passed-1366.jpg](screenshots_full/other/previous_student1-passed-1366.jpg) | previous_student1-passed-1366 | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [previous_student2-four-of-five.jpg](screenshots_full/other/previous_student2-four-of-five.jpg) | previous_student2-four-of-five | 考试作答及闭环 | 归档；未重复插入 | 历史原图归档；history目录为显示修复前问题截图 |
| [S02_my_tasks.png](screenshots_full/student/S02_my_tasks.png) | 学生我的任务接收考试 | 考试作答及闭环 | 图6-16 | 本轮浏览器实拍，页面数据真实 |
| [S03_task_submitted.png](screenshots_full/student/S03_task_submitted.png) | 交卷后任务状态更新 | 考试作答及闭环 | 图6-17 | 本轮浏览器实拍，页面数据真实 |
| [S04_exam_details.png](screenshots_full/student/S04_exam_details.png) | S04_exam_details | 考试作答及闭环 | 归档；未重复插入 | 过渡状态，未用于正文；考试信息由任务卡展示，无独立详情页 |
| [S05_exam_answering.png](screenshots_full/student/S05_exam_answering.png) | S05_exam_answering | 考试作答及闭环 | 归档；未重复插入 | 专项复测答题，与S17相同画面；普通考试作答见原图4-3 |
| [S06_submit_confirmation.png](screenshots_full/student/S06_submit_confirmation.png) | 交卷前确认窗口 | 考试作答及闭环 | 图6-19 | 真实专项复测交卷确认，本次未确认交卷；普通考试实际交卷另有75/100结果 |
| [S07_exam_score.png](screenshots_full/student/S07_exam_score.png) | 新增考试成绩与错题 | 考试作答及闭环 | 图6-20 | 本轮浏览器实拍，页面数据真实 |
| [S08_wrong_answers.png](screenshots_full/student/S08_wrong_answers.png) | 学生错题分析 | 多层次诊断与新题验证 | 图6-21 | 本轮浏览器实拍，页面数据真实 |
| [S09_diagnosis.png](screenshots_full/student/S09_diagnosis.png) | S09_diagnosis | 多层次诊断与新题验证 | 归档；未重复插入 | 本轮浏览器实拍，页面数据真实 |
| [S10_resource_recommendation.png](screenshots_full/student/S10_resource_recommendation.png) | S10_resource_recommendation | 学习资源与推荐 | 归档；未重复插入 | 本轮浏览器实拍，页面数据真实 |
| [S11_course_reading.png](screenshots_full/student/S11_course_reading.png) | 课程原页定位阅读 | 学习资源与推荐 | 图6-24 | 本轮浏览器实拍，页面数据真实 |
| [S12_retest_history.png](screenshots_full/student/S12_retest_history.png) | 学生复测记录抽屉 | 多层次诊断与新题验证 | 图6-27 | 本轮浏览器实拍，页面数据真实 |
| [S13_retest_answer_review.png](screenshots_full/student/S13_retest_answer_review.png) | 已提交复测评分结果 | 多层次诊断与新题验证 | 图6-28 | 本轮浏览器实拍，页面数据真实 |
| [S14_retest_feedback.png](screenshots_full/student/S14_retest_feedback.png) | 知识点复测反馈 | 多层次诊断与新题验证 | 图6-29 | 本轮浏览器实拍，页面数据真实 |
| [S15_history_records.png](screenshots_full/student/S15_history_records.png) | 学生考试历史记录 | 考试作答及闭环 | 图6-18 | 本轮浏览器实拍，页面数据真实 |
| [S16_student_online.png](screenshots_full/student/S16_student_online.png) | 学生在线资源列表与视频封面 | 学习资源与推荐 | 图6-25 | 封面恢复后在隔离实例重新实拍；真实视频封面已加载，无封面资源保留蓝色占位 |
| [S17_retest_answering.png](screenshots_full/student/S17_retest_answering.png) | 专项复测实际答题 | 多层次诊断与新题验证 | 图6-26 | 本轮浏览器实拍，页面数据真实 |
| [S18_reference_hidden.png](screenshots_full/student/S18_reference_hidden.png) | S18_reference_hidden | 考试作答及闭环 | 归档；未重复插入 | 本轮浏览器实拍，页面数据真实 |
| [S19_fixed_score_feedback.png](screenshots_full/student/S19_fixed_score_feedback.png) | S19_fixed_score_feedback | 考试作答及闭环 | 归档；未重复插入 | 本轮浏览器实拍，页面数据真实 |
| [T01_teacher_home.png](screenshots_full/teacher/T01_teacher_home.png) | 教师教学总览 | 考试作答及闭环 | 图6-1 | 本轮浏览器实拍，页面数据真实 |
| [T02_materials.png](screenshots_full/teacher/T02_materials.png) | 课程资料管理 | 学习资源与推荐 | 图6-2 | 本轮浏览器实拍，页面数据真实 |
| [T03_import_dialog.png](screenshots_full/teacher/T03_import_dialog.png) | 资料导入设置 | 学习资源与推荐 | 图6-3 | 本轮浏览器实拍，页面数据真实 |
| [T04_english_import.png](screenshots_full/teacher/T04_english_import.png) | 英文试卷资料检索 | 学习资源与推荐 | 图6-4 | 本轮浏览器实拍，页面数据真实 |
| [T05_online_resources.png](screenshots_full/teacher/T05_online_resources.png) | 教师在线资源列表与视频封面 | 学习资源与推荐 | 图6-5 | 封面恢复后在隔离实例重新实拍；真实视频封面已加载，无封面资源保留蓝色占位 |
| [T06_material_preview.png](screenshots_full/teacher/T06_material_preview.png) | T06_material_preview | 学习资源与推荐 | 归档；未重复插入 | 此前课程原页预览，原图保留；报告图6-6现采用新拍的R02 |
| [T07_question_bank.png](screenshots_full/teacher/T07_question_bank.png) | 正式题库与筛选 | 题库与AI生成/组卷 | 图6-7 | 本轮浏览器实拍，页面数据真实 |
| [T08_question_detail.png](screenshots_full/teacher/T08_question_detail.png) | 题目详情与章节调整 | 题库与AI生成/组卷 | 图6-8 | 展示修复后重新实拍；参考答案与评分规则正常；偏差—方差内容已按规范定义核对修订 |
| [T08b_question_rubric.png](screenshots_full/teacher/T08b_question_rubric.png) | T08b_question_rubric | 考试作答及闭环 | 归档；未重复插入 | 本轮浏览器实拍，页面数据真实 |
| [T10_ai_bank_results.png](screenshots_full/teacher/T10_ai_bank_results.png) | 已有AI题筛选结果 | 题库与AI生成/组卷 | 图6-9 | 本轮浏览器实拍，页面数据真实 |
| [T11_ai_paper_config.png](screenshots_full/teacher/T11_ai_paper_config.png) | AI自动组卷条件设置 | 题库与AI生成/组卷 | 图6-12 | 本轮浏览器实拍，页面数据真实 |
| [T12_ai_paper_preview.png](screenshots_full/teacher/T12_ai_paper_preview.png) | 自动组卷结果与预览 | 题库与AI生成/组卷 | 图6-13 | 本轮浏览器实拍，页面数据真实 |
| [T13_publish_students.png](screenshots_full/teacher/T13_publish_students.png) | 选择学生并发布考试 | 考试作答及闭环 | 图6-14 | 本轮浏览器实拍，页面数据真实 |
| [T14_published.png](screenshots_full/teacher/T14_published.png) | 发布后的考试记录 | 考试作答及闭环 | 图6-15 | 本轮浏览器实拍，页面数据真实 |
| [T15_student_attempt.png](screenshots_full/teacher/T15_student_attempt.png) | T15_student_attempt | 多层次诊断与新题验证 | 归档；未重复插入 | 本轮浏览器实拍，页面数据真实 |
| [T16_student_history.png](screenshots_full/teacher/T16_student_history.png) | 教师单人多卷诊断 | 多层次诊断与新题验证 | 图6-22 | 本轮浏览器实拍，页面数据真实 |
| [T17_class_assessment.png](screenshots_full/teacher/T17_class_assessment.png) | T17_class_assessment | 多层次诊断与新题验证 | 归档；未重复插入 | 本轮浏览器实拍，页面数据真实 |
| [T18_retest_management.png](screenshots_full/teacher/T18_retest_management.png) | 教师复测进度与结果 | 多层次诊断与新题验证 | 图6-30 | 本轮浏览器实拍，页面数据真实 |
| [T19_attempt_review.png](screenshots_full/teacher/T19_attempt_review.png) | 教师查看学生答卷 | 多层次诊断与新题验证 | 图6-23 | 本轮浏览器实拍，页面数据真实 |
| [T20_ai_question_review.png](screenshots_full/teacher/T20_ai_question_review.png) | 偏差—方差规范定义题修订结果 | 题库与AI生成/组卷 | 图6-10 | 展示修复后重新实拍；参考答案与评分规则正常；偏差—方差内容已按规范定义核对修订 |
| [T20b_bias_explanation.png](screenshots_full/teacher/T20b_bias_explanation.png) | T20b_bias_explanation | 考试作答及闭环 | 归档；未重复插入 | 本轮浏览器实拍，页面数据真实 |
| [T21_fixed_paper_answer.png](screenshots_full/teacher/T21_fixed_paper_answer.png) | T21_fixed_paper_answer | 考试作答及闭环 | 归档；未重复插入 | 本轮浏览器实拍，页面数据真实 |
| [T22_corrected_paper_preview.png](screenshots_full/teacher/T22_corrected_paper_preview.png) | T22_corrected_paper_preview | 考试作答及闭环 | 归档；未重复插入 | 本轮浏览器实拍，页面数据真实 |
| [T23_l2_formula_options.png](screenshots_full/teacher/T23_l2_formula_options.png) | L2正则化公式选项修复结果 | 考试作答及闭环 | 图6-11 | 本轮浏览器实拍，页面数据真实 |
| [T24_l2_formula_options.png](screenshots_full/teacher/T24_l2_formula_options.png) | T24_l2_formula_options | 考试作答及闭环 | 归档；未重复插入 | 本轮浏览器实拍，页面数据真实 |

## 不能单独提供的页面及限制

- T09：旧截图流程未含独立AI出题配置页；新入口及真实生成过程见[演示验收](../acceptance/demo-polish-20261119/ACCEPTANCE_AND_SCREENSHOTS.md)的A01—A08。
- T10：旧自动组卷题库足量，没有触发模型补题；新独立AI出题与旧组卷是两次操作，分别保存证据。
- T06是此前课程原页预览图，原图保留作备选；正文图6-6现采用R02新截图，视频预览另见[封面恢复验收](../acceptance/cover-restore-20261119/README.md)。
- S01与S02：学生首页即我的任务；S03待完成状态见S02，S03原图保存的是交卷后的状态。S04没有独立考试介绍页，信息在任务卡，点击直接进入答题。
- S06展示另一份未提交复测的真实确认窗口；新增自动组卷考试已真实交卷，75/100落库。两者未冒充为同一时刻。
- S05/S17为同一次复测答题，正文只插入S17；普通考试原图4-3保留。S04过渡截图只归档。
- 早期正式复测截图位于other目录，图注保留所属历史专项。不能将练习完成升级为知识点达标。

## 复现

capture_full.py为基础截图函数，capture_actions.py、capture_publish.py、capture_student.py等保存实际探索过程。部分脚本含写操作，不可对正式环境直接重跑；失败尝试亦保留。截图函数等待加载与动画结束，发现过渡图后补拍。
