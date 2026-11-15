export type ExamRecord = {
  record_id?: string
  attempt_id?: string
  source_type?: string
  absent?: boolean
  regrade?: { awarded_score?: number | null; max_score?: number | null; coverage_rate?: number | null } | null
  platform_score?: number | null
  platform_max_score?: number | null
  reported_score?: number | null
  scored_item_count?: number
  ungraded_item_count?: number
}

export function effectiveRecordScore(record: ExamRecord): { score: number; max: number; complete: boolean } | null {
  if (record.absent) return null
  if (record.source_type === 'historical_import') {
    const score = Number(record.regrade?.awarded_score)
    const max = Number(record.regrade?.max_score)
    if (record.regrade?.awarded_score == null || record.regrade?.max_score == null || !Number.isFinite(score) || !(max > 0)) return null
    return { score, max, complete: Number(record.regrade.coverage_rate) >= 1 }
  }
  const score = Number(record.platform_score)
  const max = Number(record.platform_max_score)
  if (record.platform_score == null || record.platform_max_score == null || !Number.isFinite(score) || !(max > 0)) return null
  return { score, max, complete: Number(record.ungraded_item_count || 0) === 0 }
}

export function averageCompleteScoreRate(records: ExamRecord[]): { rate: number | null; count: number } {
  const seen = new Set<string>()
  const rates: number[] = []
  for (const record of records) {
    const identity = record.attempt_id || record.record_id
    if (!identity || seen.has(identity)) continue
    seen.add(identity)
    const result = effectiveRecordScore(record)
    if (result?.complete) rates.push(result.score / result.max)
  }
  return { rate: rates.length ? rates.reduce((sum, rate) => sum + rate, 0) / rates.length : null, count: rates.length }
}
