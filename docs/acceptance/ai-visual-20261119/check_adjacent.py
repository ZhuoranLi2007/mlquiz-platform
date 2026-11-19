"""Browser spot checks for the two small adjacent UI adjustments."""
from pathlib import Path
import json
import sys
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
sys.path.insert(0, str(root / 'backend/scripts'))
from verify_workbench_browser import login, settle

base = 'http://127.0.0.1:8024'
result = {}
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe', headless=True)
    ctx, page = login(browser, base, '演示教师（teacher_demo）')
    page.goto(base + '/teacher/questions')
    settle(page)
    page.locator('.card-action.detail-action').first.click()
    dialog = page.get_by_role('dialog', name='题目详情')
    dialog.wait_for()
    page.wait_for_timeout(500)
    result['question_detail'] = page.evaluate('''() => {const e=document.querySelector('.detail-view'),r=e.getBoundingClientRect();return {stem:getComputedStyle(e.querySelector('.detail-stem')).color,body:getComputedStyle(e).color,scrollHeight:e.scrollHeight,clientHeight:e.clientHeight,bottom:r.bottom,document:[document.documentElement.scrollWidth,document.documentElement.scrollHeight]}}''')
    page.screenshot(path=str(out / 'screenshots/adjacent_question_detail.png'))
    ctx.close()

    data = json.loads((root / 'docs/acceptance/demo-polish-20261119/retest-complete-stu001.json').read_text(encoding='utf-8'))
    ctx, page = login(browser, base, 'STU001（stu001）')
    page.goto(base + '/student/feedback?attempt=' + data['source_attempt_id'])
    settle(page)
    page.get_by_role('button', name='查看复测记录', exact=True).click()
    drawer = page.get_by_role('dialog', name='复测记录')
    drawer.wait_for()
    page.wait_for_timeout(500)
    result['retest_drawer'] = drawer.evaluate('''e => {let r=e.getBoundingClientRect();return {width:r.width,right:r.right,cards:e.querySelectorAll('.round-result-card').length,scrollHeight:e.scrollHeight,clientHeight:e.clientHeight}}''')
    page.screenshot(path=str(out / 'screenshots/adjacent_retest_drawer.png'))
    ctx.close()
    browser.close()

assert result['retest_drawer']['cards'] >= 1
assert result['retest_drawer']['width'] <= 520.5
assert result['question_detail']['bottom'] <= 768
(out / 'adjacent-checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
