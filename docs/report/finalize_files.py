from pathlib import Path
p=Path('docs/submission/verify_report.py');s=p.read_text(encoding='utf-8')
s=s.replace("QA = ROOT / '.artifact-output/report-rewrite'", "QA = ROOT / '.artifact-output/report-full'\nFIGURE_QA = ROOT / '.artifact-output/report-rewrite'")
s=s.replace('课程要求与项目实现对照表.docx','课程要求与项目实现对照表_全界面版.docx').replace("matrix-release/课程要求与项目实现对照表.pdf","matrix-final/课程要求与项目实现对照表_全界面版.pdf")
s=s.replace('len(toc) == 50','len(toc) == 56').replace('35 <= len(reader.pages) <= 45','len(reader.pages) >= 35')
s=s.replace("(QA / 'figure_manifest.json')", "(FIGURE_QA / 'figure_manifest.json')").replace('== 15','== 41').replace('len(manifest) == 21','len(manifest) == 47')
s=s.replace("'screenshots_unique': 15", "'screenshots_unique': 41").replace("'new_application_tests_or_model_calls_for_report': False", "'new_unit_tests_or_model_calls_for_report': False, 'new_isolated_ui_exam': True")
s=s.replace("rendered_pages('release'", "rendered_pages('delivery'").replace("rendered_pages('matrix-release'", "rendered_pages('matrix-final'")
s=s.replace("'报告重写说明.txt'", "'报告界面截图补充说明.txt'")
p.write_text(s,encoding='utf-8')
notes='''机器学习小测平台实验报告全界面截图补充说明

本轮基于45页详版，扩充为58页图文报告，九章总体结构保留。正文共41张不同原图、6张技术图、13张表、2段真实源码；第六章按九个操作阶段展开，插入29张界面图，并引用第四、七章已经展示的同一页面，避免重复。

原图共50个文件：35张本轮浏览器截图，15张前轮报告原图归档。全部保存在docs/report/screenshots_full，按teacher、student、other分类；清单记录功能、课程要求、图号和验证范围。S04过渡状态、S05与S17同一画面等仍归档，但未重复插入正文。

补充内容包括资料管理与导入窗口、英文原文预览、教师/学生在线资源模块、题目详情与章节调整、AI题筛选和审阅、自动组卷条件与结果、选择学生发布、学生任务接收和交卷后的状态、错题详情、考试历史、教师答卷查看、双方复测记录和进度抽屉。保留分层架构、业务流程、ER和LangGraph图。

实际浏览器操作使用1366×768视口。隔离目录data/report-ui-20261119从final-acceptance复制，新增4题100分考试并发布给STU001；学生逐题保存并提交，得75/100，刷新后进入已提交记录，SQLite读回一致。新增试卷、发布及答卷ID见docs/report/ui_capture_readback.json。原验收数据库哈希未变，应用源码和旧PPT未修改。

第六章用每节一段功能与操作说明配宽图，压缩原来对截图字段的重复解说，技术计算方法仍在第四、五章。第七章继续保留原双学生、英文导入和正式复测案例，不把新增截图考试的分数或记录混入旧案例统计。已有自动化215/38项结果仍引用原日志，本次未重跑单元测试。

不足和真实界面问题：
1. 当前菜单没有独立AI出题配置页，生成条件并入组卷；T10/T20展示已有AI题。新增自动组卷题库足量，没有调用模型补题，本次未获得另一组新生成AI题的成功截图。
2. 学生首页即“我的任务”，考试信息在任务卡，点击直接进入答题；在线资源模块为卡片与外链，没有独立详情弹窗。
3. 课程原页实际加载成功；本轮未重新验证外部平台正文。英文上传/中文提取沿用既有运行记录，本轮补拍资料检索和英文原文，不冒称重新导入。
4. 交卷确认图来自未提交的专项复测；新增普通考试已正常提交，75/100落库。待做复测只打开和查看确认，没有提交空卷。
5. T20的评分规则显示[object Object]，偏差/方差题的答案解释需人工复核；T08名词解释标准答案字段不完整，解析区仍有文字。原图如实保留，未修改应用来遮盖。
6. 个别历史JPEG原图清晰度低于本轮PNG；原始图片保留比例，未锐化改字。

原报告、PDF、脚本和说明已在backups/20261119-151240-before-full-screenshots备份，附SHA256。原对照表被Word占用无法覆盖，本轮表单独保存为“课程要求与项目实现对照表_全界面版.docx”，避免强行关闭本人文档。

使用方法：先看第六章了解操作流程，再结合第四、五章回答设计与计算问题；图号对应截图清单，可按自己的实验报告需要选图。Word修改个人信息后，请更新完整目录并重新导出PDF。
'''
Path('docs/submission/报告界面截图补充说明.txt').write_text(notes,encoding='utf-8')
p=Path('docs/submission/README.txt');s=p.read_text(encoding='utf-8');p.write_text('当前交付：全界面图文版，58页，41张不同原图、6张技术图。\n主报告同名Word/PDF；本轮对照表请使用“课程要求与项目实现对照表_全界面版.docx”。\n原图与清单：../report/screenshots_full、../report/screenshot_inventory.md。\n本轮修改范围、真实操作及限制见“报告界面截图补充说明.txt”。以下为前轮历史说明：\n\n'+s,encoding='utf-8')
