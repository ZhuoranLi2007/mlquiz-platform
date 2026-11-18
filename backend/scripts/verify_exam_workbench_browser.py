"""Open an isolated acceptance assignment through UI, inspect navigation, cancel submit.

Creates or resumes a draft for the explicitly selected demo student. It never
submits an answer or changes scoring. Use only with isolated acceptance data.
"""
import argparse
import json
import re
import sqlite3
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from verify_workbench_browser import login, settle, SCROLL_CHECK

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--base-url',default='http://127.0.0.1:8004')
p.add_argument('--database',required=True)
p.add_argument('--cycle-record',required=True)
p.add_argument('--output',required=True)
p.add_argument('--chromium',required=True)
p.add_argument('--allow-open-draft',action='store_true',required=True)
args=p.parse_args()
cycle=json.loads(Path(args.cycle_record).read_text(encoding='utf-8'))
out=Path(args.output)
report={'account':'stu003','purpose':'isolated layout-only draft; deliberately not submitted','layout':[],'failures':[]}
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path=args.chromium)
    ctx,page=login(browser,args.base_url,'STU003（stu003）')
    try:
        with page.expect_response(lambda r:r.url.endswith('/student/assignments')) as response:
            page.goto(args.base_url+'/student')
        tasks=response.value.json()['items']
        settle(page)
        pending=sorted([t for t in tasks if t['status'] in ['assigned','opened']],key=lambda t:t.get('published_at') or '',reverse=True)
        index=next(i for i,t in enumerate(pending) if t['release_id']==cycle['retest_release_id'])
        task=pending[index]
        with page.expect_response(lambda r:r.url.endswith('/assignments/'+task['recipient_id']+'/start')) as started:
            page.locator('.task-card').nth(index).get_by_role('button',name=re.compile('开始答题|继续答题')).click()
        attempt=started.value.json()
        report.update(attempt_id=attempt['attempt_id'],release_id=task['release_id'],recipient_id=task['recipient_id'])
        page.wait_for_url('**/exam/*')
        settle(page)
        for w,h in [(1366,768),(1280,720),(1440,900),(1920,1080),(640,720),(683,384)]:
            page.set_viewport_size({'width':w,'height':h})
            page.wait_for_timeout(200)
            if w>760:
                page.locator('.question-sidebar .question-dot').last.click()
            else:
                page.locator('.mobile-nav-button').click()
                page.get_by_role('dialog',name='选择题目').locator('.question-dot').last.click()
                expect(page.get_by_role('dialog',name='选择题目')).to_be_hidden()
            expect(page.locator('.question-footer').get_by_role('button',name='下一题',exact=True)).to_have_count(0)
            data=page.evaluate(SCROLL_CHECK)
            data['last_question_reached']=True
            report['layout'].append(data)
            assert data['document']==data['viewport'] and data['headers_fixed'],data
            assert all(s['reaches_end'] and s['within_viewport'] for s in data['scrolls']),data
            submit=page.locator('.question-sidebar .submit-button') if w>760 else page.locator('.question-footer').get_by_role('button',name='交卷',exact=True)
            expect(submit).to_be_in_viewport()
            submit.click()
            dialog=page.get_by_role('dialog',name='确认交卷')
            expect(dialog).to_be_visible()
            expect(dialog.get_by_role('button',name='确认交卷',exact=True)).to_be_in_viewport()
            for _ in range(8):
                page.keyboard.press('Tab')
                assert dialog.evaluate('(e)=>e.contains(document.activeElement)')
            page.keyboard.press('Escape')
            expect(dialog).to_be_hidden()
            data['submit_visible']=True
            data['confirmation_keyboard_cancelled']=True
            if w==1366:
                page.screenshot(path=str(out/'active-exam-final-1366.png'))
        with sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True) as db:
            status=db.execute('select status,submitted_at,score from attempts where attempt_id=?',(attempt['attempt_id'],)).fetchone()
            assert status[0]!='submitted' and not status[1]
            report['database_after_cancel']={'status':status[0],'submitted_at':status[1],'score':status[2]}
    except Exception as exc:
        report['failures'].append(str(exc))
        page.screenshot(path=str(out/'active-exam-failure.png'))
        raise
    finally:
        (out/'active-exam-final.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
print(json.dumps({'checks':len(report['layout']),'attempt_id':report.get('attempt_id'),'failures':report['failures']},ensure_ascii=False))
