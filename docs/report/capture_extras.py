exec(open('docs/report/capture_full.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
import sqlite3
r=json.load(open('docs/acceptance/run.json',encoding='utf-8'))
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）')
 p.goto(BASE+'/teacher/resources');settle(p);p.get_by_placeholder('搜索资料名称').fill('acceptance');p.get_by_role('button',name='在线预览',exact=True).first.click();p.get_by_role('dialog').wait_for();p.wait_for_timeout(1500);shot(p,'T06','material_preview')
 p.goto(BASE+'/teacher/questions');settle(p);p.locator('.el-select').nth(3).click();p.get_by_role('option',name='AI 生成题',exact=False).first.click();shot(p,'T10','ai_bank_results')
 p.get_by_role('button',name='查看详情',exact=True).first.click();shot(p,'T20','ai_question_review')
 c,p=login(b,BASE,'STU001（stu001）');card=p.locator('.task-card').filter(has_text='第3轮');card.get_by_role('button',name='开始答题').click();shot(p,'S17','retest_answering')
 p.goto(BASE+'/student');settle(p);p.get_by_role('button',name='考试记录',exact=False).first.click();shot(p,'S15','history_records')
 b.close()
