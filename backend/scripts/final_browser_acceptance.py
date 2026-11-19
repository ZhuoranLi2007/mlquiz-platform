"""Real Chromium pages and authenticated APIs; no mocked responses or scores."""
import sys,json,sqlite3,re
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright
from verify_workbench_browser import login,settle
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'docs/acceptance';SHOTS=OUT/'screenshots';SHOTS.mkdir(parents=True,exist_ok=True)
BASE='http://127.0.0.1:8020'
def client(user):
 c=httpx.Client(base_url=BASE,timeout=90);r=c.post('/api/auth/quick-login',json={'user_id':user});r.raise_for_status();c.headers['Authorization']='Bearer '+r.json()['session_token'];return c
def post(c,path,data):
 r=c.post('/api'+path,json=data);r.raise_for_status();return r.json()
db=sqlite3.connect(ROOT/'data/final-acceptance/db/mlquiz.sqlite3');db.row_factory=sqlite3.Row
teacher=client('teacher_demo');draft=json.loads((ROOT/'data/final-draft.json').read_text(encoding='utf-8'))
students=[dict(r) for r in db.execute("select * from students where display_code in ('STU001','STU002')")]
record={'method':'Browser login and answer controls; real API composition/publication; SQLite readback','students':{}}
release=post(teacher,'/teacher/releases',{'paper_id':draft['paper_id'],'title':draft['title'],'student_ids':[s['student_id'] for s in students],'idempotency_key':'final-acceptance-20261119-main'})
record['release']=release
(OUT/'run.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
with sync_playwright() as pw:
 browser=pw.chromium.launch(executable_path=r'C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 ctx,tp=login(browser,BASE,'演示教师（teacher_demo）');tp.screenshot(path=str(SHOTS/'01_teacher_home.png'))
 tp.goto(BASE+'/teacher/questions');settle(tp);tp.screenshot(path=str(SHOTS/'02_question_bank.png'))
 tp.goto(BASE+'/teacher');settle(tp);tp.screenshot(path=str(SHOTS/'03_exam_publish.png'))
 for idx,user in enumerate(['stu001','stu002']):
  c=client(user);assign=c.get('/api/student/assignments').json()['items'];assignment=next(r for r in assign if r['release_id']==release['release_id']);attempt=post(c,'/student/assignments/'+assignment['recipient_id']+'/start',{})
  ctx,page=login(browser,BASE,('STU001（stu001）' if idx==0 else 'STU002（stu002）'))
  page.goto(BASE+'/exam/'+attempt['attempt_id']+'?recipient='+assignment['recipient_id']);settle(page)
  if idx==0:page.screenshot(path=str(SHOTS/'04_student_exam.png'))
  print('EXAM CONTROLS',page.locator('body').inner_text()[-2000:],flush=True)
  for i,q in enumerate(attempt['questions']):
   answer=json.loads(db.execute('select answer_payload from questions where question_id=? and question_version=?',(q['question_id'],q['question_version'])).fetchone()[0])
   value=answer.get('value',answer.get('answer'))
   if i==idx:value=not value
   page.get_by_text('正确' if value else '错误',exact=True).click()
   page.get_by_role('button',name='立即保存',exact=True).click()
   if i<len(attempt['questions'])-1:page.get_by_role('button',name='下一题',exact=True).click()
  page.get_by_role('button',name=re.compile('交卷')).first.click()
  print('SUBMIT',page.locator('body').inner_text()[-1400:],flush=True)
  confirm=page.get_by_role('button',name=re.compile('确认交卷|确认提交'))
  if confirm.count():confirm.first.click()
  settle(page)
  record['students'][user]={'attempt_id':attempt['attempt_id'],'source_attempt_id':attempt['attempt_id'],'recipient_id':assignment['recipient_id']}
  (OUT/'run.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
  if idx==0:page.screenshot(path=str(SHOTS/'05_exam_result.png'))
 browser.close()
