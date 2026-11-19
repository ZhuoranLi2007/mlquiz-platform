exec(open('docs/report/capture_full.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
import sqlite3
con=sqlite3.connect('data/report-ui-20261119/db/mlquiz.sqlite3');con.row_factory=sqlite3.Row
print([dict(r) for r in con.execute("select paper_id,title from papers where title like '报告截图%' ")])
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）')
 paper=con.execute("select paper_id from papers where title like '报告截图%' order by rowid desc").fetchone()[0]
 p.goto(BASE+'/teacher/papers/'+paper);settle(p);p.get_by_role('button',name='保存并发布',exact=True).click()
 d=p.get_by_role('dialog');d.locator('.el-select').first.click();p.get_by_role('option',name='STU001',exact=False).click();d.get_by_text('发布标题',exact=True).click();shot(p,'T13','publish_students')

 with p.expect_response(lambda r:'/api/teacher/releases' in r.url and r.request.method=='POST') as response:
  d.get_by_role('button',name='确认发布',exact=True).click()
 print('PUBLISH',response.value.status,flush=True)
 d.wait_for(state='hidden');p.goto(BASE+'/teacher');shot(p,'T14','published')
 c,p=login(b,BASE,'STU001（stu001）');shot(p,'S02','my_tasks');dump(p)
 b.close()
