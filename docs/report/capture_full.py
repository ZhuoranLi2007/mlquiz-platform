from pathlib import Path
import sys,json
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path('backend/scripts').resolve()))
from verify_workbench_browser import login,settle
ROOT=Path.cwd(); OUT=ROOT/'docs/report/screenshots_full';BASE='http://127.0.0.1:8021'
def shot(p,code,name):
 settle(p); p.wait_for_timeout(1000); p.screenshot(path=str(OUT/('teacher' if code.startswith('T') else 'student')/(code+'_'+name+'.png')))
 print('SHOT',code,name,flush=True)
def dump(p): print(p.locator('body').inner_text()[-9000:],flush=True)
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）')
 shot(p,'T01','teacher_home')
 p.goto(BASE+'/teacher/resources');shot(p,'T02','materials')
 p.get_by_role('button',name='导入资料',exact=True).first.click();shot(p,'T03','import_dialog')
 p.goto(BASE+'/teacher/online');shot(p,'T05','online_resources')
 p.goto(BASE+'/teacher/questions');shot(p,'T07','question_bank')
 dump(p)
 p.get_by_role('button',name='开始组卷',exact=True).click();shot(p,'T11','ai_paper_config');dump(p)
 b.close()
