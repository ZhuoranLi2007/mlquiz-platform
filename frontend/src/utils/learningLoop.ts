export function roundLabel(row: any): string {
  return ({ passed: '本轮复测通过', not_passed: '本轮未通过', practice_completed: '练习完成',
    baseline_completed: '基线自测完成', scoring_pending: '等待评分', published: '等待作答',
    opened: '继续作答', cancelled: '任务已结束' } as Record<string, string>)[row?.status] || '尚未复测'
}

export function actionLabel(summary: any): string {
  if (['published', 'opened'].includes(summary?.task_state)) return '继续作答'
  if (summary?.task_state === 'scoring_pending') return '先做巩固练习'
  return summary?.round_result ? '再次复测' : '开始复测'
}

export function conceptLabel(concept: any): string {
  return concept?.learning_result?.label || '证据不足'
}

export function loopTime(value: unknown): string {
  if (!value) return ""
  const date = new Date(String(value))
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hour12:false })
}

export function sourceLabel(counts: any): string {
  if (!counts) return ""
  return [counts.ai_generated ? `AI 新题 ${counts.ai_generated}` : "", counts.bank_selected ? `题库 ${counts.bank_selected}` : "", counts.original_reused ? `重复练习 ${counts.original_reused}` : ""].filter(Boolean).join(" · ") || '历史题源未记录'
}
