import json,sqlite3,re
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
from verify_workbench_browser import login,settle
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'docs/acceptance';S=OUT/'screenshots';BASE='http://127.0.0.1:8020'
r=json.loads((OUT/'run.json').read_text(encoding='utf-8'));d=sqlite3.connect(ROOT/'data/final-acceptance/db/mlquiz.sqlite3');d.row_factory=sqlite3.Row

def client(user):
 c=httpx.Client(base_url=BASE,timeout=90);c.headers['Authorization']='Bearer '+c.post('/api/auth/quick-login',json={'user_id':user}).json()['session_token'];return c

def post(c,path,payload):
 x=c.post('/api'+path,json=payload);x.raise_for_status();return x.json()

def save(): (OUT/'run.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
t=client('teacher_demo');c=client('stu001')
job=c.get('/api/student/retests/jobs/'+r['retest_job']['job_id']);job.raise_for_status();r['retest_job_result']=job.json();save()
cycle=d.execute('select * from targeted_retest_cycles where source_attempt_id=? order by round_no desc',(r['students']['stu001']['attempt_id'],)).fetchone();assert cycle
retest_id=cycle['retest_attempt_id'];r['retest_attempt_id']=retest_id;save()
second=json.loads((ROOT/'data/final-second-draft.json').read_text(encoding='utf-8'))
sid=d.execute("select student_id from students where linked_user_id='stu001'").fetchone()[0]
release=post(t,'/teacher/releases',{'paper_id':second['paper_id'],'title':second['title'],'student_ids':[sid],'idempotency_key':'final-acceptance-second-20261119'})
r['second_release']=release;save()
assignment=next(x for x in c.get('/api/student/assignments').json()['items'] if x['release_id']==release['release_id'])
second_attempt=post(c,'/student/assignments/'+assignment['recipient_id']+'/start',{});r['second_attempt_id']=second_attempt['attempt_id'];save()
with sync_playwright() as pw:
 browser=pw.chromium.launch(executable_path=r'C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 ctx,page=login(browser,BASE,'STU001（stu001）')
 for aid,recipient,shot in [(retest_id,'','10_retest.png'),(second_attempt['attempt_id'],assignment['recipient_id'],'13_imported_exam.png')]:
  page.goto(BASE+'/exam/'+aid+('?recipient='+recipient if recipient else ''));settle(page);page.screenshot(path=str(S/shot))
  a=d.execute('select * from attempts where attempt_id=?',(aid,)).fetchone()
  if a['status']=='submitted':continue
  items=d.execute('select snapshot from paper_items where paper_id=? order by position',(a['paper_id'],)).fetchall()
  for i,item in enumerate(items):
   q=json.loads(item[0]);answer=q.get('answer_payload') or {};typ=q['question_type']
   if typ=='true_false':page.get_by_text('正确' if answer.get('value') else '错误',exact=True).click()
   elif typ in ['single_choice','multi_choice','multiple_choice']:
    keys=answer.get('selected_keys',answer.get('keys',answer.get('key',answer.get('value'))));keys=keys if isinstance(keys,list) else [keys]
    for key in keys:
     page.locator('.option-item').filter(has=page.locator('.option-key',has_text=str(key)+'.')).click()
   else:
    value=answer.get('reference_answer',answer.get('text',answer.get('value','')))
    page.locator('textarea').first.fill(str(value))
   page.get_by_role('button',name='立即保存',exact=True).click()
   if i<len(items)-1:page.get_by_role('button',name='下一题',exact=True).click()
  page.get_by_role('button',name=re.compile('交卷')).first.click();page.get_by_role('button',name='确认交卷',exact=True).click();settle(page)
  print('submitted',aid,page.locator('body').inner_text()[:450],flush=True)
 page.goto(BASE+'/student/feedback?attempt='+r['students']['stu001']['attempt_id']);settle(page);page.screenshot(path=str(S/'11_progress_comparison.png'))
 ctx,tp=login(browser,BASE,'演示教师（teacher_demo）')
 av=r['release']['assessment_version_id']
 for scope,name in [('student_attempt','06_single_diagnosis.png'),('student_history','07_history_diagnosis.png'),('class_assessment','08_group_diagnosis.png')]:
  tp.goto(BASE+f'/teacher/analysis?scope={scope}&student_id={sid}&assessment_version_id={av}');settle(tp);tp.screenshot(path=str(S/name))
 tp.goto(BASE+'/teacher');settle(tp);tp.screenshot(path=str(S/'12_teacher_overview.png'))
 browser.close()
r['persisted_attempts']=[dict(x) for x in d.execute('select attempt_id,status,score,max_score from attempts where attempt_id in (?,?,?,?)',(r['students']['stu001']['attempt_id'],r['students']['stu002']['attempt_id'],retest_id,r['second_attempt_id']))];save()
