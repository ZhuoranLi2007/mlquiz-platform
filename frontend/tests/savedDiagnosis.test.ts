import assert from 'node:assert/strict'
import test from 'node:test'
import { canReuseDiagnosis } from '../src/utils/savedDiagnosis.ts'

test('saved current-version narrative survives reload when evidence is unchanged', () => {
  const current = { student: { student_id: 's1' }, statistics: { count: 2 }, attempts: [{ attempt_id: 'a1', score: 0 }] }
  assert.equal(canReuseDiagnosis({ ...current, ai_analysis: { summary: 'recorded model explanation' } }, current), true)
})

test('same-count regrading, new attempts, other students and retest updates invalidate old interpretation', () => {
  const current = { student: { student_id: 's1' }, attempts: [{ attempt_id: 'a1', score: 0 }], retest_history: [{ status: 'pending' }] }
  const saved = { ...current, ai_analysis: { summary: 'old' } }
  for (const changed of [
    { ...current, attempts: [{ attempt_id: 'a1', score: 10 }] },
    { ...current, attempts: [...current.attempts, { attempt_id: 'a2', score: 0 }] },
    { ...current, student: { student_id: 's2' } },
    { ...current, retest_history: [{ status: 'passed' }] },
  ]) assert.equal(canReuseDiagnosis(saved, changed), false)
})

test('class question-answer distribution changes invalidate saved narrative at unchanged average', () => {
  const current = { score_overview: { average_score: 50 }, question_analysis: [{ common_wrong_answers: [{ answer: 'A', count: 2 }] }] }
  const saved = { ...current, question_analysis: [{ common_wrong_answers: [{ answer: 'B', count: 2 }] }], ai_analysis: {} }
  assert.equal(canReuseDiagnosis(saved, current), false)
})
