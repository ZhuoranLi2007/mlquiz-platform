// Run from the repository root with Playwright + Edge. Creates real demo exams and answers.
// The target must use isolated MLQ_DATA_DIR and disabled model settings (see docs/MATRIX_ADAPTIVE.md).
import {createRequire} from 'node:module';import fs from 'node:fs/promises';
const require=createRequire(process.env.RUNTIME_NODE_MODULES ? process.env.RUNTIME_NODE_MODULES + '/_matrix.cjs' : import.meta.url);
const {chromium,request}=require('playwright');const {expect}=require('playwright/test');
const base=process.env.MLQ_ACCEPTANCE_URL || 'http://127.0.0.1:8023';
if (process.env.MLQ_ACCEPTANCE_ISOLATED !== '1') throw Error('Only run against isolated demo data: set MLQ_ACCEPTANCE_ISOLATED=1');
const out=process.env.MLQ_ACCEPTANCE_OUTPUT || 'data/acceptance/matrix-adaptive-' + Date.now();
await fs.mkdir(out, {recursive:true});
const teacher=await request.newContext({baseURL:base});
const tokens=new WeakMap();
async function call(client,method,url,data){const r=await client[method](url,{...(data?{data}:{}),headers:tokens.has(client)?{Authorization:'Bearer '+tokens.get(client)}:{}});if(!r.ok())throw Error(url+' '+r.status()+' '+(await r.text()).slice(0,300));return r.json()}
async function apiLogin(client,uid){const login=await call(client,'post','/api/auth/quick-login',{user_id:uid});tokens.set(client,login.session_token);return login.principal;}
await apiLogin(teacher,'teacher_demo');let fixture;
try{fixture=JSON.parse(await fs.readFile(out+'/fixture.json','utf8'))}catch{
 const room=await call(teacher,'get','/api/teacher/official-classroom');
 const bank=await call(teacher,'get','/api/teacher/question-bank?availability=usable&limit=2000');
 const groups=new Map();for(const q of bank.items.filter(q=>q.question_type==='true_false'&&typeof q.answer_payload?.value==='boolean'&&q.main_kp_id)) {const rows=groups.get(q.main_kp_id)||[];rows.push(q);groups.set(q.main_kp_id,rows)}
 const [kp,questions]=[...groups].sort((a,b)=>b[1].length-a[1].length)[0];if(questions.length<5)throw Error('No sufficient real bank');
 const selected=questions.slice(0,2);const paper=await call(teacher,'post','/api/teacher/papers/from-questions',{title:'班级矩阵与自适应训练验收',question_ids:selected.map(q=>q.question_id),scores:[5,5]});
 const learners=[];for(const uid of ['stu001','stu002']){const c=await request.newContext({baseURL:base});const p=await apiLogin(c,uid);learners.push({uid,client:c,student_id:p.student_id})}
 const release=await call(teacher,'post','/api/teacher/releases',{title:'班级矩阵与自适应训练验收',paper_id:paper.paper_id,classroom_id:room.classroom_id,student_ids:learners.map(l=>l.student_id)});
 for(const learner of learners){const recipient=release.recipients.find(r=>r.student_id===learner.student_id);const attempt=await call(learner.client,'post','/api/student/assignments/'+recipient.recipient_id+'/start',{});learner.attempt_id=attempt.attempt_id;
  for(const q of selected) await call(learner.client,'put','/api/student/attempts/'+attempt.attempt_id+'/answers/'+q.question_id,{response:{value:learner.uid==='stu001'?q.answer_payload.value:!q.answer_payload.value}});
  await call(learner.client,'post','/api/student/assignments/'+recipient.recipient_id+'/submit',{});await learner.client.dispose();delete learner.client;
 }
 fixture={classroom_id:room.classroom_id,assessment_version_id:release.assessment_version_id,kp_id:kp,students:learners};
 if(!fixture.assessment_version_id){const r=await call(teacher,'get','/api/teacher/releases');fixture.assessment_version_id=r.items.find(r=>r.release_id===release.release_id).assessment_version_id}
 await fs.writeFile(out+'/fixture.json',JSON.stringify(fixture,null,2));
}
const browser=await chromium.launch({channel:'msedge',headless:true});const errors=[];
async function login(p,user){p.on('pageerror',e=>errors.push(e.message));await p.goto(base+'/login');await p.getByRole('combobox').click();await p.getByRole('option').filter({hasText:user}).click();await p.waitForURL(/\/(teacher|student)$/)}
const page=await browser.newPage({viewport:{width:1366,height:900}});await login(page,'teacher_demo');
await page.goto(base+'/teacher/analysis?scope=class_assessment&assessment_version_id='+fixture.assessment_version_id);
await page.getByRole('button',{name:'知识点矩阵',exact:true}).click();await expect(page.locator('.matrix-scroll tbody tr')).toHaveCount(16);
await page.getByRole('textbox',{name:'搜索学生编号'}).fill('STU001');await expect(page.locator('.matrix-scroll tbody tr')).toHaveCount(1);await page.getByRole('textbox',{name:'搜索学生编号'}).fill('');
const first=page.locator('.matrix-scroll tbody tr').filter({hasText:'STU001'});const second=page.locator('.matrix-scroll tbody tr').filter({hasText:'STU002'});
await expect(first.locator('.matrix-cell')).toContainText('证据不足');await expect(second.locator('.matrix-cell')).toContainText('需加强');await page.getByText('优先关注',{exact:true}).click();await expect(page.locator('.matrix-scroll tbody tr').filter({hasText:'STU002'})).toHaveCount(1);await page.getByText('优先关注',{exact:true}).click();
await second.locator('.matrix-cell').click();await expect(page.locator('.el-drawer .matrix-question')).toHaveCount(2);await page.locator('.el-drawer').filter({has:page.locator('.matrix-question')}).locator(':scope > header > button').click();
await page.getByRole('checkbox',{name:'选择 STU001',exact:true}).check();await page.getByRole('checkbox',{name:'选择 STU002',exact:true}).check();
async function publish(){await page.getByRole('button',{name:'发布分组训练',exact:true}).click();await page.getByRole('button',{name:'确认发布',exact:true}).click();await expect(page.locator('.batch-results li')).toHaveCount(2);await expect(page.locator('.batch-results')).not.toContainText('正在准备',{timeout:60000});await expect(page.locator('.batch-results')).toContainText(/准备|继续已有任务/)}
await publish();await page.screenshot({path:out+'/matrix-published.png',animations:'disabled'});
// Republishing joins or resumes existing scopes instead of duplicating papers.
await page.getByRole('button',{name:'发布分组训练',exact:true}).click();await page.getByRole('button',{name:'确认发布',exact:true}).click();await expect(page.locator('.batch-results')).toContainText('继续已有任务',{timeout:60000});
for(const learner of fixture.students){const p=await browser.newPage({viewport:{width:1366,height:900}});await login(p,learner.uid.toUpperCase()+'（'+learner.uid+'）');await p.goto(base+'/student/feedback?attempt='+learner.attempt_id);await p.getByRole('button',{name:'学习路径',exact:true}).click();await expect(p.locator('.adaptive-path>header')).toContainText(learner.uid==='stu001'?'应用验证':'基础巩固');await p.screenshot({path:out+'/'+learner.uid+'-path.png',animations:'disabled'});
 await p.getByRole('button',{name:'继续当前复测',exact:true}).click();await p.waitForURL('**/exam/**');await expect(p.locator('.answer-area')).toBeVisible();await p.waitForLoadState('networkidle');await p.screenshot({path:out+'/'+learner.uid+'-training.png',animations:'disabled'});await p.close();}
await page.setViewportSize({width:390,height:844});await expect(page.locator('.matrix-scroll')).toBeVisible();if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('page overflow');await page.getByRole('button',{name:'发布分组训练',exact:true}).scrollIntoViewIfNeeded();await expect(page.getByRole('button',{name:'发布分组训练',exact:true})).toBeInViewport();await page.screenshot({path:out+'/matrix-mobile.png',animations:'disabled'});
if(errors.length)throw Error(errors.join('\n'));await fs.writeFile(out+'/browser.json',JSON.stringify({checks:['16_student_fixed_version_matrix','search','attention_filter','reliable_wrong_answer_drawer','two_student_group_publication','duplicate_publication_resume','different_learning_stages','path_opens_real_training','mobile_matrix_scroll','mobile_publish_button_reachable'],errors,mode:'verified_bank_group_training'},null,2));
await browser.close();await teacher.dispose();console.log('Matrix and adaptive path browser checks passed');
