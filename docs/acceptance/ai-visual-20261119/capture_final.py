"""Read the existing real AI generation job in a 1366x768 Chromium session.

No model request, approval, revision, or database mutation is made here.
"""
from pathlib import Path
import json
import shutil
import sys

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SHOTS = OUT / 'screenshots'
SHOTS.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / 'backend/scripts'))
from verify_workbench_browser import login, settle

BASE = 'http://127.0.0.1:8024'
saved = json.loads((ROOT / 'data/demo-polish-20261119/private/teacher-browser.json').read_text(encoding='utf-8'))
entries = [v for origin in saved['origins'] for v in origin['localStorage'] if v['name'].startswith('mlq_ai_question_job:')]
assert len(entries) == 1

before = ROOT / 'docs/acceptance/demo-polish-20261119/screenshots'
for name in ('A01_ai_configuration.png', 'A03_ai_candidates.png', 'A08_approved_question_detail.png'):
    shutil.copy2(before / name, SHOTS / ('before_' + name))

checks = {}
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe', headless=True)
    context, page = login(browser, BASE, '演示教师（teacher_demo）')
    page.goto(BASE + '/teacher/questions')
    settle(page)
    page.get_by_role('button', name='AI出新题', exact=True).click()
    dialog = page.get_by_role('dialog', name='AI出新题')
    dialog.locator('.generation-empty').wait_for()
    dialog.locator('.generation-settings .el-select').nth(1).click()
    page.get_by_role('option', name='偏差-方差均衡', exact=True).click()
    page.keyboard.press('Escape')
    dialog.get_by_placeholder('可指定应用情境、难度或考查角度').fill(json.loads(entries[0]['value'])['form']['instruction'])
    dialog.locator('.generation-results>header').click()
    page.wait_for_timeout(700)
    page.evaluate('document.fonts.ready')
    page.screenshot(path=str(SHOTS / 'after_01_configuration.png'))
    checks['initial'] = {'empty': dialog.locator('.generation-empty').count(), 'generate_enabled': dialog.get_by_role('button', name='生成新题').is_enabled()}
    dialog.get_by_role('button', name='关闭').click()
    page.evaluate('(items) => items.forEach(e => localStorage.setItem(e.name, e.value))', entries)
    page.get_by_role('button', name='AI出新题', exact=True).click()
    dialog = page.get_by_role('dialog', name='AI出新题')
    dialog.locator('.candidate-list button').first.wait_for(timeout=15000)
    page.wait_for_timeout(800)
    page.evaluate('document.fonts.ready')
    page.screenshot(path=str(SHOTS / 'after_02_candidates.png'))
    checks['candidates'] = {'count': dialog.locator('.candidate-list button').count(), 'header': dialog.locator('.generation-results header').inner_text(), 'statuses': dialog.locator('.candidate-status').all_inner_texts()}
    dialog.locator('.candidate-list button').nth(1).click()
    page.wait_for_timeout(250)
    page.screenshot(path=str(SHOTS / 'after_03_another_candidate.png'))
    checks['another'] = {'selected': dialog.locator('.candidate-list button.selected').inner_text()[:100], 'detail': dialog.locator('.candidate-detail h3').inner_text()[:130]}
    dialog.locator('.candidate-list button').first.click()
    dialog.locator('.candidate-detail').evaluate('(e) => e.scrollTop = 0')
    page.wait_for_timeout(250)
    page.screenshot(path=str(SHOTS / 'after_04_approved.png'))
    checks['layout'] = page.evaluate('''() => {
      const rect = (sel) => { const r=document.querySelector(sel).getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom} };
      const detail=document.querySelector('.candidate-detail');
      return {viewport:[innerWidth,innerHeight],document:[document.documentElement.scrollWidth,document.documentElement.scrollHeight],dialog:rect('.ai-generation-dialog'),settings:rect('.generation-settings'),list:rect('.candidate-list'),detail:rect('.candidate-detail'),detailScrollable:detail.scrollHeight>detail.clientHeight,detailScrollHeight:detail.scrollHeight,detailClientHeight:detail.clientHeight,font:getComputedStyle(detail).fontFamily,stemSize:getComputedStyle(detail.querySelector('h3')).fontSize,stemColor:getComputedStyle(detail.querySelector('h3')).color,bodyColor:getComputedStyle(detail).color}
    }''')
    browser.close()

assert checks['candidates']['count'] == 3
assert checks['layout']['document'] == checks['layout']['viewport']
assert checks['layout']['dialog']['bottom'] <= 768
assert checks['layout']['detail']['right'] <= 1366
(OUT / 'browser-checks.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(checks, ensure_ascii=False, indent=2))
