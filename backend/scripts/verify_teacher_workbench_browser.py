"""Real teacher UI checks; --run-model explicitly runs and saves diagnosis jobs."""
import argparse
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright, expect
from verify_workbench_browser import login, settle, SCROLL_CHECK

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--base-url', default='http://127.0.0.1:8004')
p.add_argument('--database', required=True)
p.add_argument('--cycle-record', required=True)
p.add_argument('--output', required=True)
p.add_argument('--chromium', required=True)
p.add_argument('--run-model', action='store_true')
args = p.parse_args()
out = Path(args.output)
cycle = json.loads(Path(args.cycle_record).read_text(encoding='utf-8'))
db = sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro', uri=True)
db.row_factory = sqlite3.Row
student_id = db.execute('select student_id from attempts where attempt_id=?', (cycle['stu001']['source_attempt_id'],)).fetchone()[0]
report = {'scopes':{},'layout':[],'page_errors':[],'failures':[]}
checkpoint = out/'teacher-workbench-final.json'
prior = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {}
def save():
    checkpoint.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
def geometry(page,name):
    for w,h in [(1366,768),(1280,720),(1440,900),(1920,1080),(640,720),(910,512)]:
        page.set_viewport_size({'width':w,'height':h})
        page.wait_for_timeout(200)
        observation = page.evaluate(SCROLL_CHECK)
        observation['page']=name
        report['layout'].append(observation)
        assert observation['document']==observation['viewport'] and observation['headers_fixed'], observation
        assert all(s['reaches_end'] and s['within_viewport'] for s in observation['scrolls']), observation
        if w==1366:
            page.screenshot(path=str(out/f'{name}-final-1366.png'))
    page.set_viewport_size({'width':1366,'height':768})

def wait_pdf(page, number):
    for _ in range(100):
        frame=next((f for f in page.frames if f.url.startswith('chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/')),None)
        if frame:
            try:
                if not frame.evaluate("() => document.querySelector('pdf-viewer')?.initialLoadComplete_ === true"):
                    page.wait_for_timeout(100)
                    continue
                expect(frame.locator('#pageSelector')).to_have_value(str(number),timeout=500)
                return frame
            except Exception:
                pass
        page.wait_for_timeout(100)
    raise AssertionError(f'Native PDF viewer did not render page {number}')

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path=args.chromium)
    ctx,page=login(browser,args.base_url,'演示教师（teacher_demo）')
    page.on('pageerror',lambda e:report['page_errors'].append({'message':str(e),'stack':e.stack,'page':page.url}))
    try:
        for scope,endpoint in [('student_attempt','student-attempt'),('student_history','student-history'),('class_assessment','class-assessment')]:
            query=urlencode({'scope':scope,'student_id':student_id,'assessment_version_id':cycle['assessment_version_id']})
            url=args.base_url+'/teacher/analysis?'+query
            with page.expect_response(lambda r:'/teacher/'+endpoint+'?' in r.url) as response:
                page.goto(url)
            baseline=response.value.json()
            settle(page)
            report['scopes'][scope]={'baseline':baseline}
            if scope=='student_attempt':
                assert baseline['attempt_id']==cycle['stu001']['source_attempt_id']
                assert baseline['score_summary']['awarded_score']==0
            elif scope=='student_history':
                ids=[r['attempt_id'] for r in baseline['attempts']]
                assert cycle['stu001']['source_attempt_id'] in ids and cycle['stu001']['retest_attempt_id'] in ids
                assert cycle['stu002']['source_attempt_id'] not in ids and cycle['stu002']['retest_attempt_id'] not in ids
                for row in baseline['attempts']:
                    assert db.execute('select student_id from attempts where attempt_id=?',(row['attempt_id'],)).fetchone()[0]==student_id
                assert baseline['statistics']['participated_assessments']==len(ids)
                assert len(ids)>1
            else:
                assert baseline['participation']['scored']==2 and baseline['score_overview']['average_score']==25
            if scope!='student_attempt' and args.run_model:
                old=prior.get('scopes',{}).get(scope,{})
                if old.get('job_id'):
                    report['scopes'][scope]['job_id']=old['job_id']
                else:
                    with page.expect_response(lambda r:'/teacher/diagnoses/jobs' in r.url and r.request.method=='POST') as response:
                        page.get_by_role('button',name='运行诊断',exact=True).click()
                    report['scopes'][scope]['job_id']=response.value.json()['job_id']
                    save()
                    print(scope,'job',report['scopes'][scope]['job_id'],flush=True)
                    page.locator('.run-button.is-loading').wait_for(state='hidden',timeout=300000)
                    settle(page)
                # Reopen from the same selection, requiring the saved model text.
                with page.expect_response(lambda r:'/teacher/diagnoses/latest?' in r.url and 'scope_type='+scope in r.url) as latest:
                    page.goto(url)
                saved=latest.value.json()
                settle(page)
                analysis=saved['result']['ai_analysis']
                assert analysis.get('source')=='deepseek', analysis.get('source')
                call=db.execute('select status,run_id from model_calls where call_id=?',(analysis['call_id'],)).fetchone()
                assert call and call['status']=='ok' and call['run_id']==saved['run_id']
                assert db.execute('select response_text from prompt_records where call_id=?',(analysis['call_id'],)).fetchone()[0]
                assert not analysis.get('model_status'), analysis.get('model_status')
                if scope=='student_history':
                    page.locator('.full-analysis>summary').click()
                expect(page.locator('.analysis-summary').first).to_have_text(analysis['summary'])
                report['scopes'][scope]['model_reload']={'diagnosis_id':saved['diagnosis_id'],'engine':saved['engine'],'summary':analysis['summary'],'persisted_on_reload':True}
            geometry(page,'teacher-'+scope)
            if scope=='student_history':
                page.get_by_role('tab',name=re.compile('^考试记录')).click()
                expect(page.locator('.record-row')).to_have_count(len(baseline['attempts']))
                geometry(page,'teacher-history-records')
                page.get_by_role('tab',name=re.compile('^错题 ')).click()
                geometry(page,'teacher-history-errors')
            save()
        for name,path in [('teacher-home','/teacher'),('teacher-bank','/teacher/questions'),('teacher-paper','/teacher/papers/'+cycle['paper_id']),('teacher-review','/teacher/attempts/'+cycle['stu001']['source_attempt_id']),('teacher-resources','/teacher/resources'),('teacher-online','/teacher/online')]:
            page.goto(args.base_url+path)
            settle(page)
            geometry(page,name)
        page.goto(args.base_url+'/teacher/resources')
        settle(page)
        pdf_card=page.locator('.material-card').filter(has=page.locator('.kind-pdf')).first
        pdf_card.get_by_role('button',name='在线预览',exact=True).click()
        dialog=page.get_by_role('dialog')
        expect(dialog).to_be_visible()
        iframe=page.get_by_title('课程资料阅读区')
        src=iframe.get_attribute('src')
        file_response=page.request.get(args.base_url+src.split('#')[0]) if src.startswith('/') else page.request.get(src.split('#')[0])
        assert file_response.status==200
        assert file_response.body().startswith(b'%PDF'), file_response.headers.get('content-type')
        wait_pdf(page,1)
        page.get_by_role('spinbutton',name='PDF页码').fill('2')
        page.get_by_role('spinbutton',name='PDF页码').press('Tab')
        expect(iframe).to_have_attribute('src',re.compile('#page=2$'))
        wait_pdf(page,2)
        for _ in range(12):
            page.keyboard.press('Tab')
            page.wait_for_function("!!document.activeElement.closest('.el-dialog')",timeout=3000)
        page.screenshot(path=str(out/'teacher-pdf-final-1366.png'))
        page.get_by_role('button',name='返回资源列表',exact=True).click()
        expect(dialog).to_be_hidden()
        pdf_card.get_by_role('button',name='在线预览',exact=True).click()
        page.keyboard.press('Escape')
        expect(dialog).to_be_hidden()
        report['pdf']={'response_status':200,'pdf_signature_verified':True,'page_fragment':2,'native_viewer_rendered_page':2,'tab_focus_contained':True,'return_button':True,'escape':True}
        text_card=page.locator('.material-card').filter(has=page.locator('.kind-text')).filter(has=page.get_by_role('button',name='在线预览',exact=True)).first
        if text_card.count():
            text_card.get_by_role('button',name='在线预览',exact=True).click()
            expect(dialog).to_be_visible()
            settle(page)
            expect(page.get_by_role('spinbutton',name='PDF页码')).to_have_count(0)
            text=page.locator('.material-text pre').inner_text()
            assert len(text)>0
            expect(page.locator('.material-text [role="alert"]')).to_have_count(0)
            page.screenshot(path=str(out/'teacher-text-preview-final-1366.png'),animations='disabled')
            page.get_by_role('button',name='返回资源列表',exact=True).click()
            expect(dialog).to_be_hidden()
            report['text_preview']={'actual_text_characters':len(text),'no_pdf_page_control':True,'return_button':True}
        else:
            report['text_preview']={'status':'not_available','reason':'This runtime has no readable text upload; do not count as a text preview pass.'}
        assert not report['page_errors'],report['page_errors']
    except Exception as exc:
        report['failures'].append(str(exc))
        page.screenshot(path=str(out/'teacher-verification-failure.png'))
        raise
    finally:
        save()
        browser.close()
        db.close()
print(json.dumps({'scopes':list(report['scopes']),'layout_checks':len(report['layout']),'failures':report['failures']},ensure_ascii=False))
