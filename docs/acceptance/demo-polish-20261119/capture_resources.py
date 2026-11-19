exec(open('docs/acceptance/demo-polish-20261119/capture_ai.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
import re
record={'page_errors':[],'layouts':[],'preview_images':[]}
def layout(p,name):
 sizes=p.evaluate('({width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth,documentHeight:document.documentElement.scrollHeight})');record['layouts'].append({'page':name,**sizes});assert sizes['documentHeight']<=sizes['height']+2 and sizes['documentWidth']<=sizes['width']+2,sizes

def ready_image(d):
 img=d.locator('.page-image img');img.wait_for();img.evaluate('(e)=>e.complete&&e.naturalWidth>0 || new Promise((resolve,reject)=>{e.onload=resolve;e.onerror=reject})');assert img.evaluate('(e)=>e.naturalWidth>0');record['preview_images'].append(img.get_attribute('src'))
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）');p.on('pageerror',lambda e:record['page_errors'].append(str(e)))
 p.goto(BASE+'/teacher/online');settle(p);layout(p,'teacher_online');shot(p,'R01_teacher_online.png')
 p.get_by_placeholder('搜索标题、知识点').fill('正则化');p.locator('.resource-card').filter(has_text='scikit-learn').get_by_role('button',name='预览资源',exact=True).click();d=p.get_by_role('dialog');ready_image(d)
 d.locator('.el-select').click();p.get_by_role('option',name='L1/L2 正则化',exact=True).click();ready_image(d);shot(p,'R02_teacher_resource_preview.png')
 href=d.get_by_role('link',name='打开原站内容').get_attribute('href');record['external_url']=href
 with p.expect_popup() as popup:d.get_by_role('link',name='打开原站内容').click()
 ext=popup.value
 try:
  ext.wait_for_load_state('domcontentloaded',timeout=60000);ext.get_by_role('heading',name='1.1.11. Logistic regression',exact=False).first.wait_for(timeout=30000);ext.wait_for_timeout(2000);shot(ext,'R06_external_resource_open.png');record['external_readable']=True;record['external_title']=ext.title();record['external_final_url']=ext.url
 except Exception as e:
  record['external_readable']=False;record['external_error']=type(e).__name__+':'+str(e)[:180]
 ext.close();d.get_by_role('button',name='完成预览',exact=True).click()
 c,p=login(b,BASE,'STU002（stu002）');p.on('pageerror',lambda e:record['page_errors'].append(str(e)))
 p.goto(BASE+'/student/online');settle(p);layout(p,'student_online');shot(p,'R03_student_online.png')
 r=json.load(open('docs/acceptance/run.json',encoding='utf-8'));attempt=r['students']['stu002']['attempt_id'];p.goto(BASE+'/student/feedback?attempt='+attempt);settle(p)
 p.locator('.learning-resource-list .diagnosis-resource-link').first.click();d=p.get_by_role('dialog');ready_image(d);shot(p,'R04_recommended_resource_preview.png')
 href=d.locator('.original-link').get_attribute('href');assert href.startswith('/learn/');d.locator('.original-link').click();p.wait_for_url('**/learn/**');settle(p);p.locator('.source-image img').wait_for();p.locator('.source-image img').evaluate('(e)=>e.complete&&e.naturalWidth>0||new Promise(r=>e.onload=r)');layout(p,'course_reader');shot(p,'R05_course_page_reader.png')
 b.close()
assert not record['page_errors'],record['page_errors']
(OUT/'resource-checks.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(record,ensure_ascii=False),flush=True)
