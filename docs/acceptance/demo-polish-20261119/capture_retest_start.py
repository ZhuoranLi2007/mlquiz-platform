"""Start a real student retest in the isolated demo and capture its states."""
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path('backend/scripts').resolve()))
from verify_workbench_browser import login, settle

ROOT = Path.cwd()
OUT = ROOT / 'docs/acceptance/demo-polish-20261119'
BASE = 'http://127.0.0.1:8022'
SOURCES = {
    'stu001': 'attempt_fe1348e54d8543a6',
    'stu002': 'attempt_1da4e3b4d5754de2',
}


def capture(account: str):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',
            headless=True,
        )
        context, page = login(browser, BASE, account.upper() + f'（{account}）')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        source = SOURCES[account]
        page.goto(BASE + '/student/feedback?attempt=' + source)
        settle(page)
        page.screenshot(path=str(OUT / 'screenshots' / f'B_{account}_01_before.png'))
        button = page.get_by_role('button', name='尝试生成新题' if account == 'stu001' else '开始复测', exact=True)
        with page.expect_response(lambda r: '/api/student/retests/jobs' in r.url and r.request.method == 'POST') as response:
            button.click()
        queued = response.value.json()
        page.screenshot(path=str(OUT / 'screenshots' / f'B_{account}_02_preparing.png'))
        token = page.evaluate("sessionStorage.getItem('mlq_tab_session')")
        headers = {'Authorization': 'Bearer ' + token}
        for _ in range(180):
            job = context.request.get(BASE + '/api/student/retests/jobs/' + queued['job_id'], headers=headers).json()
            if job['status'] not in ('queued', 'running'):
                break
            page.wait_for_timeout(2000)
        else:
            raise RuntimeError('Retest job did not finish after six minutes')
        result = {'source_attempt_id': source, 'job_id': queued['job_id'], 'job': job, 'page_errors': errors}
        if job['status'] == 'succeeded' and job.get('result', {}).get('status') == 'published':
            page.wait_for_url(re.compile(r'/exam/'), timeout=30000)
            settle(page)
            result['attempt_id'] = page.url.split('/exam/')[1].split('?')[0]
            result['student_exam_text'] = page.locator('body').inner_text()[:1200]
            page.screenshot(path=str(OUT / 'screenshots' / f'B_{account}_03_new_questions.png'))
        (OUT / f'retest-start-{account}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(account, job['status'], job.get('result', {}).get('status'), result.get('attempt_id'), flush=True)
        browser.close()


if __name__ == '__main__':
    capture(sys.argv[1])
