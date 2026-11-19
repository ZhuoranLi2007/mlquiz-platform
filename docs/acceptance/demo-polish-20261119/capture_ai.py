from pathlib import Path
import sys,json,time
from playwright.sync_api import sync_playwright,expect
sys.path.insert(0,str(Path('backend/scripts').resolve()))
from verify_workbench_browser import login,settle
ROOT=Path.cwd();OUT=ROOT/'docs/acceptance/demo-polish-20261119';BASE='http://127.0.0.1:8022'
def shot(p,name):
 p.evaluate('document.fonts.ready');p.wait_for_timeout(700);p.screenshot(path=str(OUT/'screenshots'/name));print('SHOT',name,flush=True)
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）');p.goto(BASE+'/teacher/questions');settle(p);p.get_by_role('button',name='AI出新题',exact=True).click();d=p.get_by_role('dialog')
 d.locator('.generation-settings .el-select').nth(1).click();p.get_by_role('option',name='偏差-方差均衡',exact=True).click();p.keyboard.press('Escape')
 d.get_by_placeholder('可指定应用情境、难度或考查角度').fill('生成三道题干、情境和考查角度均不同的单选题，限定平方损失回归。分别以农业产量、传感器温度、房价预测为背景，用给定的多次预测值考查平均预测的偏差、方差及不可约噪声的区别。每题必须给出足够的数值条件和唯一答案，避免简单重复定义题；写出可复核的计算或推理解析，引用真实课程片段。')
 shot(p,'A01_ai_configuration.png')
 with p.expect_response(lambda r:'/api/teacher/ai-questions/jobs' in r.url and r.request.method=='POST') as resp:d.get_by_role('button',name='生成新题',exact=True).click()
 queued=resp.value.json();(OUT/'generation-job.json').write_text(json.dumps(queued),encoding='utf-8');shot(p,'A02_ai_running.png')
 token=p.evaluate("sessionStorage.getItem('mlq_tab_session')")
 # Poll the durable job directly while preserving exactly the same UI request.
 for _ in range(150):
  r=c.request.get(BASE+'/api/teacher/jobs/'+queued['job_id'],headers={'Authorization':'Bearer '+token}).json()
  if r['status'] not in ['queued','running']:break
  p.wait_for_timeout(2000)
 else:raise RuntimeError('AI job still running after five minutes')
 p.wait_for_timeout(2500);shot(p,'A03_ai_candidates.png')
 (OUT/'generation-result.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
 c.storage_state(path=str(ROOT/'data/demo-polish-20261119/private/teacher-browser.json'))
 print('GENERATION',r['status'],'candidates',len((r.get('result') or {}).get('result',{}).get('candidates',[])),flush=True)
 b.close()
