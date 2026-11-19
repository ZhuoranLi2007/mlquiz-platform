exec(open('docs/report/capture_full.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
r=json.load(open('docs/report/ui_capture_readback.json',encoding='utf-8'))
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）');p.goto(BASE+'/teacher/papers/'+r['paper']['paper_id']);settle(p)
 assert '查看参考答案与解析' in p.locator('body').inner_text() and '标准答案' not in p.locator('body').inner_text()
 shot(p,'T12','ai_paper_preview');b.close()
