"""Read-only UI regression for student feedback; uses real login, API data and saved attempts.
Run from repository root with Playwright installed; no model calls or answer submissions.
"""
import argparse
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright, expect
from verify_workbench_browser import login, settle, SCROLL_CHECK


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8004')
    parser.add_argument('--database', required=True)
    parser.add_argument('--cycle-record', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--chromium', required=True)
    args = parser.parse_args()
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    cycle = json.loads(Path(args.cycle_record).read_text(encoding='utf-8'))
    db = sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    snapshot = lambda: [tuple(r) for r in db.execute('select attempt_id,student_id,score,max_score,status,grading_status,retest_of_attempt_id from attempts order by attempt_id')]
    before = snapshot()
    report = {'time': datetime.now(timezone.utc).isoformat(), 'students': {}, 'layouts': [], 'errors': [], 'failures': [], 'mocked_responses': False}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=args.chromium)
        try:
            for account, expected_score, group in [('stu001', 0, 'secure'), ('stu002', 50, 'review')]:
                ctx, page = login(browser, args.base_url, f'{account.upper()}（{account}）')
                page.on('pageerror', lambda e: report['errors'].append(str(e)))
                source_id = cycle[account]['source_attempt_id']
                url = args.base_url+'/student/feedback?attempt='+source_id
                with page.expect_response(lambda r:r.url.endswith('/student/feedback/'+source_id)) as response:
                    page.goto(url)
                payload = response.value.json(); settle(page)
                wb = payload['diagnosis_workbench']
                source = dict(db.execute('select attempt_id,student_id,score from attempts where attempt_id=?', (source_id,)).fetchone())
                retest = dict(db.execute('select attempt_id,student_id,score,retest_of_attempt_id from attempts where attempt_id=?', (cycle[account]['retest_attempt_id'],)).fetchone())
                assert source['score'] == payload['score_summary']['awarded_score'] == expected_score
                assert source['student_id'] == retest['student_id'] and retest['retest_of_attempt_id'] == source_id
                expect(page.locator('.workbench-metric')).to_have_count(4)
                expect(page.locator('.workbench-metric').nth(0).locator('strong')).to_contain_text(str(expected_score))
                expect(page.locator('.workbench-metric').nth(2).locator('strong')).to_contain_text(str(wb['current_weak_concept_count']))
                expect(page.locator(f'.concept-group.{group} button')).to_have_count(2)
                shot = lambda name: page.screenshot(path=str(out/f'{account}-{name}.png'), animations='disabled')
                shot('feedback-1366')
                checks = []
                for concept in wb['concepts']:
                    page.locator('.concept-list button').filter(has_text=concept['name']).click()
                    expect(page.locator('.concept-detail>header')).to_contain_text(concept['name'])
                    expect(page.locator('.next-advice')).to_contain_text(concept['action'])
                    footer = page.locator('.retest-action')
                    expect(footer).to_contain_text(concept['retests'][-1]['label'])
                    footer_box = footer.bounding_box()
                    assert footer_box['y']+footer_box['height'] <= 768
                    links = page.locator('.compact .diagnosis-resource-link')
                    expected_links = list(dict.fromkeys(r['url'] for r in concept['resources']))
                    more = page.locator('.compact>button')
                    if more.count():
                        more.click()
                    assert links.evaluate_all('(els)=>els.map(e=>e.getAttribute("href"))') == expected_links
                    assert all(r['target']=='_blank' for r in links.evaluate_all('(els)=>els.filter(e=>/^https?:/.test(e.getAttribute("href"))).map(e=>({target:e.target}))'))
                    notes = page.locator('.resource-notes>summary').first
                    if notes.count():
                        notes.click(); expect(page.locator('.resource-notes').first).to_have_attribute('open','')
                        notes.click()
                    errors = page.locator('.detail-fold').nth(0)
                    errors.locator(':scope>summary').click()
                    expect(errors.locator('.question-card')).to_have_count(len(concept['errors'])+len(concept['retests'][-1]['wrong_items']))
                    for item in concept['errors']+concept['retests'][-1]['wrong_items']:
                        expect(errors).to_contain_text(item['stem'])
                    errors.locator(':scope>summary').click()
                    correction = page.locator('.detail-fold').nth(1)
                    correction.locator(':scope>summary').click()
                    expect(correction).to_contain_text(concept['success_check'])
                    correction.locator(':scope>summary').click()
                    history = page.locator('.detail-fold').nth(2)
                    history.locator(':scope>summary').click()
                    expect(history.locator('.concept-round')).to_have_count(len(concept['retests']))
                    history.locator(':scope>summary').click()
                    checks.append({'name':concept['name'], 'resource_links':expected_links, 'errors':len(concept['errors']), 'latest_retest_errors':len(concept['retests'][-1]['wrong_items']), 'footer_visible':True})
                # Original wrong-question navigation, full reference evidence and return to selected concept.
                chosen = page.locator('.concept-detail>header>span').inner_text()
                page.locator('.concept-list button').filter(has_text=wb['concepts'][0]['name']).click()
                page.locator('.detail-fold').nth(0).locator(':scope>summary').click()
                page.get_by_role('button', name=f"定位第 {wb['concepts'][0]['errors'][0]['position']} 题 ↗", exact=True).click()
                expect(page.get_by_role('tab', name='错题详情', exact=True)).to_have_attribute('aria-selected','true')
                expect(page.locator('.question-scroll .question-card')).to_have_count(wb['statistics']['wrong_count'])
                shot('wrong-details-1366')
                page.get_by_role('tab',name='学情诊断',exact=True).click()
                # Select another real exam then return; original concept selection must survive.
                page.locator('.concept-list button').filter(has_text=chosen).click()
                page.locator('.feedback-heading .el-select').click()
                with page.expect_response(lambda r:'/student/feedback/attempt_' in r.url) as other_response:
                    page.get_by_role('option').filter(has_not_text=cycle['title']).first.click()
                alternate = other_response.value.json(); settle(page)
                assert alternate['attempt_id'] != source_id
                expect(page.locator('.workbench-metric').nth(0).locator('strong')).to_contain_text(str(int(alternate['score_summary']['awarded_score'])))
                if not alternate['retest_history']:
                    expect(page.locator('.retest-action')).to_contain_text('等待教师发布')
                    expect(page.locator('.retest-action .el-button')).to_have_count(0)
                page.locator('.feedback-heading .el-select').click()
                page.get_by_role('option').filter(has_text=cycle['title']).first.click(); settle(page)
                expect(page.locator('.concept-detail>header>span')).to_have_text(chosen)
                # Drawer keyboard and exact student's existing retest route.
                page.get_by_role('button',name='查看复测记录',exact=True).click()
                drawer = page.get_by_role('dialog'); expect(drawer).to_be_visible()
                for _ in range(15):
                    page.keyboard.press('Tab'); assert drawer.evaluate('(e)=>e.contains(document.activeElement)')
                page.keyboard.press('Escape'); expect(drawer).to_be_hidden()
                page.locator('.retest-action .el-button').click()
                page.wait_for_url('**/exam/'+cycle[account]['retest_attempt_id']+'?recipient=*'); settle(page)
                expect(page.locator('body')).to_contain_text(f"{int(retest['score'])}")
                page.goto(url); settle(page)
                # Real local course link: check actual page image loads, then return.
                page.locator('.compact a[href^="/learn/"]').first.click(); settle(page)
                page.wait_for_function('()=>[...document.images].some(i=>i.naturalWidth>100)')
                page.go_back(); settle(page)
                for width,height in [(1366,768),(1280,720),(1440,900),(1920,1080),(640,720),(910,512)]:
                    page.set_viewport_size({'width':width,'height':height})
                    observation = page.evaluate(SCROLL_CHECK)
                    observation['account'] = account
                    assert observation['document'] == observation['viewport'], observation
                    assert observation['headers_fixed'], observation
                    assert all(s['reaches_end'] and s['within_viewport'] for s in observation['scrolls']), observation
                    if width >= 1280:
                        button = page.locator('.retest-action .el-button')
                        rect = button.bounding_box()
                        assert rect['y'] >= 0 and rect['y']+rect['height'] <= height
                        assert button.evaluate('(e)=>{let r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}')
                    overflow = page.locator('.concept-list button,.retest-action,.compact-resource,.workbench-metric').evaluate_all('(es)=>es.filter(e=>e.getBoundingClientRect().width&&e.scrollWidth>e.clientWidth+2).map(e=>e.className)')
                    assert not overflow, overflow
                    report['layouts'].append(observation)
                page.set_viewport_size({'width':1366,'height':768}); shot('feedback-1366')
                report['students'][account] = {'source':source,'retest':retest,'concepts':checks,'exam_switch':alternate['attempt_id'],'alternate_no_retest':not alternate['retest_history'],'drawer_keyboard':True,'wrong_navigation':True,'retest_route':True,'course_image_loaded':True}
                multi = db.execute('select source_attempt_id from targeted_retest_cycles where student_id=? group by source_attempt_id having count(*)>1 order by count(*) desc limit 1', (source['student_id'],)).fetchone()
                if multi:
                    with page.expect_response(lambda r:r.url.endswith('/student/feedback/'+multi[0])) as multi_response:
                        page.goto(args.base_url+'/student/feedback?attempt='+multi[0])
                    multi_data = multi_response.value.json(); settle(page)
                    expect(page.locator('.concept-group.review button')).to_have_count(multi_data['diagnosis_workbench']['current_weak_concept_count'])
                    page.locator('.detail-fold').nth(2).locator(':scope>summary').click()
                    expect(page.locator('.concept-round')).to_have_count(len(multi_data['diagnosis_workbench']['concepts'][0]['retests']))
                    report['students'][account]['multi_round'] = {'source_attempt_id':multi[0], 'rounds':len(multi_data['retest_history']), 'latest_status':multi_data['retest_history'][-1]['status'], 'weak_concepts':multi_data['diagnosis_workbench']['current_weak_concept_count']}
                    shot('multi-round-1366')
                no_retest = db.execute("select attempt_id from attempts where student_id=? and status='submitted' and is_effective=1 and (mode is null or mode<>'retest') and attempt_id not in (select source_attempt_id from targeted_retest_cycles) order by submitted_at desc limit 1", (source['student_id'],)).fetchone()
                if no_retest:
                    with page.expect_response(lambda r:r.url.endswith('/student/feedback/'+no_retest[0])) as empty_response:
                        page.goto(args.base_url+'/student/feedback?attempt='+no_retest[0])
                    no_retest_data = empty_response.value.json(); settle(page)
                    assert not no_retest_data['retest_history']
                    expect(page.locator('.retest-action')).to_contain_text('等待教师发布')
                    expect(page.locator('.retest-action .el-button')).to_have_count(0)
                    report['students'][account]['no_retest'] = {'source_attempt_id':no_retest[0], 'waiting_for_teacher':True}
                    shot('no-retest-1366')
                ctx.close()
            assert snapshot() == before, 'Saved attempt records changed'
            report['attempt_records_unchanged'] = True
            assert not report['errors'], report['errors']
        except Exception as error:
            report['failures'].append(str(error)); page.screenshot(path=str(out/'failure.png'), animations='disabled'); raise
        finally:
            (out/'browser-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2),encoding='utf-8')
            browser.close(); db.close()
    print(json.dumps({'students':len(report['students']),'layouts':len(report['layouts']),'failures':report['failures']},ensure_ascii=False))


if __name__ == '__main__':
    main()
