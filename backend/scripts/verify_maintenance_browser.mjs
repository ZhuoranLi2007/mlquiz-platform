// Run against a disposable, restored course instance. This creates demo questions.
import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs/promises';
const require = createRequire(process.env.RUNTIME_NODE_MODULES
  ? path.join(process.env.RUNTIME_NODE_MODULES, '_acceptance.cjs') : import.meta.url);
const { chromium } = require('playwright');
const { expect } = require('playwright/test');
await fs.mkdir('docs/acceptance/review1122', { recursive: true });
const browser=await chromium.launch({channel:'msedge',headless:true});
const base=process.env.MLQ_ACCEPTANCE_URL || 'http://127.0.0.1:8022';const errors=[];const checks=[];
const page=await browser.newPage({viewport:{width:1366,height:900}});page.on('pageerror',e=>errors.push(e.message));
async function login(p, user) {
 await p.goto(base+'/login');await p.getByRole('combobox').click();await p.getByRole('option').filter({hasText:user}).click();
 await p.waitForURL(/\/(teacher|student)$/);
}
await login(page,'teacher_demo');
await page.goto(base+'/teacher/questions');await page.getByRole('button',{name:'新增题目',exact:true}).click();
const dialog=page.getByRole('dialog',{name:'新增题目'});await dialog.getByRole('combobox').first().click();await page.getByRole('option').first().click();
await dialog.getByText('判断',{exact:true}).click();
const title='浏览器验收判断题 '+Date.now();
await dialog.locator('textarea').nth(0).fill(title);
await dialog.locator('textarea').nth(1).fill('教师补充题目，用于验证版本与历史保留。');
await dialog.getByRole('button',{name:'保存题目',exact:true}).click();await expect(dialog).not.toBeVisible();
await page.getByPlaceholder('搜索题干、知识点或章节').fill(title);
await expect(page.locator('.question-card')).toHaveCount(1);
await page.locator('.question-card').getByRole('button',{name:'查看详情'}).click();
await page.getByRole('button',{name:'编辑题目',exact:true}).click();
const edit=page.getByRole('dialog',{name:'编辑题目'});await edit.locator('textarea').nth(0).fill(title+' 修订');
await edit.getByRole('button',{name:'保存题目',exact:true}).click();await expect(edit).not.toBeVisible(); await expect(page.locator('.question-card .stem')).toContainText('修订');
await page.locator('.question-card').getByRole('button',{name:'查看详情'}).click();
await page.getByRole('button',{name:'停用题目',exact:true}).click();await page.getByRole('button',{name:'确认停用',exact:true}).click();
await expect(page.getByText('题目已停用',{exact:true}).last()).toBeVisible();checks.push('teacher_create_edit_archive');
await page.screenshot({path:'docs/acceptance/review1122/question-bank.png'});
const student=await browser.newPage({viewport:{width:1366,height:768}});student.on('pageerror',e=>errors.push(e.message));
await login(student,'STU001（stu001）');await student.goto(base+'/student/online');
await student.locator('.res-cover').first().click();const preview=student.getByRole('dialog',{name:'资源预览'});
const save=preview.getByRole('button',{name:'收藏资料',exact:true});await expect(save).toBeEnabled();await save.click();
await expect(preview.getByRole('button',{name:'已收藏 · 取消',exact:true})).toBeVisible();
await preview.getByRole('button',{name:'完成预览'}).click();await student.reload();await student.locator('.res-cover').first().click();
await expect(student.getByRole('button',{name:'已收藏 · 取消',exact:true})).toBeVisible();checks.push('student_bookmark_survives_reload');
await student.waitForLoadState('networkidle'); await student.screenshot({animations:'disabled', path:'docs/acceptance/review1122/bookmark.png'});
await student.getByRole('button',{name:'已收藏 · 取消',exact:true}).click();await expect(student.getByRole('button',{name:'收藏资料',exact:true})).toBeVisible();
for(const route of ['/teacher','/teacher/analysis','/teacher/resources','/teacher/online']) {
 await page.goto(base+route);await page.waitForLoadState('networkidle');
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2);if(overflow) throw Error('overflow '+route);
 checks.push(route);
}
if(errors.length)throw Error(errors.join('\n'));
await fs.writeFile('docs/acceptance/review1122/browser.json',JSON.stringify({checks,errors,modelCallsTriggered:false},null,2));
console.log(checks);await browser.close();
