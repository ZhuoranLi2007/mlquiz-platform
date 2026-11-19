exec(open('docs/report/capture_full.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
import sqlite3,re
con=sqlite3.connect('data/report-ui-20261119/db/mlquiz.sqlite3');con.row_factory=sqlite3.Row
r=json.load(open('docs/acceptance/run.json',encoding='utf-8'))
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'STU001（stu001）');shot(p,'S02','my_tasks')
 card=p.locator('.task-card').filter(has_text='报告截图验收');print('CARD',card.count(),flush=True)
 card.get_by_role('button',name='开始答题').click();settle(p);shot(p,'S04','exam_details');shot(p,'S05','exam_answering')
 aid=p.url.split('/exam/')[1].split('?')[0]
 a=con.execute('select * from attempts where attempt_id=?',(aid,)).fetchone()
 items=con.execute('select snapshot from paper_items where paper_id=? order by position',(a['paper_id'],)).fetchall()
 for i,item in enumerate(items):
  q=json.loads(item[0]);answer=q.get('answer_payload') or {};typ=q['question_type']
  if typ=='true_false':p.get_by_text('正确' if answer.get('value') else '错误',exact=True).click()
  elif typ in ['single_choice','multi_choice','multiple_choice']:
   keys=answer.get('selected_keys',answer.get('keys',answer.get('key',answer.get('value'))));keys=keys if isinstance(keys,list) else [keys]
   if i==0: keys=['A' if str(keys[0])!='A' else 'B']
   for key in keys:p.locator('.option-item').filter(has=p.locator('.option-key',has_text=str(key)+'.')).click()
  else:p.locator('textarea').first.fill(str(answer.get('reference_answer',answer.get('text',answer.get('value','')))))
  p.get_by_role('button',name='立即保存',exact=True).click()
  if i<len(items)-1:p.get_by_role('button',name='下一题',exact=True).click()
 p.get_by_role('button',name=re.compile('交卷')).first.click();shot(p,'S06','submit_confirmation')
 p.get_by_role('button',name='确认交卷',exact=True).click();settle(p);p.wait_for_timeout(1800);shot(p,'S07','exam_score')
 p.goto(BASE+'/student');shot(p,'S03','task_submitted')
 p.goto(BASE+'/student/feedback?attempt='+r['students']['stu001']['attempt_id']);shot(p,'S09','diagnosis')
 p.get_by_role('tab',name='错题详情').click();shot(p,'S08','wrong_answers')
 p.get_by_role('tab',name='学情诊断').click();p.get_by_role('button',name='查看复测记录',exact=True).click();shot(p,'S12','retest_history')
 p.get_by_role('button',name='关闭记录',exact=True).click();p.get_by_role('button',name='复测记录',exact=True).click();shot(p,'S14','retest_feedback')
 p.goto(BASE+'/exam/'+r['retest_attempt_id']);shot(p,'S13','retest_answer_review')
 p.goto(BASE+'/student/online');shot(p,'S16','student_online')
 c,p=login(b,BASE,'STU002（stu002）');p.goto(BASE+'/student/feedback?attempt='+r['students']['stu002']['attempt_id']);shot(p,'S10','resource_recommendation')
 p.locator('a[href*="/learn/"]').first.click();settle(p);p.wait_for_function('() => [...document.images].some(i=>i.complete&&i.naturalWidth>0)');shot(p,'S11','course_reading')
 c,p=login(b,BASE,'演示教师（teacher_demo）')
 sid=con.execute("select student_id from students where linked_user_id='stu001'").fetchone()[0];av=r['release']['assessment_version_id']
 for scope,code in [('student_attempt','T15'),('student_history','T16'),('class_assessment','T17')]:
  p.goto(BASE+f'/teacher/analysis?scope={scope}&student_id={sid}&assessment_version_id={av}');shot(p,code,scope)
 p.goto(BASE+'/teacher/attempts/'+r['students']['stu001']['attempt_id']);shot(p,'T19','attempt_review')
 p.goto(BASE+'/teacher/resources');settle(p);p.get_by_placeholder('搜索资料名称').fill('acceptance');settle(p);shot(p,'T04','english_import');dump(p)
 b.close()
