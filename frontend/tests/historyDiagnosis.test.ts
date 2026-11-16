import assert from 'node:assert/strict'
import test from 'node:test'
import {
  chapterWrongDistribution, filterHistoryAttempts, historyScoringStatus,
  scoreRateLabel,
} from '../src/utils/historyDiagnosis.ts'

test('真实零分与未评分保持不同状态', () => {
  assert.equal(scoreRateLabel(0), '0%')
  assert.equal(scoreRateLabel(null), '—')
  assert.equal(historyScoringStatus({ score_rate: 0, scored_items: 2 }), 'graded')
  assert.equal(historyScoringStatus({ score_rate: null, scored_items: 0, ungraded_items: 2 }), 'ungraded')
  assert.equal(historyScoringStatus({ score_rate: 0.5, scored_items: 2, ungraded_items: 1 }), 'partial')
})

test('考试类型和评分状态筛选互不覆盖', () => {
  const rows = [
    { source_label: '历史课堂小测', score_rate: 0, scored_items: 2 },
    { source_label: '在线考试', score_rate: null, scored_items: 0 },
    { source_label: '针对性复测', score_rate: 0.7, scored_items: 3 },
  ]
  assert.deepEqual(filterHistoryAttempts(rows, 'quiz', 'graded'), [rows[0]])
  assert.deepEqual(filterHistoryAttempts(rows, 'exam', 'ungraded'), [rows[1]])
  assert.deepEqual(filterHistoryAttempts(rows, 'retest', 'all'), [rows[2]])
})

test('知识点分布使用真实错题按章节聚合', () => {
  const rows = [
    { chapter: { chapter_id: 'a', title: '模型评估' } },
    { chapter: { chapter_id: 'a', title: '模型评估' } },
    { chapter: { chapter_id: 'b', title: '神经网络' } },
  ]
  assert.deepEqual(chapterWrongDistribution(rows), [
    { key: 'a', title: '模型评估', count: 2, percent: 67 },
    { key: 'b', title: '神经网络', count: 1, percent: 33 },
  ])
})
