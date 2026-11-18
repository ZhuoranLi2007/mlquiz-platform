// The API already selects the current skill version and exact requested scope.
// Reuse its narrative only when the underlying current evidence still matches.
function stable(value: any): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${stable(value[k])}`).join(',')}}`
  return JSON.stringify(value) ?? 'null'
}

export function canReuseDiagnosis(saved: any, current: any): boolean {
  if (!saved?.ai_analysis || !current) return false
  const evidenceKeys = [
    'attempt_id', 'classroom_id', 'student', 'classroom', 'assessment', 'filters',
    'items', 'score_summary', 'statistics', 'attempts', 'wrong_events', 'ungraded_items',
    'participation', 'score_overview', 'question_analysis', 'anonymous_comparison',
    'knowledge_points', 'chapters', 'retest_history', 'evidence_refs',
  ]
  return evidenceKeys.every(key => stable(saved[key]) === stable(current[key]))
}
