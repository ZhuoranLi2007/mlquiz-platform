type ReviewScore = { correct?: boolean | null; awarded_score?: number; max_score?: number } | null
export type ReviewItem = {
  [key: string]: any
  position: number
  question_type: string
  score?: ReviewScore
  selection_status?: string
  selected_keys?: string[]
  reference_keys?: string[]
  reference_available?: boolean
  student_boolean?: boolean | null
  reference_boolean?: boolean | null
  student_response?: unknown
  answer_reference?: { payload?: unknown; explanation?: string; rubric?: unknown }
}

export function itemResult(item: ReviewItem): 'wrong' | 'correct' | 'ungraded' {
  if (item.score?.correct === true) return 'correct'
  if (item.score?.correct === false) return 'wrong'
  return 'ungraded'
}

export function optionMark(selected: boolean, reference: boolean, comparable: boolean) {
  if (!comparable) return { tone: reference ? 'reference' : '', label: selected ? '学生选择' : reference ? '参考答案' : '' }
  if (selected && reference) return { tone: 'correct', label: '已选 · 参考答案' }
  if (selected) return { tone: 'wrong', label: '学生错选' }
  if (reference) return { tone: 'missed', label: '漏选 · 参考答案' }
  return { tone: '', label: '' }
}

export function choiceMark(item: ReviewItem, key: string) {
  const selected = item.selection_status === 'reliable' && (item.selected_keys || []).includes(key)
  const reference = Boolean(item.reference_available && (item.reference_keys || []).includes(key))
  return optionMark(selected, reference, item.selection_status === 'reliable' && Boolean(item.reference_available))
}

export function truthMark(item: ReviewItem, value: boolean) {
  const selected = item.student_boolean === value
  const reference = Boolean(item.reference_available && item.reference_boolean === value)
  return optionMark(selected, reference,
    item.student_boolean != null && Boolean(item.reference_available) && item.reference_boolean != null)
}

function readablePart(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'boolean') return value ? '正确' : '错误'
  if (typeof value === 'string' || typeof value === 'number') return String(value).trim()
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    for (const key of ['text', 'value', 'raw_text', 'answer']) {
      if (record[key] !== undefined) return readablePart(record[key])
    }
  }
  return ''
}

/** Show only answer content; never serialize internal response metadata. */
export function readableAnswer(value: unknown): string {
  if (value == null) return '未作答'
  if (typeof value !== 'object') return readablePart(value) || '未作答'
  const row = value as Record<string, unknown>
  if (row.answered === false) return '未作答'
  const keys = Array.isArray(row.keys) ? row.keys.map(readablePart).filter(Boolean) : []
  if (keys.length) return keys.join('、')
  if (row.key !== undefined) return readablePart(row.key) || '未作答'
  for (const field of ['parts', 'values', 'texts']) {
    const entries = row[field]
    if (Array.isArray(entries)) {
      const parts = entries.map((entry, index) => {
        if (entry && typeof entry === 'object' && Array.isArray((entry as Record<string, unknown>).accepted)) {
          const accepted = ((entry as Record<string, unknown>).accepted as unknown[])
            .map(readablePart).filter(Boolean)
          return accepted.join(' / ')
        }
        if (entry && typeof entry === 'object' && 'label' in entry) {
          const part = entry as Record<string, unknown>
          return `${readablePart(part.label) || `第${index + 1}项`}：${readablePart(part.value) || '未作答'}${part.unit ? ` ${readablePart(part.unit)}` : ''}`
        }
        return readablePart(entry)
      }).filter(Boolean)
      if (parts.length) return parts.join('；')
    }
  }
  for (const field of ['value', 'text', 'raw_text', 'reference_answer']) {
    if (row[field] !== undefined) {
      const answer = readablePart(row[field])
      return answer ? `${answer}${row.unit ? ` ${readablePart(row.unit)}` : ''}` : '未作答'
    }
  }
  return '未作答'
}

export function studentAnswer(item: ReviewItem): string {
  if (item.selection_status === 'reliable' && item.selected_keys?.length) {
    return item.selected_keys.join('、')
  }
  return readableAnswer(item.student_response)
}

export function referenceAnswer(item: ReviewItem): string {
  if (!item.reference_available) return '待核对'
  if (item.reference_keys?.length) return item.reference_keys.join('、')
  return readableAnswer(item.answer_reference?.payload)
}

/** Imported answers sometimes contain bare LaTeX commands without delimiters. */
export function mathReadyAnswer(text: string): string {
  const mathSegment = /(\$\$[\s\S]*?\$\$|\$[^$]*?\$)/g
  return text.split(mathSegment).map((part, index) => {
    if (index % 2) return part
    return part
      .replace(/\\frac\{(?:[^{}]|\{[^{}]*\})*\}\{(?:[^{}]|\{[^{}]*\})*\}/g,
        formula => `$${formula}$`)
      .replace(/\\sum\{(?:[^{}]|\{[^{}]*\})*\}/g,
        formula => `$${formula}$`)
  }).join('')
}
