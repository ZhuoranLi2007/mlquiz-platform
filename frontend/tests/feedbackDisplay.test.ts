import test from 'node:test'
import assert from 'node:assert/strict'
import { conceptGroup, conceptStatus } from '../src/utils/feedbackDisplay.ts'

const scored = { scored_count: 2, ungraded_count: 0, wrong_count: 0 }

test('原卷全对不自动等于知识点达标，展示使用后端证据判定', () => {
  assert.equal(conceptGroup({ statistics: scored }), 'unknown')
  const retested = { statistics: { ...scored, wrong_count: 1 }, needs_review: false,
    retests: [{ ...scored, state: 'correct_this_round' }], learning_result: {state:'achieved',label:'知识点达标'} }
  assert.equal(conceptGroup(retested), 'secure')
  assert.equal(conceptStatus(retested), '知识点达标')
})

test('新轮待作答不覆盖已有达标结果，可靠错答能够重新显示需加强', () => {
  const concept={statistics:scored,needs_review:false,learning_result:{state:'achieved',label:'知识点达标'},retests:[{state:'pending'}]}
  assert.equal(conceptGroup(concept),'secure')
  assert.equal(conceptStatus(concept),'知识点达标')
  assert.equal(conceptGroup({...concept,needs_review:true,learning_result:{state:'needs_review',label:'需加强'}}),'review')
})

test('原卷答对不能掩盖复测待评分、未覆盖或待作答', () => {
  for (const state of ['awaiting_score', 'not_covered', 'pending']) {
    const concept = { statistics: scored, retests: [{ ...scored, state }] }
    assert.equal(conceptGroup(concept), 'unknown')
    assert.notEqual(conceptStatus(concept), '本轮全对')
  }
})

test('无评分、部分评分和仍需加强的知识点不进入已掌握分组', () => {
  assert.equal(conceptGroup({ statistics: { ...scored, scored_count: 0 } }), 'unknown')
  assert.equal(conceptGroup({ statistics: { ...scored, ungraded_count: 1 } }), 'unknown')
  assert.equal(conceptGroup({ statistics: scored, needs_review: true }), 'review')
  assert.equal(conceptGroup({ retests: [{ ...scored, state: 'correct_this_round', ungraded_count: 1 }] }), 'unknown')
})
