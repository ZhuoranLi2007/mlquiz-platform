# Skill: recommend_resources

- **版本**：skills-1.0/recommend_resources-1.0
- **执行函数**：backend/app/skills/implementations.py::recommend_resources
- **调用方**：诊断后台任务与LangGraph推荐节点
- **是否需要模型**：否，使用知识点ID匹配已经核对的课程页和外部资源

## 输入

```json
{"kp_ids":["kp.regularization.l1l2"],"attempt_id":"可选答卷ID"}
```

未指定kp_ids而提供attempt_id时，只取该答卷有有效评分且答错的知识点；全部答对、待评分不能被算为弱点。两者均未提供时取当前用户状态为需加强的知识点。

## 输出

result包含kp_ids、recommendations、uncovered_kp_ids、search_summary和retrieval_mode。当前retrieval_mode为curated_course_pages+verified_resources。

recommendations的type为local_course或external；包含kp_id、kp_ids、kp_name、title、url、reason、study_focus、location和核验状态。本地材料另含source_refs、suggested_minutes和success_check。缺少材料的知识点放在uncovered_kp_ids，不用不相关内容凑数。

## 选择规则

learning_resources.py为教师诊断、学生反馈和skill提供同一份资源契约。35个课程知识点优先返回对应的本地原页，80张唯一图片有文件哈希验证；再选择同知识点的已核验具体外部内容。排除测试资源、搜索入口、失效记录和缺少主题定位的泛课程。资源数量上限不截断首份知识点覆盖。

## 有效样例

请求L1/L2正则化，返回/learn/kp.regularization.l1l2，原页为相关理论课件第15、16页，并提供对照L1/L2惩罚项的阅读动作和自测标准。已核验抖音或官方文档可作为额外入口，具体数量取决于数据库可用记录。

## 边界

模型不产生URL、页码或视频时间。原页定位来自清单；外链定位来自实际核验记录。建议复习分钟数不等同视频时长或掌握承诺。新知识点和未映射历史题不能冒称已有对应材料，需补充真实映射和内容后才能推荐。
