exec(open('docs/acceptance/demo-polish-20261119/capture_ai.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
import re,sqlite3,statistics
job=json.load(open(OUT/'generation-result.json',encoding='utf-8'));ids=[q['question_id'] for q in job['result']['result']['candidates']]
def readq(qid):
 with sqlite3.connect('file:data/demo-polish-20261119/db/mlquiz.sqlite3?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row;q=dict(db.execute('select * from questions where question_id=? order by question_version desc limit 1',(qid,)).fetchone());return q
specs={}
for qid in ids:
 q=readq(qid)
 if '温度' in q['stem']:x=[20,22,24,26];f=25;noise=1;stem='在平方损失回归中，固定同一环境输入 $x$。同一学习方法在4种等概率训练集上训练，所得模型对该输入的温度预测依次为 $20,22,24,26$ ℃。真实回归函数值 $f(x)=25$ ℃，不可约噪声方差为 $1$ ℃²。按上述训练集分布计算，偏差平方、方差与期望平方损失分别为：';key='B'
 elif '农业' in q['stem']:x=[100,102,98,104,96];f=100;noise=4;stem='在平方损失回归中，固定同一试验田输入 $x$。同一学习方法在5种等概率训练集上训练，所得模型对该输入的产量预测依次为 $100,102,98,104,96$ kg。真实回归函数值 $f(x)=100$ kg，不可约噪声方差为 $4$ kg²。按上述训练集分布计算，偏差平方与方差分别为：';key='A'
 else:x=[200,210,220];f=215;noise=25;stem='在平方损失回归中，固定同一住房输入 $x$。同一学习方法在3种等概率训练集上训练，所得模型对该输入的房价预测依次为 $200,210,220$ 万元。真实回归函数值 $f(x)=215$ 万元，不可约噪声方差为 $25$ 万元²。按上述训练集分布计算，下列对偏差平方、方差和不可约噪声的描述正确的是：';key='A'
 specs[qid]={'stem':stem,'key':key,'mean':statistics.mean(x),'bias_squared':(statistics.mean(x)-f)**2,'variance':statistics.pvariance(x),'noise':noise,'before_version':q['question_version'],'original_answer':json.loads(q['answer_payload']),'old_stem':q['stem']}
# Inspect the actual mathematics before clicking the teacher approval control.
assert sorted((s['bias_squared'],round(s['variance'],2)) for s in specs.values())==[(0,8),(4,5),(25,66.67)]
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）');p.goto(BASE+'/teacher/questions');settle(p)
 saved=json.load(open(ROOT/'data/demo-polish-20261119/private/teacher-browser.json',encoding='utf-8'));entries=[v for origin in saved['origins'] for v in origin['localStorage'] if v['name'].startswith('mlq_ai_question_job:')];p.evaluate('(entries)=>entries.forEach(e=>localStorage.setItem(e.name,e.value))',entries)
 p.get_by_role('button',name='AI出新题',exact=True).click();d=p.get_by_role('dialog',name='AI出新题',exact=True);d.locator('.candidate-list button').first.wait_for()
 for i,(qid,spec) in enumerate(specs.items()):
  if readq(qid)['review_status']=='verified':continue
  marker='农业' if '试验田' in spec['stem'] else '温度' if '温度' in spec['stem'] else '房价'
  d.locator('.candidate-list button').filter(has_text=marker).click();d.get_by_role('button',name='修订内容',exact=True).click();ed=p.get_by_role('dialog',name='修订候选题',exact=True)
  ed.locator('label').filter(has_text='题干').locator('textarea').fill(spec['stem'])
  ed.locator('label').filter(has_text='参考答案').locator('.el-select').click();p.get_by_role('option',name=spec['key'],exact=True).click()
  ed.get_by_placeholder('记录核对依据').fill('按等概率训练集的有限分布独立计算均值、偏差平方和总体方差；补齐固定输入与更换训练集条件。温度题答案应为B，与解析4+5+1=10一致。')
  if marker=='温度':shot(p,'A04_candidate_revision.png')
  with p.expect_response(lambda r:'/api/admin/questions/' in r.url and '/revise' in r.url) as resp:ed.get_by_role('button',name='保存新版本',exact=True).click()
  assert resp.value.status==200;ed.wait_for(state='hidden');p.wait_for_timeout(600)
  q=readq(qid);assert json.loads(q['answer_payload'])['key']==spec['key'] and q['question_version']>=2
  d.locator('.candidate-detail details').click()
  if marker=='农业':shot(p,'A05_candidate_review_sources.png')
  with p.expect_response(lambda r:'/approve' in r.url) as resp:d.get_by_role('button',name='审核入库',exact=True).click()
  assert resp.value.status==200;p.wait_for_timeout(1200)
  q=readq(qid);assert q['active'] and q['review_status']=='verified';spec['after_version']=q['question_version'];spec['approved']=True
 shot(p,'A06_ai_reviewed_results.png')
 d.get_by_role('button',name=re.compile('用已审核题目组卷')).click();p.wait_for_url('**/teacher/papers/**');settle(p);shot(p,'A07_ai_paper_preview.png')
 paper_id=p.url.split('/teacher/papers/')[1]
 (OUT/'new-case.json').write_text(json.dumps({'paper_id':paper_id,'question_ids':ids},ensure_ascii=False,indent=2),encoding='utf-8')
 b.close()
(OUT/'candidate-review.json').write_text(json.dumps(specs,ensure_ascii=False,indent=2),encoding='utf-8');print('reviewed and composed',paper_id,flush=True)
