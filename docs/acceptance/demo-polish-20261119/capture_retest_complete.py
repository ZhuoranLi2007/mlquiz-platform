"""Submit a real five-question retest via the student browser and read it back."""
import json
import sqlite3
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path('backend/scripts').resolve()))
from verify_workbench_browser import login, settle

ROOT = Path.cwd()
OUT = ROOT / 'docs/acceptance/demo-polish-20261119'
DB = ROOT / 'data/demo-polish-20261119/db/mlquiz.sqlite3'
BASE = 'http://127.0.0.1:8022'


def capture(account: str):
    start = json.loads((OUT / f'retest-start-{account}.json').read_text(encoding='utf-8'))
    attempt_id = start['attempt_id']
    with sqlite3.connect(DB) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('select position,snapshot from paper_items where paper_id=(select paper_id from attempts where attempt_id=?) order by position', (attempt_id,)).fetchall()
        questions = [json.loads(row['snapshot']) for row in rows]
    assert len(questions) == 5 and all(q['question_type'] in ('single_choice', 'multi_choice', 'true_false') for q in questions)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe', headless=True)
        context, page = login(browser, BASE, account.upper() + f'（{account}）')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE + '/exam/' + attempt_id)
        settle(page)
        answers = []
        for index, q in enumerate(questions):
            kind = q['question_type']
            reference = q['answer_payload'].get('key', q['answer_payload'].get('keys', q['answer_payload'].get('value')))
            selected = reference
            if account == 'stu002' and index == 1:
                selected = next(option['key'] for option in q['options'] if option['key'] != reference)
            answers.append({'question_id': q['question_id'], 'selected': selected, 'reference': reference})
            if kind == 'true_false':
                page.get_by_text('正确' if selected else '错误', exact=True).click()
            else:
                keys = selected if isinstance(selected, list) else [selected]
                for key in keys:
                    page.locator('.option-item').filter(has=page.locator('.option-key', has_text=str(key) + '.')).click()
            page.get_by_role('button', name='立即保存', exact=True).click()
            if index < len(questions) - 1:
                page.get_by_role('button', name='下一题', exact=True).click()
        page.screenshot(path=str(OUT / 'screenshots' / f'B_{account}_04_answered.png'))
        page.get_by_role('button', name='交卷').first.click()
        page.screenshot(path=str(OUT / 'screenshots' / f'B_{account}_05_submit_confirmation.png'))
        page.get_by_role('button', name='确认交卷', exact=True).click()
        settle(page)
        page.screenshot(path=str(OUT / 'screenshots' / f'B_{account}_06_score.png'))
        token = page.evaluate("sessionStorage.getItem('mlq_tab_session')")
        headers = {'Authorization': 'Bearer ' + token}
        source = start['source_attempt_id']
        page.goto(BASE + '/student/feedback?attempt=' + source)
        settle(page)
        page.screenshot(path=str(OUT / 'screenshots' / f'B_{account}_07_feedback_updated.png'))
        page.get_by_role('button', name='查看复测记录', exact=True).click()
        page.screenshot(path=str(OUT / 'screenshots' / f'B_{account}_08_retest_history.png'))
        feedback = context.request.get(BASE + '/api/student/feedback/' + source, headers=headers).json()
        state = context.request.get(BASE + '/api/student/retests/state?attempt_id=' + source, headers=headers).json()
        with sqlite3.connect(DB) as db:
            db.row_factory = sqlite3.Row
            attempt = dict(db.execute('select attempt_id,mode,status,score,max_score,grading_status,manual_pending_count,retest_of_attempt_id from attempts where attempt_id=?', (attempt_id,)).fetchone())
        result = {
            'account': account, 'source_attempt_id': source, 'attempt_id': attempt_id,
            'answers': answers, 'attempt_database': attempt, 'state': state,
            'feedback': {
                'score_summary': feedback.get('score_summary'),
                'retest_history': feedback.get('retest_history'),
                'learning_loop': feedback.get('learning_loop'),
                'current_weak_concept_count': (feedback.get('diagnosis_workbench') or {}).get('current_weak_concept_count'),
            },
            'visible_control': page.locator('.loop-control').inner_text(),
            'page_errors': errors,
        }
        (OUT / f'retest-complete-{account}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(account, attempt['score'], attempt['max_score'], state.get('round_result', {}).get('status'), result['visible_control'].replace('\n', ' | '), flush=True)
        browser.close()


if __name__ == '__main__':
    capture(sys.argv[1])
