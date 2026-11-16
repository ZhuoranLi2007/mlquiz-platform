export type HistoryRecordType = 'all' | 'quiz' | 'exam' | 'retest'
export type HistoryScoringStatus = 'all' | 'graded' | 'partial' | 'ungraded'

export interface HistoryAttempt {
  score_rate?: number | null
  scored_items?: number | null
  ungraded_items?: number | null
  grading_status?: string | null
  source_label?: string | null
  source?: string | null
  [key: string]: unknown
}

export interface HistoryWrongItem {
  chapter?: { chapter_id?: string | null; title?: string | null } | null
  [key: string]: unknown
}

export function validScoreRate(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function scoreRateLabel(value: unknown): string {
  const rate = validScoreRate(value)
  return rate === null ? '—' : `${Math.round(rate * 100)}%`
}

export function historyRecordType(row: HistoryAttempt): Exclude<HistoryRecordType, 'all'> {
  const source = `${row.source_label || ''} ${row.source || ''}`
  if (source.includes('复测') || source.includes('retest')) return 'retest'
  if (source.includes('在线考试') || source.includes('online')) return 'exam'
  return 'quiz'
}

export function historyScoringStatus(row: HistoryAttempt): Exclude<HistoryScoringStatus, 'all'> {
  const scored = Number(row.scored_items || 0)
  const ungraded = Number(row.ungraded_items || 0)
  if (row.grading_status === 'partial' || (scored > 0 && ungraded > 0)) return 'partial'
  if (validScoreRate(row.score_rate) === null) return 'ungraded'
  return 'graded'
}

export function filterHistoryAttempts<T extends HistoryAttempt>(
  rows: T[], type: HistoryRecordType, status: HistoryScoringStatus,
): T[] {
  return rows.filter(row =>
    (type === 'all' || historyRecordType(row) === type)
    && (status === 'all' || historyScoringStatus(row) === status))
}

export function chapterWrongDistribution(items: HistoryWrongItem[]) {
  const groups = new Map<string, { key: string; title: string; count: number }>()
  for (const item of items) {
    const key = String(item.chapter?.chapter_id || item.chapter?.title || 'unmapped')
    const title = String(item.chapter?.title || '未映射章节')
    const group = groups.get(key) || { key, title, count: 0 }
    group.count += 1
    groups.set(key, group)
  }
  return [...groups.values()]
    .sort((a, b) => b.count - a.count || a.title.localeCompare(b.title, 'zh-CN'))
    .map(group => ({ ...group, percent: items.length ? Math.round(group.count / items.length * 100) : 0 }))
}
