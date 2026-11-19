"""Real-browser display regression, no exam submissions or model calls."""
exec(open('docs/report/capture_full.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
import sqlite3,hashlib,re
from playwright.sync_api import expect
D=ROOT/'data/report-ui-20261119/db/mlquiz.sqlite3'
def scores():
 with sqlite3.connect('file:'+D.as_posix()+'?mode=ro',uri=True) as db:
  return db.execute('select attempt_id,status,score,max_score from attempts order by attempt_id').fetchall()
before=scores();record={'viewport':[1366,768],'page_errors':[],'checks':{},'backend_changed':False}
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）');p.on('pageerror',lambda e:record['page_errors'].append(str(e)))
 with p.expect_response(lambda r:'/api/teacher/question-bank' in r.url) as response:
  p.goto(BASE+'/teacher/questions')
 bank=response.value.json();settle(p)
 p.get_by_role('button',name='查看详情',exact=True).first.click();d=p.get_by_role('dialog');expect(d).to_be_visible();p.wait_for_timeout(600)
 text=d.inner_text();assert '统计学习理论' in text and '参考答案待补充' not in text and '[object Object]' not in text
 assert '泛化' in text and '分）' in text
 record['checks']['reference_answer_visible']=True
 record['checks']['reference_formula_rendered']=d.locator('.katex').count()>0
 shot(p,'T08','question_detail')
 d.get_by_text('评分规则',exact=True).scroll_into_view_if_needed();shot(p,'T08b','question_rubric')
 d.get_by_role('button',name='关闭',exact=True).click()
 p.locator('.el-select').nth(3).click();p.get_by_role('option',name='AI 生成题',exact=False).first.click();p.get_by_placeholder('搜索题干、知识点或章节').fill('在平方损失回归');p.get_by_role('button',name='查看详情',exact=True).first.click();p.wait_for_timeout(600)
 text=d.inner_text();assert '偏差' in text and '选项 B' in text and '（2分）' in text and '[object Object]' not in text
 record['checks']['point_field_and_score_visible']=True;shot(p,'T20','ai_question_review')
 p.reload();settle(p);p.locator('.el-select').nth(3).click();p.get_by_role('option',name='AI 生成题',exact=False).first.click();p.get_by_placeholder('搜索题干、知识点或章节').fill('在平方损失回归');p.get_by_role('button',name='查看详情',exact=True).first.click();p.wait_for_timeout(400)
 assert '[object Object]' not in p.get_by_role('dialog').inner_text();record['checks']['refresh_ok']=True
 with sqlite3.connect('file:'+D.as_posix()+'?mode=ro',uri=True) as db:
  paper=db.execute("select paper_id from papers where title like '报告截图%' order by rowid desc limit 1").fetchone()[0]
 p.goto(BASE+'/teacher/papers/'+paper);settle(p);p.locator('summary').filter(has_text='查看参考答案与解析').first.click();p.wait_for_timeout(400)
 assert '参考答案：' in p.locator('body').inner_text() and '[object Object]' not in p.locator('body').inner_text()
 record['checks']['paper_preview_ok']=True
 p.screenshot(path=str(OUT/'teacher/T21_fixed_paper_answer.png'))
 c,p=login(b,BASE,'STU001（stu001）');p.on('pageerror',lambda e:record['page_errors'].append(str(e)))
 p.locator('.task-card').filter(has_text='第3轮').get_by_role('button',name=re.compile('开始答题|继续答题')).click();p.wait_for_url('**/exam/**');settle(p);p.get_by_role('button',name='立即保存',exact=True).wait_for();p.wait_for_timeout(500)
 text=p.locator('body').inner_text();assert '参考答案' not in text and '评分要点' not in text and '[object Object]' not in text
 record['checks']['unfinished_exam_reference_hidden']=True
 p.screenshot(path=str(OUT/'student/S18_reference_hidden.png'))
 r=json.load(open('docs/acceptance/run.json',encoding='utf-8'))
 p.goto(BASE+'/exam/'+r['students']['stu001']['attempt_id']);settle(p);p.wait_for_timeout(500)
 text=p.locator('body').inner_text();assert '75' in text and '[object Object]' not in text
 record['checks']['student_score_feedback_ok']=True
 p.screenshot(path=str(OUT/'student/S19_fixed_score_feedback.png'))
 b.close()
record['checks']['historical_scores_unchanged']=before==scores();assert all(record['checks'].values()) and not record['page_errors'],record
record['question_ids']=[q['question_id'] for q in bank['items'] if (q.get('stem')=='统计学习理论' or q.get('question_id')=='slq_62cc358de7_p12_3')]
(ROOT/'docs/report/question_display_verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(record,ensure_ascii=False))
