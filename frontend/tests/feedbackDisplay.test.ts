import test from 'node:test'
import assert from 'node:assert/strict'
import { conceptGroup, conceptStatus } from '../src/utils/feedbackDisplay.ts'

const scored = { scored_count: 2, ungraded_count: 0, wrong_count: 0 }

test('已掌握展示只依据当前范围的完整正确证据', () => {
  assert.equal(conceptGroup({ statistics: scored }), 'secure')
  const retested = { statistics: { ...scored, wrong_count: 1 }, needs_review: false,
    retests: [{ ...scored, state: 'correct_this_round' }] }
  assert.equal(conceptGroup(retested), 'secure')
  assert.equal(conceptStatus(retested), '本轮全对')
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
