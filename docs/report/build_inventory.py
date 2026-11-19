from pathlib import Path
import sys,json,hashlib,sqlite3,collections
from PIL import Image
root=Path.cwd();out=root/'docs/submission';sys.path.insert(0,str(out))
from report_content import BLOCKS
mapping=[{'figure':b[1],'path':'docs/'+b[2],'caption':b[3]} for b in BLOCKS if b[0]=='figure']
(root/'docs/report/insertions.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2),encoding='utf-8')
byfile={m['path']:m for m in mapping}
rows=[];records=[]
for p in sorted((root/'docs/report/screenshots_full').rglob('*')):
 if not p.is_file():continue
 rel=str(p.relative_to(root)).replace('\\','/');m=byfile.get(rel);old=p.parent.name=='other' or 'history' in p.parts;code=p.name.split('_')[0]
 status='本轮浏览器实拍，页面数据真实' if not old else '历史原图归档；history目录为显示修复前问题截图'
 if code in ['T08','T20'] and not old: status='展示修复后重新实拍；参考答案与评分规则正常；偏差—方差内容已按规范定义核对修订'
 if old: m=None
 if code=='S04':status='过渡状态，未用于正文；考试信息由任务卡展示，无独立详情页'
 if code=='S05':status='专项复测答题，与S17相同画面；普通考试作答见原图4-3'
 if code=='S06':status='真实专项复测交卷确认，本次未确认交卷；普通考试实际交卷另有75/100结果'
 if code in ['T05','S16'] and not old:status='封面恢复后在隔离实例重新实拍；真实视频封面已加载，无封面资源保留蓝色占位'
 if code=='T06' and not old:status='此前课程原页预览，原图保留；报告图6-6现采用新拍的R02'
 title=m['caption'] if m else p.stem
 req='题库与AI生成/组卷' if code in ['T07','T08','T10','T11','T12','T20'] else '学习资源与推荐' if code in ['T02','T03','T04','T05','T06','S10','S11','S16'] else '多层次诊断与新题验证' if code in ['T15','T16','T17','T18','T19','S08','S09','S12','S13','S14','S17'] else '考试作答及闭环'
 fig='图'+m['figure'] if m else '归档；未重复插入'
 records.append({'file':rel,'title':title,'requirement':req,'report':fig,'status':status,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':Image.open(p).size})
 rows.append(f'| [{p.name}]({p.relative_to(root/'docs/report').as_posix()}) | {title} | {req} | {fig} | {status} |')
md='''# 全界面截图清单

显示修复：T08/T20已替换为修复后实拍，旧问题截图保存在history目录；补充评分要点、试卷预览、作答前答案隔离和成绩反馈检查。前端45项测试、类型检查、生产构建通过；偏差—方差定义、相邻章节与两道L2公式选项已核对并创建新版本；仅展示库修订。浏览器核验见visible_content_verification.json，版本依据见visible_content_revision.json。

本轮使用真实Chromium页面与1366×768视口，在隔离实例http://127.0.0.1:8021采集。数据目录为data/report-ui-20261119，由final-acceptance复制；原验收数据库未写入。所有原图保留，未改数字或拼接。

封面恢复补拍：T05、S16已用data/demo-polish-20261119隔离实例（127.0.0.1:8023）重新拍摄，显示项目已有的视频封面。教师、学生资源预览及蓝色占位见[封面恢复验收](../acceptance/cover-restore-20261119/README.md)；其余本轮截图沿用上述实例。

主报告现包含50张不同原图；本表保留早期全界面截图，AI出新题、双学生正式复测及资源预览的新图请见[最终演示截图总清单](../acceptance/FINAL_SCREENSHOT_CATALOG.md)。

## 覆盖与操作

教师实际操作：自动组卷条件→生成4题100分→预览→选择STU001→确认发布（HTTP 200）→学生任务接收→逐题保存→交卷→75/100→任务进入已提交。原有双学生75分案例、英文导入与复测证据继续保留，未混为本轮新考试。

## 清单

| 文件 | 实际页面及功能 | 对应课程要求 | 报告位置 | 测试情况 |
|---|---|---|---|---|
'''+ '\n'.join(rows)+'''

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
'''
(root/'docs/report/screenshot_inventory.md').write_text(md,encoding='utf-8')
(root/'docs/report/screenshot_manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
c=sqlite3.connect('file:data/report-ui-20261119/db/mlquiz.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
paper=c.execute("select paper_id,title from papers where title like '报告截图%' order by rowid desc limit 1").fetchone()
proof={'isolated_data':'data/report-ui-20261119','paper':dict(paper),'attempts':[dict(r) for r in c.execute('select attempt_id,status,score,max_score from attempts where paper_id=?',(paper['paper_id'],))],'release':[dict(r) for r in c.execute("select release_id,title,status from assessment_releases where title like '报告截图%' ")],'original_db_sha256':hashlib.sha256((root/'data/final-acceptance/db/mlquiz.sqlite3').read_bytes()).hexdigest()}
(root/'docs/report/ui_capture_readback.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
# Report figure references are owned by the report builder; inventory is read-only toward it.
print('raw_files',len(records),'new',sum('/other/' not in r['file'] and '/history/' not in r['file'] for r in records),'insertions',len(mapping),'proof',proof)
