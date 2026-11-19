当前交付：全界面图文版，58页，41张不同原图、6张技术图。
主报告同名Word/PDF；本轮对照表请使用“课程要求与项目实现对照表_全界面版.docx”。
原图与清单：../report/screenshots_full、../report/screenshot_inventory.md。
本轮修改范围、真实操作及限制见“报告界面截图补充说明.txt”。以下为前轮历史说明：

2026-11-19 全面重写交付版
course_report_detailed.docx：45页可编辑详版，九章正文及附录。
course_report_detailed.pdf：由同一最终Word导出的对应预览；目录50项页码已核对。
课程要求与项目实现对照表.docx：2页横向六列要求、功能、源码、证据、章节及截图对照。
报告重写说明.txt：结构、实现、案例、排版和复核方法说明。
待本人补充事项.txt：个人信息、成员分工、指定开发平台与早期提示词等缺失材料。

报告使用15张不重复真实运行截图、6张架构/业务/ER/工作流图、13张表和2段真实源码。
源码基线21c58b0；最终双学生闭环、英文导入、此前正式复测和章节修复分开记录。
215项后端与38项前端测试来自既有验收日志；本次只重写报告、只读核对证据，不新增业务验收或模型调用。
报告与对照表已渲染并逐页检查。原报告及脚本保存在backups/20261119-132433-before-rewrite，附SHA256。

配套源：report_content.py（正文）、build_detailed_report.py（排版和图）、export_report_evidence.py（只读证据导出）、verify_report.py（文件与目录核对）。
report_evidence.json保存白名单证据摘要；report_verification.json保存文件哈希、图源、页码及检查结果。
渲染过程位于.artifact-output/report-rewrite/release，对照表位于matrix-release。
修改封面或正文后需在Word更新整个目录并重新导出PDF；生成脚本可使用--toc-pdf从PDF书签更新目录缓存。
旧简版和旧PPT保持原样，未随详版同步更新。以下历史统计不可替代本次报告中的明确案例范围。

2026-11-18历史版本
course_report.docx及course_report.pdf：8页，含工作台布局、学习反馈、双学生闭环及历史专项。
course_demo.pptx：8页，可编辑并含讲解备注，已逐页渲染检查。
当前自动化：后端189项、前端33项；类型检查/构建通过。真实提示词累计116条；102组尺寸和33组真实缩放检查。
普通组卷题库优先、缺题AI补；诊断专项复测沿用AI新题及独立验答。
完整当前对照、启动与边界：../WORKBENCH_ACCEPTANCE.md。
