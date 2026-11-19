"""Capture the reviewed candidate with its entire stem visible after reload."""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path('backend/scripts').resolve()))
from verify_workbench_browser import login, settle

ROOT = Path.cwd()
OUT = ROOT / 'docs/acceptance/demo-polish-20261119'
BASE = 'http://127.0.0.1:8022'
saved = json.loads((ROOT / 'data/demo-polish-20261119/private/teacher-browser.json').read_text(encoding='utf-8'))
entries = [value for origin in saved['origins'] for value in origin['localStorage'] if value['name'].startswith('mlq_ai_question_job:')]

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe', headless=True)
    context, page = login(browser, BASE, '演示教师（teacher_demo）')
    page.goto(BASE + '/teacher/questions')
    settle(page)
    page.evaluate('(entries) => entries.forEach(e => localStorage.setItem(e.name,e.value))', entries)
    page.get_by_role('button', name='AI出新题', exact=True).click()
    dialog = page.get_by_role('dialog', name='AI出新题')
    dialog.get_by_text('3道候选 · 已审核3道').wait_for()
    dialog.locator('.candidate-list button').filter(has_text='试验田').click()
    detail = dialog.locator('.candidate-detail')
    detail.evaluate('(e) => e.scrollTop=0')
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / 'screenshots/A08_approved_question_detail.png'))
    result = {'candidate_count': dialog.locator('.candidate-list button').count(), 'status': dialog.locator('.generation-results header').inner_text(), 'detail_text': detail.inner_text()[:1800]}
    (OUT / 'approved-reload.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(result['status'], result['candidate_count'], flush=True)
    browser.close()
