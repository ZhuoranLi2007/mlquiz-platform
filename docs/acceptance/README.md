# 验收截图

日期：2026-11-19。全部为真实 Chromium 运行截图，1366×768；未拼接或修改结果。测试作答仅用于功能验证，不代表真实学生学习效果。

| 截图 | 功能与操作 | 实验报告图注 |
|---|---|---|
| [01_teacher_home.png](screenshots/01_teacher_home.png) | 教师首页：教师登录后查看班级和已发布考试。 | 图1 教师教学总览 |
| [02_question_bank.png](screenshots/02_question_bank.png) | 题库及章节：修复章节关联后打开正式题库；两列题卡。 | 图2 题库中的章节与知识点 |
| [03_exam_publish.png](screenshots/03_exam_publish.png) | 考试发布：真实组卷与发布接口创建考试，教师页面查看发布记录。 | 图3 教师发布的验收考试 |
| [04_student_exam.png](screenshots/04_student_exam.png) | 学生作答：学生 A 打开新发布考试并在浏览器逐题作答。 | 图4 学生在线答题 |
| [05_exam_result.png](screenshots/05_exam_result.png) | 成绩与反馈：学生 B 提交后重新打开答卷结果。 | 图5 自动评分与错题反馈 |
| [06_single_diagnosis.png](screenshots/06_single_diagnosis.png) | 单人单卷：教师查看学生 A 第一份考试的诊断。 | 图6 单次考试学情诊断 |
| [07_history_diagnosis.png](screenshots/07_history_diagnosis.png) | 单人多卷：学生 A 完成两份考试和复测后查看历史。 | 图7 同一学生的多次作答记录 |
| [08_group_diagnosis.png](screenshots/08_group_diagnosis.png) | 多人单卷：教师查看 A、B 共同参加的考试。 | 图8 班级共性薄弱环节 |
| [09_resource_recommendation.png](screenshots/09_resource_recommendation.png) | 资源推荐：学生 B 查看薄弱知识点对应资料。 | 图9 面向薄弱知识点的学习资源 |
| [10_retest.png](screenshots/10_retest.png) | 复测作答：从新考试诊断发起复测，打开实际生成的任务。 | 图10 针对性复测作答 |
| [11_progress_comparison.png](screenshots/11_progress_comparison.png) | 复测后反馈：学生 A 复测 5/5；含重复题，因此只计练习完成。 | 图11 复测结果更新及证据边界 |
| [12_teacher_overview.png](screenshots/12_teacher_overview.png) | 最终教师总览：完成新考试、第二份考试与复测后刷新教师首页。 | 图12 教师端最终考试概况 |
| [13_imported_exam.png](screenshots/13_imported_exam.png) | 英文资料衍生试卷：学生 A 作答从英文资料导入和衍生的新试卷。 | 图13 导入资料进入学生测验 |
| [14_course_resource.png](screenshots/14_course_resource.png) | 课程原页：从推荐链接实际点击进入课程精读，确认图片加载。 | 图14 可打开的课程原页 |

组卷、发布、导入使用真实鉴权接口；登录、答题、提交、章节调整和资料打开使用浏览器。见 [验收记录](final_acceptance.md)，不要把接口操作写成全部手工点击。