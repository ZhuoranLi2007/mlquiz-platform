import test from 'node:test'
import assert from 'node:assert/strict'
import {actionLabel,roundLabel} from '../src/utils/learningLoop.ts'

test('每种复测状态都有下一步，练习不伪装成通过',()=>{
  assert.equal(actionLabel(null),'开始复测')
  assert.equal(actionLabel({task_state:'opened'}),'继续作答')
  assert.equal(actionLabel({task_state:'scoring_pending'}),'先做巩固练习')
  assert.equal(actionLabel({round_result:{status:'passed'}}),'再次复测')
  assert.equal(roundLabel({status:'practice_completed'}),'练习完成')
  assert.equal(roundLabel({status:'passed'}),'本轮复测通过')
})
