import assert from 'node:assert/strict'
import test from 'node:test'
import { averageCompleteScoreRate, effectiveRecordScore } from '../src/utils/studentTaskOverview.ts'

test('average uses each complete scored attempt once and excludes missing or partial scores', () => {
  const records = [
    { record_id: 'source-1', attempt_id: 'attempt-1', source_type: 'historical_import', regrade: { awarded_score: 15, max_score: 30, coverage_rate: 1 } },
    { record_id: 'duplicate-source', attempt_id: 'attempt-1', source_type: 'historical_import', regrade: { awarded_score: 30, max_score: 30, coverage_rate: 1 } },
    { record_id: 'source-2', attempt_id: 'attempt-2', source_type: 'historical_import', regrade: { awarded_score: 5, max_score: 15, coverage_rate: .75 } },
    { record_id: 'source-3', source_type: 'historical_import', absent: true, reported_score: 0 },
    { record_id: 'source-4', source_type: 'historical_import', reported_score: 9 },
    { record_id: 'online-1', attempt_id: 'online-1', source_type: 'online_exam', platform_score: 10, platform_max_score: 10, ungraded_item_count: 0 },
    { record_id: 'online-2', attempt_id: 'online-2', source_type: 'online_exam', platform_score: 5, platform_max_score: 10, ungraded_item_count: 1 },
  ]
  assert.deepEqual(averageCompleteScoreRate(records), { rate: .75, count: 2 })
  assert.equal(effectiveRecordScore(records[1])?.score, 30)
  assert.equal(effectiveRecordScore(records[2])?.complete, false)
  assert.equal(effectiveRecordScore(records[3]), null)
})
