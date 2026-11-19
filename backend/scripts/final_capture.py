import json,sqlite3
from pathlib import Path
from playwright.sync_api import sync_playwright
from verify_workbench_browser import login,settle
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'docs/acceptance';S=OUT/'screenshots';BASE='http://127.0.0.1:8020'
r=json.loads((OUT/'run.json').read_text(encoding='utf-8'));report={'layout':[],'page_errors':[]}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path=r'C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 ctx,page=login(b,BASE,'演示教师（teacher_demo）');page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
 for route,name in [('/teacher','01_teacher_home.png'),('/teacher/questions','02_question_bank.png'),('/teacher/resources',None),('/teacher/online',None)]:
  page.goto(BASE+route);settle(page)
  report['layout'].append({'route':route,**page.evaluate('() => ({viewport:[innerWidth,innerHeight],document:[document.documentElement.scrollWidth,document.documentElement.scrollHeight]})')})
  if name:page.screenshot(path=str(S/name))
 page.goto(BASE+'/teacher/questions');settle(page);page.get_by_role('button',name='查看详情',exact=True).first.click();page.get_by_role('button',name='保存章节',exact=True).click();page.get_by_text('章节已保存，新组卷使用更新后的版本',exact=True).wait_for();report['manual_chapter_save']=True
 ctx,sp=login(b,BASE,'STU002（stu002）');sp.goto(BASE+'/student/feedback?attempt='+r['students']['stu002']['attempt_id']);settle(sp);sp.screenshot(path=str(S/'09_resource_recommendation.png'))
 link=sp.locator('a[href*="/learn/"]').first
 link.click()
 lp=sp;settle(lp);lp.locator('img').first.wait_for();lp.wait_for_function('() => [...document.images].some(i=>i.complete&&i.naturalWidth>0)');report['course_resource_opened']=lp.url;lp.screenshot(path=str(S/'14_course_resource.png'))
 sp.goto(BASE+'/exam/'+r['students']['stu002']['attempt_id']);settle(sp);sp.screenshot(path=str(S/'05_exam_result.png'))
 b.close()
assert all(x['viewport']==x['document'] for x in report['layout']),report
(OUT/'browser-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
