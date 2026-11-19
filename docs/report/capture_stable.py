exec(open('docs/report/capture_full.py',encoding='utf-8-sig').read().split('with sync_playwright()')[0])
import re
r=json.load(open('docs/acceptance/run.json',encoding='utf-8'))
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path='C:/Users/lizhi/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe',headless=True)
 c,p=login(b,BASE,'演示教师（teacher_demo）')
 p.goto(BASE+'/teacher/resources');settle(p);p.get_by_role('button',name='导入资料',exact=True).click();shot(p,'T03','import_dialog')
 p.goto(BASE+'/teacher/questions');settle(p);p.get_by_role('button',name='查看详情',exact=True).first.click();shot(p,'T08','question_detail')
 p.get_by_role('button',name='关闭',exact=True).click();p.get_by_role('button',name='开始组卷',exact=True).click();p.get_by_placeholder('请输入试卷标题').fill('报告截图验收-自动组卷-20261119');p.get_by_role('dialog').get_by_role('spinbutton').first.fill('4');p.get_by_role('dialog').get_by_text('基本设置',exact=True).click();shot(p,'T11','ai_paper_config')
 c,p=login(b,BASE,'STU001（stu001）');p.locator('.task-card').filter(has_text='第3轮').get_by_role('button',name=re.compile('开始答题|继续答题')).click();p.wait_for_url('**/exam/**');settle(p);p.wait_for_function("() => document.body.innerText.includes('第 1 题') || document.querySelector('.question-stem')");shot(p,'S17','retest_answering');shot(p,'S05','exam_answering')
 p.get_by_role('button',name=re.compile('^交卷')).first.click();p.get_by_role('button',name='确认交卷',exact=True).wait_for();shot(p,'S06','submit_confirmation');p.get_by_role('button',name='取消',exact=True).click()
 p.goto(BASE+'/student/feedback?attempt='+r['students']['stu001']['attempt_id']);settle(p);p.get_by_role('button',name='查看复测记录',exact=True).click();shot(p,'S12','retest_history')
 b.close()
