"""Read teacher's three diagnosis scopes after the real student retests."""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path('backend/scripts').resolve()))
from verify_workbench_browser import login, settle

ROOT = Path.cwd()
OUT = ROOT / 'docs/acceptance/demo-polish-20261119'
BASE = 'http://127.0.0.1:8022'
RUN = json.loads((ROOT / 'docs/acceptance/run.json').read_text(encoding='utf-8'))
STUDENTS = {
    'stu001': 'student_5a8c57d381a25cb3b0d46e568b4c327c3b6c4b95',
    'stu002': 'student_5fca5cbf9f5898f3ba6e26825834854661581ee3',
}

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe', headless=True)
    context, page = login(browser, BASE, '演示教师（teacher_demo）')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    records = []
    version = RUN['release']['assessment_version_id']
    cases = [
        ('student_attempt', STUDENTS['stu001'], 'T21_student_a_retest'),
        ('student_attempt', STUDENTS['stu002'], 'T22_student_b_retest'),
        ('student_history', STUDENTS['stu001'], 'T23_student_history_retest'),
        ('class_assessment', '', 'T24_class_retest'),
    ]
    for scope, student, name in cases:
        url = BASE + f'/teacher/analysis?scope={scope}&assessment_version_id={version}'
        if student:
            url += '&student_id=' + student
        page.goto(url)
        settle(page)
        page.wait_for_timeout(800)
        text = page.locator('body').inner_text()
        page.screenshot(path=str(OUT / 'screenshots' / (name + '.png')))
        dims = page.evaluate('({width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth,documentHeight:document.documentElement.scrollHeight})')
        page.get_by_role('button', name='查看进度与结果', exact=True).click()
        drawer = page.get_by_role('dialog', name='复测进度与学习证据')
        drawer.wait_for()
        page.wait_for_timeout(700)
        drawer_text = drawer.inner_text()
        page.screenshot(path=str(OUT / 'screenshots' / (name + '_progress.png')))
        records.append({'scope': scope, 'student': student, 'url': page.url, 'text': text[:3500], 'progress_text': drawer_text[:3500], 'geometry': dims})
    result = {'records': records, 'page_errors': errors}
    (OUT / 'teacher-retest-readback.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{'scope': r['scope'], 'student': r['student'], 'retest_mentions': [line for line in r['text'].splitlines() if '复测' in line or '达标' in line or '需加强' in line][:12], 'geometry': r['geometry']} for r in records], ensure_ascii=False, indent=2), flush=True)
    browser.close()
