<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import MathText from '../components/MathText.vue'
import MasonryGrid from '../components/MasonryGrid.vue'
import OriginalPreview from '../components/OriginalPreview.vue'
import PageImage from '../components/PageImage.vue'
import { friendlyError, questionTypeLabel } from '../utils/presentation'

type TagType = '' | 'success' | 'warning' | 'info' | 'danger'

interface ListResponse<T> {
  total: number
  card_count?: number
  dedup?: { raw_count: number; unique_count: number; merged_count: number }
  items: T[]
}

interface KnowledgePoint {
  kp_id: string
  name: string
  chapter: string
}

interface QuestionOption {
  key: string
  text: string
}

interface RubricPoint {
  id?: string
  point?: string
  text?: string
  score?: number
}

/** /api/wrong-book 中 question 是 student_view(q, reveal=True) 的结果。 */
interface QuestionView {
  question_id?: string
  question_version?: number
  question_type?: string
  stem?: string
  options?: QuestionOption[]
  answer_payload?: Record<string, any>
  explanation?: string
  rubric?: { points?: RubricPoint[]; reference_answer?: string } | null
  answer_label?: string
  answer_origin?: string
  answer_status?: string
  review_status?: string
  options_incomplete?: boolean
  repair_hint?: string
  prompt_prefix?: string
  score?: number
  source_refs?: Array<Record<string, any>>
}

interface SelectionSource {
  kind: string
  kind_label: string
  kind_basis: string
  display_name: string
  raw_name: string
  rel_path: string
  page?: number | null
  effective_page?: number | null
  page_inferred: boolean
  jump_kind: 'original' | 'page' | 'search'
  lookup_term: string
  materials_url: string
  document_id: string
  upload_id: string
  mime: string
  chunk_label: string
  note: string
  page_image_url: string
  original_url: string
  can_open_original: boolean
}

interface SelectionBasis {
  reason: string
  conditions: string[]
  blueprint_title: string
  paper_id: string
  sources: SelectionSource[]
  source_count: number
  note: string
}

interface WrongHistory {
  entry_id: string
  attempt_id: string
  question_id: string
  question_version: number
  created_at: string
  original_response: Record<string, any>
  lost_points: number
  status: string
  status_label?: string
}

interface WrongEntry {
  entry_id: string
  attempt_id: string
  question_id: string
  question_version: number
  kp_id: string
  family_id: string
  original_response: Record<string, any>
  lost_points: number
  misconception_candidates: string[]
  correction: string
  self_report: string
  status: string
  created_at: string
  material_refs: Record<string, any>[]
  question?: QuestionView | null
  selection_basis?: SelectionBasis
  // V3.3 展示去重：同指纹只出一张卡，历次作答完整挂在 history
  question_fingerprint?: string
  canonical_question_id?: string
  canonical_entry_id?: string
  attempt_count?: number
  duplicate_count?: number
  source_question_ids?: string[]
  history?: WrongHistory[]
}

interface RetestTask {
  task_id: string
  kp_id: string
  from_wrong_id: string
  family_id: string
  kind: string
  due_at?: string
  status: string
  paper_id?: string
  result?: Record<string, any>
  created_at: string
}

interface KnowledgeState {
  kp_id: string
  state: string
  state_label?: string
  evidence_count: number
  independent_family_count: number
  time_span_days: number
  explanation?: string
  kp_name?: string
  chapter?: string
}

interface RetestGroup {
  kpId: string
  tasks: RetestTask[]
  earliestDue: string
}

interface ExternalItem {
  item_id: string
  kind: string
  scope_class: string
  scope_label?: string
  title: string
  url?: string
  url_verified?: boolean
  recommended?: string
}

const route = useRoute()
const router = useRouter()
const wrong = ref<ListResponse<WrongEntry>>({ total: 0, card_count: 0, items: [] })
const tasks = ref<ListResponse<RetestTask>>({ total: 0, items: [] })
const knowledgePoints = ref<KnowledgePoint[]>([])
const states = ref<KnowledgeState[]>([])
const criteria = ref<{ min_evidence: number; min_families: number; stable_days: number; retest_intervals_days: number[] }>({
  min_evidence: 3, min_families: 2, stable_days: 7, retest_intervals_days: [1, 3, 7],
})
const loading = ref(true)
const error = ref('')
const notice = ref('')
const generateBusy = ref(false)
const startBusy = ref('')
const expanded = ref('')
// 「解析展开」与「订正编辑」互相独立：收回解析不应影响正在写的订正。
const analysisOpen = ref<Record<string, boolean>>({})
// 「选题依据」右侧抽屉：与解析、订正三套状态互不干扰。
const basisOpen = ref(false)
const basisEntry = ref<WrongEntry | null>(null)
const sourcePageKey = ref('')
const originalOpen = ref(false)
const originalTarget = ref<SelectionSource | null>(null)
const statusFilter = ref('all')
const kpFilter = ref('')
const visibleCount = ref(8)
const technicalOpen = ref(false)

const extByKp = ref<Record<string, ExternalItem[]>>({})
const extLoading = ref('')

const kpMap = computed(() => new Map(knowledgePoints.value.map((item) => [item.kp_id, item])))

function kpName(kpId: string) {
  return kpMap.value.get(kpId)?.name || '未命名考点'
}

function kpChapter(kpId: string) {
  return kpMap.value.get(kpId)?.chapter || '课程考点'
}

/** 用一个不同题族的新题去验证这个考点。 */
function practiceKp(kpId: string) {
  if (!kpId) return
  router.push({ path: '/compose', query: { tab: 'quick', kp: kpId } })
}

function wrongStatusLabel(value: string) {
  const labels: Record<string, string> = {
    open: '待订正', corrected: '已订正', verified: '已巩固',
  }
  return labels[value] || '待处理'
}

function wrongStatusType(value: string): TagType {
  if (value === 'verified') return 'success'
  if (value === 'corrected') return 'warning'
  return 'danger'
}

function retestStatusLabel(value: string) {
  const labels: Record<string, string> = {
    pending: '待完成', running: '进行中', completed: '已完成', failed: '未通过',
  }
  return labels[value] || '待处理'
}

function retestKindLabel(value: string) {
  const labels: Record<string, string> = {
    new_transfer: '新题迁移', delayed: '延迟复测', immediate: '即时巩固',
  }
  return labels[value] || '巩固练习'
}

function formatDate(value?: string, withTime = false) {
  if (!value) return '未设置'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', withTime
    ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }
    : { year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
}

function isDue(task: RetestTask) {
  if (task.status !== 'pending') return false
  if (!task.due_at) return true
  const due = new Date(task.due_at).getTime()
  return !Number.isNaN(due) && due <= Date.now()
}

function groupRetests(items: RetestTask[]): RetestGroup[] {
  const grouped = new Map<string, RetestTask[]>()
  for (const task of items) {
    const key = task.kp_id || '__unknown__'
    grouped.set(key, [...(grouped.get(key) || []), task])
  }
  return Array.from(grouped.entries()).map(([kpId, groupedTasks]) => {
    const sorted = groupedTasks.slice().sort((a, b) =>
      new Date(a.due_at || 0).getTime() - new Date(b.due_at || 0).getTime())
    return { kpId, tasks: sorted, earliestDue: sorted[0]?.due_at || '' }
  }).sort((a, b) => new Date(a.earliestDue || 0).getTime() - new Date(b.earliestDue || 0).getTime())
}

const dueGroups = computed(() => groupRetests(tasks.value.items.filter(isDue)))
const upcomingGroups = computed(() => groupRetests(tasks.value.items.filter((item) => item.status === 'pending' && !isDue(item))))
const completedRetests = computed(() => tasks.value.items.filter((item) => item.status === 'completed'))

const wrongKpOptions = computed(() => {
  const ids = Array.from(new Set(wrong.value.items.map((item) => item.kp_id).filter(Boolean)))
  return ids.sort((a, b) => kpName(a).localeCompare(kpName(b), 'zh-CN'))
})

const filteredWrong = computed(() => wrong.value.items.filter((item) => {
  const statusMatch = statusFilter.value === 'all' || item.status === statusFilter.value
  const kpMatch = !kpFilter.value || item.kp_id === kpFilter.value
  return statusMatch && kpMatch
}))

const visibleWrong = computed(() => filteredWrong.value.slice(0, visibleCount.value))
const hasMore = computed(() => visibleWrong.value.length < filteredWrong.value.length)

watch([statusFilter, kpFilter], () => {
  visibleCount.value = 8
  expanded.value = ''
  analysisOpen.value = {}
})

async function refresh() {
  const [w, t, k, s] = await Promise.all([
    api.get<ListResponse<WrongEntry>>('/wrong-book'),
    api.get<ListResponse<RetestTask>>('/retest-tasks'),
    api.get<ListResponse<KnowledgePoint>>('/knowledge-points'),
    api.get<{ items: KnowledgeState[]; criteria: typeof criteria.value }>('/knowledge-states'),
  ])
  wrong.value = w
  tasks.value = t
  knowledgePoints.value = k.items || []
  states.value = (s.items || []).slice().sort((a, b) => stateRank(a.state) - stateRank(b.state))
  if (s.criteria) criteria.value = s.criteria
}

const STATE_ORDER: Record<string, number> = {
  需加强: 0, 证据不足: 1, 未测: 2, 初步通过: 3, 阶段稳定: 4,
}
const STATE_TONE: Record<string, string> = {
  需加强: 'danger', 证据不足: 'warning', 未测: 'info',
  初步通过: 'success', 阶段稳定: 'success',
}
function stateRank(value: string) { return STATE_ORDER[value] ?? 9 }
function stateTone(value: string) { return (STATE_TONE[value] || 'info') as any }
function stateLabel(value: string) {
  return value === '需加强' ? '需加强' : value || '未测'
}

/** 距离「学会」还差什么：把规则讲成人话，学生一眼知道下一步做什么。 */
function gapText(row: KnowledgeState) {
  const needEvidence = Math.max(0, criteria.value.min_evidence - (row.evidence_count || 0))
  const needFamilies = Math.max(0, criteria.value.min_families - (row.independent_family_count || 0))
  const needDays = Math.max(0, criteria.value.stable_days - Math.round(row.time_span_days || 0))
  if (row.state === '阶段稳定') return '已满足跨日期与延迟复测证据'
  if (row.state === '需加强') return '先在错题本订正，再用不同题族的新题做对一次'
  if (row.state === '初步通过') return needDays > 0 ? `再做 ${needDays} 天后的延迟复测即可到「阶段稳定」` : '安排一次延迟复测即可升级'
  if (row.state === '未测') return `还没有作答证据：需要 ${criteria.value.min_evidence} 条独立作答、${criteria.value.min_families} 个不同题族`
  const parts: string[] = []
  if (needEvidence > 0) parts.push(`${needEvidence} 条独立证据`)
  if (needFamilies > 0) parts.push(`${needFamilies} 个不同题族`)
  return parts.length ? `还差 ${parts.join('、')}` : '再做一道不同题族的新题即可判定'
}

const focusStates = computed(() => states.value.slice(0, 8))

async function generateRetests() {
  notice.value = ''
  generateBusy.value = true
  try {
    const result = await api.post<Record<string, any>>('/retest-tasks/generate', {})
    const created = Number(result.created || 0)
    notice.value = created
      ? `已按间隔规则新增 ${created} 个巩固任务，到期后会在本页置顶提醒。`
      : '当前错题都已经安排了巩固任务，没有重复创建。'
    await refresh()
  } catch (err: any) {
    notice.value = `巩固任务暂时未生成，但错题记录已经安全保存。${err?.message ? ` 原因：${err.message}` : ''}`
  } finally {
    generateBusy.value = false
  }
}

async function startRetest(group: RetestGroup) {
  const task = group.tasks[0]
  if (!task) return
  startBusy.value = group.kpId
  notice.value = ''
  try {
    const result = await api.post<Record<string, any>>(`/retest-tasks/${task.task_id}/start`, {})
    if (result.attempt_id) {
      await router.push(`/exam/${result.attempt_id}`)
    } else {
      notice.value = result.message || '暂时无法创建复测，请稍后重试。'
    }
  } catch (err: any) {
    notice.value = `复测启动失败：${err?.message || String(err)}`
  } finally {
    startBusy.value = ''
  }
}

async function saveEntry(entry: WrongEntry) {
  try {
    await api.patch(`/wrong-book/${entry.entry_id}`, {
      correction: entry.correction,
      self_report: entry.self_report,
      status: entry.status,
    })
    ElMessage.success('订正已保存')
    await refresh()
  } catch (err: any) {
    ElMessage.error(err?.message || '订正保存失败')
  }
}

async function loadExt(kpId: string) {
  if (!kpId) return
  if (extByKp.value[kpId]) {
    const next = { ...extByKp.value }
    delete next[kpId]
    extByKp.value = next
    return
  }
  extLoading.value = kpId
  try {
    const result = await api.get<ListResponse<ExternalItem>>('/external/items', { kp_id: kpId })
    const items = (result.items || [])
      .filter((item) => item.kind === 'video' || item.scope_class === 'extension')
      .slice(0, 4)
    extByKp.value = { ...extByKp.value, [kpId]: items }
  } catch (err: any) {
    ElMessage.error(err?.message || '学习资源加载失败')
  } finally {
    extLoading.value = ''
  }
}

function toggleAnalysis(entryId: string) {
  analysisOpen.value = { ...analysisOpen.value, [entryId]: !analysisOpen.value[entryId] }
}

// V3.3：同一题只显示一张卡，全部历次作答从这里查看（历史记录一条不删）
const historyOpen = ref(false)
const historyEntry = ref<WrongEntry | null>(null)

function openHistory(entry: WrongEntry) {
  historyEntry.value = entry
  historyOpen.value = true
}

function historyRows(entry: WrongEntry | null): WrongHistory[] {
  return [...(entry?.history || [])].sort((a, b) =>
    String(b.created_at || '').localeCompare(String(a.created_at || '')))
}

function historyResponseText(row: WrongHistory) {
  const q = historyEntry.value?.question
  return formatAnswerText(row.original_response, q?.question_type || '', q?.options || [])
}

function openBasis(entry: WrongEntry) {
  basisEntry.value = entry
  sourcePageKey.value = ''
  basisOpen.value = true
}

function sourceKey(source: SelectionSource) {
  return `${source.document_id || source.raw_name}:${source.page || 0}`
}

function toggleSourcePage(source: SelectionSource) {
  const key = sourceKey(source)
  sourcePageKey.value = sourcePageKey.value === key ? '' : key
}

function openSourceOriginal(source: SelectionSource) {
  originalTarget.value = source
  originalOpen.value = true
}

function openSourceInMaterials(source: SelectionSource) {
  basisOpen.value = false
  router.push({ path: source.materials_url || '/materials',
    query: { q: source.lookup_term } })
}

function questionOf(entry: WrongEntry): QuestionView {
  return entry.question || {}
}

function typeOf(entry: WrongEntry) {
  return questionOf(entry).question_type || ''
}

function typeLabel(entry: WrongEntry) {
  return questionTypeLabel(typeOf(entry) || 'unknown')
}

function answerKeys(payload: Record<string, any> | null | undefined): string[] {
  if (!payload || typeof payload !== 'object') return []
  if (Array.isArray(payload.keys)) return payload.keys.map(String)
  if (Array.isArray(payload.key)) return payload.key.map(String)
  if (payload.key !== undefined && payload.key !== null && payload.key !== '') {
    return [String(payload.key)]
  }
  // 判断题存的是 {"value": true|false}
  if (typeof payload.value === 'boolean') return [payload.value ? 'true' : 'false']
  return []
}

function optionText(options: QuestionOption[] | undefined, key: string) {
  const match = (options || []).find((item) =>
    String(item.key).toLowerCase() === String(key).toLowerCase())
  return match?.text ? `${key}：${match.text}` : String(key)
}

function truthLabel(value: any) {
  if (typeof value === 'boolean') return value ? '正确' : '错误'
  const raw = String(value ?? '').trim().toLowerCase()
  if (['true', '1', 't', 'yes', 'y', '对', '正确', '√', 'a'].includes(raw)) return '正确'
  if (['false', '0', 'f', 'no', 'n', '错', '错误', '×', 'b'].includes(raw)) return '错误'
  return String(value)
}

function plainPayload(payload: any): Record<string, any> {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return {}
  return Object.fromEntries(Object.entries(payload).filter(([key]) =>
    !key.startsWith('__') && !['hints_used', 'solution_viewed'].includes(key)))
}

/** 把「我的作答」或「参考答案」按题型统一渲染成可读文本。 */
function formatAnswerText(payload: any, qtype: string,
  options?: QuestionOption[], reference = false) {
  const value = plainPayload(payload)
  const fallback = reference ? '暂无参考答案记录' : '未作答'
  if (!Object.keys(value).length) return fallback

  const keys = answerKeys(value)
  if (keys.length) {
    if (qtype === 'true_false') return `判断为：${truthLabel(keys[0])}`
    return `选择 ${keys.map((key) => optionText(options, key)).join('；')}`
  }
  if (qtype === 'true_false' && value.value !== undefined) {
    return `判断为：${truthLabel(value.value)}`
  }
  if (value.value !== undefined) {
    const unit = value.unit ? ` ${String(value.unit)}` : ''
    const tol = reference && value.tol != null && Number(value.tol) !== 0
      ? `（容差 ${value.tol}）` : ''
    return `${reference ? '参考答案：' : '回答：'}${String(value.value)}${unit}${tol}`
  }

  // 主观题：参考答案优先取 reference_answer，学生作答优先取 text。
  const fields = reference
    ? ['reference_answer', 'answer', 'text', 'content', 'formula', 'result']
    : ['text', 'content', 'answer', 'reference_answer']
  for (const field of fields) {
    const text = value[field]
    if (typeof text === 'string' && text.trim()) return text.trim()
    if (Array.isArray(text) && text.length) return text.map(String).join('；')
  }
  const rest = Object.entries(value).filter(([key]) =>
    !['tol', 'rounding', 'partial_rule', 'answer_version', 'confidence'].includes(key))
  if (rest.length) {
    return rest.map(([key, item]) =>
      `${key}：${typeof item === 'object' ? JSON.stringify(item) : String(item)}`).join('；')
  }
  return fallback
}

function correctAnswerText(entry: WrongEntry) {
  const q = questionOf(entry)
  const fromPayload = formatAnswerText(q.answer_payload, typeOf(entry), q.options, true)
  if (fromPayload !== '暂无参考答案记录') return fromPayload
  // 少数推导题把标准答案写在 rubric.reference_answer，而不是 answer_payload。
  const rubricAnswer = q.rubric?.reference_answer
  if (typeof rubricAnswer === 'string' && rubricAnswer.trim()) return rubricAnswer.trim()
  return fromPayload
}

function friendlyResponse(entry: WrongEntry) {
  return formatAnswerText(entry.original_response, typeOf(entry), questionOf(entry).options, false)
}

/** 选项行：正确答案 / 上次错选 / 上次选对 / 普通，四种状态。
 *  没有可靠答案时不涂绿色，避免把「无答案」或 AI 推测冒充成标准答案。 */
function optionRows(entry: WrongEntry) {
  const q = questionOf(entry)
  const meta = answerMeta(entry)
  const correct = meta.has
    ? answerKeys(q.answer_payload).map((k) => String(k).toLowerCase()) : []
  const mine = answerKeys(entry.original_response).map((k) => String(k).toLowerCase())
    const isMulti = ['multi_choice'].includes(typeOf(entry))
    const isJudge = typeOf(entry) === 'true_false'
    const options = isJudge && !(q.options || []).length
      ? [{ key: 'true', text: '正确' }, { key: 'false', text: '错误' }]
      : (q.options || [])
  return options.map((option) => {
    const key = String(option.key).toLowerCase()
    // 判断题的「正确/错误」由 opt-key 显示，避免渲染成「正确 正确」。
    const judgeLabel = key === 'true' ? '正确' : key === 'false' ? '错误' : ''
    const text = judgeLabel && (!option.text || option.text === judgeLabel)
      ? '' : option.text
    const isCorrect = correct.includes(key)
    const isMine = mine.includes(key)
    const state = isCorrect && isMine ? 'correct-picked'
      : isCorrect ? 'correct-missed'
        : isMine ? 'wrong-picked' : 'neutral'
    const mark = state === 'correct-picked' ? '上次选择正确'
      : state === 'correct-missed'
        ? (isMulti
          ? `${meta.provisional ? '参考答案' : '正确答案'} · 上次漏选`
          : (meta.provisional ? 'AI参考答案' : '正确答案'))
        : state === 'wrong-picked' ? '上次错选' : ''
    return { key: option.key, text, isCorrect, isMine, state, mark }
  })
}

/** 答案来源与措辞：AI 生成 / 待核验答案只能叫「参考答案」，不能冒充标准答案。 */
function answerMeta(entry: WrongEntry) {
  const q = questionOf(entry)
  const origin = q.answer_origin || ''
  const isAi = ['deepseek_generated', 'ai_generated', 'ai_candidate'].includes(origin)
  const provisional = isAi || q.answer_status === 'candidate'
    || q.review_status === 'pending'
  const text = correctAnswerText(entry)
  const has = text !== '暂无参考答案记录' && q.answer_label !== '暂无参考答案'
  return {
    has, isAi, provisional, text,
    label: q.answer_label || (has ? '其他来源答案' : '暂无参考答案'),
    heading: provisional ? '参考答案（待核验）' : '正确答案',
  }
}

/** 上次选择 / 正确答案的简写文本，用于卡片底部汇总。 */
function answerSummary(entry: WrongEntry) {
  const q = questionOf(entry)
  const meta = answerMeta(entry)
  const isChoice = ['single_choice', 'multi_choice', 'true_false'].includes(typeOf(entry))
  const mine = answerKeys(entry.original_response)
  const correct = meta.has ? answerKeys(q.answer_payload) : []
  if (isChoice) {
    const label = (key: string) => {
      if (key === 'true') return '正确'
      if (key === 'false') return '错误'
      return key.toUpperCase()
    }
    const norm = (keys: string[]) => keys.map((k) => k.toLowerCase()).sort()
    const allCorrect = correct.length > 0
      && norm(mine).length === norm(correct).length
      && norm(mine).every((k, i) => k === norm(correct)[i])
    return {
      isChoice: true, allCorrect,
      mine: mine.length ? mine.map(label).join('、') : '未作答',
      correct: correct.length ? correct.map(label).join('、') : '',
    }
  }
  return { isChoice: false, allCorrect: false, mine: '', correct: '' }
}

function rubricRows(entry: WrongEntry) {
  const points = questionOf(entry).rubric?.points
  return Array.isArray(points) ? points : []
}

function analysisText(entry: WrongEntry) {
  return (questionOf(entry).explanation || '').trim()
}

function sourceLabel(entry: WrongEntry) {
  const refs = questionOf(entry).source_refs || []
  const found = refs.find((ref) => ref.page || ref.rel_path)
  if (!found) return ''
  return [found.rel_path, found.page ? `第 ${found.page} 页` : ''].filter(Boolean).join(' · ')
}

function pageOf(refs: Record<string, any>[]) {
  const source = (refs || []).find((item) => item.assets?.length && item.document_id && item.page)
  return source ? { docId: source.document_id as string, page: Number(source.page), label: source.rel_path as string } : null
}

onMounted(() => {
  // 深链：?compose=1 直接打开组卷弹窗，便于演示与截图
  if (String(route.query.compose || '') === '1') openCompose()
})

onMounted(async () => {
  try {
    await refresh()
  } catch (err: any) {
    error.value = err?.message || String(err)
  } finally {
    loading.value = false
  }
})
/* ---------------- V3.0 错题复测：原题重做 + AI 相似题 ---------------- */
const retestBusy = ref('')

async function retestOriginal(entry: any) {
  if (retestBusy.value) return
  retestBusy.value = entry.entry_id
  try {
    const out: any = await api.post(`/wrong-book/${entry.entry_id}/retest`, {})
    ElMessage.success(out.message || '已生成重做卷')
    await router.push(`/exam/${out.attempt_id}`)
  } catch (error) {
    ElMessage.error(friendlyError(error, '无法重做原题'))
  } finally {
    retestBusy.value = ''
  }
}

async function generateSimilar(entry: any) {
  if (retestBusy.value) return
  retestBusy.value = `${entry.entry_id}:sim`
  try {
    const out: any = await api.post(`/wrong-book/${entry.entry_id}/similar`, { count: 1 })
    if (out.ok === false) {
      // 模型没配好 / 候选没过校验：如实告诉学生，不生成占位题
      ElMessage.warning(out.message || '这次没有生成可用的相似题')
      return
    }
    ElMessage.success(out.message || '已生成相似题')
    await router.push(`/exam/${out.attempt_id}`)
  } catch (error) {
    ElMessage.error(friendlyError(error, '相似题生成失败'))
  } finally {
    retestBusy.value = ''
  }
}

/* ---------------- V3.1 页面内组卷复测 ---------------- */
const composeOpen = ref(false)
const composeBusy = ref(false)
const composeResult = ref<any>(null)
const composeForm = reactive({
  scope: 'all',
  kp_id: '',
  count: 5,
  difficulty: 'same',
  mode: 'mix',
})

const composeKpOptions = computed(() => {
  const seen = new Map<string, string>()
  for (const item of visibleWrong.value || []) {
    // 接口在 WrongPatch 之外还回了 kp_name，这里按运行时字段读
    const row = item as any
    if (row.kp_id && row.kp_name && !seen.has(row.kp_id)) {
      seen.set(row.kp_id, row.kp_name)
    }
  }
  return [...seen.entries()].map(([value, label]) => ({ value, label }))
})

function openCompose(entry?: any) {
  composeResult.value = null
  if (entry) {
    composeForm.scope = 'kp'
    composeForm.kp_id = entry.kp_id || ''
  }
  composeOpen.value = true
}

async function runCompose() {
  if (composeBusy.value) return
  composeBusy.value = true
  composeResult.value = null
  try {
    const payload: any = {
      scope: composeForm.scope,
      kp_id: composeForm.scope === 'kp' ? composeForm.kp_id : '',
      count: composeForm.count,
      difficulty: composeForm.difficulty,
      use_original: composeForm.mode !== 'ai',
      use_ai: composeForm.mode !== 'original',
    }
    const out: any = await api.post('/wrong-book/retest-paper', payload)
    if (out.ok === false) {
      ElMessage.warning(out.message || '没有组成复测卷')
      return
    }
    composeResult.value = out
  } catch (error) {
    ElMessage.error(friendlyError(error, '组卷失败'))
  } finally {
    composeBusy.value = false
  }
}

function startComposed() {
  const attempt = composeResult.value?.attempt_id
  if (!attempt) return
  composeOpen.value = false
  router.push(`/exam/${attempt}`)
}

</script>

<template>
  <main v-loading="loading" class="wrong-page">
    <header class="page-head wrong-head">
      <div>
        <p class="eyebrow">错题复测</p>
        <h2>错题复测：重做原题，再练相似题</h2>
        <p>做对一次不等于学会。这里按「独立证据 × 不同题族 × 时间跨度 × 延迟复测」判定，
          每个考点都写明还差什么。</p>
      </div>
      <div class="head-actions">
        <el-button size="large" @click="openCompose()">组卷复测</el-button>
        <el-button type="primary" size="large" :loading="generateBusy" @click="generateRetests">
          安排巩固任务
        </el-button>
      </div>
    </header>

    <div v-if="error" class="hint error-hint" role="alert">{{ error }}</div>
    <div v-if="notice" class="notice" aria-live="polite">{{ notice }}</div>

            <section class="card wrong-list-card" aria-labelledby="wrong-list-title">
      <div class="section-heading list-heading">
        <div>
          <h3 id="wrong-list-title">错题记录</h3>
          <p>
            共 {{ wrong.card_count ?? wrong.total }} 道不重复错题，当前筛选出 {{ filteredWrong.length }} 道
            <span v-if="wrong.dedup?.merged_count"> · 合并展示 {{ wrong.dedup.merged_count }} 条重复作答，历史 {{ wrong.total }} 条全部保留</span>
          </p>
        </div>
        <div class="filters" aria-label="错题筛选">
          <el-select v-model="statusFilter" aria-label="按订正状态筛选">
            <el-option label="全部状态" value="all" />
            <el-option label="待订正" value="open" />
            <el-option label="已订正" value="corrected" />
            <el-option label="已巩固" value="verified" />
          </el-select>
          <el-select v-model="kpFilter" clearable filterable placeholder="全部考点" aria-label="按考点筛选">
            <el-option v-for="kpId in wrongKpOptions" :key="kpId" :label="kpName(kpId)" :value="kpId" />
          </el-select>
        </div>
      </div>

      <MasonryGrid v-if="visibleWrong.length" :items="visibleWrong" class="wrong-grid">
        <template #default="{ item: entry }">
        <article class="wrong-card">
          <div class="wc-tags">
            <span class="wc-type">{{ typeLabel(entry) }}</span>
            <span v-if="entry.kp_id" class="wc-kp">{{ kpName(entry.kp_id) }}</span>
            <span v-if="(entry.attempt_count || 1) > 1" class="wc-repeat">
              共 {{ entry.attempt_count }} 次作答
            </span>
          </div>

          <div class="wc-stem">
            <span v-if="questionOf(entry).prompt_prefix" class="wc-prompt">
              {{ questionOf(entry).prompt_prefix }}
            </span>
            <MathText :text="entry.question?.stem || '（题干缺失）'" />
          </div>

          <div v-if="questionOf(entry).options_incomplete" class="wc-option-missing">
            {{ questionOf(entry).repair_hint || '该题选项缺失，待修复' }}
          </div>

          <div v-if="optionRows(entry).length" class="wc-options">
            <div v-for="opt in optionRows(entry)" :key="opt.key"
              class="wc-option" :class="`is-${opt.state}`">
              <span v-if="opt.key !== 'true' && opt.key !== 'false'" class="opt-key">
                {{ opt.key.toUpperCase() }}.
              </span>
              <span v-else class="opt-key">{{ opt.key === 'true' ? '正确' : '错误' }}</span>
              <span v-if="opt.text" class="opt-text"><MathText :text="opt.text" /></span>
              <span v-if="opt.mark" class="opt-mark"
                :class="opt.state === 'wrong-picked' ? 'bad' : 'ok'">{{ opt.mark }}</span>
            </div>
          </div>

          <div v-if="answerSummary(entry).isChoice" class="wc-summary">
            <span>上次选择：<b>{{ answerSummary(entry).mine }}</b></span>
            <span v-if="answerMeta(entry).has">
              {{ answerMeta(entry).heading }}：<b class="ok">{{ answerSummary(entry).correct }}</b>
            </span>
            <span v-else><b class="wc-empty-answer">暂无标准答案</b></span>
            <span v-if="answerSummary(entry).allCorrect && !answerMeta(entry).provisional"
              class="wc-all-correct">上次选择正确</span>
            <span class="wc-answer-source">答案来源：{{ answerMeta(entry).label }}</span>
            <span v-if="answerMeta(entry).provisional && answerMeta(entry).has"
              class="wc-provisional">
              暂无可靠标准答案，以上为待核验参考答案
            </span>
          </div>

          <div v-else class="wc-summary wc-summary--text">
            <div>
              <span class="wc-label">上次作答</span>
              <MathText :text="friendlyResponse(entry) || '未作答'" />
            </div>
            <div>
              <span class="wc-label">{{ answerMeta(entry).heading }}</span>
              <MathText v-if="answerMeta(entry).has" :text="answerMeta(entry).text" />
              <span v-else class="wc-empty-answer">暂无标准答案</span>
            </div>
            <div v-if="rubricRows(entry).length" class="wc-rubric">
              <span class="wc-label">关键得分点</span>
              <ul>
                <li v-for="(point, index) in rubricRows(entry)" :key="point.id || index">
                  <MathText :text="point.point || point.text || ''" />
                  <span v-if="point.score != null" class="rubric-score">{{ point.score }} 分</span>
                </li>
              </ul>
            </div>
            <span class="wc-answer-source">答案来源：{{ answerMeta(entry).label }}</span>
            <span v-if="answerMeta(entry).provisional && answerMeta(entry).has"
              class="wc-provisional">
              暂无可靠标准答案，以上为待核验参考答案
            </span>
          </div>

          <div class="wc-actions">
            <el-button type="primary" size="small"
              :loading="retestBusy === entry.entry_id"
              @click="retestOriginal(entry)">重做原题</el-button>
            <el-button size="small"
              :loading="retestBusy === `${entry.entry_id}:sim`"
              @click="generateSimilar(entry)">AI 生成相似题</el-button>
            <el-button size="small" plain @click="toggleAnalysis(entry.entry_id)">
              {{ analysisOpen[entry.entry_id] ? '收起解析' : '查看解析' }}
            </el-button>
            <el-button v-if="(entry.attempt_count || 1) > 1" size="small" plain
              @click="openHistory(entry)">
              历次作答（{{ entry.attempt_count }}）
            </el-button>
          </div>

          <div v-if="analysisOpen[entry.entry_id]" class="wc-analysis">
            <div class="wc-correct">
              <span class="wc-label">{{ answerMeta(entry).heading }}</span>
              <MathText v-if="answerMeta(entry).has" :text="answerMeta(entry).text" />
              <span v-else class="wc-empty-answer">暂无标准答案</span>
            </div>
            <div v-if="rubricRows(entry).length" class="wc-explain">
              <span class="wc-label">关键得分点</span>
              <ul>
                <li v-for="(point, index) in rubricRows(entry)" :key="point.id || index">
                  <MathText :text="point.point || point.text || ''" />
                  <span v-if="point.score != null" class="rubric-score">{{ point.score }} 分</span>
                </li>
              </ul>
            </div>
            <div v-if="analysisText(entry)" class="wc-explain">
              <span class="wc-label">解题思路</span>
              <MathText :text="analysisText(entry)" />
            </div>
            <p v-else class="wc-explain-empty">
              这道题暂未记录解析，可对照正确答案与课程资料自行订正。
            </p>
            <button type="button" class="wc-basis-link" @click="openBasis(entry)">
              这道题从哪来
            </button>
          </div>
        </article>
        </template>
      </MasonryGrid>

      <div v-else class="empty-state">
        <div class="empty-icon" aria-hidden="true">✓</div>
        <h4>{{ wrong.total ? '没有符合筛选条件的错题' : '暂时没有错题' }}</h4>
        <p>{{ wrong.total ? '可以调整上方筛选条件。' : '完成练习后，答错的题目会自动归入这里。' }}</p>
      </div>

      <div v-if="hasMore" class="load-more">
        <el-button plain @click="visibleCount += 8">加载更多（还剩 {{ filteredWrong.length - visibleWrong.length }} 道）</el-button>
      </div>
    </section>

      </main>

  <el-drawer v-model="basisOpen" direction="rtl" size="min(560px, 100%)"
    :title="`选题依据 · ${kpName(basisEntry?.kp_id || '')}`">
    <template v-if="basisEntry?.selection_basis">
      <section class="basis-block">
        <h4>为什么选这道题</h4>
        <p class="basis-reason">{{ basisEntry.selection_basis.reason }}</p>
        <ul v-if="basisEntry.selection_basis.conditions?.length" class="basis-conditions">
          <li v-for="condition in basisEntry.selection_basis.conditions" :key="condition">
            {{ condition }}
          </li>
        </ul>
        <p v-if="basisEntry.selection_basis.blueprint_title" class="muted">
          组卷：{{ basisEntry.selection_basis.blueprint_title }}
        </p>
      </section>

      <section class="basis-block">
        <h4>出处资料（{{ basisEntry.selection_basis.source_count }}）</h4>
        <article v-for="source in basisEntry.selection_basis.sources"
          :key="sourceKey(source)" class="basis-source">
          <div class="basis-source-head">
            <span class="basis-kind">{{ source.kind_label }}</span>
            <strong>{{ source.display_name }}</strong>
          </div>
          <p class="basis-source-meta">
            {{ source.page ? `第 ${source.page} 页` : source.page_inferred ? '页码未记录，可看资料首页' : '未定位到页码' }}
            <span v-if="source.chunk_label"> · {{ source.chunk_label }}</span>
          </p>
          <p class="muted">原件名：{{ source.raw_name }}</p>
          <p class="muted">类型依据：{{ source.kind_basis }}</p>
          <p v-if="source.note" class="muted">{{ source.note }}</p>
          <div class="basis-source-actions">
            <el-button v-if="source.page_image_url" size="small"
              @click="toggleSourcePage(source)">
              {{ sourcePageKey === sourceKey(source) ? '收起原页' : source.page_inferred ? '打开资料首页' : '查看原页' }}
            </el-button>
            <el-button v-if="source.can_open_original" size="small" type="primary" plain
              @click="openSourceOriginal(source)">打开原件</el-button>
            <el-button v-if="source.jump_kind === 'search'" size="small"
              @click="openSourceInMaterials(source)">在我的资料中查找</el-button>
          </div>
          <div v-if="sourcePageKey === sourceKey(source)" class="basis-page-preview">
            <PageImage :doc-id="source.document_id" :page="source.effective_page || 1"
              :label="source.raw_name" />
          </div>
        </article>
        <p v-if="!basisEntry.selection_basis.sources?.length" class="muted">
          这道题没有记录页级出处，无法跳转到具体资料。
        </p>
      </section>
      <p class="basis-note">{{ basisEntry.selection_basis.note }}</p>
    </template>
    <p v-else class="muted">这道题暂时没有选题依据记录。</p>
  </el-drawer>

  <OriginalPreview
    v-if="originalTarget"
    v-model="originalOpen"
    :upload-id="originalTarget.upload_id"
    :filename="originalTarget.raw_name"
    :title="originalTarget.display_name"
    :mime="originalTarget.mime"
  />
    <el-dialog v-model="composeOpen" title="生成错题复测卷"
      width="min(560px, 94vw)" align-center class="retest-compose-dialog">
      <div v-if="!composeResult" class="compose-form">
        <p class="compose-lead">
          从错题出发组一张复测卷：优先放原题，不够的部分由 AI 生成相似题补足。
        </p>

        <div class="form-row">
          <span class="form-label">复测范围</span>
          <el-radio-group v-model="composeForm.scope" size="small">
            <el-radio label="all">全部待复测</el-radio>
            <el-radio label="kp">指定知识点</el-radio>
          </el-radio-group>
        </div>
        <el-select v-if="composeForm.scope === 'kp'" v-model="composeForm.kp_id"
          placeholder="选择知识点" size="small" class="kp-select">
          <el-option v-for="item in composeKpOptions" :key="item.value"
            :label="item.label" :value="item.value" />
        </el-select>

        <div class="form-row">
          <span class="form-label">题目数量</span>
          <el-radio-group v-model="composeForm.count" size="small">
            <el-radio :label="5">5 题</el-radio>
            <el-radio :label="10">10 题</el-radio>
            <el-radio :label="15">15 题</el-radio>
          </el-radio-group>
        </div>

        <div class="form-row">
          <span class="form-label">难度</span>
          <el-radio-group v-model="composeForm.difficulty" size="small">
            <el-radio label="same">与原题相同</el-radio>
            <el-radio label="harder">略高于原题</el-radio>
            <el-radio label="smart">智能搭配</el-radio>
          </el-radio-group>
        </div>

        <div class="form-row">
          <span class="form-label">生成方式</span>
          <el-radio-group v-model="composeForm.mode" size="small">
            <el-radio label="mix">原题优先 + AI 补足</el-radio>
            <el-radio label="original">只要原题</el-radio>
            <el-radio label="ai">只要 AI 相似题</el-radio>
          </el-radio-group>
        </div>
      </div>

      <div v-else class="compose-result">
        <p class="result-title">{{ composeResult.message }}</p>
        <dl class="result-facts">
          <div><dt>题目总数</dt><dd>{{ composeResult.item_count }} 题</dd></div>
          <div><dt>原题</dt><dd>{{ composeResult.original_count }} 道</dd></div>
          <div><dt>AI 相似题</dt><dd>{{ composeResult.ai_count }} 道</dd></div>
          <div><dt>总分</dt><dd>{{ composeResult.total_score }} 分</dd></div>
          <div><dt>预计用时</dt><dd>{{ composeResult.estimated_minutes }} 分钟</dd></div>
        </dl>
        <p v-if="composeResult.kp_names?.length" class="result-kps">
          涉及知识点：{{ composeResult.kp_names.join('、') }}
        </p>
        <p v-if="composeResult.note" class="result-note">{{ composeResult.note }}</p>
      </div>

      <template #footer>
        <template v-if="!composeResult">
          <el-button @click="composeOpen = false">取消</el-button>
          <el-button type="primary" :loading="composeBusy" @click="runCompose">
            {{ composeBusy ? '正在生成…' : '生成复测卷' }}
          </el-button>
        </template>
        <template v-else>
          <el-button @click="composeOpen = false">返回错题复测</el-button>
          <el-button type="primary" @click="startComposed">开始复测</el-button>
        </template>
      </template>
    </el-dialog>

  <!-- V3.3：同一道题的历次作答，历史记录一条不删 -->
  <el-dialog v-model="historyOpen" title="历次作答记录" width="min(680px, 92%)">
    <template v-if="historyEntry">
      <p class="wc-history-note">
        同一道题共 {{ historyEntry.attempt_count }} 次作答，卡片只展示最近一次有效作答；
        以下记录全部来自真实历史提交，未删除、未覆盖。
        <template v-if="historyEntry.source_question_ids?.length">
          原始题目 ID：{{ historyEntry.source_question_ids.join('、') }}。
        </template>
      </p>
      <div class="wc-history-list">
        <div v-for="(row, i) in historyRows(historyEntry)" :key="row.entry_id"
          class="wc-history-row"
          :class="{ 'is-current': row.entry_id === historyEntry.entry_id }">
          <span class="wc-history-index">{{ historyRows(historyEntry).length - i }}</span>
          <div class="wc-history-body">
            <div class="wc-history-meta">
              <span>{{ formatDate(row.created_at, true) }}</span>
              <span>答卷 {{ String(row.attempt_id).slice(0, 16) }}…</span>
              <span>失分 {{ row.lost_points }}</span>
              <span>{{ row.status_label || row.status }}</span>
              <span v-if="row.entry_id === historyEntry.entry_id">当前展示</span>
            </div>
            <div class="wc-history-answer"><MathText :text="historyResponseText(row)" /></div>
          </div>
        </div>
      </div>
    </template>
  </el-dialog>

</template>

<style scoped>
.wrong-page { --wrong-accent: #4f46e5; color: #172033; }
.wrong-head { align-items: center; }
.wrong-head h2 { font-size: 30px; letter-spacing: -.02em; }
.wrong-head p:not(.eyebrow) { font-size: 14px; }
.eyebrow { margin: 0 0 5px; color: var(--wrong-accent); font-size: 14px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
.error-hint { background: #fff7f7; border-color: #fecaca; color: #991b1b; }
.notice { margin-bottom: 16px; padding: 11px 14px; border: 1px solid #d8e2ef; border-radius: 10px; background: #f8fafc; color: #475569; font-size: 14px; line-height: 1.6; }
.due-section { margin-bottom: 16px; padding: 22px; border: 1px solid #dedcfb; border-radius: 16px; background: linear-gradient(135deg, #fff 15%, #f5f3ff 100%); box-shadow: 0 10px 30px rgba(79, 70, 229, .06); }

/* 掌握判定：四条件 + 每个考点还差什么 */
.mastery-section {
  margin-bottom: 16px; padding: 22px; border: 1px solid var(--border);
  border-radius: 16px; background: #fff; box-shadow: 0 10px 30px rgba(31, 41, 55, .04);
}
.mastery-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 14px; flex-wrap: wrap; }
.mastery-head h3 { margin: 0; font-size: 18px; }
.criteria-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.criteria-item {
  display: flex; flex-direction: column; gap: 2px; padding: 12px 14px;
  border-radius: 12px; background: var(--accent-soft); border: 1px solid #dbe3ff;
}
.criteria-item strong { font-size: 22px; color: var(--accent); line-height: 1.2; }
.criteria-item span { font-size: 14px; color: var(--text); font-weight: 600; }
.criteria-item small { color: var(--muted); font-size: 14px; line-height: 1.5; }
.gap-list { display: grid; gap: 8px; }
.gap-row {
  display: flex; align-items: center; justify-content: space-between; gap: 14px;
  padding: 12px 14px; border: 1px solid var(--border); border-radius: 10px;
}
.gap-title { display: flex; align-items: center; gap: 8px; }
.gap-title strong { font-size: 14px; }
.gap-meta { margin: 3px 0 2px; color: var(--muted); font-size: 14px; }
.gap-advice { margin: 0; color: var(--text-2); font-size: 14px; }
.gap-reason { margin: 3px 0 0; color: var(--muted); font-size: 14px; }
@media (max-width: 900px) {
  .criteria-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .gap-row { flex-direction: column; align-items: flex-start; }
}
.section-heading { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 16px; }
.section-heading h3 { margin: 0; font-size: 18px; }
.section-heading p:not(.eyebrow) { margin: 4px 0 0; color: #64748b; font-size: 14px; }
.due-count { padding: 7px 10px; border-radius: 999px; background: #ede9fe; color: #5b21b6; font-size: 14px; font-weight: 700; }
.due-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.due-card { display: grid; grid-template-columns: 38px minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 15px; border: 1px solid #e2e4eb; border-radius: 12px; background: #fff; }
.due-icon { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 10px; background: #eef2ff; color: #4338ca; font-size: 18px; font-weight: 800; }
.due-content > span { color: #64748b; font-size: 14px; }
.due-content h4 { margin: 2px 0 4px; font-size: 15px; }
.due-content p { margin: 0; color: #64748b; font-size: 14px; }
.due-empty { display: flex; gap: 12px; align-items: center; padding: 17px; border: 1px dashed #d8dce5; border-radius: 12px; background: rgba(255,255,255,.7); }
.due-empty-icon { display: grid; place-items: center; width: 38px; height: 38px; flex: none; border-radius: 50%; background: #dcfce7; color: #15803d; font-weight: 800; }
.due-empty strong { font-size: 14px; }
.due-empty p { margin: 4px 0 0; color: #64748b; font-size: 14px; }
.upcoming-tasks { margin-top: 10px; }
.upcoming-tasks summary { min-height: 44px; color: #526072; cursor: pointer; font-size: 14px; font-weight: 600; line-height: 44px; }
.upcoming-list { overflow: hidden; border: 1px solid #e5e7eb; border-radius: 10px; background: #fff; }
.upcoming-row { display: flex; justify-content: space-between; gap: 12px; padding: 10px 12px; border-top: 1px solid #edf0f4; font-size: 14px; }
.upcoming-row:first-child { border-top: 0; }
.upcoming-row div { display: flex; flex-direction: column; gap: 3px; }
.upcoming-row div span, .upcoming-row > span { color: #64748b; }
.wrong-list-card { padding: 20px; }
.list-heading { align-items: center; }
.filters { display: grid; grid-template-columns: 145px minmax(180px, 240px); gap: 8px; }
.wrong-list { display: grid; gap: 12px; }
.wrong-entry { padding: 18px; border: 1px solid #e2e6ed; border-radius: 13px; background: #fff; }
.wrong-entry-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.chapter { display: block; margin-bottom: 3px; color: #64748b; font-size: 14px; }
.wrong-entry-head h4 { margin: 0; font-size: 16px; }
.created-time { align-self: center; color: #8490a2; font-size: 14px; }
.question-block { margin-top: 15px; padding-top: 14px; border-top: 1px solid #edf0f4; }
.question-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px; }
.qtype-chip { padding: 2px 9px; border-radius: 999px; background: #eef2ff; color: #4338ca; font-size: 14px; font-weight: 700; }
.source-hint { color: #8490a2; font-size: 14px; }
.question-stem { color: #1f2937; font-size: 14.5px; font-weight: 600; line-height: 1.8; }
.question-options { display: grid; gap: 6px; margin-top: 10px; }
.question-option {
  display: flex; align-items: flex-start; gap: 9px; padding: 8px 11px;
  border: 1px solid #e6e9f0; border-radius: 8px; background: #fbfcfe;
  font-size: 14px; line-height: 1.7;
}
.question-option.is-correct { border-color: #b7e0c8; background: #f0fbf4; }
.question-option.is-mine { border-color: #f3c8c8; background: #fff6f6; }
.option-key { flex: none; min-width: 18px; color: #4338ca; font-weight: 700; }
.option-mark { flex: none; margin-left: auto; padding: 1px 7px; border-radius: 999px; font-size: 14px; font-weight: 700; white-space: nowrap; }
.option-mark--correct { background: #dcfce7; color: #15803d; }
.option-mark--mine { background: #fee2e2; color: #b91c1c; }
.answer-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 12px; }
.answer-panel { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 8px; padding: 11px 12px; border-radius: 9px; font-size: 14px; line-height: 1.7; }
.answer-panel--mine { border: 1px solid #f6dada; background: #fff7f7; }
.answer-panel--correct { border: 1px solid #cdebd8; background: #f2fbf5; }
.answer-panel > span { color: #64748b; font-weight: 700; }
.answer-value { min-width: 0; max-height: 300px; overflow: auto; overflow-wrap: anywhere; }
.analysis-panel { margin-top: 12px; padding: 14px 15px; border: 1px solid #dfe4ec; border-radius: 10px; background: #fbfcfe; font-size: 14px; line-height: 1.8; }
.analysis-title { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: #4338ca; }
.analysis-title span { color: #8490a2; font-size: 14px; }
.analysis-body { margin-top: 8px; overflow-wrap: anywhere; }
.rubric-block { margin-top: 12px; padding-top: 10px; border-top: 1px dashed #e2e6ed; }
.rubric-block ul { margin: 6px 0 0; padding-left: 18px; }
.rubric-block li { margin-bottom: 5px; overflow-wrap: anywhere; }
.rubric-score { margin-left: 6px; color: #166044; font-size: 14px; font-weight: 700; }
.muted { color: #8490a2; }
.basis-block { margin-bottom: 22px; }
.basis-block h4 { margin: 0 0 10px; color: #4338ca; font-size: 14px; }
.basis-reason { margin: 0 0 10px; font-size: 14px; line-height: 1.8; }
.basis-conditions { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; padding: 0; list-style: none; }
.basis-conditions li { padding: 3px 9px; border-radius: 999px; background: #eef2ff; color: #4338ca; font-size: 14px; }
.basis-block p.muted { margin: 3px 0; font-size: 14px; line-height: 1.6; }
.basis-source { margin-bottom: 10px; padding: 14px; border: 1px solid #e3e6ee; border-radius: 10px; background: #fbfcfe; }
.basis-source-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.basis-source-head strong { min-width: 0; overflow-wrap: anywhere; font-size: 14px; }
.basis-kind { flex: none; padding: 2px 8px; border-radius: 999px; background: #eef2ff; color: #4338ca; font-size: 14px; font-weight: 700; }
.basis-source-meta { margin: 8px 0; color: #526072; font-size: 14px; }
.basis-source-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.basis-source-actions :deep(.el-button) { min-height: 40px; margin-left: 0; }
.basis-page-preview { margin-top: 12px; overflow: auto; }
.basis-note { padding-top: 12px; border-top: 1px dashed #e2e6ed; color: #8490a2; font-size: 14px; line-height: 1.7; }
.misconception, .correction-preview { display: grid; grid-template-columns: 100px minmax(0, 1fr); gap: 10px; margin-top: 9px; padding: 0 12px; color: #526072; font-size: 14px; line-height: 1.6; }
.misconception strong { color: #a15c0b; }
.correction-preview strong { color: #166044; }
.entry-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px; }
.resource-panel { display: grid; gap: 6px; margin-top: 12px; padding: 12px; border-radius: 10px; background: #f8fafc; }
.resource-row { display: flex; justify-content: space-between; gap: 10px; align-items: center; padding: 8px; border-radius: 8px; color: #334155; text-decoration: none; }
.resource-row:hover { background: #eef2ff; color: #4338ca; }
.resource-row > span:first-child { display: flex; flex-direction: column; gap: 3px; }
.resource-row strong { font-size: 14px; }
.resource-row small { color: #64748b; font-size: 14px; }
.correction-editor { margin-top: 14px; padding: 15px; border: 1px solid #dfe4ec; border-radius: 11px; background: #fbfcfe; }
.correction-editor label { display: block; margin-bottom: 6px; color: #475569; font-size: 14px; font-weight: 700; }
.editor-row { display: grid; grid-template-columns: 180px 160px auto; gap: 10px; align-items: end; margin-top: 12px; }
.editor-row :deep(.el-select) { width: 100%; }
.editor-row > :deep(.el-button) { min-height: 32px; }
.source-preview { margin-top: 15px; }
.entry-technical { margin-top: 12px; padding-top: 8px; border-top: 1px solid #e5e7eb; }
.entry-technical summary { min-height: 40px; color: #64748b; cursor: pointer; font-size: 14px; line-height: 40px; }
.entry-technical pre, .technical-content pre { max-height: 360px; overflow: auto; padding: 12px; border-radius: 8px; background: #0f172a; color: #dbeafe; font-size: 14px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.empty-state { padding: 42px 16px; text-align: center; color: #64748b; }
.empty-state h4 { margin: 8px 0 5px; color: #334155; font-size: 16px; }
.empty-state p { margin: 0; font-size: 14px; }
.empty-icon { display: grid; place-items: center; width: 42px; height: 42px; margin: 0 auto; border-radius: 50%; background: #dcfce7; color: #15803d; font-size: 20px; font-weight: 800; }
.load-more { display: flex; justify-content: center; margin-top: 16px; }
.load-more :deep(.el-button) { min-height: 44px; }
.technical-shell { margin-top: 16px; overflow: hidden; border: 1px solid #e1e6ee; border-radius: 12px; background: #fff; }
.technical-toggle { display: flex; width: 100%; min-height: 64px; padding: 12px 16px; align-items: center; justify-content: space-between; border: 0; background: #fff; color: #334155; text-align: left; cursor: pointer; }
.technical-toggle > span:first-child { display: flex; flex-direction: column; gap: 3px; }
.technical-toggle small { color: #8490a2; font-size: 14px; font-weight: 400; }
.technical-toggle:focus-visible { outline: 3px solid rgba(79, 70, 229, .24); outline-offset: -3px; }
.technical-content { padding: 0 16px 16px; border-top: 1px solid #edf0f4; }
.technical-content details { padding: 10px 0; border-bottom: 1px solid #edf0f4; }
.technical-content summary { min-height: 36px; color: #475569; cursor: pointer; font-size: 14px; font-weight: 600; line-height: 36px; }

@media (max-width: 900px) {
  .due-grid { grid-template-columns: 1fr; }
  .filters { grid-template-columns: 1fr 1fr; width: 100%; }
  .list-heading { align-items: stretch; flex-direction: column; }
  .wrong-entry-head { flex-direction: column; }
  .editor-row { grid-template-columns: 1fr 1fr; }
  .editor-row > :deep(.el-button) { grid-column: 1 / -1; min-height: 44px; }
}

@media (max-width: 640px) {
  .wrong-head { align-items: stretch; }
  .wrong-head h2 { font-size: 25px; }
  .wrong-head :deep(.el-button) { width: 100%; min-height: 44px; }
  .due-section { padding: 16px; }
  .section-heading { align-items: flex-start; }
  .due-count { flex: none; }
  .due-card { grid-template-columns: 36px 1fr; }
  .due-card :deep(.el-button) { grid-column: 1 / -1; width: 100%; min-height: 44px; }
  .wrong-list-card { padding: 15px; }
  .filters { grid-template-columns: 1fr; }
  .wrong-entry { padding: 15px; }
  .wrong-entry-head .tag-line { align-items: flex-start; }
  .created-time { width: 100%; }
  .answer-grid { grid-template-columns: 1fr; }
  .question-option { flex-wrap: wrap; }
  .answer-panel, .misconception, .correction-preview { grid-template-columns: 1fr; gap: 3px; }
  .misconception, .correction-preview { padding: 0; }
  .entry-actions { align-items: stretch; flex-direction: column; }
  .entry-actions :deep(.el-button) { width: 100%; min-height: 44px; margin-left: 0; }
  .editor-row { grid-template-columns: 1fr; }
  .editor-row > :deep(.el-button) { grid-column: 1; }
  .upcoming-row { align-items: flex-start; flex-direction: column; }
  .technical-toggle { min-height: 72px; }
}
.head-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.compose-lead { margin: 0 0 14px; color: #697181; font-size: 14px; line-height: 1.7; }
.form-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.form-label { width: 64px; flex: none; color: #4b5563; font-size: 14px; }
.kp-select { width: 100%; margin: -4px 0 12px; }
.compose-result { padding: 4px 0; }
.result-title { margin: 0 0 12px; color: #1f2937; font-size: 14px; font-weight: 600; }
.result-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; margin: 0 0 12px; }
.result-facts div { display: flex; justify-content: space-between; padding-bottom: 6px; border-bottom: 1px dashed #eef0f4; font-size: 14px; }
.result-facts dt { color: #7b8494; }
.result-facts dd { margin: 0; color: #1f2937; }
.result-kps { margin: 0 0 6px; color: #4338ca; font-size: 14px; }
.result-note { margin: 0; color: #8b93a3; font-size: 14px; }

/* ---- V3.2 双列错题卡片 ---- */
/* V3.3：列分配交给 MasonryGrid（最短列自动补位），卡片自身永远是自然高度。 */
.wrong-grid { width: 100%; }
.wrong-card {
  display: flex; min-width: 0; flex-direction: column; gap: 10px;
  height: auto;
  padding: 16px; border: 1px solid #e5e7eb; border-radius: 12px; background: #fff;
}
.wrong-card:hover { border-color: #c7d2fe; box-shadow: 0 4px 16px rgb(31 41 55 / 6%); }
.wc-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.wc-type { padding: 1px 8px; border-radius: 6px; background: #eef2ff; color: #4338ca; font-size: 14px; }
.wc-kp { padding: 1px 8px; border-radius: 6px; background: #f4f6fb; color: #4b5563; font-size: 14px; }
.wc-repeat {
  padding: 1px 8px; border-radius: 6px; background: #fff7ed;
  color: #b45309; font-size: 14px; font-weight: 600;
}
.wc-stem {
  color: #1f2937; font-size: 14px; line-height: 1.72;
  overflow-wrap: anywhere;
}
.wc-prompt { margin-right: 4px; color: #6b7280; font-weight: 600; }
.wc-last {
  margin: 0; color: #8b93a3; font-size: 14px; line-height: 1.6;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.wc-label { margin-right: 6px; color: #9aa1ae; font-size: 14px; }
.wc-options { display: flex; flex-direction: column; gap: 6px; }
.wc-option {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 8px 10px; border: 1px solid #eef0f4; border-radius: 8px;
  background: #fafbfd; font-size: 14px; line-height: 1.65;
}
.wc-option .opt-key { flex: none; color: #6b7280; font-weight: 600; }
.wc-option .opt-text { min-width: 0; overflow-wrap: anywhere; }
.wc-option .opt-mark {
  flex: none; margin-left: auto; padding: 1px 7px; border-radius: 999px;
  font-size: 14px; white-space: nowrap;
}
.wc-option.is-correct-picked,
.wc-option.is-correct-missed { background: #ecfdf5; border-color: #a7f3d0; }
.wc-option.is-wrong-picked { background: #fef2f2; border-color: #fecaca; }
.opt-mark.ok { background: #d1fae5; color: #047857; }
.opt-mark.bad { background: #fee2e2; color: #b91c1c; }
.wc-summary {
  display: flex; flex-wrap: wrap; gap: 6px 16px;
  padding: 8px 10px; border-radius: 8px; background: #f7f8fb;
  color: #4b5563; font-size: 14px;
}
.wc-summary b { color: #1f2937; }
.wc-summary b.ok { color: #047857; }
.wc-summary--text { flex-direction: column; gap: 6px; }
.wc-summary--text > div { display: flex; flex-wrap: wrap; gap: 6px; }
.wc-empty-answer { color: #9aa1ae; }
.wc-answer-source {
  align-self: flex-start; padding: 1px 8px; border-radius: 999px;
  background: #eef2ff; color: #4338ca; font-size: 14px;
}
.wc-actions {
  display: flex; flex-wrap: wrap; gap: 8px;
  margin-top: auto; padding-top: 10px; border-top: 1px solid #f1f3f8;
}
.wc-actions :deep(.el-button) { min-height: 30px; margin-left: 0; font-size: 14px; }
.wc-analysis {
  display: flex; flex-direction: column; gap: 8px;
  padding-top: 10px; border-top: 1px dashed #eef0f4; font-size: 14px;
}
.wc-correct { color: #047857; line-height: 1.7; }
.wc-explain { color: #4b5563; line-height: 1.75; }
.wc-explain-empty { margin: 0; color: #8b93a3; font-size: 14px; line-height: 1.6; }
.wc-basis-link {
  align-self: flex-start; padding: 0; border: 0; background: none;
  color: #4338ca; font-size: 14px; cursor: pointer; text-decoration: underline;
}
.wc-option-missing {
  padding: 7px 10px; border: 1px solid #fde68a; border-radius: 8px;
  background: #fffbeb; color: #92400e; font-size: 14px;
}
.wc-all-correct {
  padding: 1px 8px; border-radius: 999px;
  background: #d1fae5; color: #047857; font-size: 14px; font-weight: 700;
}
.wc-rubric ul { margin: 4px 0 0; padding-left: 18px; }
.wc-rubric li { margin-bottom: 3px; overflow-wrap: anywhere; }
.rubric-score { margin-left: 6px; color: #166044; font-size: 14px; font-weight: 700; }
.wc-summary--text .wc-answer-source { margin-top: 2px; }
.wc-provisional {
  width: 100%; color: #92400e; font-size: 14px; line-height: 1.5;
}
.wc-history-list { display: flex; flex-direction: column; gap: 10px; }
.wc-history-row {
  display: flex; gap: 12px; align-items: flex-start;
  padding: 12px; border: 1px solid #eef0f4; border-radius: 10px; background: #fafbfd;
}
.wc-history-row.is-current { border-color: #a7f3d0; background: #f0fdf4; }
.wc-history-index {
  flex: 0 0 30px; height: 30px; border-radius: 8px;
  background: #eef2ff; color: #4338ca; font-size: 14px; font-weight: 700;
  display: grid; place-items: center;
}
.wc-history-body { flex: 1; min-width: 0; }
.wc-history-meta { display: flex; flex-wrap: wrap; gap: 8px; color: #6b7280; font-size: 14px; }
.wc-history-answer { margin-top: 4px; color: #1f2937; font-size: 14px; line-height: 1.7; overflow-wrap: anywhere; }
.wc-history-note { margin: 0 0 10px; color: #6b7280; font-size: 14px; line-height: 1.7; }

@media (max-width: 1080px) {
  /* 单列由 MasonryGrid 按容器宽度自动切换，这里不再强制 Grid 列数 */
  .wrong-grid { width: 100%; }
}

</style>
