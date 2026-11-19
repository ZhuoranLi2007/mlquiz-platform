exec(open('docs/report/capture_full.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
import sqlite3
r=json.load(open('docs/acceptance/run.json',encoding='utf-8'))
con=sqlite3.connect('data/report-ui-20261119/db/mlquiz.sqlite3');con.row_factory=sqlite3.Row
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'STU001（stu001）')
 aid=con.execute("select a.attempt_id from attempts a join papers p on p.paper_id=a.paper_id where p.title like '报告截图%' order by a.rowid desc").fetchone()[0]
 p.goto(BASE+'/exam/'+aid);shot(p,'S07','exam_score')
 c,p=login(b,BASE,'演示教师（teacher_demo）')
 sid=con.execute("select student_id from students where linked_user_id='stu001'").fetchone()[0];av=r['release']['assessment_version_id']
 p.goto(BASE+f'/teacher/analysis?scope=student_history&student_id={sid}&assessment_version_id={av}');settle(p)
 p.get_by_role('button',name='查看进度与结果',exact=True).click();shot(p,'T18','retest_management')
 print(p.locator('body').inner_text()[-2000:],flush=True)
 b.close()
