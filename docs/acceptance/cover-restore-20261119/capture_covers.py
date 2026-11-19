"""Check restored video covers in the isolated teacher and student browser UI."""
import io
import json
import sqlite3
import sys
from pathlib import Path

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path('backend/scripts').resolve()))
from verify_workbench_browser import login, settle

ROOT = Path.cwd()
OUT = ROOT / 'docs/acceptance/cover-restore-20261119'
BASE = 'http://127.0.0.1:8023'
DB = ROOT / 'data/demo-polish-20261119/db/mlquiz.sqlite3'

with sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True) as db:
    covers = db.execute("select resource_id from resources where coalesce(thumbnail,'') != '' order by resource_id").fetchall()
assert len(covers) == 9
endpoints = []
for (resource_id,) in covers:
    response = httpx.get(f'{BASE}/api/resources/{resource_id}/cover', timeout=15)
    with Image.open(io.BytesIO(response.content)) as image:
        image.verify()
    assert response.status_code == 200 and response.headers['content-type'].startswith('image/jpeg')
    endpoints.append({'resource_id': resource_id, 'status': response.status_code, 'bytes': len(response.content)})
assert httpx.get(BASE + '/api/resources/no-such-cover/cover').status_code == 404

observations = {'endpoints': endpoints, 'missing_status': 404, 'roles': {}, 'page_errors': []}
with sync_playwright() as pw:
    browser = pw.chromium.launch(
        executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',
        headless=True,
    )
    for account, role, route, code in [
        ('teacher_demo', '演示教师（teacher_demo）', '/teacher/online', 'R01_teacher_online'),
        ('stu002', 'STU002（stu002）', '/student/online', 'R03_student_online'),
    ]:
        context, page = login(browser, BASE, role)
        page.on('pageerror', lambda error: observations['page_errors'].append(str(error)))
        page.goto(BASE + route)
        settle(page)
        page.locator('.res-cover img').first.wait_for()
        page.wait_for_function("() => [...document.querySelectorAll('.res-cover img')].filter(i => i.getBoundingClientRect().height>0).slice(0,2).every(i => i.complete && i.naturalWidth>0)")
        page.wait_for_timeout(500)
        dims = page.evaluate('({width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth,documentHeight:document.documentElement.scrollHeight})')
        assert dims['documentWidth'] == dims['width'] == 1366 and dims['documentHeight'] == dims['height'] == 768
        live = page.locator('.res-cover img').evaluate_all('(images) => images.filter(i => i.complete && i.naturalWidth>0).map(i => ({src:i.getAttribute("src"),width:i.naturalWidth,height:i.naturalHeight}))')
        assert len(live) >= 2
        page.screenshot(path=str(OUT / f'{code}.png'))
        page.locator('.resource-card').filter(has=page.locator('.res-cover img')).first.get_by_role('button', name='预览资源').click()
        dialog = page.get_by_role('dialog', name='资源预览')
        dialog.wait_for()
        dialog.locator('.video-cover img').wait_for()
        dialog.locator('.video-cover img').evaluate('(i) => i.complete && i.naturalWidth>0 || new Promise((resolve,reject) => {i.onload=resolve;i.onerror=reject})')
        assert dialog.locator('.video-cover img').evaluate('(i) => i.naturalWidth>0')
        original = dialog.get_by_role('link', name='打开原站内容').get_attribute('href')
        assert original.startswith('https://www.bilibili.com/video/')
        with page.expect_popup() as popup:
            dialog.get_by_role('link', name='打开原站内容').click()
        external = popup.value
        external.wait_for_url('**/video/**', timeout=15000)
        external_url = external.url
        external.close()
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / f'{code}_video_preview.png'))
        dialog.get_by_role('button', name='完成预览').click()
        dialog.wait_for(state='hidden')
        page.locator('.category-filter button').nth(1).click()
        fallback = page.locator('.resource-card').filter(has=page.locator('.cover-fallback')).first
        fallback.wait_for()
        fallback.get_by_role('button', name='预览资源').click()
        dialog = page.get_by_role('dialog', name='资源预览')
        dialog.wait_for()
        dialog.locator('.video-cover span').first.wait_for()
        assert dialog.locator('.video-cover img').count() == 0
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUT / f'{code}_blue_fallback.png'))
        observations['roles'][account] = {
            'route': route, 'geometry': dims, 'loaded_cover_images': live,
            'original_video_url': original, 'opened_video_url': external_url,
            'blue_fallback_visible': True,
        }
        context.close()
    browser.close()

assert not observations['page_errors']
(OUT / 'browser-and-endpoints.json').write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding='utf-8')
print('covers', len(endpoints), 'roles', list(observations['roles']), 'page_errors', len(observations['page_errors']))
