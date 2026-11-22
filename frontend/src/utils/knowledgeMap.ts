export type KnowledgeState = 'needs_review' | 'insufficient' | 'achieved' | 'delayed_confirmed'

const labels: Record<KnowledgeState, string> = {
  needs_review: '需加强', insufficient: '证据不足',
  achieved: '知识点达标', delayed_confirmed: '延后确认',
}

export function knowledgeState(concept: any): KnowledgeState {
  const state = concept?.learning_result?.state
  return Object.hasOwn(labels, state) ? state : 'insufficient'
}

export function knowledgeLabel(concept: any): string {
  return labels[knowledgeState(concept)]
}

export function chapterGroups(concepts: any[], query = '', state = '') {
  const groups = new Map<string, any[]>()
  const term = query.trim().toLocaleLowerCase()
  for (const concept of concepts) {
    const chapter = String(concept.map_chapter || concept.chapter || '章节待确认')
    if (state && knowledgeState(concept) !== state) continue
    if (term && ![chapter, concept.name].join(' ').toLocaleLowerCase().includes(term)) continue
    const items = groups.get(chapter) || []
    items.push(concept)
    groups.set(chapter, items)
  }
  return [...groups].map(([chapter, items]) => ({ chapter, items }))
}
