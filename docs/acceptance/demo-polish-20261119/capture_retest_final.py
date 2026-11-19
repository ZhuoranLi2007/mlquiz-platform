"""Recapture settled score and history states from persisted retest records."""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path('backend/scripts').resolve()))
from verify_workbench_browser import login, settle

OUT = Path.cwd() / 'docs/acceptance/demo-polish-20261119'
BASE = 'http://127.0.0.1:8022'

with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe', headless=True)
    results = {}
    for account in ('stu001', 'stu002'):
        data = json.loads((OUT / f'retest-complete-{account}.json').read_text(encoding='utf-8'))
        context, page = login(browser, BASE, account.upper() + f'（{account}）')
        page.goto(BASE + '/exam/' + data['attempt_id'])
        settle(page)
        page.wait_for_timeout(1000)
        result_text = page.locator('body').inner_text()
        assert '确认交卷' not in result_text and ('复测' in result_text or '成绩' in result_text)
        page.screenshot(path=str(OUT / 'screenshots' / f'B_{account}_06_score.png'))
        page.goto(BASE + '/student/feedback?attempt=' + data['source_attempt_id'])
        settle(page)
        page.get_by_role('button', name='查看复测记录', exact=True).click()
        drawer = page.get_by_role('dialog', name='复测记录')
        drawer.wait_for()
        page.wait_for_timeout(700)
        drawer_text = drawer.inner_text()
        page.screenshot(path=str(OUT / 'screenshots' / f'B_{account}_08_retest_history.png'))
        results[account] = {'score_page': result_text[:1700], 'history': drawer_text[:2200]}
        context.close()
    (OUT / 'settled-screenshots.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({account: {'score_lines': data['score_page'].splitlines()[:16], 'history_lines': data['history'].splitlines()[:16]} for account, data in results.items()}, ensure_ascii=False, indent=2), flush=True)
    browser.close()
