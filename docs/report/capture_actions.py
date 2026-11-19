from pathlib import Path
import sys,json
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path('backend/scripts').resolve()))
from verify_workbench_browser import login,settle
ROOT=Path.cwd(); OUT=ROOT/'docs/report/screenshots_full';BASE='http://127.0.0.1:8021'
def shot(p,code,name):
 settle(p); p.screenshot(path=str(OUT/('teacher' if code.startswith('T') else 'student')/(code+'_'+name+'.png')))
 print('SHOT',code,name,flush=True)
def dump(p): print(p.locator('body').inner_text()[-9000:],flush=True)
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）')
 p.goto(BASE+'/teacher/questions');settle(p)
 p.get_by_role('button',name='查看详情',exact=True).first.click();shot(p,'T08','question_detail');p.get_by_role('button',name='关闭',exact=True).click()
 p.get_by_role('button',name='开始组卷',exact=True).click()
 d=p.get_by_role('dialog');d.get_by_placeholder('请输入试卷标题').fill('报告截图验收-自动组卷-20261119')
 d.get_by_role('spinbutton').first.fill('4');shot(p,'T11','ai_paper_config')
 d.get_by_role('button',name='生成并预览',exact=True).click();p.wait_for_url('**/teacher/papers/**',timeout=180000);shot(p,'T12','ai_paper_preview')
 p.get_by_role('button',name='发布给学生',exact=True).click();settle(p);dump(p)
 b.close()
