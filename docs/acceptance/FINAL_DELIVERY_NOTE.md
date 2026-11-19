# 最后一轮界面与演示交付说明

## 本轮结果

在线资源列表与预览采用统一浅蓝白卡片；项目原有的 9 张 B 站封面可显示，缺图继续使用蓝色占位。AI 出新题、题目修订审核、入卷、学生 A/B 的正式新题复测、教师三范围读回，均沿用此前在隔离环境真实运行所得的数据和截图。本次报告同步没有重新生成题目、提交答卷或改动业务评分。

主报告现为 [Word](../submission/course_report_detailed.docx) 和 [PDF](../submission/course_report_detailed.pdf) 两种格式，共 64 页、50 张不同原图、6 张技术图、59 项目录。图 6-6 已换成教师资源预览，新图 6-31—6-38 展示教师视频预览、学生推荐预览、AI 出题与正式复测。正文把旧练习、此前正式专项和新隔离演示分开说明。

## 关键运行证据

- [真实演示验收](demo-polish-20261119/ACCEPTANCE_AND_SCREENSHOTS.md)：教师生成 3 道 AI 候选并修订审核入库；A 的新轮 2 道 AI 新题加 3 道未见题库题，10/10 且偏差—方差达标；B 的新轮 8/10，整轮通过但正则化仍需加强。任务、题源、答卷和数据库读回均在同目录的 JSON 文件中。
- [封面恢复验收](cover-restore-20261119/README.md)：9 张已有封面接口可解码为 JPEG，缺图返回 404；教师与学生预览、原站链接、蓝色占位和 1366×768 页面尺寸均有浏览器记录。外链代表案例采用可阅读的 scikit-learn 原站正文；B 站仅核对链接打开，不据此声称视频可播放。
- [最终截图总清单](FINAL_SCREENSHOT_CATALOG.md)：索引 44 个本轮截图文件，按教师端、学生端、AI 出题、复测、资源预览标注正文、答辩 PPT 或备用用途。旧全界面截图另见[原清单](../report/screenshot_inventory.md)。

## 验证与复现

后端整套测试 **219 项通过**；前端 **45 项通过**，类型检查与生产构建通过。[最终交付核对](final-delivery-check.json)确认源数据库哈希、44 个截图文件、9 张封面、两端浏览器记录和报告一致。报告验证记录在 [report_verification.json](../submission/report_verification.json) 和 [report-check.json](cover-restore-20261119/report-check.json)：50 张图源与 Word 内嵌图一致，PDF 的 59 项目录页码无错配；新增图所在页和横向对照表两页已渲染检查。

实现上，封面接口从运行环境配置的 `ASSET_DIR/covers` 读取文件；列表与预览组件按图片加载结果切换真实封面或蓝色占位。报告由 `report_content.py` 提供正文与图源、`build_detailed_report.py` 排版，再从 Word 渲染 PDF 并核对图源哈希和目录。这样改动的是显示与交付材料，原始成绩和题目历史仍可追溯。
