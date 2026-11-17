# 学情诊断与学习资源专项复查

日期：2026-11-17。保留原框架、页面结构和题库优先策略。教师的常规自动组卷仍是题库优先、缺题才让AI补；诊断页的针对性复测仍调用AI生成同知识点新题并独立验答。此次没有修改TeacherWorkspaceView.vue。

## 确认的问题与修复

| 问题 | 最终行为与实现 |
|---|---|
| AI评语混杂事实、推测和笼统建议，重点不明确 | diagnosis_coaching.py按单卷失分、多卷重复错误、班级错误人次排序，先显示3个重点，其余可展开。分列作答依据、AI解读、复习动作和自测标准。日期来自实际考试；8或10分钟仅为建议用时。 |
| 有效但分散的班级错误被解释成缺失作答 | 区分distributed_answers与真正缺失/无效作答；没有共同错误模式时不猜共性错因。 |
| 截断证据会遗漏后面的高失分或多知识点错误 | 全量错题参与优先级与覆盖，模型只看经过排序的代表性证据。未映射题逐题保留，不能合并成一个虚构概念。 |
| 泛课程、搜索入口或无关知识点资源被推荐 | learning_resources.py统一教师、学生与recommend_resources skill的资源选择。35个课程知识点各有对应原课件页；已核验外链作为补充，不让AI编URL、页码、视频时间。 |
| 推荐列表只显示前3项，遗漏其他知识点 | LearningResourceList保留全部知识点，前6项加展开入口。课程精读页展示实际原页、重点、自测标准。80张唯一课件图随包提供，离线可读。 |
| 长AI调用容易被前端短超时打断 | 三范围诊断接入teacher_diagnosis后台任务。页面显示进度，运行中按钮禁用，同请求并发只入队一次；失败原因保留，付费诊断中断后不偷偷自动重跑。 |
| 模型数学反斜杠破坏JSON，或把内部证据ID写进面向学生的文字 | 只修复明确数学片段内的JSON转义并记录修复标记，保存原始响应。提示词限制内部ID及无条件公式；逐条接受有效解读，剩余依据真实作答生成具体复习指引。 |

课程原页中朴素贝叶斯省略归一化分母的写法容易误读。精读页加了阅读说明：P(c|x)为后验，P(x|c)为类条件概率/似然；省略共同分母仅用于比较分类得分。核对来源为[scikit-learn官方朴素贝叶斯说明](https://scikit-learn.org/stable/modules/naive_bayes.html)。原PDF内容未改写。

## 真实浏览器和模型验证

三种范围均由教师浏览器点击运行，经真实worker执行，结果入库后由接口回读核对。服务使用本地已有DeepSeek配置，真实响应模型名为deepseek-flash。

| 范围 | 实际观察 | 任务记录 |
|---|---|---|
| 单人单卷 | STU001原卷0/100，两项错题分别解释L1/L2及过拟合，给出具体复习动作。学生反馈内容与教师保存分析一致。第3轮8/10保留，L1/L2仍为待复习项。 | job_1c8a2448d1024b5a |
| 多人单卷 | 两名已评分学生平均25/100，最高50、最低0；过拟合2/2人答错，L1/L2为1/2人。有效分散错误不再写成作答缺失。 | job_5c2e956b624248b7 |
| 单人多卷 | STU001的17次考试，日期2025-09-22至2026-11-16；89个已评分题次、27次错答、24道不同错题。展示交叉验证、K-means、BP等优先级及可展开重点。 | job_d71b67c0a7804af9 |

本轮新增5次真实模型调用，累计85条真实请求/响应。第一次多卷返回中数学反斜杠导致JSON解析失败，job_677038ad97f54070如实保留invalid_output与规则分析；后续修复和重试成功，没有把失败改成成功。第二次多卷解读出现内部证据ID和过于绝对的公式说明，进一步收紧提示词和逐条校验后再次真实验证。自动化替身不混入真实提示词记录。

学生浏览器实际执行“查看原卷→学习反馈→课程精读”，原页公式可见；抖音L1/L2视频实际打开并播放。B站和帖子的前轮浏览器证据保留，本轮没有声称重新逐一播放全部外链。前端构建替换期间旧标签曾请求旧文件名，刷新后学生登录与反馈正常。

截图：verification/diagnosis-resource-browser中的single-ai.jpg、class-ai-full.jpg、history-ai.jpg、student-feedback.jpg、student-course.jpg、course-original.jpg、douyin-entry.jpg。

## 自动化和持久化核对

- 后端完整182项通过；前端30项通过；类型检查、生产构建通过。日志以diagnosis-开头。
- 35个材料API和课程入口、82次图片请求全部成功，覆盖80张唯一图片，逐一与清单SHA-256一致；未知知识点返回404，不冒充已有资源。
- 每个最终诊断的Job、SkillRun、ModelCall、PromptRecord、Diagnosis关系对应；recommend_resources也有独立成功skill记录。详见diagnosis-persistence-and-api.json。
- 对上一专项快照逐行比较：832条题目版本、130份答卷、747条作答、749条评分全部保持原值。所有本轮诊断写入data/diagnosis-review隔离副本。
- 真实提示词导出为diagnosis-runtime-prompts.json；原61条和80条导出继续保留，便于区分阶段。

## 启动和复查

正常启动仍用项目根目录start.ps1，详见FINAL_DELIVERY.md。离线课程页已在包内，无需模型密钥即可打开。需要重新调用AI时，在本机.env配置已有模型，包内不含密钥。

恢复本次结果到新目录：

```powershell
.venv\Scripts\python.exe -X utf8 backend/scripts/restore_acceptance.py --snapshot diagnosis-review --output data/diagnosis-view
$env:MLQ_DATA_DIR=(Resolve-Path data/diagnosis-view).Path
.venv\Scripts\python.exe -X utf8 backend/scripts/bootstrap_demo.py
.venv\Scripts\python.exe -X utf8 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8003
# 另一个终端设置相同MLQ_DATA_DIR后启动worker
.venv\Scripts\python.exe -X utf8 -m backend.app.workers.worker --interval 2
# 服务启动后只回读现有结果，不额外调用模型
.venv\Scripts\python.exe -X utf8 backend/scripts/verify_diagnosis_resources.py --url http://127.0.0.1:8003
```

教师选择STU001和“验收20261116-基础双知识点-A”，或单人多卷的“全部历史考试”；多人单卷选择同一基础卷。点击运行诊断会产生新模型调用，仅复查已有证据无需再次点击。

## 全新解压验证

最终运行文件已在无.env的新目录解压并启动实际uvicorn和worker，354个素材文件恢复，前端资源、教师登录、诊断快照恢复和全部课程原页检查通过；恢复脚本拒绝覆盖已有数据库。该检查复用本轮按锁文件安装的Python环境，不冒充在第二台电脑重新安装验证。记录见verification/diagnosis-package-smoke.json。

## 学习要点与真实边界

把功能分成三层更容易定位问题：数据库保存可核查的作答事实，AI解释具体概念，页面把依据、解释和行动分层展示。资源推荐依据知识点ID匹配已核对的材料；页面好看或接口200都不能证明内容正确，还要回读原卷关联、评分和调用记录。

此次范围内没有遗留的已知功能阻断。35个已映射课程知识点均有资源，但个别历史题缺少可靠知识点映射，仍显示原题依据与具体复习提示，不猜测映射或宣称已有匹配资源。外部平台可能日后限制访问，随包课件原页是可用的基础入口。AI解读仍需内容校验，不能承诺每次输出都正确。过去缺失的指定开发平台历史、早期提示词和真实成员材料仍未获得，明确保留缺项，不补造。
