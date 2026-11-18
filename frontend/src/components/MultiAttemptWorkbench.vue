<script setup lang="ts">
import WorkbenchPage from "./WorkbenchPage.vue"
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { Position, WarningFilled } from '@element-plus/icons-vue'
import MathText from './MathText.vue'
import SourceQuestionContent from './SourceQuestionContent.vue'
import DiagnosisAnalysisContent from './DiagnosisAnalysisContent.vue'
import LearningResourceList from './LearningResourceList.vue'
import HistorySummaryTabs from './HistorySummaryTabs.vue'
import { friendlyQuizCode } from '../utils/presentation'
import { recommendedResourceLinks } from '../utils/resourceLinks'
import { analysisNotice } from '../utils/analysisNotice'
import {
  chapterWrongDistribution, filterHistoryAttempts, historyRecordType,
  historyScoringStatus, scoreRateLabel, validScoreRate,
  type HistoryRecordType, type HistoryScoringStatus,
} from '../utils/historyDiagnosis'

const props = defineProps<{
  result: any
  diagnosisReady: boolean
  diagnosisMeta?: any
  intervention?: any
  loading?: boolean
  runningAi?: boolean
}>()
const emit = defineEmits<{ retest: []; openAttempt: [assessmentVersionId: string] }>()
const router = useRouter()
const chartHost = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
let chartResizeObserver: ResizeObserver | null = null
const activeTab = ref<'overview' | 'wrong' | 'records'>('overview')
const viewMode = ref<'all' | 'paper' | 'chapter'>('all')
const selectedPaper = ref('')
const selectedAttemptId = ref('')
const selectedChapter = ref('')
const repeatedOnly = ref(false)
const showAllWrong = ref(false)
const showAllChapters = ref(false)
const selectedWrong = ref<any | null>(null)
const recordType = ref<HistoryRecordType>('all')
const recordStatus = ref<HistoryScoringStatus>('all')

const stats = computed(() => props.result?.statistics || {})
const attempts = computed(() => props.result?.attempts || [])
const allWrongItems = computed(() => props.result?.wrong_items || [])
const analysis = computed(() => props.result?.ai_analysis || {})
const priorityConcepts = computed(() => (props.result?.weak_concepts || analysis.value?.concepts || analysis.value?.concept_hypotheses || []).slice(0, 2))
const repeatedWrongCount = computed(() => allWrongItems.value.filter((row: any) => row.repeated).length)
const chapterDistribution = computed(() => chapterWrongDistribution(allWrongItems.value))
const visibleChapters = computed(() => showAllChapters.value ? chapterDistribution.value : chapterDistribution.value.slice(0, 4))
const overviewSummary = computed(() => {
  const rated = attempts.value.filter((row: any) => validScoreRate(row.score_rate) !== null).length
  if (!rated) return '当前统计范围尚无可计算得分率的考试。完成评分后可查看成绩与错题证据。'
  const wrong = Number(stats.value.unique_wrong_question_count || 0)
  return `当前范围有 ${rated} 次考试可计算得分率，平均为 ${scoreRateLabel(stats.value.average_score_rate)}。${wrong ? `记录到 ${wrong} 道不同错题，${repeatedWrongCount.value ? `其中 ${repeatedWrongCount.value} 道反复出错。` : '尚无重复出错记录。'}` : '已评分题目中暂未记录错题。'}`
})
const plan = computed(() => props.result?.retest_plan || {})
const planChapterNames = computed(() => (plan.value.chapters || []).map((row: any) => row.title).filter(Boolean))
const planChapterSummary = computed(() => planChapterNames.value.length > 2
  ? `${planChapterNames.value.slice(0, 2).join('、')}等${planChapterNames.value.length}章`
  : planChapterNames.value.join('、') || '当前薄弱章节')
const aiFailure = computed(() => analysisNotice(analysis.value))
const workflowResources = computed(() => props.intervention?.outputs?.recommendations?.result?.recommendations || [])
const resources = computed(() => recommendedResourceLinks(workflowResources.value, props.result?.resources || []))
const publishedRetest = computed(() => props.intervention?.outputs?.retest?.status === 'published')
const generatedCount = computed(() => props.intervention?.outputs?.generation?.result?.candidates?.length || 0)
const filteredAttempts = computed<any[]>(() => filterHistoryAttempts<any>(attempts.value, recordType.value, recordStatus.value))
const ratedAttempts = computed(() => attempts.value.filter((row: any) => validScoreRate(row.score_rate) !== null).length)
const partiallyGradedAttempts = computed(() => attempts.value.filter((row: any) => historyScoringStatus(row) === 'partial').length)

const paperOptions = computed(() => attempts.value.map((row: any) => ({
  value: row.assessment_version_id,
  label: `${friendlyQuizCode(row.quiz_code) ? `${friendlyQuizCode(row.quiz_code)} · ` : ''}${row.title}`,
})))
const chapterOptions = computed(() => chapterDistribution.value.map(row => ({
  value: row.key, label: row.title,
})))
const filteredWrongItems = computed(() => allWrongItems.value.filter((item: any) => {
  if (repeatedOnly.value && !item.repeated) return false
  if (selectedChapter.value && String(item.chapter_id || item.chapter?.chapter_id || item.chapter?.title || 'unmapped') !== selectedChapter.value) return false
  const occurrences = item.occurrences || [item]
  if (selectedAttemptId.value) {
    return occurrences.some((row: any) => row.attempt_id === selectedAttemptId.value)
  }
  if (viewMode.value === 'paper' && selectedPaper.value) {
    return occurrences.some((row: any) => row.assessment_version_id === selectedPaper.value)
  }
  return true
}))
const visibleWrongItems = computed(() => showAllWrong.value ? filteredWrongItems.value : filteredWrongItems.value.slice(0, 5))
const remainingWrongCount = computed(() => Math.max(0, filteredWrongItems.value.length - 5))
const filteredWrongEvents = computed(() => filteredWrongItems.value.reduce((sum: number, item: any) => sum + displayErrorCount(item), 0))

const TYPE_LABELS: Record<string, string> = {
  single_choice: '单选题', multiple_choice: '多选题', multi_choice: '多选题',
  true_false: '判断题', numeric: '数值计算题', calculation: '计算题',
  short_answer: '简答题', term: '名词解释', fill_blank: '填空题',
  formula_fill: '公式填空', derivation: '推导题', comprehensive: '综合题', matching: '匹配题',
}

function numberText(value: unknown) {
  if (value === null || value === undefined) return '—'
  const number = Number(value)
  return Number.isInteger(number) ? String(number) : number.toFixed(1)
}
function dateText(value: unknown) {
  const text = String(value || '')
  if (!text) return '未记录考试时间'
  return text.replace('T', ' ').slice(0, 16)
}
function answerValue(value: any): any {
  if (value === null || value === undefined || value === '') return null
  if (typeof value !== 'object' || Array.isArray(value)) return value
  for (const key of ['raw_text', 'text', 'selected_keys', 'keys', 'key', 'values', 'parts', 'value', 'answer', 'reference_answer']) {
    if (value[key] !== undefined && value[key] !== null && value[key] !== '') return value[key]
  }
  return value
}
function answerText(input: any): string {
  const value = answerValue(input)
  if (value === null || value === undefined || value === '') return '未作答'
  if (typeof value === 'boolean') return value ? '正确' : '错误'
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map((entry: any) => answerText(entry)).join('；')
  return Object.entries(value).map(([key, entry]) => `${key}：${answerText(entry)}`).join('；')
}
function answerKeys(input: any): string[] {
  const value = answerValue(input)
  return (Array.isArray(value) ? value : [value]).map((entry: any) =>
    String(entry && typeof entry === 'object' ? (entry.key ?? entry.value ?? '') : (entry ?? '')).trim().toUpperCase()).filter(Boolean)
}
function correctPayload(item: any) { return item?.answer_reference?.payload ?? null }
function optionsFor(item: any): any[] {
  const options = item?.options || []
  if (Array.isArray(options)) return options.map((option: any, index: number) =>
    typeof option === 'object' ? option : { key: String.fromCharCode(65 + index), text: String(option) })
  return Object.entries(options).map(([key, value]) => ({ key, text: value }))
}
function selected(item: any, key: string) { return answerKeys(item.response).includes(String(key).toUpperCase()) }
function correctOption(item: any, key: string) { return answerKeys(correctPayload(item)).includes(String(key).toUpperCase()) }
function optionClass(item: any, key: string) {
  const picked = selected(item, key); const correct = correctOption(item, key)
  return { 'is-correct': correct, 'is-wrong-picked': picked && !correct }
}
function resourceType(resource: any) {
  const type = String(resource?.content_type || resource?.source_type || resource?.platform || '').toLowerCase()
  if (type.includes('video') || type.includes('bilibili')) return '视频'
  if (type.includes('ppt') || type.includes('slide')) return '课件'
  if (type.includes('article')) return '文章'
  return resource?.platform || '学习资料'
}
function setView(mode: 'all' | 'paper' | 'chapter') {
  viewMode.value = mode
  selectedAttemptId.value = ''
  if (mode === 'all') { selectedPaper.value = ''; selectedChapter.value = '' }
  if (mode === 'paper') selectedChapter.value = ''
  if (mode === 'paper' && !selectedPaper.value) selectedPaper.value = paperOptions.value[0]?.value || ''
  if (mode === 'chapter' && !selectedChapter.value) selectedChapter.value = chapterDistribution.value[0]?.key || ''
}
function itemDomId(item: any) { return `history-question-${String(item?.repeat_group_key || item?.question_id || item?.position).replace(/[^\w-]/g, '-')}` }
function wrongItemTitle(item: any) {
  const concept = String(item?.knowledge_point?.name || '').trim()
  return concept && concept !== '未映射知识点'
    ? concept : (item?.position ? `未标注知识点 · 第 ${item.position} 题` : '未标注知识点题目')
}
function matchingOccurrences(item: any): any[] {
  const rows = item.occurrences || [item]
  if (selectedAttemptId.value) return rows.filter((row: any) => row.attempt_id === selectedAttemptId.value)
  if (viewMode.value === 'paper' && selectedPaper.value) return rows.filter((row: any) => row.assessment_version_id === selectedPaper.value)
  return rows
}
function displayErrorCount(item: any) {
  if (selectedAttemptId.value || (viewMode.value === 'paper' && selectedPaper.value)) return matchingOccurrences(item).length
  return Number(item.error_count || matchingOccurrences(item).length || 1)
}
function wrongSources(item: any) {
  const titles = selectedAttemptId.value || (viewMode.value === 'paper' && selectedPaper.value)
    ? [...new Set(matchingOccurrences(item).map((row: any) => row.assessment_title).filter(Boolean))]
    : item.assessment_titles || [...new Set((item.occurrences || []).map((row: any) => row.assessment_title).filter(Boolean))]
  return titles.length > 2 ? `${titles.slice(0, 2).join('、')}等${titles.length}份试卷` : titles.join('、') || item.assessment_title || '来源试卷未记录'
}
function conceptWrongCount(row: any) {
  if (row.error_events !== undefined) return Number(row.error_events || 0)
  const name = row.name || row.concept
  return allWrongItems.value.filter((item: any) => item.knowledge_point?.name === name)
    .reduce((total: number, item: any) => total + Number(item.error_count || 1), 0)
}
function recordTypeLabel(row: any) {
  return { quiz: '课堂小测', exam: '在线考试', retest: '针对性复测' }[historyRecordType(row)]
}
function recordStatusLabel(row: any) {
  return { graded: '已评分', partial: '部分评分', ungraded: '待评分' }[historyScoringStatus(row)]
}
function escapeHtml(value: unknown) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char] || char)
}
function openWrong(item: any) {
  const occurrence = viewMode.value === 'paper' && (selectedPaper.value || selectedAttemptId.value)
    ? [...(item.occurrences || [])].reverse().find((row: any) => selectedAttemptId.value
      ? row.attempt_id === selectedAttemptId.value
      : row.assessment_version_id === selectedPaper.value)
    : null
  selectedWrong.value = occurrence ? { ...item, ...occurrence, occurrences: item.occurrences, error_count: item.error_count, assessment_titles: item.assessment_titles } : item
}
function openRecordedAttempt(row: any) {
  if (row.attempt_id) void router.push(`/teacher/attempts/${encodeURIComponent(row.attempt_id)}`)
  else emit('openAttempt', row.assessment_version_id)
}
async function locateConcept(row: any) {
  const name = row?.name || row?.concept
  const item = allWrongItems.value.find((entry: any) =>
    (row?.kp_id && entry?.knowledge_point?.kp_id === row.kp_id)
    || (name && entry?.knowledge_point?.name === name)
    || (row?.chapter && entry?.chapter?.title === row.chapter))
  activeTab.value = 'wrong'
  viewMode.value = 'all'
  selectedAttemptId.value = ''
  repeatedOnly.value = false
  selectedChapter.value = ''
  selectedPaper.value = ''
  if (!item) return
  if (allWrongItems.value.indexOf(item) >= 5) showAllWrong.value = true
  await nextTick()
  document.getElementById(itemDomId(item))?.scrollIntoView({ block: 'center' })
  openWrong(item)
}
async function showWrongForAttempt(row: any) {
  activeTab.value = 'wrong'
  viewMode.value = 'paper'
  selectedPaper.value = row.assessment_version_id || ''
  selectedAttemptId.value = row.attempt_id || ''
  repeatedOnly.value = false
  showAllWrong.value = false
  await nextTick()
  document.querySelector('.wrong-workspace')?.scrollIntoView({ block: 'start' })
}

watch(() => props.result, () => {
  showAllWrong.value = false
  showAllChapters.value = false
  selectedWrong.value = null
  viewMode.value = 'all'
  selectedPaper.value = ''
  selectedAttemptId.value = ''
  selectedChapter.value = ''
  repeatedOnly.value = false
  recordType.value = 'all'
  recordStatus.value = 'all'
})
watch(activeTab, async tab => { if (tab === 'records') { await nextTick(); renderChart() } })

function renderChart() {
  if (!chartHost.value) return
  if (chart && chart.getDom() !== chartHost.value) {
    chart.dispose()
    chart = null
  }
  if (!chart) chart = echarts.init(chartHost.value)
  if (typeof ResizeObserver !== 'undefined') {
    if (!chartResizeObserver) chartResizeObserver = new ResizeObserver(resizeChart)
    chartResizeObserver.disconnect()
    chartResizeObserver.observe(chartHost.value)
  }
  const rows = attempts.value
  const labelInterval = chartLabelInterval(rows.length, chartHost.value.clientWidth)
  chart.setOption({
    animation: false,
    color: ['#2563eb'],
    grid: { left: 62, right: 24, top: 26, bottom: 44 },
    tooltip: {
      trigger: 'axis',
      textStyle: { fontSize: 14 },
      formatter: (params: any) => {
        const point = Array.isArray(params) ? params[0] : params
        const row = rows[point?.dataIndex] || {}
        return `<b>${escapeHtml(row.title || '考试')}</b><br/>${escapeHtml(recordTypeLabel(row))} · ${escapeHtml(recordStatusLabel(row))}<br/>得分：${escapeHtml(numberText(row.awarded_score))}/${escapeHtml(numberText(row.max_score))}<br/>得分率：${escapeHtml(scoreRateLabel(row.score_rate))}<br/>时间：${escapeHtml(dateText(row.exam_time))}`
      },
    },
    legend: { show: false },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: rows.map((row: any, index: number) => `第${row.exam_order || index + 1}次`),
      axisLabel: { color: '#475569', fontSize: 12, hideOverlap: true, showMaxLabel: true, interval: labelInterval },
      axisLine: { lineStyle: { color: '#d8e2ef' } },
    },
    yAxis: {
      type: 'value', min: 0, max: 100, interval: 25,
      axisLabel: { formatter: '{value}%', color: '#475569', fontSize: 12 },
      splitLine: { lineStyle: { color: '#edf2f7' } },
    },
    series: [{
      name: '得分率', type: 'line', smooth: false, symbolSize: 8,
      lineStyle: { width: 2.5 }, areaStyle: { color: 'rgba(37,99,235,.08)' },
      data: rows.map((row: any) => ({
        value: validScoreRate(row.score_rate) === null ? null : Math.round(Number(row.score_rate) * 100),
        symbol: historyRecordType(row) === 'retest' ? 'diamond' : (historyRecordType(row) === 'quiz' ? 'circle' : 'rect'),
        itemStyle: { color: historyRecordType(row) === 'retest' ? '#7c3aed' : (historyRecordType(row) === 'quiz' ? '#2563eb' : '#0ea5e9') },
      })),
    }],
  }, true)
}
function chartLabelInterval(count: number, width: number) {
  const visibleLabels = Math.max(2, Math.floor((width - 70) / 60))
  return Math.max(0, Math.ceil(count / visibleLabels) - 1)
}
function resizeChart() {
  if (!chart) return
  chart.resize()
  chart.setOption({ xAxis: { axisLabel: { interval: chartLabelInterval(attempts.value.length, chartHost.value?.clientWidth || 0) } } })
}
watch(() => props.result?.attempts, async () => { await nextTick(); renderChart() }, { deep: true })
onMounted(() => { renderChart(); window.addEventListener('resize', resizeChart) })
onBeforeUnmount(() => { window.removeEventListener('resize', resizeChart); chartResizeObserver?.disconnect(); chart?.dispose(); chart = null })
</script>

<template>
  <WorkbenchPage :scroll="false" class="history-workbench">
    <template #header>
    <HistorySummaryTabs :stats="stats" :active-tab="activeTab" @update:active-tab="activeTab = $event" />

    </template>
    <div v-if="activeTab === 'overview'" class="history-content-grid">
      <div class="history-primary">
        <section class="history-surface overview-conclusion">
          <h2>综合结论</h2>
          <p>{{ overviewSummary }}</p>
          <details class="full-analysis"><summary>查看完整分析与题目依据</summary><DiagnosisAnalysisContent :analysis="analysis" scope="student_history" locatable @locate="locateConcept" /></details>
          <p v-if="aiFailure" class="analysis-notice">{{ aiFailure }}</p>
        </section>

        <section class="history-surface overview-priorities">
          <div class="surface-heading"><h2>优先关注</h2><span>依据当前范围的真实错题</span></div>
          <div v-if="priorityConcepts.length" class="priority-list">
            <button v-for="(row, index) in priorityConcepts" :key="`${row.kp_id || row.name || row.concept}-${index}`" type="button" @click="locateConcept(row)">
              <b class="priority-index">{{ String(index + 1).padStart(2, '0') }}</b>
              <span><strong>{{ row.name || row.concept }}</strong><small>{{ row.chapter || '相关知识点' }} · 相关题目答错 {{ conceptWrongCount(row) }} 次</small></span>
              <em>查看作答依据 ›</em>
            </button>
          </div>
          <p v-else class="history-empty-inline">当前没有可定位的薄弱知识点。</p>
        </section>

        <section class="history-surface overview-actions">
          <h2>建议教学行动</h2>
          <div class="action-links">
            <button type="button" @click="activeTab = 'wrong'"><b>查看错题清单</b><small>核对具体作答与解析</small><span>›</span></button>
            <button type="button" @click="router.push('/teacher/resources')"><b>查看学习资源</b><small>查找对应章节资料</small><span>›</span></button>
            <button type="button" :disabled="!plan.ready || runningAi" @click="emit('retest')"><b>生成并发布复测</b><small>通过自动校验后立即发布</small><span>›</span></button>
          </div>
        </section>

        <section class="history-surface overview-distribution">
          <div class="surface-heading"><h2>知识点分布</h2><span>按不同错题所属章节统计</span></div>
          <div v-if="chapterDistribution.length" class="distribution-table">
            <div class="distribution-head"><span>章节</span><span>错题数（占比）</span></div>
            <div v-for="row in visibleChapters" :key="row.key" class="distribution-row"><span>{{ row.title }}</span><div class="distribution-bar"><i :style="{ width: `${row.percent}%` }"></i></div><b>{{ row.count }} 道（{{ row.percent }}%）</b></div>
            <button v-if="chapterDistribution.length > 4" type="button" class="text-action" @click="showAllChapters = !showAllChapters">{{ showAllChapters ? '收起其他章节' : `查看全部 ${chapterDistribution.length} 个章节` }}</button>
          </div>
          <p v-else class="history-empty-inline">当前范围没有已评分错题，暂无分布数据。</p>
        </section>
      </div>

      <aside class="history-side history-surface">
        <section><h2>本次关注</h2><div v-if="priorityConcepts.length" class="side-priorities"><button v-for="row in priorityConcepts.slice(0, 2)" :key="row.kp_id || row.name" type="button" @click="locateConcept(row)"><b>{{ row.name || row.concept }}</b><small>相关题目答错 {{ conceptWrongCount(row) }} 次</small><span>查看相关错题 ›</span></button></div><p v-else class="history-empty-inline">暂无明确的重点错题。</p></section>
        <section><div class="side-heading"><h2>推荐学习资源</h2><button type="button" class="text-action" @click="router.push('/teacher/resources')">查看相关资料</button></div><LearningResourceList :resources="resources" /></section>
        <section class="side-retest"><h2>针对性复测</h2><p>优先从题库选择符合薄弱知识点的新题，缺题时由 AI 补充，校验后发布。</p><strong v-if="plan.ready">{{ plan.item_count }} 题</strong><p>{{ plan.ready ? `${planChapterSummary}。通过自动校验后立即发布。` : (plan.reason || '当前没有适合生成复测的错题。') }}</p><p v-if="generatedCount" class="workflow-state">已生成 {{ generatedCount }} 道候选题，自动筛选并核验后发布</p><p v-if="publishedRetest" class="workflow-state">复测已发布</p><el-button type="primary" :icon="Position" :loading="runningAi" :disabled="!plan.ready" @click="emit('retest')">生成并发布复测</el-button></section>
      </aside>
    </div>

    <div v-else-if="activeTab === 'wrong'" class="history-content-grid wrong-workspace">
      <div class="history-primary">
        <section class="history-surface wrong-surface">
          <header class="wrong-heading"><div><h2>错题清单</h2><p>共 {{ filteredWrongItems.length }} 道不同错题 · {{ filteredWrongEvents }} 次错误作答</p></div><div class="wrong-filters"><el-select v-model="selectedChapter" clearable placeholder="全部章节" aria-label="按章节筛选"><el-option v-for="row in chapterOptions" :key="row.value" :label="row.label" :value="row.value" /></el-select><el-checkbox v-model="repeatedOnly">仅看重复错误</el-checkbox></div></header>
          <div class="wrong-view-tabs"><button type="button" :class="{ active: viewMode === 'all' }" :aria-pressed="viewMode === 'all'" @click="setView('all')">全部错题</button><button type="button" :class="{ active: viewMode === 'paper' }" :aria-pressed="viewMode === 'paper'" @click="setView('paper')">按试卷查看</button><button type="button" :class="{ active: viewMode === 'chapter' }" :aria-pressed="viewMode === 'chapter'" @click="setView('chapter')">按章节统计</button><el-select v-if="viewMode === 'paper'" v-model="selectedPaper" placeholder="选择试卷" @change="selectedAttemptId = ''"><el-option v-for="row in paperOptions" :key="row.value" :label="row.label" :value="row.value" /></el-select></div>
          <div v-if="viewMode === 'chapter'" class="chapter-stat-list" aria-label="错题章节统计"><button v-for="row in chapterDistribution" :key="row.key" type="button" :class="{ active: selectedChapter === row.key }" @click="selectedChapter = row.key"><span>{{ row.title }}</span><b>{{ row.count }} 道 · {{ row.percent }}%</b></button></div>
          <div class="wrong-table">
            <div class="wrong-table-head"><span>题目与章节</span><span>答错次数</span><span>操作</span></div>
            <article v-for="item in visibleWrongItems" :id="itemDomId(item)" :key="item.repeat_group_key" class="wrong-row"><div><b>{{ wrongItemTitle(item) }}</b><small>{{ item.chapter?.title || '未映射章节' }} · {{ TYPE_LABELS[item.question_type] || item.question_type }} · {{ wrongSources(item) }}</small></div><strong :class="{ repeated: displayErrorCount(item) > 1 }">{{ displayErrorCount(item) }} 次</strong><button type="button" @click="openWrong(item)">查看题目与解析</button></article>
            <div v-if="!visibleWrongItems.length" class="history-empty"><el-icon><WarningFilled /></el-icon><span>当前筛选下没有错题。可调整试卷、章节或重复错误条件。</span></div>
          </div>
          <button v-if="remainingWrongCount" type="button" class="text-action more-wrong" @click="showAllWrong = !showAllWrong">{{ showAllWrong ? '收起其他错题' : `查看全部 ${filteredWrongItems.length} 道错题` }}</button>
          <details v-if="result.ungraded_items?.length" class="ungraded-list"><summary>另有 {{ result.ungraded_items.length }} 题次待评分</summary><p v-for="(item, index) in result.ungraded_items" :key="`${item.attempt_id}-${item.paper_item_id}-${index}`">{{ friendlyQuizCode(item.quiz_code) || item.assessment_title }} · 第 {{ item.position }} 题</p></details>
        </section>
      </div>
      <aside class="history-side history-surface">
        <section><h2>本次关注</h2><div v-if="priorityConcepts.length" class="side-priorities"><button v-for="row in priorityConcepts.slice(0, 3)" :key="row.kp_id || row.name" type="button" @click="locateConcept(row)"><b>{{ row.name || row.concept }}</b><small>相关题目答错 {{ conceptWrongCount(row) }} 次</small><span>查看作答依据 ›</span></button></div><p v-else class="history-empty-inline">暂无明确的重点错题。</p></section>
        <section><div class="side-heading"><h2>推荐学习资源</h2><button type="button" class="text-action" @click="router.push('/teacher/resources')">查看相关资料</button></div></section>
        <section class="side-retest"><h2>针对性复测</h2><p>优先从题库选择符合薄弱知识点的新题，缺题时由 AI 补充，校验后发布。</p><strong v-if="plan.ready">{{ plan.item_count }} 题</strong><p>{{ plan.ready ? `${planChapterSummary}。通过自动校验后立即发布。` : (plan.reason || '当前没有适合生成复测的错题。') }}</p><p v-if="publishedRetest" class="workflow-state">复测已发布</p><el-button type="primary" :icon="Position" :loading="runningAi" :disabled="!plan.ready" @click="emit('retest')">生成并发布复测</el-button></section>
      </aside>
    </div>

    <div v-else class="history-content-grid records-workspace">
      <div class="history-primary">
        <section class="history-surface score-chart-surface"><div class="surface-heading"><h2>历次成绩</h2><div class="chart-legend" aria-label="考试类型图例"><span><i class="dot-quiz"></i>课堂小测</span><span><i class="dot-exam"></i>在线考试</span><span><i class="dot-retest"></i>针对性复测</span></div></div><div v-if="attempts.length" ref="chartHost" class="history-score-chart"></div><p v-else class="history-empty-inline">当前范围没有考试记录。</p><p class="chart-note">仅展示可计算得分率的记录；不同试卷难度、题型和范围可能不同，折线不能单独证明进步或退步。</p></section>
        <section class="history-surface records-table-surface"><div class="surface-heading"><h2>考试记录</h2><span>显示 {{ filteredAttempts.length }} / {{ attempts.length }} 次</span></div><div class="records-table"><div class="records-head"><span>考试名称</span><span>类型</span><span>得分率</span><span>状态</span><span>操作</span></div><article v-for="row in filteredAttempts" :key="row.attempt_id" class="record-row"><div><b>第 {{ row.exam_order }} 次 · {{ row.title }}</b><small>{{ dateText(row.exam_time) }} · {{ numberText(row.awarded_score) }}/{{ numberText(row.max_score) }} 分</small></div><span class="record-type" :class="historyRecordType(row)">{{ recordTypeLabel(row) }}</span><strong>{{ scoreRateLabel(row.score_rate) }}</strong><span class="record-status" :class="historyScoringStatus(row)">{{ recordStatusLabel(row) }}</span><div class="record-actions"><button type="button" @click="openRecordedAttempt(row)">查看试卷</button><button type="button" @click="showWrongForAttempt(row)">看错题</button></div></article><p v-if="!filteredAttempts.length" class="history-empty">当前筛选下没有考试记录。</p></div></section>
      </div>
      <aside class="history-side history-surface record-side"><section><h2>记录摘要</h2><div class="record-facts"><p><span>考试次数</span><b>{{ attempts.length }} 次</b></p><p><span>平均得分率</span><b>{{ scoreRateLabel(stats.average_score_rate) }}</b></p><p><span>可计算得分率</span><b>{{ ratedAttempts }} 次</b></p><p><span>部分评分</span><b>{{ partiallyGradedAttempts }} 次</b></p><p><span>待评分题次</span><b>{{ stats.ungraded_item_events ?? 0 }} 题次</b></p></div></section><section><h2>查看方式</h2><label class="record-filter"><span>考试类型</span><el-select v-model="recordType" aria-label="按考试类型筛选"><el-option label="全部类型" value="all" /><el-option label="课堂小测" value="quiz" /><el-option label="在线考试" value="exam" /><el-option label="针对性复测" value="retest" /></el-select></label><label class="record-filter"><span>评分状态</span><el-select v-model="recordStatus" aria-label="按评分状态筛选"><el-option label="全部状态" value="all" /><el-option label="已评分" value="graded" /><el-option label="部分评分" value="partial" /><el-option label="待评分" value="ungraded" /></el-select></label></section><section class="record-navigation"><h2>与诊断联动</h2><button type="button" @click="activeTab = 'wrong'">查看错题清单 ›</button><button type="button" @click="activeTab = 'overview'">返回诊断概览 ›</button></section></aside>
    </div>

    <el-dialog :model-value="Boolean(selectedWrong)" :title="selectedWrong ? `第 ${selectedWrong.position} 题 · ${wrongItemTitle(selectedWrong)}` : '错题详情'" width="min(960px, 94vw)" append-to-body class="history-question-dialog" @close="selectedWrong = null">
      <div v-if="selectedWrong" class="question-detail"><p class="question-origin">{{ wrongSources(selectedWrong) }} · {{ selectedWrong.chapter?.title || '未映射章节' }} · {{ TYPE_LABELS[selectedWrong.question_type] || selectedWrong.question_type }} · 当前筛选 {{ displayErrorCount(selectedWrong) }} 次出错</p><div class="question-stem"><SourceQuestionContent :question="selectedWrong" /></div><div v-if="optionsFor(selectedWrong).length" class="question-options"><div v-for="option in optionsFor(selectedWrong)" :key="option.key" class="question-option" :class="optionClass(selectedWrong, option.key)"><b>{{ option.key }}</b><MathText :text="option.text || option.value || ''" /><span v-if="correctOption(selectedWrong, option.key)">正确答案</span><span v-if="selected(selectedWrong, option.key) && !correctOption(selectedWrong, option.key)">学生选择</span></div></div><div class="question-answers"><div><small>学生作答</small><MathText :text="answerText(selectedWrong.response)" /></div><div><small>正确答案</small><MathText :text="answerText(correctPayload(selectedWrong))" /></div></div><p class="question-score">本题得分 {{ numberText(selectedWrong.awarded_score) }}/{{ numberText(selectedWrong.max_score) }} · 所属知识点 {{ selectedWrong.knowledge_point?.name || '未映射' }}</p><section class="question-explanation"><h3>参考解析</h3><MathText :text="selectedWrong.answer_reference?.explanation || '该题暂未提供独立解析。'" /></section><details v-if="selectedWrong.occurrences?.length" class="question-history"><summary>历史作答记录（{{ selectedWrong.occurrences.length }} 次）</summary><article v-for="(row, index) in selectedWrong.occurrences" :key="`${row.attempt_id}-${index}`"><div><b>{{ friendlyQuizCode(row.quiz_code) || row.assessment_title }} · 第 {{ row.position }} 题</b><small>{{ dateText(row.exam_time) }}</small></div><span>作答：{{ answerText(row.response) }}</span><strong>{{ numberText(row.awarded_score) }}/{{ numberText(row.max_score) }} 分</strong></article></details></div>
    </el-dialog>
  </WorkbenchPage>
</template>



<style scoped src="../styles/multiAttemptWorkbench.css"></style>

<style scoped>
.history-workbench.workbench-page{display:flex;gap:12px}.history-content-grid{height:100%;min-height:0;align-items:stretch}.history-primary,.history-side{min-height:0;overflow:auto;display:block}.history-primary>*+*{margin-top:12px}.history-side{position:static;max-height:none}.records-head{position:sticky;top:0;background:#f5f8fc;z-index:1}@media(max-width:850px){.history-content-grid{grid-template-columns:1fr;grid-template-rows:1fr 1fr}}
</style>
