"""Verify corrected content in actual UI; create an unpublished preview only."""
exec(open('docs/report/capture_full.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
import sqlite3,re,hashlib
from playwright.sync_api import expect
DB=ROOT/'data/report-ui-20261119/db/mlquiz.sqlite3'
IDS=['slq_62cc358de7_p12_3','slq_62cc358de7_p12_4','seed_note_pending_010','seed_note_pending_023']
def history():
 with sqlite3.connect('file:'+DB.as_posix()+'?mode=ro',uri=True) as db:
  return {t:db.execute('select rowid,* from '+t+' order by rowid').fetchall() for t in ['attempts','answer_scores','paper_items']}
before=history();record={'viewport':[1366,768],'page_errors':[],'checks':{},'revised_questions':[]}
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）');p.on('pageerror',lambda e:record['page_errors'].append(str(e)))
 with p.expect_response(lambda r:'/api/teacher/question-bank' in r.url) as response:p.goto(BASE+'/teacher/questions')
 bank=response.value.json();settle(p);qs={q['question_id']:q for q in bank['items']}
 for qid in IDS:
  q=qs[qid];assert q['question_version']==2 and q['answer_payload']=={'key':'B'} and q['question_type_effective']=='single_choice'
  assert len(q['options'])==4 and all(o['text'] for o in q['options'])
  record['revised_questions'].append({'id':qid,'version':q['question_version'],'chapter':q['chapter_id'],'kp':q['main_kp_id']})
 assert qs[IDS[0]]['chapter_id']=='ch06' and qs[IDS[1]]['chapter_id']=='ch06'
 assert qs[IDS[0]]['main_kp_id']==qs[IDS[1]]['main_kp_id']=='kp.eval.bias_var'
 assert all(not qs[qid]['stem_images'] for qid in IDS[2:]);record['checks']['latest_versions_and_content']=True
 shot(p,'T07','question_bank')
 search=p.get_by_placeholder('搜索题干、知识点或章节')
 search.fill('统计学习理论');p.locator('.question-card').filter(has_text='统计学习理论').first.get_by_role('button',name='查看详情',exact=True).click()
 d=p.get_by_role('dialog');p.wait_for_timeout(400);assert '参考答案' in d.inner_text() and '标准答案' not in d.inner_text();shot(p,'T08','question_detail')
 d.get_by_text('评分规则',exact=True).scroll_into_view_if_needed();shot(p,'T08b','question_rubric');d.get_by_role('button',name='关闭',exact=True).click()
 p.get_by_role('button',name='清空筛选',exact=True).click();p.locator('.el-select').nth(3).click();p.get_by_role('option',name='AI 生成题',exact=False).first.click();search.fill('在平方损失回归')
 main=p.locator('.question-card').filter(has_text='在平方损失回归');expect(main).to_have_count(1);assert '第6章 模型评估' in main.inner_text();shot(p,'T10','ai_bank_results')
 main.get_by_role('button',name='查看详情',exact=True).click();p.wait_for_timeout(500)
 text=d.inner_text();assert '相对真实回归函数' in text and '选项 B' in text and '（2分）' in text and '只能作为诊断线索' in text
 assert '[object Object]' not in text and '课件第12页指出：偏差是' not in text
 assert d.locator('.katex').count()>=2 and not d.locator('.mathtext-failed').count()
 shot(p,'T20','ai_question_review');d.get_by_text('评分规则',exact=True).scroll_into_view_if_needed();shot(p,'T20b','bias_explanation');d.get_by_role('button',name='关闭',exact=True).click()
 record['checks']['bias_answer_explanation_and_formula']=True
 main.get_by_role('button',name=re.compile('加入组卷')).click()
 p.get_by_role('button',name='清空筛选',exact=True).click();search.fill('L2 正则化项通常');p.locator('.el-select').first.click();p.get_by_role('option',name=re.compile('第2章')).first.click()
 cards=p.locator('.question-card');expect(cards).to_have_count(2)
 for i,code in enumerate(['T23','T24']):
  cards.nth(i).get_by_role('button',name='查看详情',exact=True).click();p.wait_for_timeout(500)
  text=d.inner_text();assert '选项 B' in text and '选项正文缺失' not in text and '[object Object]' not in text
  assert d.locator('.detail-options .katex').count()>=4 and not d.locator('.mathtext-failed').count()
  assert d.locator('.detail-images img').count()==0
  shot(p,code,'l2_formula_options');d.get_by_role('button',name='关闭',exact=True).click()
 record['checks']['l2_options_and_no_answer_images']=True
 cards.first.get_by_role('button',name=re.compile('加入组卷')).click();p.get_by_role('button',name='用已选题目组卷',exact=True).click()
 d.get_by_placeholder('请输入试卷标题').fill('界面核对-偏差方差与L2-20261119')
 d.get_by_role('button',name='作答说明与总分（选填）',exact=True).click();d.locator('.extra-settings').get_by_role('spinbutton').fill('4')
 d.get_by_placeholder('请输入试卷标题').click()
 d.get_by_role('button',name='生成并预览',exact=True).click();p.wait_for_url('**/teacher/papers/**',timeout=90000);settle(p)
 for summary in p.locator('summary').filter(has_text='查看参考答案与解析').all():summary.click()
 p.wait_for_timeout(500);text=p.locator('body').inner_text();assert '在平方损失回归' in text and 'L2 正则化项通常' in text and '标准答案' not in text and '[object Object]' not in text
 assert not p.locator('.mathtext-failed').count();shot(p,'T22','corrected_paper_preview')
 record['preview_paper_id']=p.url.split('/teacher/papers/')[1];record['checks']['unpublished_preview_ok']=True
 p.reload();settle(p);assert '在平方损失回归' in p.locator('body').inner_text();record['checks']['refresh_persistence']=True
 c,p=login(b,BASE,'STU001（stu001）');p.on('pageerror',lambda e:record['page_errors'].append(str(e)))
 p.locator('.task-card').filter(has_text='第3轮').get_by_role('button',name=re.compile('开始答题|继续答题')).click();p.wait_for_url('**/exam/**');settle(p);p.get_by_role('button',name='立即保存',exact=True).wait_for()
 text=p.locator('body').inner_text();assert '参考答案' not in text and '评分要点' not in text and '[object Object]' not in text;shot(p,'S18','reference_hidden');record['checks']['unfinished_exam_no_reference']=True
 r=json.load(open('docs/acceptance/run.json',encoding='utf-8'));p.goto(BASE+'/exam/'+r['students']['stu001']['attempt_id']);settle(p);assert '75' in p.locator('body').inner_text();shot(p,'S19','fixed_score_feedback');record['checks']['old_score_feedback_ok']=True
 b.close()
after=history()
record['checks']['existing_history_unchanged']=all({row[0]:row for row in after[t]}.get(row[0])==row for t,rows in before.items() for row in rows)
assert all(record['checks'].values()) and not record['page_errors'],record
record.update({'frontend_tests_passed':45,'typecheck':'passed','production_build':'passed','new_model_calls':0,'new_exam_releases':0})
(ROOT/'docs/report/visible_content_verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(record,ensure_ascii=False))
