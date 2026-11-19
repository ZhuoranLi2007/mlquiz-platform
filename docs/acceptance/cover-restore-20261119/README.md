# 在线资源视频封面恢复验收（2026-11-19）

## 原因与改动

项目已有 9 张 B 站视频封面，保存在运行数据目录的 `assets/covers`。封面接口先前固定读取项目根目录下的 `data/assets/covers`，在隔离数据实例里找不到图片，返回 404，列表随之显示蓝色占位。现在接口从配置的 `ASSET_DIR/covers` 读取，URL 与缺图时的 404 行为保持一致。没有改数据库，没有抓取或生成封面。

在线资源列表继续使用原有简洁卡片：能加载已有封面时显示图片，没有封面或图片加载失败时显示蓝色卡片。资源预览弹窗也显示可用的视频封面，同时保留课程原页预览和原站入口。

## 真实浏览器截图

截图来自 `data/demo-polish-20261119` 隔离实例，Chromium 视口均为 1366×768。

| 角色 | 在线资源列表 | 视频预览 | 无封面时蓝色占位 |
| --- | --- | --- | --- |
| 教师 | [R01_teacher_online.png](R01_teacher_online.png) | [R01_teacher_online_video_preview.png](R01_teacher_online_video_preview.png) | [R01_teacher_online_blue_fallback.png](R01_teacher_online_blue_fallback.png) |
| 学生 | [R03_student_online.png](R03_student_online.png) | [R03_student_online_video_preview.png](R03_student_online_video_preview.png) | [R03_student_online_blue_fallback.png](R03_student_online_blue_fallback.png) |

教师、学生列表图已同步到 `docs/report/screenshots_full`、前一轮验收截图和详细报告图 6-5、图 6-25。旧版图与报告备份在 `docs/submission/backups/20261119-before-cover-restore`。

## 验证

- [browser-and-endpoints.json](browser-and-endpoints.json)：9 张已有封面的接口均返回可解码的 JPEG；不存在的封面返回 404。教师和学生页面中真实封面加载完成，视频预览可见；原站视频链接实际打开，缺图显示蓝色占位。两端页面均无整体长滚动、无页面脚本错误。
- `backend/tests/test_demo_polish.py`：封面路径与缺图行为测试，连同该文件其他测试共 4 项通过。
- 前端类型检查、45 项测试及生产构建均通过。
- [report-check.json](report-check.json)：详细报告现为 64 页，Word 内嵌的资源、AI 出题与复测图均与原截图一致；图 6-5、图 6-6、图 6-25及图 6-31—6-38 的 PDF 图注页码已核对，新增图所在页已逐页检查。

复现脚本：[capture_covers.py](capture_covers.py)、[verify_report_covers.py](verify_report_covers.py)。
