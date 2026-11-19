exec(open('docs/report/capture_full.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
r=json.load(open('docs/acceptance/run.json',encoding='utf-8'))
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'STU001（stu001）');p.goto(BASE+'/student/feedback?attempt='+r['students']['stu001']['attempt_id']);settle(p);p.get_by_role('button',name='查看复测记录',exact=True).click();shot(p,'S12','retest_history')
 b.close()
