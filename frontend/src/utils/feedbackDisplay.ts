// Presentation labels only: scoring, coverage and diagnosis remain API-owned.
export function conceptGroup(concept: any): 'review' | 'secure' | 'unknown' {
  if (concept.needs_review) return 'review'
  const round = concept.retests?.at(-1)
  const evidence = round || concept.statistics || {}
  if (round && round.state !== 'correct_this_round') return 'unknown'
  return evidence.scored_count > 0 && evidence.ungraded_count === 0 && evidence.wrong_count === 0
    ? 'secure' : 'unknown'
}

export function conceptStatus(concept: any): string {
  const round = concept.retests?.at(-1)
  if (round) return ({
    correct_this_round: '本轮全对', needs_review: '本轮仍有错题',
    not_covered: '本轮未覆盖', awaiting_score: '等待评分', pending: '等待复测',
  } as Record<string, string>)[round.state] || round.label || '待确认'
  if (concept.statistics?.ungraded_count) return '有题待评分'
  if (concept.needs_review) return '需要加强'
  return conceptGroup(concept) === 'secure' ? '本卷已答对' : '暂无完整评分'
}
