<script setup lang="ts">
import WorkbenchPage from "../components/WorkbenchPage.vue"
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api, runTeacherJob } from '../api/client'
import MathText from '../components/MathText.vue'
import MasonryGrid from '../components/MasonryGrid.vue'
import { friendlyError, questionTypeLabel } from '../utils/presentation'
import { readQuestionBankCache, saveQuestionBankCache } from '../utils/questionBankCache'

type Question = Record<string, any>

const route = useRoute()
const router = useRouter()
const initialCache = readQuestionBankCache()
const loading = ref(!initialCache)
const loadError = ref('')
const questions = shallowRef<Question[]>(initialCache?.items || [])
const bankTotal = ref(initialCache?.total || 0)
const metadata = ref<any>(initialCache?.metadata || { knowledge_points: [], chapters: [], question_types: [] })
const drafts = ref<any[]>(initialCache?.drafts || [])
const selectedIds = ref<string[]>([])
const detail = ref<Question | null>(null)
const detailOpen = ref(false)
const draftsOpen = ref(false)
const composeOpen = ref(false)
const composing = ref(false)
const advancedOpen = ref(false)
const PAGE_SIZE = 48
const visibleCount = ref(PAGE_SIZE)
const loadMoreSentinel = ref<HTMLElement | null>(null)
let loadMoreObserver: IntersectionObserver | null = null

const filters = reactive({
  chapter: '', kpId: '', qtype: '', difficulty: '', source: '',
  keyword: '', availability: 'all', scope: 'all',
})

const DIFFICULTY_LABELS: Record<string, string> = {
  easy: '简单', medium: '中等', hard: '困难', unknown: '未标注',
}
const SOURCE_LABELS: Record<string, string> = {
  teacher: '教师原题', ai: 'AI 生成题', imported: '其他导入题目',
}
const ORIGIN_LABELS: Record<string, string> = {
  original: '历年原题', human: '人工整理', teacher: '教师原题', manual: '教师原题',
  ai: 'AI 新题', generated: 'AI 新题', deepseek_generated: 'AI 新题',
  openai_generated: 'AI 新题', note: '复习笔记', external: '外部导入',
  local_class_image: '课堂题目',
}
const SCOPE_LABELS: Record<string, string> = {
  in_scope: '课程范围', in_scope_advanced: '课内提高',
  extension: '扩展题', unconfirmed: '范围待核验',
}

const kpMap = computed(() => new Map(
  (metadata.value.knowledge_points || []).map((item: any) => [item.kp_id, item])))
const chapterOptions = computed(() => metadata.value.chapters || [])
const kpOptions = computed(() => (metadata.value.knowledge_points || []).filter(
  (item: any) => !filters.chapter || item.chapter === filters.chapter))
const typeOptions = computed(() => metadata.value.question_types || [])
const difficultyOptions = computed(() => metadata.value.difficulties || [])
const sourceOptions = computed(() => metadata.value.sources || [])

function effectiveType(question: Question) {
  return question.question_type_effective || question.question_type || 'unknown'
}
function stemOf(question: Question) { return question.stem_zh || question.stem || '（题干缺失）' }
function kpName(id: string) { return (kpMap.value.get(id) as any)?.name || '知识点未标注' }
function questionChapter(question: Question) {
  return question.chapter_title
    || (kpMap.value.get(question.main_kp_id) as any)?.chapter
    || question.chapter_id
    || '未分章'
}
function optionKey(option: any, index: number) { return option?.key || option?.label || String.fromCharCode(65 + index) }
function optionText(option: any) { return option?.text || option?.content || option?.text_zh || '（选项正文缺失）' }
function sourceLabel(question: Question) {
  return ORIGIN_LABELS[String(question.origin || '').toLowerCase()]
    || question.source_category_label
    || SOURCE_LABELS[question.source_category]
    || '来源未登记'
}
function sourceText(question: Question) {
  const first = (question.source_refs || [])[0]
  if (first) {
    const name = String(first.rel_path || first.document_id || '').split(/[/\\]/).pop()
    return `${name || sourceLabel(question)}${first.page ? ` · 第 ${first.page} 页` : ''}`
  }
  if (question.original_exam_id) {
    return `${question.original_exam_id}${question.original_question_no ? ` · 第 ${question.original_question_no} 题` : ''}`
  }
  return sourceLabel(question)
}
function questionImageUrl(value: any) {
  const raw = typeof value === 'string'
    ? value : String(value?.url || value?.path || value?.rel_path || '')
  if (!raw) return ''
  if (/^(https?:|data:|blob:)/i.test(raw) || raw.startsWith('/')) return raw
  const normalized = raw.replace(/^\.?[/\\]+/, '').replace(/\\/g, '/')
  if (normalized.startsWith('data/assets/')) {
    return `/_mlq-assets/${normalized.slice('data/assets/'.length)}`
  }
  return `/${normalized}`
}

const filtered = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return questions.value.filter((question) => {
    const kpIds = [question.main_kp_id, ...(question.aux_kp_ids || [])]
    const chapterMatch = !filters.chapter || questionChapter(question) === filters.chapter
    const text = [stemOf(question), questionChapter(question), ...kpIds.map(kpName)]
      .join(' ').toLowerCase()
    return chapterMatch
      && (!filters.kpId || kpIds.includes(filters.kpId))
      && (!filters.qtype || effectiveType(question) === filters.qtype)
      && (!filters.difficulty || question.difficulty_band === filters.difficulty)
      && (!filters.source || question.source_category === filters.source)
      && (!filters.availability || filters.availability === 'all'
        || (filters.availability === 'usable' ? question.usable_for_quiz : !question.usable_for_quiz))
      && (!filters.scope || filters.scope === 'all'
        || (filters.scope === 'course'
          ? ['in_scope', 'in_scope_advanced'].includes(question.scope_class)
          : !['in_scope', 'in_scope_advanced'].includes(question.scope_class)))
      && (!keyword || text.includes(keyword))
  })
})
const visibleQuestions = computed(() => filtered.value.slice(0, visibleCount.value))
const hasMoreQuestions = computed(() => visibleCount.value < filtered.value.length)

const hasActiveFilters = computed(() => Boolean(
  filters.chapter || filters.kpId || filters.qtype || filters.difficulty || filters.source
  || filters.keyword.trim() || filters.availability !== 'all' || filters.scope !== 'all'))
const filteredTypeCounts = computed(() => {
  const result: Record<string, number> = {}
  for (const question of filtered.value) {
    const type = effectiveType(question)
    result[type] = (result[type] || 0) + 1
  }
  return result
})
const summaryTypes = computed(() => [
  { label: '单选', count: filteredTypeCounts.value.single_choice || 0 },
  { label: '多选', count: filteredTypeCounts.value.multi_choice || 0 },
  { label: '判断', count: filteredTypeCounts.value.true_false || 0 },
  { label: '填空', count: (filteredTypeCounts.value.fill_blank || 0)
    + (filteredTypeCounts.value.blank || 0) + (filteredTypeCounts.value.fill_in || 0) },
])
const summarizedTypeCount = computed(() => summaryTypes.value.reduce((sum, item) => sum + item.count, 0))
const otherTypeCount = computed(() => Math.max(0, filtered.value.length - summarizedTypeCount.value))

const selectedQuestions = computed(() => selectedIds.value
  .map((id) => questions.value.find((question) => question.question_id === id))
  .filter(Boolean) as Question[])
const selectedScore = computed(() => selectedQuestions.value.reduce(
  (sum, question) => sum + Number(question.score || 1), 0))
const selectedChapterCoverage = computed(() => new Set(
  selectedQuestions.value.map(questionChapter).filter((value) => value !== '未分章')).size)

const COMMON_TYPES = ['single_choice', 'multi_choice', 'true_false', 'fill_blank']
const compose = reactive({
  mode: 'ai' as 'manual' | 'ai' | 'mixed',
  title: '机器学习课堂测验', instructions: '',
  chapters: [] as string[], difficulties: [] as string[], totalScore: 100,
  totalCount: 10, detailedTypes: false, moreTypesOpen: false,
  optionalOpen: false, selectedListOpen: false,
  typeTargets: {
    single_choice: 0, multi_choice: 0, true_false: 0, fill_blank: 0,
  } as Record<string, number>,
})
const commonTypeOptions = computed(() => COMMON_TYPES.map((value) => ({ value })))
const moreTypeOptions = computed(() => (typeOptions.value as any[])
  .filter((item: any) => !COMMON_TYPES.includes(item.value)))
const detailedTargetCount = computed(() => Object.values(compose.typeTargets)
  .reduce((sum, value) => sum + Number(value || 0), 0))
const supplementCount = computed(() => compose.mode === 'mixed'
  ? Math.max(0, Number(compose.totalCount || 0) - selectedIds.value.length)
  : Number(compose.totalCount || 0))
const finalQuestionCount = computed(() => compose.mode === 'manual'
  ? selectedIds.value.length : Number(compose.totalCount || 0))
const selectedChapterIds = computed(() => (chapterOptions.value as any[])
    .filter((item: any) => compose.chapters.includes(item.value))
    .map((item: any) => item.chapter_id)
    .filter(Boolean))
const composeErrors = computed(() => {
  const errors: string[] = []
  if (!compose.title.trim()) errors.push('请填写试卷标题')
  if (Number(compose.totalScore || 0) <= 0) errors.push('试卷总分必须大于 0')
  if (compose.mode === 'manual' && !selectedIds.value.length) errors.push('请先从题库选择至少一道题')
  if (compose.mode === 'mixed' && !selectedIds.value.length) errors.push('混合组卷需要先选择至少一道题')
  if (compose.mode !== 'manual' && (finalQuestionCount.value < 1 || finalQuestionCount.value > 100)) {
    errors.push('题目数量需在 1～100 道之间')
  }
  if (compose.mode === 'mixed' && finalQuestionCount.value <= selectedIds.value.length) {
    errors.push('混合组卷的最终题数必须大于已选题数')
  }
  if (compose.mode !== 'manual' && compose.detailedTypes && detailedTargetCount.value < 1) {
    errors.push('请至少设置一种题型数量')
  }
  return errors
})

function clearFilters() {
  Object.assign(filters, { chapter: '', kpId: '', qtype: '', difficulty: '',
    source: '', keyword: '', availability: 'all', scope: 'all' })
}
function loadMoreQuestions() {
  visibleCount.value = Math.min(filtered.value.length, visibleCount.value + PAGE_SIZE)
}
function onPageScroll() {
  if (!hasMoreQuestions.value || loading.value) return
  const remaining = document.documentElement.scrollHeight - (window.scrollY + window.innerHeight)
  if (remaining < 700) loadMoreQuestions()
}
async function observeLoadMore() {
  await nextTick()
  loadMoreObserver?.disconnect()
  if (loadMoreSentinel.value) loadMoreObserver?.observe(loadMoreSentinel.value)
}
function removeSelected(questionId: string) {
  selectedIds.value = selectedIds.value.filter((id) => id !== questionId)
}
function moveSelected(questionId: string, offset: number) {
  const index = selectedIds.value.indexOf(questionId)
  const target = index + offset
  if (index < 0 || target < 0 || target >= selectedIds.value.length) return
  const next = [...selectedIds.value]
  ;[next[index], next[target]] = [next[target], next[index]]
  selectedIds.value = next
}
function distributeTypes(count: number) {
  for (const key of Object.keys(compose.typeTargets)) compose.typeTargets[key] = 0
  const safeCount = Math.max(1, count)
  const sequence = ['single_choice', 'single_choice', 'multi_choice', 'true_false', 'fill_blank']
  for (let index = 0; index < safeCount; index += 1) {
    const key = sequence[index % sequence.length]
    compose.typeTargets[key] = Number(compose.typeTargets[key] || 0) + 1
  }
}
function setDetailedTypes(enabled: boolean) {
  compose.detailedTypes = enabled
  if (enabled) distributeTypes(supplementCount.value || 10)
}
function switchComposeMode(mode: 'manual' | 'ai' | 'mixed') {
  compose.mode = mode
  compose.selectedListOpen = false
  if (mode === 'mixed') compose.totalCount = Math.max(10, selectedIds.value.length + 1)
  if (mode === 'ai' && compose.totalCount < 1) compose.totalCount = 10
  if (compose.detailedTypes && mode !== 'manual') distributeTypes(supplementCount.value)
}
function toggle(question: Question) {
  if (!question.usable_for_quiz) {
    ElMessage.warning(question.blocked_reason || '该题不能进入正式试卷')
    return
  }
  selectedIds.value = selectedIds.value.includes(question.question_id)
    ? selectedIds.value.filter((id) => id !== question.question_id)
    : [...selectedIds.value, question.question_id]
}
function showDetail(question: Question) { detail.value = question; detailOpen.value = true }
function answerText(question: Question | null) {
  const answer = question?.answer_payload || {}
  const type = effectiveType(question || {})
  if (type === 'single_choice') return answer.key ? `选项 ${answer.key}` : '未提供可用答案'
  if (type === 'multi_choice') return Array.isArray(answer.keys) ? `选项 ${answer.keys.join('、')}` : '未提供可用答案'
  if (type === 'true_false') return typeof answer.value === 'boolean' ? (answer.value ? '正确' : '错误') : '未提供可用答案'
  if (answer.numeric !== undefined || answer.value !== undefined) return String(answer.numeric ?? answer.value)
  if (Array.isArray(answer.values)) return answer.values.join('；')
  return answer.text || answer.reference || '未提供可用答案'
}
function rubricText(question: Question | null) {
  const rubric = question?.rubric || {}
  if (typeof rubric === 'string') return rubric
  const points = rubric.points || rubric.items || rubric.criteria
  if (Array.isArray(points)) return points.map((item: any) => item.text || item.description || item).join('；')
  return rubric.note || rubric.description || '按固定答案自动评分'
}
function duplicateLabel(question: Question) {
  const kind = question.duplicate?.kind
  return kind === 'exact' ? '发现重复题' : kind === 'family' ? '同题族近似题'
    : kind === 'near' ? '发现近似题' : ''
}

async function load() {
  const cache = readQuestionBankCache()
  if (cache) {
    questions.value = cache.items
    bankTotal.value = cache.total
    metadata.value = cache.metadata
    drafts.value = cache.drafts
  }
  loading.value = !cache
  loadError.value = ''
  try {
    const [bank, saved] = await Promise.all([
      api.get('/teacher/question-bank', { include_inactive: true, limit: 2000 }),
      api.get('/teacher/paper-drafts'),
    ]) as any[]
    questions.value = bank.items || []
    bankTotal.value = Number(bank.total || questions.value.length)
    metadata.value = bank.metadata || {}
    drafts.value = saved.items || []
    saveQuestionBankCache({ items: questions.value, total: bankTotal.value,
      metadata: metadata.value, drafts: drafts.value })
    for (const item of metadata.value.question_types || []) {
      if (compose.typeTargets[item.value] === undefined) compose.typeTargets[item.value] = 0
    }
    if (!cache) visibleCount.value = PAGE_SIZE
    const kp = String(route.query.kp || '')
    if (kp) filters.kpId = kp
    if (String(route.query.compose || '') === '1') openCompose('ai')
  } catch (error) {
    const message = friendlyError(error, '教师题库加载失败')
    if (!cache) loadError.value = message
    ElMessage.error(cache ? '题库更新失败，当前显示上次加载的内容' : message)
  } finally { loading.value = false }
}

async function exportPrompts() {
  try {
    const history = await api.get('/teacher/prompt-history/export')
    const url = URL.createObjectURL(new Blob([JSON.stringify(history, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url; link.download = 'runtime-prompts.json'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (error) { ElMessage.error(friendlyError(error, '提示词记录导出失败')) }
}

function openCompose(mode: 'manual' | 'ai' | 'mixed' = 'ai') {
  switchComposeMode(mode)
  compose.optionalOpen = false
  compose.moreTypesOpen = false
  composeOpen.value = true
}

async function createDraft() {
  if (composing.value) return
  if (composeErrors.value.length) {
    ElMessage.warning(composeErrors.value[0])
    return
  }
  composing.value = true
  try {
    const selectedItems = compose.mode === 'ai' ? [] : selectedQuestions.value
    const requestedSupplement = compose.mode === 'mixed'
      ? finalQuestionCount.value - selectedIds.value.length : finalQuestionCount.value
    const result: any = await runTeacherJob('/teacher/paper-drafts/compose/jobs', {
      title: compose.title.trim(), instructions: compose.instructions, mode: compose.mode,
      selected_items: selectedItems.map((question) => ({
        question_id: question.question_id,
        question_version: question.question_version,
        score: Number(question.score || 1),
      })),
      requirements: {
        kp_ids: [],
        chapters: compose.mode === 'manual' ? [] : compose.chapters,
        chapter_ids: compose.mode === 'manual' ? [] : selectedChapterIds.value,
        difficulties: compose.difficulties,
        type_targets: compose.detailedTypes ? Object.entries(compose.typeTargets)
          .filter(([, count]) => Number(count) > 0)
          .map(([question_type, count]) => ({ question_type, count: Number(count) })) : [],
        total_count: compose.mode === 'manual' ? selectedIds.value.length : requestedSupplement,
        total_score: compose.totalScore,
        source_policy: compose.mode === 'manual' ? 'existing' : 'mixed',
        allow_ai_fill: compose.mode !== 'manual',
        auto_type_distribution: !compose.detailedTypes,
      },
    })
    composeOpen.value = false
    selectedIds.value = []
    ElMessage.success(`草稿已保存：${result.item_count} 题，共 ${result.total_score} 分`)
    await router.push(`/teacher/papers/${result.paper_id}`)
  } catch (error) {
    ElMessage.error(friendlyError(error, '组卷失败；系统没有缩减题量或创建空试卷'))
  } finally { composing.value = false }
}

watch(() => filters.chapter, () => {
  if (filters.kpId && !(kpOptions.value as any[]).some((item) => item.kp_id === filters.kpId)) filters.kpId = ''
})
watch(() => [filters.chapter, filters.kpId, filters.qtype, filters.difficulty,
  filters.source, filters.keyword, filters.availability, filters.scope], () => {
  visibleCount.value = PAGE_SIZE
}, { deep: true })
watch(detailedTargetCount, (count) => {
  if (!compose.detailedTypes || compose.mode === 'manual') return
  compose.totalCount = compose.mode === 'mixed' ? selectedIds.value.length + count : count
})
watch(hasMoreQuestions, () => { void observeLoadMore() })

onMounted(() => {
  window.addEventListener('scroll', onPageScroll, { passive: true })
  loadMoreObserver = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) loadMoreQuestions()
  }, { rootMargin: '600px 0px' })
  void load()
})
onBeforeUnmount(() => {
  window.removeEventListener('scroll', onPageScroll)
  loadMoreObserver?.disconnect()
})
</script>

<template>
  <WorkbenchPage class="workspace-page" :aria-busy="loading" label="题库题目列表">
    <template #header>
    <header class="page-head">
      <div>
        <h2>题库与组卷</h2>
      </div>
    </header>

    <section class="filter-panel">
      <div class="filter-toolbar">
        <label class="filter-control chapter-filter"><span>章节</span><el-select v-model="filters.chapter" clearable placeholder="全部章节">
          <el-option v-for="item in chapterOptions" :key="item.value"
            :label="`${item.value}（${item.count} 道题）`" :value="item.value" />
        </el-select></label>
        <label class="filter-control"><span>题型</span><el-select v-model="filters.qtype" clearable placeholder="全部题型">
          <el-option v-for="item in typeOptions" :key="item.value"
            :label="`${questionTypeLabel(item.value)}（${item.count}）`" :value="item.value" />
        </el-select></label>
        <label class="filter-control"><span>难度</span><el-select v-model="filters.difficulty" clearable placeholder="全部难度">
          <el-option v-for="item in difficultyOptions" :key="item.value"
            :label="`${DIFFICULTY_LABELS[item.value] || '未标注'}（${item.count}）`" :value="item.value" />
        </el-select></label>
        <label class="filter-control"><span>来源</span><el-select v-model="filters.source" clearable placeholder="全部来源">
          <el-option v-for="item in sourceOptions" :key="item.value"
            :label="`${item.label || SOURCE_LABELS[item.value] || '其他来源'}（${item.count}）`" :value="item.value" />
        </el-select></label>
        <el-input v-model="filters.keyword" clearable class="keyword-search" placeholder="搜索题干、知识点或章节">
          <template #prefix>⌕</template>
        </el-input>
        <el-button class="compose-button" type="primary" @click="openCompose('ai')">开始组卷</el-button>
      </div>
      <div class="filter-secondary">
        <el-button link class="advanced-trigger" @click="advancedOpen = !advancedOpen">
          {{ advancedOpen ? '收起高级筛选' : '更多筛选' }}{{ filters.kpId || filters.availability !== 'all' || filters.scope !== 'all' ? '（已应用）' : '' }}
        </el-button>
        <el-button link @click="draftsOpen = true">已保存试卷 {{ drafts.length }}</el-button>
        <el-button link @click="exportPrompts">导出运行提示词</el-button>
        <el-button link :disabled="!hasActiveFilters" @click="clearFilters">清空筛选</el-button>
      </div>
      <div v-if="advancedOpen" class="advanced-filters">
        <label><span>知识点</span><el-select v-model="filters.kpId" clearable filterable placeholder="全部知识点">
          <el-option v-for="item in kpOptions" :key="item.kp_id" :label="item.name" :value="item.kp_id" />
        </el-select></label>
        <label><span>组卷状态</span><el-select v-model="filters.availability" placeholder="全部状态">
          <el-option label="全部状态" value="all" />
          <el-option label="正式试卷可用" value="usable" />
          <el-option label="待核验或缺少可靠答案" value="needs_review" />
        </el-select></label>
        <label><span>课程范围</span><el-select v-model="filters.scope" placeholder="全部范围">
          <el-option label="全部范围" value="all" />
          <el-option label="课程范围内" value="course" />
          <el-option label="扩展与待确认" value="extension" />
        </el-select></label>
      </div>
    </section>

    <section class="bank-summary" aria-live="polite">
      <span>题库共 <b>{{ bankTotal || questions.length }}</b> 道</span>
      <span v-if="hasActiveFilters">当前结果 <b>{{ filtered.length }}</b> 道</span>
      <span v-for="item in summaryTypes" :key="item.label">{{ item.label }} {{ item.count }}</span>
      <span>其他 {{ otherTypeCount }}</span>
      <span class="usable-count">可正式组卷 {{ metadata.usable || 0 }} 道</span>
    </section>

    <section v-if="selectedIds.length" class="selection-bar">
      <div><b>已选择 {{ selectedIds.length }} 题</b><span>约 {{ selectedScore }} 分 · 覆盖 {{ selectedChapterCoverage }} 个章节</span></div>
      <div><el-button @click="selectedIds = []">清空</el-button><el-button type="primary" @click="openCompose('manual')">用已选题目组卷</el-button></div>
    </section>

    </template>
    <section v-if="loading" class="list-state loading-state" aria-live="polite">
      <el-skeleton :rows="8" animated />
      <p>正在加载题库…</p>
    </section>
    <section v-else-if="loadError" class="list-state error-state" role="alert">
      <b>题库加载失败</b><p>{{ loadError }}</p><el-button type="primary" plain @click="load">重新加载</el-button>
    </section>
    <template v-else-if="visibleQuestions.length">
    <MasonryGrid :items="visibleQuestions" :gap="14" :max-columns="3"
      :two-column-below="1040" :single-column-below="660" item-key="question_id"
      class="question-masonry">
      <template #default="{ item: question }">
        <article class="question-card"
          :class="{ selected: selectedIds.includes(question.question_id), blocked: !question.usable_for_quiz }">
          <div class="tag-row card-tags">
            <span class="type-tag">{{ questionTypeLabel(effectiveType(question)) }}</span>
            <span class="chapter-tag">{{ questionChapter(question) }}</span>
            <span class="difficulty-tag" :class="`is-${question.difficulty_band || 'unknown'}`">难度：{{ DIFFICULTY_LABELS[question.difficulty_band] || '未标注' }}</span>
            <span class="source-tag" :title="`实际来源：${sourceText(question)}`">来源：{{ sourceLabel(question) }}</span>
            <span v-if="!question.usable_for_quiz" class="blocked-tag">暂不可组卷</span>
          </div>

          <div class="stem"><MathText :text="stemOf(question)" /></div>
          <div v-if="question.stem_images?.length" class="stem-images">
            <img v-for="(image, index) in question.stem_images.slice(0, 2)" :key="image"
              :src="questionImageUrl(image)" :alt="`题目配图 ${index + 1}`" loading="lazy" />
          </div>
          <div v-if="question.options?.length" class="option-preview">
            <div v-for="(option, index) in question.options.slice(0, 4)" :key="index">
              <b>{{ optionKey(option, index) }}.</b><MathText :text="optionText(option)" />
            </div>
            <small v-if="question.options.length > 4">另有 {{ question.options.length - 4 }} 个选项，详情中可查看</small>
          </div>
          <p v-if="!question.usable_for_quiz" class="blocked-note">{{ question.blocked_reason || '缺少可靠评分依据，该题暂不能用于正式试卷' }}</p>
          <footer>
            <div class="knowledge-meta"><b>{{ kpName(question.main_kp_id) }}</b><small>{{ SCOPE_LABELS[question.scope_class] || '范围待核验' }}</small></div>
            <div class="card-actions">
              <button type="button" class="card-action detail-action" @click="showDetail(question)">查看详情</button>
              <button type="button" class="card-action select-action"
                :class="{ active: selectedIds.includes(question.question_id) }"
                :disabled="!question.usable_for_quiz"
                :aria-pressed="selectedIds.includes(question.question_id)"
                @click="toggle(question)">{{ selectedIds.includes(question.question_id) ? '取消选择' : '＋ 加入组卷' }}</button>
            </div>
          </footer>
        </article>
      </template>
    </MasonryGrid>
    <div v-if="hasMoreQuestions" ref="loadMoreSentinel" class="load-more-row">
      <el-button plain @click="loadMoreQuestions">再显示 {{ Math.min(PAGE_SIZE, filtered.length - visibleCount) }} 道</el-button>
      <span>已显示 {{ visibleQuestions.length }} / {{ filtered.length }} 道</span>
    </div>
    </template>
    <el-empty v-else :description="hasActiveFilters ? '没有符合当前组合条件的题目，请清空部分筛选条件' : '题库中暂时没有题目'" />

    <el-dialog v-model="detailOpen" title="完整题目" width="min(820px, 94vw)" top="5vh">
      <div v-if="detail" class="detail-view">
        <div class="tag-row"><span>{{ questionTypeLabel(effectiveType(detail)) }}</span><span>{{ kpName(detail.main_kp_id) }}</span><span>{{ detail.source_category_label }}</span></div>
        <div class="detail-stem"><MathText :text="stemOf(detail)" /></div>
        <div v-if="detail.stem_images?.length" class="detail-images">
          <img v-for="(image, index) in detail.stem_images" :key="image"
            :src="questionImageUrl(image)" :alt="`题目配图 ${index + 1}`" />
        </div>
        <div v-if="detail.options?.length" class="detail-options">
          <div v-for="(option, index) in detail.options" :key="index"><b>{{ optionKey(option, index) }}</b><MathText :text="optionText(option)" /></div>
        </div>
        <div class="answer-box"><b>标准答案</b><p>{{ answerText(detail) }}</p><b>解析</b><div><MathText :text="detail.explanation || '暂无解析'" /></div><b>评分规则</b><p>{{ rubricText(detail) }}</p></div>
        <p v-if="duplicateLabel(detail)" class="duplicate-note">{{ duplicateLabel(detail) }}，相似度 {{ Math.round((detail.duplicate.similarity || 0) * 100) }}%。组卷时系统会避免同题族重复进入。</p>
      </div>
      <template #footer><el-button @click="detailOpen = false">关闭</el-button><el-button v-if="detail?.usable_for_quiz" type="primary" @click="toggle(detail!); detailOpen=false">{{ selectedIds.includes(detail?.question_id) ? '移出组卷' : '加入组卷' }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="composeOpen" title="开始组卷" width="min(800px, 94vw)" top="8vh"
      class="compose-dialog" :close-on-click-modal="false">
      <div class="compose-layout">
        <el-segmented :model-value="compose.mode"
          :options="[{label:'手动组卷',value:'manual'},{label:'AI 自动组卷',value:'ai'},{label:'混合组卷',value:'mixed'}]"
          @change="switchComposeMode" />

        <section class="compose-section required-section">
          <div class="section-title"><b>基本设置</b><span>必填</span></div>
          <div class="compose-fields">
            <label>试卷标题<el-input v-model="compose.title" maxlength="256" placeholder="请输入试卷标题" /></label>
            <label v-if="compose.mode === 'ai'">题目数量
              <el-input-number v-model="compose.totalCount" :min="1" :max="100" :disabled="compose.detailedTypes" controls-position="right" />
            </label>
            <label v-else-if="compose.mode === 'mixed'">最终总题数
              <el-input-number v-model="compose.totalCount" :min="selectedIds.length + 1" :max="100" :disabled="compose.detailedTypes" controls-position="right" />
            </label>
            <div v-else class="selected-count"><span>当前已选择</span><b>{{ selectedIds.length }} 道题</b></div>
          </div>
        </section>

        <section v-if="compose.mode !== 'ai'" class="selected-section">
          <div class="selected-heading">
            <div><b>{{ compose.mode === 'manual' ? '已选题目' : '保留的手动题目' }}</b><span>{{ selectedIds.length }} 道，按下列顺序进入试卷</span></div>
            <el-button link @click="compose.selectedListOpen = !compose.selectedListOpen">{{ compose.selectedListOpen ? '收起列表' : '查看并调整' }}</el-button>
          </div>
          <div v-if="compose.mode === 'mixed'" class="lock-note">AI 只补充 {{ supplementCount }} 道题，不会替换已选题目。</div>
          <div v-if="compose.selectedListOpen" class="selected-question-list">
            <div v-for="(question, index) in selectedQuestions" :key="question.question_id">
              <span>{{ index + 1 }}. {{ stemOf(question).slice(0, 54) }}{{ stemOf(question).length > 54 ? '…' : '' }}</span>
              <div><el-button link :disabled="index === 0" @click="moveSelected(question.question_id, -1)">上移</el-button><el-button link :disabled="index === selectedQuestions.length - 1" @click="moveSelected(question.question_id, 1)">下移</el-button><el-button link type="danger" @click="removeSelected(question.question_id)">移除</el-button></div>
            </div>
            <el-empty v-if="!selectedQuestions.length" :image-size="54" description="尚未选择题目" />
          </div>
        </section>

        <section v-if="compose.mode !== 'manual'" class="compose-section optional-section">
          <div class="section-title"><b>筛选范围</b><span>选填</span></div>
          <div class="compose-grid">
            <label>章节<el-select v-model="compose.chapters" multiple collapse-tags clearable placeholder="全部章节"><el-option v-for="item in chapterOptions" :key="item.value" :label="item.value" :value="item.value" /></el-select></label>
            <label>难度<el-select v-model="compose.difficulties" multiple collapse-tags clearable placeholder="不限难度"><el-option v-for="value in ['easy','medium','hard']" :key="value" :label="DIFFICULTY_LABELS[value]" :value="value" /></el-select></label>
          </div>
        </section>

        <section v-if="compose.mode !== 'manual'" class="compose-section type-section">
          <div class="type-heading"><div><b>题型设置</b><span>{{ compose.detailedTypes ? `已分配 ${detailedTargetCount} 道` : '系统根据合格题库自动分配' }}</span></div><el-switch :model-value="compose.detailedTypes" active-text="精细分配" @change="setDetailedTypes" /></div>
          <template v-if="compose.detailedTypes">
            <div class="type-targets compact-targets">
              <div v-for="item in commonTypeOptions" :key="item.value"><span>{{ questionTypeLabel(item.value) }}</span><el-input-number v-model="compose.typeTargets[item.value]" :min="0" :max="100" controls-position="right" /></div>
            </div>
            <el-button v-if="moreTypeOptions.length" link class="more-types-trigger" @click="compose.moreTypesOpen = !compose.moreTypesOpen">{{ compose.moreTypesOpen ? '收起更多题型' : '更多题型' }}</el-button>
            <div v-if="compose.moreTypesOpen" class="type-targets compact-targets more-targets">
              <div v-for="item in moreTypeOptions" :key="item.value"><span>{{ questionTypeLabel(item.value) }}</span><el-input-number v-model="compose.typeTargets[item.value]" :min="0" :max="100" controls-position="right" /></div>
            </div>
          </template>
        </section>

        <section class="extra-settings">
          <el-button link @click="compose.optionalOpen = !compose.optionalOpen">{{ compose.optionalOpen ? '收起作答说明与总分' : '作答说明与总分（选填）' }}</el-button>
          <div v-if="compose.optionalOpen" class="compose-grid">
            <label>作答说明<el-input v-model="compose.instructions" type="textarea" :rows="2" placeholder="可填写作答要求" /></label>
            <label>试卷总分<el-input-number v-model="compose.totalScore" :min="1" :max="1000" controls-position="right" /></label>
          </div>
        </section>
        <p v-if="composeErrors.length" class="inline-error">{{ composeErrors[0] }}</p>
      </div>
      <template #footer>
        <div class="compose-footer">
          <div><b>{{ finalQuestionCount }} 道题</b><span>总分 {{ compose.totalScore }} 分</span></div>
          <div><el-button @click="composeOpen=false">取消</el-button><el-button type="primary" :loading="composing" :disabled="composeErrors.length > 0" @click="createDraft">生成并预览</el-button></div>
        </div>
      </template>
    </el-dialog>

    <el-drawer v-model="draftsOpen" title="已保存试卷" size="min(560px, 92vw)">
      <div v-if="drafts.length" class="draft-list">
        <article v-for="paper in drafts" :key="paper.paper_id" @click="router.push(`/teacher/papers/${paper.paper_id}`)">
          <div><b>{{ paper.title }}</b><span>{{ paper.item_count }} 题 · {{ paper.total_score }} 分</span></div>
          <em :class="paper.status">{{ paper.status === 'published' ? '已发布固定版本' : '草稿' }}</em>
        </article>
      </div>
      <el-empty v-else description="暂无教师组卷草稿" />
    </el-drawer>
  </WorkbenchPage>
</template>

<style scoped>
.workspace-page {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  min-width: 0;
  gap: 14px;
  color: #34445f;
}
.workspace-page :deep(.el-button--primary) {
  --el-button-bg-color: #315cf0;
  --el-button-border-color: #315cf0;
  --el-button-hover-bg-color: #244fd1;
  --el-button-hover-border-color: #244fd1;
  --el-button-active-bg-color: #1f46bb;
  --el-button-active-border-color: #1f46bb;
}
.page-head {
  padding: 0 10px 2px;
}
.page-head h2 {
  margin: 0 0 3px;
  color: #17243d;
  font-size: 27px;
  font-weight: 750;
  letter-spacing: -.02em;
}
.page-head > div > p:last-child {
  max-width: 780px;
  margin: 0;
  color: #66758d;
  font-size: 14px;
  line-height: 1.65;
}
.filter-panel {
  padding: 12px 14px 7px;
  border: 1px solid #e4eaf4;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 3px 12px rgb(40 69 112 / 3%);
}
.filter-toolbar {
  display: grid;
  grid-template-columns:
    minmax(185px, 1.15fr)
    repeat(3, minmax(142px, .9fr))
    minmax(240px, 1.5fr)
    auto;
  align-items: center;
  gap: 10px;
}
.filter-control {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 7px;
}
.filter-control > span {
  flex: none;
  color: #34445f;
  font-size: 14px;
  font-weight: 700;
}
.filter-control :deep(.el-select) {
  min-width: 0;
  flex: 1;
}
.filter-panel :deep(.el-select__wrapper),
.filter-panel :deep(.el-input__wrapper) {
  min-height: 36px;
  border-radius: 8px;
  box-shadow: 0 0 0 1px #dce4f1 inset;
}
.filter-panel :deep(.el-select__wrapper.is-focused),
.filter-panel :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px #315cf0 inset;
}
.keyword-search {
  min-width: 0;
}
.keyword-search :deep(.el-input__prefix) {
  color: #718099;
  font-size: 19px;
}
.compose-button {
  min-width: 108px;
  min-height: 36px;
  border-radius: 8px;
  box-shadow: 0 5px 13px rgb(49 92 240 / 18%);
}
.filter-secondary {
  display: flex;
  justify-content: flex-end;
  gap: 2px;
  padding-top: 3px;
}
.filter-secondary :deep(.el-button) {
  min-height: 27px;
  padding: 2px 7px;
  font-size: 14px;
}
.advanced-trigger {
  margin-right: auto;
}
.advanced-filters {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  padding: 10px 0 5px;
  border-top: 1px solid #edf1f7;
}
.advanced-filters label {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  color: #5f6f86;
  font-size: 14px;
  font-weight: 650;
}
.bank-summary {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0;
  min-height: 30px;
  padding: 0 4px;
  color: #526178;
  font-size: 14px;
  white-space: nowrap;
}
.bank-summary span {
  display: inline-flex;
  align-items: baseline;
}
.bank-summary span + span::before {
  content: '';
  width: 1px;
  height: 13px;
  margin: 0 12px;
  background: #dce4f0;
}
.bank-summary b {
  color: #315cf0;
  font-size: 18px;
}
.bank-summary .usable-count {
  margin-left: auto;
  color: #7c8ba4;
}
.bank-summary .usable-count::before {
  display: none;
}
.selection-bar {
  position: sticky;
  z-index: 6;
  top: 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 9px 13px;
  border: 1px solid #c7d5fb;
  border-radius: 11px;
  background: #f2f5ff;
  box-shadow: 0 7px 20px rgb(43 83 145 / 8%);
}
.selection-bar > div:first-child {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.selection-bar b {
  color: #315cf0;
}
.selection-bar span {
  color: #66758d;
  font-size: 14px;
}
.selection-bar > div:last-child {
  display: flex;
  gap: 8px;
}
.question-masonry {
  width: 100%;
}
.list-state {
  min-height: 320px;
  padding: 28px;
  border: 1px solid #e2e8f2;
  border-radius: 13px;
  background: #fff;
  text-align: center;
}
.list-state p {
  margin: 12px 0;
  color: #6f7e93;
}
.error-state b { color: #b13b3b; }
.load-more-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 12px 0 4px;
  color: #718099;
  font-size: 14px;
}
.question-card {
  box-sizing: border-box;
  display: block;
  width: 100%;
  padding: 13px 13px 10px;
  border: 1px solid #e0e7f1;
  border-radius: 13px;
  background: #fff;
  box-shadow: 0 2px 7px rgb(31 62 94 / 2.5%);
  transition: border-color .16s ease, box-shadow .16s ease;
}
.question-card:hover {
  border-color: #becce0;
  box-shadow: 0 7px 19px rgb(31 62 94 / 5%);
}
.question-card.selected {
  border-color: #6684f5;
  box-shadow: 0 0 0 2px rgb(49 92 240 / 9%);
}
.question-card.blocked {
  background: #fcfcfd;
}
.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.tag-row span {
  padding: 3px 7px;
  border-radius: 5px;
  background: #eef2f6;
  color: #586981;
  font-size: 14px;
  line-height: 1.45;
}
.card-tags .type-tag {
  background: #e7eeff;
  color: #315cf0;
  font-weight: 750;
}
.card-tags .chapter-tag {
  background: #f1f4f9;
  color: #47566d;
}
.card-tags .difficulty-tag {
  background: #fff3d9;
  color: #95600b;
}
.card-tags .difficulty-tag.is-easy {
  background: #e9f8eb;
  color: #2d7b38;
}
.card-tags .difficulty-tag.is-hard {
  background: #ffebeb;
  color: #c33434;
}
.card-tags .difficulty-tag.is-unknown {
  background: #f1f3f6;
  color: #758196;
}
.card-tags .source-tag {
  background: #edf3ff;
  color: #3865c4;
}
.card-tags .blocked-tag {
  background: #fff1e7;
  color: #ad5f18;
}
.tag-row .warning-tag {
  background: #fff2db;
  color: #986713;
}
.stem {
  margin: 11px 2px 9px;
  color: #17243d;
  font-size: 14px;
  font-weight: 640;
  line-height: 1.65;
  overflow-wrap: anywhere;
}
.stem :deep(.katex-display),
.detail-stem :deep(.katex-display) {
  max-width: 100%;
  overflow-x: auto;
  overflow-y: hidden;
}
.stem-images {
  display: grid;
  gap: 7px;
  margin: 8px 0;
}
.stem-images img {
  display: block;
  width: 100%;
  max-height: 190px;
  object-fit: contain;
  border: 1px solid #e2e8ef;
  border-radius: 8px;
  background: #f7f9fb;
}
.option-preview {
  display: grid;
  gap: 3px;
  margin: 3px 2px 1px;
  color: #44546c;
  font-size: 14px;
  line-height: 1.55;
}
.option-preview > div,
.detail-options > div {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr);
  align-items: start;
  gap: 4px;
}
.option-preview b,
.detail-options b {
  color: #315cf0;
}
.option-preview small {
  margin-top: 2px;
  color: #8793a4;
}
.blocked-note {
  margin: 9px 0 2px;
  padding: 7px 9px;
  border-radius: 7px;
  background: #fff7ed;
  color: #9a5b1e;
  font-size: 14px;
  line-height: 1.5;
}
.question-card footer {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  margin-top: 11px;
  padding-top: 9px;
  border-top: 1px solid #edf1f7;
}
.knowledge-meta {
  min-width: 0;
}
.knowledge-meta b,
.knowledge-meta small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.knowledge-meta b {
  color: #526178;
  font-size: 14px;
  font-weight: 650;
}
.knowledge-meta small {
  margin-top: 2px;
  color: #7c8ba4;
  font-size: 14px;
}
.card-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 5px;
}
.card-action {
  min-height: 29px;
  padding: 3px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #53647d;
  font: inherit;
  font-size: 14px;
  font-weight: 650;
  white-space: nowrap;
  cursor: pointer;
}
.card-action:hover {
  background: #f1f5f9;
}
.select-action {
  color: #315cf0;
}
.select-action:hover,
.select-action.active {
  background: #e8eeff;
  color: #244fd1;
}
.select-action:disabled {
  background: transparent;
  color: #a1aab6;
  cursor: not-allowed;
}
.detail-view {
  display: grid;
  gap: 17px;
}
.detail-stem {
  color: #18273c;
  font-size: 17px;
  font-weight: 650;
  line-height: 1.8;
}
.detail-images {
  display: grid;
  gap: 10px;
  padding: 10px;
  border-radius: 9px;
  background: #f5f7fa;
}
.detail-images img {
  width: 100%;
  max-height: 460px;
  object-fit: contain;
  border-radius: 7px;
}
.detail-options {
  display: grid;
  gap: 10px;
  padding: 14px;
  border-radius: 9px;
  background: #f7f9fa;
}
.answer-box {
  display: grid;
  gap: 7px;
  padding: 15px;
  border-left: 4px solid #2c8a82;
  border-radius: 7px;
  background: #eef7f6;
}
.answer-box b {
  color: #216b66;
}
.answer-box p {
  margin: 0;
}
.duplicate-note {
  padding: 10px;
  border-radius: 7px;
  background: #fff5df;
  color: #926315;
}
:global(.compose-dialog) {
  max-height: 82vh;
  overflow: hidden;
  border-radius: 15px;
}
:global(.compose-dialog .el-dialog__header) {
  padding: 18px 22px 12px;
  border-bottom: 1px solid #edf1f6;
}
:global(.compose-dialog .el-dialog__body) {
  max-height: calc(82vh - 142px);
  padding: 16px 22px;
  overflow-y: auto;
}
:global(.compose-dialog .el-dialog__footer) {
  padding: 12px 22px 15px;
  border-top: 1px solid #e7edf5;
  background: #fff;
}
.compose-layout {
  display: grid;
  gap: 13px;
}
.compose-layout > :deep(.el-segmented) {
  width: 100%;
  padding: 4px;
  border-radius: 10px;
  background: #eef3f9;
}
.compose-section,
.selected-section,
.extra-settings {
  padding: 13px 14px;
  border: 1px solid #e3eaf3;
  border-radius: 11px;
  background: #fff;
}
.required-section { background: #f7f9fd; }
.section-title,
.type-heading,
.selected-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.section-title > span {
  padding: 2px 7px;
  border-radius: 99px;
  background: #e7eeff;
  color: #315cf0;
  font-size: 14px;
}
.compose-fields,
.compose-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.compose-fields label,
.compose-grid label {
  display: grid;
  gap: 6px;
  color: #34445f;
  font-size: 14px;
  font-weight: 650;
}
.compose-fields :deep(.el-input-number),
.compose-grid :deep(.el-input-number) { width: 100%; }
.selected-count {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  border: 1px solid #dce4f1;
  border-radius: 8px;
  background: #fff;
}
.selected-count b { color: #315cf0; font-size: 18px; }
.selected-heading { margin: 0; }
.selected-heading > div,
.type-heading > div {
  display: grid;
  gap: 2px;
}
.selected-heading span,
.type-heading span {
  color: #718099;
  font-size: 14px;
}
.lock-note {
  margin-top: 9px;
  padding: 8px 10px;
  border-radius: 8px;
  background: #edf5ff;
  color: #37618c;
  font-size: 14px;
}
.selected-question-list {
  display: grid;
  gap: 7px;
  max-height: 210px;
  margin-top: 10px;
  overflow-y: auto;
}
.selected-question-list > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 9px;
  border-radius: 8px;
  background: #f5f7fa;
  color: #526178;
  font-size: 14px;
}
.selected-question-list > div > span { min-width: 0; flex: 1; }
.type-heading { margin-bottom: 0; }
.type-section:has(.compact-targets) .type-heading { margin-bottom: 10px; }
.type-targets {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}
.type-targets > div {
  display: grid;
  grid-template-columns: 1fr 86px;
  align-items: center;
  gap: 5px;
  padding: 7px 8px;
  border: 1px solid #e1e8f2;
  border-radius: 8px;
  background: #f7f9fc;
  font-size: 14px;
}
.type-targets :deep(.el-input-number) { width: 86px; }
.more-types-trigger { margin-top: 5px; }
.more-targets { margin-top: 4px; }
.extra-settings { padding: 7px 12px; background: #f8fafc; }
.extra-settings > :deep(.el-button) { padding-left: 0; }
.extra-settings .compose-grid { margin-top: 6px; }
.inline-error {
  margin: 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: #fff0f0;
  color: #b33a3a;
  font-size: 14px;
}
.compose-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.compose-footer > div:first-child {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.compose-footer b { color: #315cf0; font-size: 17px; }
.compose-footer span { color: #718099; font-size: 14px; }
.draft-list {
  display: grid;
  gap: 10px;
}
.draft-list article {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px;
  border: 1px solid #e1e8ec;
  border-radius: 10px;
  cursor: pointer;
}
.draft-list article:hover {
  border-color: #8ebdb8;
  background: #f8fbfb;
}
.draft-list b,
.draft-list span {
  display: block;
}
.draft-list span {
  margin-top: 5px;
  color: #74838e;
  font-size: 14px;
}
.draft-list em {
  padding: 5px 8px;
  border-radius: 99px;
  background: #eef3f5;
  color: #60717d;
  font-size: 14px;
  font-style: normal;
}
.draft-list em.published {
  background: #e7f5f2;
  color: #21766e;
}

@media (max-width: 1200px) {
  .filter-toolbar {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
  .keyword-search {
    grid-column: span 3;
  }
  .compose-button {
    width: 100%;
  }
  .bank-summary {
    overflow-x: auto;
  }
}
@media (max-width: 820px) {
  .filter-toolbar {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .keyword-search {
    grid-column: 1 / -1;
  }
  .compose-button {
    grid-column: 1 / -1;
  }
  .advanced-filters {
    grid-template-columns: 1fr;
  }
  .bank-summary .usable-count {
    margin-left: 0;
  }
  .bank-summary .usable-count::before {
    display: block;
  }
  .type-targets {
    grid-template-columns: repeat(2, 1fr);
  }
}
@media (max-width: 680px) {
  .page-head,
  .selection-bar {
    align-items: stretch;
    flex-direction: column;
  }
  .head-actions {
    width: 100%;
  }
  .filter-toolbar,
  .compose-fields,
  .compose-grid {
    grid-template-columns: 1fr;
  }
  .keyword-search,
  .compose-button {
    grid-column: auto;
  }
  .filter-control {
    display: grid;
    grid-template-columns: 42px minmax(0, 1fr);
  }
  .bank-summary {
    flex-wrap: wrap;
    row-gap: 4px;
    overflow-x: visible;
    white-space: normal;
  }
  .selection-bar {
    top: 85px;
  }
  .selection-bar > div:first-child {
    align-items: flex-start;
    flex-direction: column;
    gap: 2px;
  }
  .selection-bar > div:last-child {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
  .question-card footer {
    grid-template-columns: 1fr;
  }
  .card-actions {
    justify-content: space-between;
  }
  .type-targets {
    grid-template-columns: 1fr;
  }
}
</style>

<style scoped>
.workspace-page.workbench-page{display:flex;gap:12px}.page-head,.filter-panel,.bank-summary,.selection-bar{margin-top:0;margin-bottom:0}.filter-panel{padding:12px 16px}.page-head h1,.page-head h2{font-size:26px}
</style>
