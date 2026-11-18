"""Check real Chromium tab zoom with a temporary local extension/profile.

The extension only grants tab-zoom control in this isolated browser and is removed
on exit. No credentials/browser profiles are exported. No mock API responses.
"""
import argparse
import json
import re
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from verify_workbench_browser import settle, SCROLL_CHECK

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', default='http://127.0.0.1:8004')
parser.add_argument('--chromium', required=True)
parser.add_argument('--cycle-record', required=True)
parser.add_argument('--output', required=True)
args=parser.parse_args()
out=Path(args.output)
cycle=json.loads(Path(args.cycle_record).read_text(encoding='utf-8'))
report={'checks':[],'page_errors':[],'failures':[]}
with tempfile.TemporaryDirectory(prefix='mlq-browser-zoom-') as folder:
    root=Path(folder)
    ext=root/'extension'
    ext.mkdir()
    (ext/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Local acceptance zoom control','version':'1.0','permissions':['tabs'],'background':{'service_worker':'background.js'}}))
    (ext/'background.js').write_text('chrome.runtime.onInstalled.addListener(() => {});')
    with sync_playwright() as p:
        ctx=p.chromium.launch_persistent_context(str(root/'profile'),executable_path=args.chromium,headless=True,viewport={'width':1366,'height':768},args=[f'--disable-extensions-except={ext}',f'--load-extension={ext}'])
        worker=ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event('serviceworker')
        try:
            for label,paths in [
                ('STU001（stu001）', [('feedback','/student/feedback?attempt='+cycle['stu001']['source_attempt_id']),('tasks','/student'),('result','/exam/'+cycle['stu001']['retest_attempt_id']),('course','/learn/kp.regularization.l1l2'),('resources','/student/online')]),
                ('演示教师（teacher_demo）', [('teacher-home','/teacher'),('teacher-bank','/teacher/questions'),('teacher-single','/teacher/analysis'),('teacher-history','/teacher/analysis?scope=student_history'),('teacher-class','/teacher/analysis?scope=class_assessment'),('teacher-materials','/teacher/resources')]),
            ]:
                page=ctx.new_page()
                page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
                page.goto(args.base_url+'/login')
                page.get_by_role('combobox').click()
                page.get_by_role('option',name=label,exact=True).click()
                page.wait_for_url(re.compile('/(teacher|student)$'))
                settle(page)
                for name,path in paths:
                    page.goto(args.base_url+path)
                    settle(page)
                    for factor in [1.25,1.5,2]:
                        actual=worker.evaluate('''async ({url,factor})=>{const tabs=await chrome.tabs.query({});const tab=tabs.find(t=>t.url===url);await chrome.tabs.setZoom(tab.id,factor);return await chrome.tabs.getZoom(tab.id)}''',{'url':page.url,'factor':factor})
                        page.wait_for_timeout(200)
                        data=page.evaluate(SCROLL_CHECK)
                        data.update(page=name,requested_zoom=factor,actual_zoom=actual)
                        report['checks'].append(data)
                        assert actual==factor
                        assert data['document']==data['viewport'] and data['headers_fixed'],data
                        assert all(s['reaches_end'] and s['within_viewport'] for s in data['scrolls']),data
                        if factor==1.5:
                            page.screenshot(path=str(out/f'{name}-zoom150.png'))
                page.close()
            assert not report['page_errors'],report['page_errors']
        except Exception as exc:
            report['failures'].append(str(exc))
            page.screenshot(path=str(out/'zoom-failure.png'))
            raise
        finally:
            (out/'browser-zoom-final.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            ctx.close()
print(json.dumps({'checks':len(report['checks']),'failures':report['failures']},ensure_ascii=False))
