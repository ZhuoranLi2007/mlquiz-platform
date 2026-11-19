exec(open('docs/report/capture_full.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
r=json.load(open('docs/report/visible_content_verification.json',encoding='utf-8'))
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）');p.goto(BASE+'/teacher/papers/'+r['preview_paper_id']);settle(p)
 item=p.locator('.paper-item').first;item.locator('summary').click();item.locator('.solution').scroll_into_view_if_needed();p.wait_for_timeout(1500);shot(p,'T22','corrected_paper_preview');b.close()
