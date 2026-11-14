import type { DiagnosisView, NextAction, NextActionInput } from '../types/viewModels'

const STATUS_LABELS: Record<string, string> = {
  in_progress: '作答中', submitted: '已交卷', completed: '已完成', complete: '已完成',
  pending: '等待中', queued: '排队中', running: '生成中', failed: '失败', cancelled: '已取消',
  open: '待订正', corrected: '已订正', verified: '已巩固', due: '待巩固', overdue: '已到期',
  active: '进行中', archived: '已归档', ready: '可使用', processing: '处理中',
  practice: '练习模式', quiz: '模拟测验', not_started: '尚未开始',
}

const QUESTION_TYPE_LABELS: Record<string, string> = {
  single_choice: '单选题', multi_choice: '多选题', true_false: '判断题', numeric: '数值题',
  term: '名词解释', short_answer: '简答题', derivation: '推导题', comprehensive: '综合题',
  fill_blank: '填空题', blank: '填空题', fill_in: '填空题', matching: '匹配题',
  coding: '编程题', programming: '编程题', code: '编程题', unknown: '其他题型',
}

const KIND_LABELS: Record<string, string> = {
  retest: '巩固复测', review: '复习', practice: '专项练习', reading: '资料学习',
  quiz: '模拟测验', correction: '错题订正', delayed: '延迟复测',
  new_transfer: '新题迁移', original_correction: '原题订正',
}

export function statusLabel(value?: string | null, fallback = '状态未知') {
  if (!value) return fallback
  return STATUS_LABELS[value] || value
}

export function questionTypeLabel(value?: string | null) {
  if (!value) return '题目'
  return QUESTION_TYPE_LABELS[value] || '其他题型'
}

export function friendlyQuizCode(value: unknown): string {
  const code = String(value || '').trim()
  return /^(paper|attempt|release|av|assess|assessment|quiz)_[\w-]{8,}$/i.test(code) ? '' : code
}

export function kindLabel(value?: string | null) {
  if (!value) return '学习任务'
  return KIND_LABELS[value] || value
}

export function formatDateTime(value?: string | number | Date | null, empty = '暂无时间') {
  if (!value) return empty
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return empty
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date)
}

export function formatPercent(value?: number | null, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '暂无'
  const normalized = Number(value) <= 1 ? Number(value) * 100 : Number(value)
  return `${normalized.toFixed(digits)}%`
}

export function answerPayload(response: Record<string, unknown> | null | undefined) {
  if (!response || typeof response !== 'object') return {}
  return Object.fromEntries(Object.entries(response).filter(([key]) => !key.startsWith('__')))
}

export function hasMeaningfulAnswer(response: Record<string, unknown> | null | undefined) {
  const values = Object.values(answerPayload(response))
  return values.some((value) => {
    if (Array.isArray(value)) return value.length > 0
    if (typeof value === 'string') return value.trim().length > 0
    return value !== null && value !== undefined
  })
}

export function formatAnswer(response: Record<string, unknown> | null | undefined) {
  const value = answerPayload(response)
  if ('keys' in value && Array.isArray(value.keys)) return value.keys.join('、') || '未作答'
  if ('key' in value) return String(value.key || '未作答')
  if ('parts' in value && Array.isArray(value.parts)) {
    const parts = value.parts.map((item, index) => {
      if (item && typeof item === 'object') {
        const part = item as Record<string, unknown>
        const label = String(part.label || `第 ${index + 1} 项`)
        const unit = part.unit ? ` ${String(part.unit)}` : ''
        return `${label}：${String(part.value ?? '未作答')}${unit}`
      }
      return String(item ?? '未作答')
    })
    return parts.join('；') || '未作答'
  }
  if ('values' in value && Array.isArray(value.values)) {
    const blanks = value.values.map((item) => {
      if (item && typeof item === 'object') {
        const accepted = (item as Record<string, unknown>).accepted
        return Array.isArray(accepted) ? String(accepted[0] ?? '未作答') : '未作答'
      }
      return String(item ?? '未作答')
    })
    return blanks.join('；') || '未作答'
  }
  if ('value' in value) {
    if (typeof value.value === 'boolean') return value.value ? '正确' : '错误'
    const unit = value.unit ? ` ${String(value.unit)}` : ''
    return `${String(value.value ?? '未作答')}${unit}`
  }
  if ('reference_answer' in value) return String(value.reference_answer || '未作答')
  if ('text' in value) return String(value.text || '未作答')
  if (!Object.keys(value).length) return '未作答'
  return Object.entries(value).map(([key, item]) => `${key}：${String(item)}`).join('；')
}

/**
 * 资料统一显示名（与后端 material_naming.normalize 同一套口径）。
 * 后端 upload_public / selection_basis 已直接给 display_name，
 * 这里只用于没有该字段的兜底场景（例如答案册里的原卷名）。
 * 只改变展示文字，磁盘文件与 filename 一律不改名。
 */
const MATERIAL_KIND_LABELS: Record<string, string> = {
  exam_paper: '考试卷', slides: '老师课件', notes: '笔记',
  code: '实验代码', other: '其他资料',
}

export function materialDisplayName(filename: string, materialType = '') {
  const raw = String(filename || '未命名资料').split('/').pop() || '未命名资料'
  let stem = raw.replace(/\.(pdf|docx|pptx|xlsx|ipynb|html|markdown|md|txt|py|png|jpe?g)$/i, '')
  let term = ''
  const match = stem.match(/20\d{2}\s*[-–]\s*(?:20\d{2}|\d{1,2})/)
  const year = stem.match(/20\d{2}(?!\d)/)
  if (match && match.index !== undefined) {
    term = match[0].replace(/\s/g, '')
    stem = stem.slice(0, match.index) + ' ' + stem.slice(match.index + match[0].length)
    stem = stem.replace(/[-–]\s*\d\s*/g, ' ')
  } else if (year && year.index !== undefined) {
    term = year[0]
    stem = stem.slice(0, year.index) + ' ' + stem.slice(year.index + year[0].length)
  }
  stem = stem.replace(/^\s*\d+\s*[.、\-_]\s*/, '')
  stem = stem.replace(/[（(]\s*([AB])\s*[)）]/gi, ' $1卷')
  stem = stem.replace(/(?:Asever|验证用|已核验|验证|副本|copy|final|desktop)/gi, ' ')
  const topic = stem.replace(/[\s._\-–]+/g, ' ').trim().slice(0, 28) || '未命名资料'

  let kind = 'other'
  if (materialType in MATERIAL_KIND_LABELS) kind = materialType
  else if (/期末|考试|试卷|A卷|B卷|真题/.test(raw)) kind = 'exam_paper'
  else if (/笔记|note|复习资料/i.test(raw)) kind = 'notes'
  else if (/课件|lecture|slide/i.test(raw)) kind = 'slides'
  return `${MATERIAL_KIND_LABELS[kind]} · ${topic}${term ? `（${term}）` : ''}`
}

/** 程序化触发同源文件下载，不依赖 window.open。
 * 嵌入式预览面板常拦截新标签页，而 <a download> 在当前页面内即可完成。
 */
export function downloadFile(url: string, filename = '') {
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.rel = 'noopener'
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
}

export function friendlyError(error: unknown, fallback = '操作没有完成，请稍后重试') {
  const candidate = error as any
  const detail = candidate?.response?.data?.detail ?? candidate?.message ?? candidate
  if (typeof detail === 'string') {
    try {
      const parsed = JSON.parse(detail)
      return friendlyError(parsed, fallback)
    } catch {
      return detail.replace(/^Error:\s*/i, '') || fallback
    }
  }
  if (Array.isArray(detail)) return detail.map((item) => friendlyError(item, '')).filter(Boolean).join('；') || fallback
  if (detail && typeof detail === 'object') {
    return String(detail.message || detail.msg || detail.reason || detail.detail || fallback)
  }
  return fallback
}

export function diagnosisView(item: any): DiagnosisView {
  const payload = item?.result || item?.payload || item?.report || {}
  const list = (value: unknown) => Array.isArray(value)
    ? value.map((entry) => typeof entry === 'string'
      ? entry
      : entry?.kp_name || entry?.name || entry?.title || entry?.message).filter(Boolean)
    : []
  const weaknesses = list(payload.weaknesses || payload.weak_points || payload.priority_kps)
  const strengths = list(payload.strengths || payload.strong_points)
  const suggestions = list(payload.suggestions || payload.recommendations || payload.next_steps)
  return {
    run_id: item?.run_id || item?.id,
    target_id: item?.target_id,
    status: item?.status || 'completed',
    status_label: statusLabel(item?.status || 'completed'),
    summary: payload.summary || item?.summary || (weaknesses.length ? `建议优先巩固 ${weaknesses[0]}` : '诊断已生成'),
    strengths,
    weaknesses,
    suggestions,
    created_at: item?.created_at,
    raw: item,
  }
}

export function pickNextAction(input: NextActionInput): NextAction {
  if (input.activeAttempt?.attempt_id) {
    return {
      kind: 'continue', priority: 1, eyebrow: '继续学习', title: '继续未完成的练习',
      description: input.activeAttempt.paper_title || '你的答案已自动保存，可以接着上次的位置继续。',
      actionLabel: '继续答题', to: `/exam/${input.activeAttempt.attempt_id}`,
    }
  }
  const retest = input.dueRetests?.[0]
  if (retest) {
    const name = retest.kp_name || retest.title || '薄弱考点'
    return {
      kind: 'retest', priority: 2, eyebrow: '今日到期', title: `巩固「${name}」`,
      description: '趁遗忘前完成一次短练习，让知识真正稳定下来。', actionLabel: '开始巩固',
      to: `/compose?tab=create&kp=${encodeURIComponent(retest.kp_id || '')}`,
    }
  }
  const plan = input.todayPlans?.find((item) => !['completed', 'complete', 'done'].includes(item.status || ''))
  if (plan) {
    return {
      kind: 'plan', priority: 3, eyebrow: '今日计划', title: plan.title || '完成今天的学习任务',
      description: '按计划完成一个小目标，学习节奏会更稳定。', actionLabel: '查看计划', to: '/reports?tab=plan',
    }
  }
  const weak = input.weakStates?.[0]
  if (weak) {
    const name = weak.kp_name || '当前薄弱考点'
    return {
      kind: 'weakness', priority: 4, eyebrow: '优先提升', title: `专项练习：${name}`,
      description: '根据最近作答证据推荐，从最薄弱的地方开始。', actionLabel: '立即练习',
      to: `/compose?tab=create&kp=${encodeURIComponent(weak.kp_id)}`,
    }
  }
  if (!input.hasMaterials) {
    return {
      kind: 'import', priority: 5, eyebrow: '开始使用', title: '导入第一份课程资料',
      description: '导入讲义或试卷后，平台会整理题目并生成练习。', actionLabel: '导入资料', to: '/materials?action=import',
    }
  }
  return {
    kind: 'quick', priority: 6, eyebrow: '开始学习', title: '来一组快速练习',
    description: '用几道题快速检验今天的掌握情况。', actionLabel: '快速练习', to: '/compose?tab=quick',
  }
}
export type StudentIdentity = {
  user_id?: string
  student_code?: string | null
  classroom_name?: string | null
  identity_scope?: 'official' | 'test' | 'unassigned' | null
}

export function studentIdentityLabel(identity: StudentIdentity | null | undefined) {
  if (!identity) return '当前学生身份未加载'
  const student = identity.student_code || '未绑定档案'
  const account = identity.user_id || '未知账号'
  if (identity.identity_scope === 'official') {
    return `${student} · ${account} · ${identity.classroom_name || '正式班'}`
  }
  if (identity.identity_scope === 'test') {
    return `${student} · ${account} · 测试数据范围`
  }
  return `${student} · ${account}`
}

export function noTaskDescription(identity: StudentIdentity | null | undefined) {
  const account = identity?.user_id || '当前账号'
  const student = identity?.student_code || '未绑定档案'
  return `${account}（档案 ${student}）当前没有待完成任务`
}
