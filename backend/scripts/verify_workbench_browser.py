"""Read-only browser/SQLite verification of an existing isolated acceptance cycle.

Install Playwright and its Chromium first. This script uses actual UI login and
responses, never mocks APIs, and does not persist tokens or browser storage.
Pass --cycle-record pointing to a cycle created through the real teacher/student
workflow. Existing artifacts are observations, not substitutes for this run.
"""
import argparse
import json
import os
import re
import sqlite3
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def arguments():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url', default='http://127.0.0.1:8004')
    p.add_argument('--database', required=True)
    p.add_argument('--cycle-record', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--chromium', default=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
    return p.parse_args()


def settle(page):
    page.wait_for_load_state('networkidle')
    page.locator('.el-loading-mask:visible').first.wait_for(state='hidden', timeout=30000)


def login(browser, base, label):
    ctx = browser.new_context(viewport={'width': 1366, 'height': 768})
    page = ctx.new_page()
    page.goto(base + '/login')
    page.get_by_role('combobox').click()
    page.get_by_role('option', name=label, exact=True).click()
    page.wait_for_url(re.compile('/(student|teacher)$'))
    settle(page)
    return ctx, page


SCROLL_CHECK = """async () => {
 const rect=e=>{let r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};
 const headers=[...document.querySelectorAll('.workbench-page-header')].filter(e=>e.getBoundingClientRect().height);
 const before=headers.map(rect), results=[];
 for(const e of document.querySelectorAll('*')) {
  const s=getComputedStyle(e),r=e.getBoundingClientRect();
  if(!r.width||!r.height||!['auto','scroll'].includes(s.overflowY)||e.scrollHeight<=e.clientHeight+2)continue;
  const ancestors=[];
  for(let a=e.parentElement;a;a=a.parentElement){
   if(['auto','scroll'].includes(getComputedStyle(a).overflowY)&&a.scrollHeight>a.clientHeight+2){
    ancestors.push([a,a.scrollTop]);
    a.scrollTop+=e.getBoundingClientRect().bottom-a.getBoundingClientRect().bottom;
   }
  }
  e.scrollTop=e.scrollHeight;
  await new Promise(resolve=>requestAnimationFrame(resolve));
  const current=e.getBoundingClientRect();let visibleTop=Math.max(0,current.top),visibleBottom=Math.min(innerHeight,current.bottom);
  for(let a=e.parentElement;a;a=a.parentElement){
   if(['hidden','auto','scroll','clip'].includes(getComputedStyle(a).overflowY)){
    const ar=a.getBoundingClientRect();visibleTop=Math.max(visibleTop,ar.top);visibleBottom=Math.min(visibleBottom,ar.bottom);
   }
  }
  results.push({class:e.className,client:e.clientHeight,scroll:e.scrollHeight,top:e.scrollTop,
   reaches_end:Math.abs(e.scrollHeight-e.clientHeight-e.scrollTop)<=2,
   visible_height:visibleBottom-visibleTop,
   within_viewport:e.clientHeight>=24&&visibleBottom-visibleTop>=24&&current.bottom<=visibleBottom+2});
  e.scrollTop=0;
  for(const [a,top] of ancestors.reverse())a.scrollTop=top;
 }
 return {viewport:[innerWidth,innerHeight],document:[document.documentElement.scrollWidth,document.documentElement.scrollHeight],
  headers_fixed:headers.every((e,i)=>rect(e).every((v,j)=>Math.abs(v-before[i][j])<2)),scrolls:results};
}"""


def main():
    args = arguments()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    cycle = json.loads(Path(args.cycle_record).read_text(encoding='utf-8'))
    db = sqlite3.connect(Path(args.database).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    report = {'students': {}, 'layout': [], 'page_errors': [], 'failures': []}
    def save():
        (out/'browser-persistence-final.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with sync_playwright() as p:
        options = {'headless': True}
        if args.chromium:
            options['executable_path'] = args.chromium
        browser = p.chromium.launch(**options)
        try:
            for account, expected in [('stu001', (0, 10, 1, 0)), ('stu002', (50, 6, .6, 2))]:
                ctx, page = login(browser, args.base_url, f'{account.upper()}（{account}）')
                page.on('pageerror', lambda e: report['page_errors'].append(str(e)))
                source_id = cycle[account]['source_attempt_id']
                retest_id = cycle[account]['retest_attempt_id']
                with page.expect_response(lambda r: r.url.endswith('/student/feedback/'+source_id)) as response:
                    page.goto(args.base_url+'/student/feedback?attempt='+source_id)
                payload = response.value.json()
                settle(page)
                wb = payload['diagnosis_workbench']
                assert payload['attempt_id'] == source_id
                assert payload['score_summary']['awarded_score'] == expected[0]
                assert wb['current_weak_concept_count'] == expected[3]
                expect(page.locator('.workbench-metric').nth(2).locator('strong')).to_have_text(str(expected[3]))
                source = dict(db.execute('select attempt_id,student_id,score,status from attempts where attempt_id=?', (source_id,)).fetchone())
                retest = dict(db.execute('select attempt_id,student_id,score,max_score,status,retest_of_attempt_id,grading_status from attempts where attempt_id=?', (retest_id,)).fetchone())
                rows = db.execute('select * from targeted_retest_cycles where source_attempt_id=? and retest_attempt_id=?', (source_id,retest_id)).fetchall()
                assert len(rows) == 1
                saved = dict(rows[0])
                assert saved['student_id'] == source['student_id'] == retest['student_id']
                assert retest['retest_of_attempt_id'] == source_id
                assert source['score'] == expected[0] and retest['score'] == saved['score'] == expected[1]
                assert saved['accuracy'] == expected[2] and saved['round_no'] == 1
                assert saved['release_id'] == cycle['retest_release_id']
                skills = [dict(r) for r in db.execute("select run_id,status,engine,input_payload,output_payload from skill_runs where skill_name='verify_learning'") if json.loads(r['input_payload']).get('attempt_id') == retest_id]
                assert skills and all(r['status']=='succeeded' for r in skills)
                skill = skills[-1]
                result = json.loads(skill.pop('output_payload'))['result']
                skill.pop('input_payload')
                assert result['cycle_id'] == saved['cycle_id'] and result['accuracy'] == expected[2]
                history = payload['retest_history'][-1]
                assert history['retest_attempt_id'] == retest_id and history['accuracy'] == saved['accuracy']
                report['students'][account] = {'source':source,'retest':retest,'cycle':saved,'verify_learning':skill,'api':payload,'metrics':page.locator('.workbench-metrics').inner_text()}
                page.screenshot(path=str(out/f'{account}-feedback-final-1366.png'))
                # All rendered concepts and the corresponding current retest errors.
                for index, concept in enumerate(wb['concepts']):
                    page.locator('.concept-list button').nth(index).click()
                    expect(page.locator('.concept-detail>header')).to_contain_text(concept['name'])
                    if account == 'stu002':
                        expect(page.locator('.concept-round .question-card')).to_have_count(len(concept['retests'][-1]['wrong_items']))
                trigger = page.get_by_role('button', name='查看复测记录', exact=True)
                trigger.click()
                drawer = page.get_by_role('dialog')
                expect(drawer).to_be_visible()
                for _ in range(15):
                    page.keyboard.press('Tab')
                    assert drawer.evaluate('(e)=>e.contains(document.activeElement)')
                page.keyboard.press('Escape')
                expect(drawer).to_be_hidden()
                report['students'][account]['drawer_keyboard'] = {'tab_focus_contained':True,'escape_closes':True}
                if account=='stu001':
                    selected_name=page.locator('.concept-detail>header h2').inner_text()
                    page.locator('.feedback-heading .el-select').click()
                    alternate=page.get_by_role('option').filter(has_not_text=cycle['title']).first
                    with page.expect_response(lambda r:'/student/feedback/attempt_' in r.url) as switched:
                        alternate.click()
                    other=switched.value.json()
                    settle(page)
                    assert other['attempt_id']!=source_id
                    expect(page.locator('.view-switch')).to_contain_text(other['assessment']['title'])
                    expect(page.locator('.workbench-metric').nth(1).locator('strong')).to_contain_text(str(other['diagnosis_workbench']['statistics']['wrong_count']))
                    page.get_by_role('tab',name='错题详情',exact=True).click()
                    expect(page.locator('.question-scroll .question-card')).to_have_count(other['diagnosis_workbench']['statistics']['wrong_count'])
                    page.locator('.feedback-heading .el-select').click()
                    with page.expect_response(lambda r:r.url.endswith('/student/feedback/'+source_id)):
                        page.get_by_role('option').filter(has_text=cycle['title']).first.click()
                    settle(page)
                    page.get_by_role('tab',name='学情诊断',exact=True).click()
                    expect(page.locator('.concept-detail>header h2')).to_have_text(selected_name)
                    expect(page.locator('.workbench-metric').nth(2).locator('strong')).to_have_text(str(expected[3]))
                    report['students'][account]['exam_switch']={'alternate_attempt_id':other['attempt_id'],'scores_questions_title_updated':True,'returned_to_original':source_id,'concept_selection_retained':True}
                paths = [('feedback','/student/feedback?attempt='+source_id),('tasks','/student'),('resources','/student/online'),('course','/learn/kp.regularization.l1l2'),('result','/exam/'+retest_id)] if account == 'stu001' else []
                for name, path in paths:
                    page.goto(args.base_url+path)
                    settle(page)
                    for width,height in [(1366,768),(1280,720),(1440,900),(1920,1080),(640,720),(910,512)]:
                        page.set_viewport_size({'width':width,'height':height})
                        page.wait_for_timeout(200)
                        observation = page.evaluate(SCROLL_CHECK)
                        observation.update(page=name, account=account)
                        report['layout'].append(observation)
                        assert observation['document']==observation['viewport'], observation
                        assert observation['headers_fixed'], observation
                        assert all(s['reaches_end'] and s['within_viewport'] for s in observation['scrolls']), observation
                        if width==1366:
                            page.screenshot(path=str(out/f'{name}-final-1366.png'))
                ctx.close()
                save()
            assert not report['page_errors'], report['page_errors']
        except Exception as exc:
            report['failures'].append(str(exc))
            page.screenshot(path=str(out/'verification-failure.png'))
            raise
        finally:
            save()
            browser.close()
            db.close()
    print(json.dumps({'students':len(report['students']),'layout_checks':len(report['layout']),'failures':report['failures']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
