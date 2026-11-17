# 本轮真实闭环验收快照

专项修复新增 `mlquiz-ai-retest-reviewed.sqlite3.gz`：含累计80条真实模型提示词、本次第3轮5道AI新题复测（8/10）以及旧相似题服务的真实模型验证。它在独立数据目录中形成，保留首轮所有128份答卷、741条作答和743条评分。副本清除了登录会话与密码哈希，完整性检查通过。

用 `backend/scripts/restore_acceptance.py --snapshot ai-review --output data/ai-review-view` 恢复到新目录，再按 `docs/AI_RETEST_REVIEW.md` 启动与核查。默认不指定 `--snapshot` 时仍恢复下面的首轮快照。

`mlquiz-verified.sqlite3.gz`保存2026-11-16真实浏览器新增的6份答卷、4个复测周期、61条真实模型提示词及原始匿名教学数据。完整性检查通过；只在副本中清除了登录会话和密码哈希，并执行secure_delete和VACUUM。原始`data/demo`快照保持不变。

请按照`docs/FINAL_DELIVERY.md`将它恢复到新的数据目录。恢复脚本拒绝覆盖已有数据库；bootstrap会生成本机演示登录信息。验收数据是教学演示证据，不代表真实学生已经学习掌握。

老师可对照`docs/verification/current-persistence-and-api.json`及`browser-current`截图检查评分、原卷关联、复测轮次和诊断；也可运行`backend/scripts/verify_persisted_acceptance.py --url http://127.0.0.1:8002`重新核对（设置相同的MLQ_DATA_DIR）。
