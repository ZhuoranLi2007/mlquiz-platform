exec(open('docs/report/capture_full.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
r=json.load(open('docs/acceptance/run.json',encoding='utf-8'));attempt=r['students']['stu001']['attempt_id'];record=[]
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 for label,route,code,name in [('演示教师（teacher_demo）','/teacher/attempts/'+attempt,'T19','attempt_review'),('STU001（stu001）','/exam/'+attempt,'S19','fixed_score_feedback')]:
  c,p=login(b,BASE,label);errors=[];p.on('pageerror',lambda e:errors.append(str(e)));p.goto(BASE+route);settle(p)
  p.locator('.boolean-option').first.wait_for();sizes=p.locator('.boolean-option > b').evaluate_all('(els)=>els.map(e=>({text:e.textContent,height:e.getBoundingClientRect().height,lineHeight:parseFloat(getComputedStyle(e).lineHeight)}))')
  assert sizes and all(x['height']<=x['lineHeight']+1 for x in sizes),sizes
  text=p.locator('body').inner_text();assert '[object Object]' not in text and '75' in text
  shot(p,code,name);record.append({'role':code,'labels':sizes,'page_errors':errors});assert not errors;c.close()
 b.close()
p=ROOT/'docs/report/visible_content_verification.json';r=json.loads(p.read_text(encoding='utf-8'));r['boolean_layout_browser']=record;r['checks']['teacher_student_boolean_labels_single_line']=True;p.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');print('teacher/student boolean layout passed')
