<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { api } from '../api/client'
import { friendlyError } from '../utils/presentation'
import ThreeLayerPanel from '../components/ThreeLayerPanel.vue'

type TabName = 'three' | 'overview' | 'weak' | 'trend' | 'plan'
type TagType = '' | 'success' | 'warning' | 'info' | 'danger'

interface ListResponse<T> {
  total: number
  items: T[]
}

interface KnowledgePoint {
  kp_id: string
  name: string
  chapter: string
}

interface KnowledgeState {
  kp_id: string
  state: string
  evidence_count: number
  independent_family_count: number
  time_span_days: number
  hint_count?: number
  last_evidence_at?: string
  explanation?: string
}

interface Diagnosis {
  diagnosis_id: string
  level: string
  target_id: string
  payload: Record<string, any>
  evidence_refs: Record<string, any>[]
  uncertainties: string[]
  next_actions: string[]
  skill_version: string
  run_id: string
  engine: string
  created_at: string
}

interface AttemptSummary {
  attempt_id: string
  status: string
  started_at: string
  submitted_at?: string
  score: number | null
  max_score: number | null
  manual_pending_count: number
  diagnosis_status?: string
}

interface PlanTask {
  task_id: string
  kp_id: string
  title: string
  kind: string
  material_ref?: string
  question_ids?: string[]
  minutes: number
  due_date?: string
  completion_criteria?: string
  rationale?: string
  status: string
  completed_at?: string
}

interface StudyPlanResponse {
  plan: null | {
    plan_id: string
    exam_date: string
    daily_minutes: number
    created_at: string
    payload?: Record<string, any>
  }
  tasks: PlanTask[]
  total_minutes: number
}

const route = useRoute()
const router = useRouter()
const validTabs: TabName[] = ['three', 'overview', 'weak', 'trend', 'plan']

function routeTab(value: unknown): TabName {
  const tab = String(value || '') as TabName
  return validTabs.includes(tab) ? tab : 'three'
}

const activeTab = ref<TabName>(routeTab(route.query.tab))
const loading = ref(true)
const error = ref('')
const diagnoses = ref<ListResponse<Diagnosis>>({ total: 0, items: [] })
const states = ref<ListResponse<KnowledgeState>>({ total: 0, items: [] })
const attempts = ref<ListResponse<AttemptSummary>>({ total: 0, items: [] })
const knowledgePoints = ref<KnowledgePoint[]>([])
const plan = ref<StudyPlanResponse>({ plan: null, tasks: [], total_minutes: 0 })
const config = ref<Record<string, any>>({})

const diagnosisBusy = ref(false)
const diagnosisNotice = ref('')
const diagnosisOutcome = ref<'idle' | 'running' | 'success' | 'partial' | 'empty' | 'failed'>('idle')
const planBusy = ref(false)
const planNotice = ref('')
const examDate = ref('')
const dailyMinutes = ref(60)

const technicalOpen = ref(false)
const technicalLoading = ref(false)
const technicalLoaded = ref(false)
const runs = ref<ListResponse<Record<string, any>>>({ total: 0, items: [] })
const jobs = ref<ListResponse<Record<string, any>>>({ total: 0, items: [] })
const events = ref<{ events: Record<string, any>[] }>({ events: [] })

const chartEl = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

const kpMap = computed(() => new Map(knowledgePoints.value.map((item) => [item.kp_id, item])))

function kpName(kpId: string) {
  return kpMap.value.get(kpId)?.name || '未命名考点'
}

function kpChapter(kpId: string) {
  return kpMap.value.get(kpId)?.chapter || '课程考点'
}

const stateRank: Record<string, number> = {
  '需加强': 0,
  '证据不足': 1,
  '未测': 2,
  '初步通过': 3,
  '阶段稳定': 4,
}

const weakStates = computed(() => states.value.items
  .filter((item) => ['需加强', '证据不足'].includes(item.state))
  .sort((a, b) => {
    const byState = (stateRank[a.state] ?? 9) - (stateRank[b.state] ?? 9)
    return byState || a.evidence_count - b.evidence_count
  }))

const topWeakStates = computed(() => weakStates.value.slice(0, 3))
const latestPaperDiagnosis = computed(() => diagnoses.value.items.find((item) => item.level === 'paper'))
const latestHistoryDiagnosis = computed(() => diagnoses.value.items.find((item) => item.level === 'history'))
const submittedAttempts = computed(() => attempts.value.items.filter((item) => item.status === 'submitted'))
const latestSubmittedAttempt = computed(() => submittedAttempts.value[0])
const pendingPlanTasks = computed(() => plan.value.tasks.filter((item) => item.status !== 'completed' && item.status !== 'done'))
const completedPlanTasks = computed(() => plan.value.tasks.filter((item) => item.status === 'completed' || item.status === 'done'))

const trendItems = computed(() => submittedAttempts.value
  .filter((item) => Number(item.max_score) > 0 && item.score !== null)
  .slice()
  .sort((a, b) => new Date(a.submitted_at || a.started_at).getTime() - new Date(b.submitted_at || b.started_at).getTime()))

function stateLabel(value: string) {
  const labels: Record<string, string> = {
    untested: '未测', insufficient: '证据不足', weak: '需加强',
    passed: '初步通过', stable: '阶段稳定',
  }
  return labels[value] || value || '未测'
}

function stateTagType(value: string): TagType {
  const label = stateLabel(value)
  if (label === '需加强') return 'danger'
  if (label === '证据不足') return 'warning'
  if (label === '初步通过' || label === '阶段稳定') return 'success'
  return 'info'
}

function diagnosisStatusLabel(value?: string) {
  const labels: Record<string, string> = {
    not_requested: '尚未生成', pending: '等待生成', queued: '等待生成',
    running: '生成中', succeeded: '已完成', failed: '生成失败',
  }
  return labels[value || ''] || '尚未生成'
}

function diagnosisStatusType(value?: string): TagType {
  if (value === 'succeeded') return 'success'
  if (value === 'failed') return 'danger'
  if (value === 'pending' || value === 'queued' || value === 'running') return 'warning'
  return 'info'
}

function planStatusLabel(value: string) {
  return value === 'completed' || value === 'done' ? '已完成' : '待完成'
}

function planKindLabel(value: string) {
  const labels: Record<string, string> = {
    retest: '复测巩固', correction: '错题订正', review: '考点复习', practice: '专项练习',
  }
  return labels[value] || '学习任务'
}

function formatDate(value?: string, withTime = false) {
  if (!value) return '未记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', withTime
    ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }
    : { year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
}

function scoreText(attempt?: AttemptSummary) {
  if (!attempt || attempt.score === null || !attempt.max_score) return '暂无成绩'
  return `${attempt.score} / ${attempt.max_score}`
}

function practiceFor(kpId: string) {
  router.push({ path: '/compose', query: { tab: 'create', kp: kpId } })
}

async function refreshPrimary() {
  const [d, s, a, k, p, c] = await Promise.all([
    api.get<ListResponse<Diagnosis>>('/diagnoses'),
    api.get<ListResponse<KnowledgeState>>('/knowledge-states'),
    api.get<ListResponse<AttemptSummary>>('/attempts'),
    api.get<ListResponse<KnowledgePoint>>('/knowledge-points'),
    api.get<StudyPlanResponse>('/study-plan'),
    api.get<Record<string, any>>('/config/status'),
  ])
  diagnoses.value = d
  states.value = s
  attempts.value = a
  knowledgePoints.value = k.items || []
  plan.value = p
  config.value = c
  examDate.value = p.plan?.exam_date || c.project_rules?.exam_date || examDate.value
  dailyMinutes.value = p.plan?.daily_minutes || dailyMinutes.value
}

async function runDiagnosis() {
  diagnosisBusy.value = true
  diagnosisOutcome.value = 'running'
  diagnosisNotice.value = '正在整理成绩与作答证据，请稍候……'
  try {
    const result = await api.post<Record<string, any>>('/diagnoses/run', { limit: 3 })
    if (result.status === 'no_submitted_attempt') {
      diagnosisOutcome.value = 'empty'
      diagnosisNotice.value = '还没有已交卷的练习。完成并提交一次练习后，就能生成学情分析。'
    } else if (result.status === 'partial_failure') {
      diagnosisOutcome.value = 'partial'
      diagnosisNotice.value = '成绩已保留，部分学情分析暂时未生成。你可以稍后重试。'
    } else {
      diagnosisOutcome.value = 'success'
      diagnosisNotice.value = '学情分析已更新。成绩与错题记录不受分析过程影响。'
    }
    await refreshPrimary()
  } catch (err: any) {
    diagnosisOutcome.value = 'failed'
    diagnosisNotice.value = `学情分析暂时未生成，但成绩与答卷已经安全保存。${err?.message ? ` 原因：${err.message}` : ''}`
  } finally {
    diagnosisBusy.value = false
  }
}

async function generatePlan() {
  if (!examDate.value) {
    ElMessage.warning('请先选择考试日期')
    return
  }
  planBusy.value = true
  planNotice.value = '正在根据薄弱考点和到期任务安排计划……'
  try {
    const result = await api.post<Record<string, any>>('/study-plan/generate', {
      exam_date: examDate.value,
      daily_minutes: dailyMinutes.value,
    })
    if (result.status && result.status !== 'succeeded') {
      planNotice.value = '计划暂时未能完整生成，请稍后重试。现有学习记录不会丢失。'
    } else {
      planNotice.value = result.message || '今日学习计划已更新。'
      ElMessage.success('学习计划已更新')
    }
    plan.value = await api.get<StudyPlanResponse>('/study-plan')
  } catch (err: any) {
    planNotice.value = `计划生成失败：${err?.message || String(err)}`
  } finally {
    planBusy.value = false
  }
}

async function setPlanTaskStatus(task: PlanTask, status: 'completed' | 'pending') {
  try {
    await api.patch(`/plan-tasks/${task.task_id}`, { status })
    task.status = status
    ElMessage.success(status === 'completed' ? '已标记完成' : '已恢复为待完成')
    plan.value = await api.get<StudyPlanResponse>('/study-plan')
  } catch (err: any) {
    ElMessage.error(err?.message || '任务状态更新失败')
  }
}

async function loadTechnicalDetails() {
  technicalOpen.value = !technicalOpen.value
  if (!technicalOpen.value || technicalLoaded.value || technicalLoading.value) return
  technicalLoading.value = true
  try {
    const [r, j, e] = await Promise.all([
      api.get<ListResponse<Record<string, any>>>('/skill-runs', { limit: 20 }),
      api.get<ListResponse<Record<string, any>>>('/jobs', { limit: 20 }),
      api.get<{ events: Record<string, any>[] }>('/learning-records', { limit: 40 }),
    ])
    runs.value = r
    jobs.value = j
    events.value = e
    technicalLoaded.value = true
  } catch (err: any) {
    ElMessage.error(err?.message || '技术详情加载失败')
  } finally {
    technicalLoading.value = false
  }
}

function renderTrendChart() {
  if (activeTab.value !== 'trend' || !chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  if (!trendItems.value.length) {
    chart.clear()
    return
  }
  chart.setOption({
    animationDuration: 400,
    grid: { left: 52, right: 24, top: 34, bottom: 52 },
    tooltip: {
      trigger: 'axis',
      formatter(params: any) {
        const point = Array.isArray(params) ? params[0] : params
        return `${point.axisValue}<br/>得分率：${point.value}%`
      },
    },
    xAxis: {
      type: 'category',
      name: '提交日期',
      data: trendItems.value.map((item) => formatDate(item.submitted_at || item.started_at)),
      axisLabel: { color: '#475569', rotate: trendItems.value.length > 6 ? 28 : 0 },
      axisLine: { lineStyle: { color: '#cbd5e1' } },
    },
    yAxis: {
      type: 'value', min: 0, max: 100, name: '得分率（%）',
      axisLabel: { color: '#475569' },
      splitLine: { lineStyle: { color: '#eef2f7' } },
    },
    series: [{
      name: '得分率',
      type: 'line',
      smooth: true,
      symbolSize: 8,
      lineStyle: { color: '#4f46e5', width: 3 },
      itemStyle: { color: '#4f46e5' },
      areaStyle: { color: 'rgba(79, 70, 229, 0.10)' },
      data: trendItems.value.map((item) => Math.round((Number(item.score) / Number(item.max_score)) * 100)),
    }],
  }, true)
  chart.resize()
}

function handleResize() {
  chart?.resize()
}

watch(() => route.query.tab, (value) => {
  const next = routeTab(value)
  if (next !== activeTab.value) activeTab.value = next
})

watch(activeTab, async (tab) => {
  if (route.query.tab !== tab) {
    await router.replace({ query: { ...route.query, tab } })
  }
  if (tab === 'trend') await nextTick(renderTrendChart)
})

watch(trendItems, () => nextTick(renderTrendChart))

onMounted(async () => {
  window.addEventListener('resize', handleResize)
  try {
    await refreshPrimary()
    if (activeTab.value === 'trend') await nextTick(renderTrendChart)
  } catch (err: any) {
    error.value = err?.message || String(err)
  } finally {
    loading.value = false
  }
  // 深链：?graph=1 直接跑一次 LangGraph 工作流，方便演示与截图
  if (String(route.query.graph || '') === '1') {
    void runLearningSkill()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})
/* ---------------- V3.0 学习闭环 Skill：诊断→推荐→出题→复测 ---------------- */
const skillBusy = ref(false)
const skillOpen = ref(false)
const skillResult = ref<any>(null)

async function runLearningSkill() {
  skillBusy.value = true
  try {
    // 走 LangGraph 版本，返回里带节点执行轨迹
    const out: any = await api.post('/skills/learning_diagnosis/graph', {})
    skillResult.value = out
    skillOpen.value = true
  } catch (error) {
    ElMessage.error(friendlyError(error, '学习诊断运行失败'))
  } finally {
    skillBusy.value = false
  }
}

const NODE_LABELS: Record<string, string> = {
  diagnose_paper: '试卷诊断',
  three_layers: '三层认知',
  recommend_resources: '资源推荐',
  generate_questions: '新题生成',
  verify_learning: '复测验证',
}

function nodeLabel(node: string) {
  return NODE_LABELS[node] || node
}

function traceStatus(summary: Record<string, any>) {
  const status = String(summary?.status || 'done')
  if (status === 'error' || status === 'failed') return '异常'
  if (status === 'skipped' || status === 'no_data') return '跳过'
  return '完成'
}

function layerPercent(value: number | null | undefined) {
  if (value === null || value === undefined) return 0
  return Math.round(value * 100)
}

</script>

<template>
  <main v-loading="loading" class="reports-page">
    <header class="page-head reports-head">
      <div>
        <p class="eyebrow">学习证据中心</p>
        <h2>学情诊断</h2>
        <p>题目层看错在哪，试卷层看这张卷覆盖了什么，历次层看同一个考点几次作答的变化。
          所有结论都能回到真实作答与原卷页。</p>
      </div>
      <div class="head-actions">
        <el-button size="large" :loading="skillBusy" @click="runLearningSkill">
          {{ skillBusy ? '正在运行' : '一键诊断与推荐' }}
        </el-button>
        <el-button type="primary" size="large" :loading="diagnosisBusy" @click="runDiagnosis">
          {{ diagnosisBusy ? '正在分析' : '更新学情分析' }}
        </el-button>
      </div>
    </header>

    <div v-if="error" class="hint error-hint" role="alert">{{ error }}</div>
    <div
      v-if="diagnosisNotice"
      class="diagnosis-notice"
      :class="`is-${diagnosisOutcome}`"
      aria-live="polite"
    >
      <strong>{{ diagnosisOutcome === 'running' ? '分析进行中' : '分析状态' }}</strong>
      <span>{{ diagnosisNotice }}</span>
    </div>

    <el-dialog v-model="skillOpen" title="智能学习诊断" width="min(760px, 94vw)"
      class="skill-dialog">
      <div v-if="skillResult?.layers" class="skill-result">
        <p class="skill-lead">
          按「诊断 → 三层认知 → 推荐 → 出题 → 复测」跑完一轮；每步结果都来自真实作答记录。
        </p>
        <div class="layer-list">
          <div v-for="layer in skillResult.layers?.layers || []"
            :key="layer.key" class="layer-row">
            <div class="layer-head">
              <strong>{{ layer.name }}</strong>
              <span class="layer-status" :class="`ls-${layer.status}`">
                {{ layer.status }}<template v-if="layer.accuracy !== null">
                  · {{ layerPercent(layer.accuracy) }}%</template>
              </span>
            </div>
            <div class="layer-bar">
              <span :style="{ width: `${layerPercent(layer.accuracy)}%` }" />
            </div>
            <small>{{ layer.meaning }}</small>
          </div>
        </div>
        <div v-if="skillResult.trace?.length" class="graph-trace">
          <p class="trace-title">
            LangGraph 工作流
            <span class="trace-engine">{{ skillResult.graph?.engine || 'langgraph' }}</span>
          </p>
          <div class="trace-list">
            <div v-for="(step, index) in skillResult.trace" :key="index" class="trace-row">
              <span class="trace-index">{{ index + 1 }}</span>
              <span class="trace-node">{{ nodeLabel(step.node) }}</span>
              <span class="trace-status" :class="`ts-${step.summary?.status || 'done'}`">
                {{ traceStatus(step.summary) }}
              </span>
            </div>
          </div>
        </div>

        <div class="skill-facts">
          <span>推荐资源 {{ skillResult.recommendations?.recommendations?.length || 0 }} 条</span>
          <span>薄弱知识点 {{ skillResult.diagnosis?.weak_points?.length || 0 }} 个</span>
          <span>新题 {{ skillResult.generated?.skipped ? '未生成（默认关闭）' : (skillResult.generated?.candidates || 0) + ' 道' }}</span>
        </div>
        <ul v-if="skillResult.uncertainties?.length" class="skill-notes">
          <li v-for="note in skillResult.uncertainties" :key="note">{{ note }}</li>
        </ul>
      </div>
      <p v-else class="muted">还没有结果。</p>
    </el-dialog>

    <el-tabs v-model="activeTab" class="report-tabs">
      <el-tab-pane label="三层诊断" name="three">
        <ThreeLayerPanel :attempt-id="String(route.query.attempt || '')" />
      </el-tab-pane>

      <el-tab-pane label="学习概览" name="overview">
        <section class="overview-hero" aria-labelledby="weak-heading">
          <div class="section-heading">
            <div>
              <p class="eyebrow">优先处理</p>
              <h3 id="weak-heading">最需要关注的 3 个考点</h3>
            </div>
            <button v-if="weakStates.length > 3" type="button" class="text-action" @click="activeTab = 'weak'">
              查看全部 {{ weakStates.length }} 个
            </button>
          </div>

          <div v-if="topWeakStates.length" class="weak-priority-grid">
            <article v-for="(item, index) in topWeakStates" :key="item.kp_id" class="weak-priority-card">
              <div class="rank" aria-hidden="true">{{ index + 1 }}</div>
              <div class="weak-card-main">
                <div class="weak-card-title">
                  <div>
                    <span class="chapter">{{ kpChapter(item.kp_id) }}</span>
                    <h4>{{ kpName(item.kp_id) }}</h4>
                  </div>
                  <el-tag :type="stateTagType(item.state)" effect="light">{{ stateLabel(item.state) }}</el-tag>
                </div>
                <p>{{ item.explanation || `已有 ${item.evidence_count} 条作答证据，建议通过新题继续巩固。` }}</p>
                <div class="weak-card-footer">
                  <span>{{ item.evidence_count }} 条证据 · {{ item.independent_family_count }} 个独立题族</span>
                  <el-button type="primary" plain @click="practiceFor(item.kp_id)">立即练习</el-button>
                </div>
              </div>
            </article>
          </div>
          <div v-else class="empty-state">
            <div class="empty-icon" aria-hidden="true">✓</div>
            <h4>暂时没有明确的薄弱考点</h4>
            <p>{{ submittedAttempts.length ? '继续完成练习，系统会随着证据增加更新判断。' : '先完成并提交一次练习，系统才能根据真实作答分析学情。' }}</p>
            <el-button type="primary" plain @click="router.push('/compose')">去组卷</el-button>
          </div>
        </section>

        <section class="summary-grid" aria-label="学习概览">
          <article class="summary-card">
            <span class="summary-label">已完成练习</span>
            <strong>{{ submittedAttempts.length }}</strong>
            <span>次有效提交</span>
          </article>
          <article class="summary-card">
            <span class="summary-label">有证据的考点</span>
            <strong>{{ states.total }}</strong>
            <span>个考点已被观察</span>
          </article>
          <article class="summary-card">
            <span class="summary-label">最近成绩</span>
            <strong class="score-value">{{ scoreText(latestSubmittedAttempt) }}</strong>
            <span v-if="latestSubmittedAttempt">{{ formatDate(latestSubmittedAttempt.submitted_at, true) }}</span>
            <span v-else>完成练习后显示</span>
          </article>
          <article class="summary-card">
            <span class="summary-label">最近诊断</span>
            <el-tag :type="diagnosisStatusType(latestSubmittedAttempt?.diagnosis_status)" effect="light">
              {{ diagnosisStatusLabel(latestSubmittedAttempt?.diagnosis_status) }}
            </el-tag>
            <span>失败不影响成绩</span>
          </article>
        </section>

        <section class="card readable-card">
          <div class="section-heading compact">
            <div>
              <h3>本次分析建议</h3>
              <p v-if="latestPaperDiagnosis" class="section-subtitle">更新于 {{ formatDate(latestPaperDiagnosis.created_at, true) }}</p>
            </div>
          </div>
          <div v-if="latestPaperDiagnosis">
            <div class="diagnosis-score" v-if="latestPaperDiagnosis.payload?.max_score">
              <span>最近一次分析成绩</span>
              <strong>{{ latestPaperDiagnosis.payload.score }} / {{ latestPaperDiagnosis.payload.max_score }}</strong>
            </div>
            <ul v-if="latestPaperDiagnosis.next_actions?.length" class="action-list">
              <li v-for="(action, index) in latestPaperDiagnosis.next_actions" :key="index">{{ action }}</li>
            </ul>
            <p v-else class="muted">继续完成练习后，这里会给出更具体的下一步建议。</p>
          </div>
          <div v-else class="inline-empty">
            <span>尚无学情分析。</span>
            <span>完成练习后点击“更新学情分析”即可生成。</span>
          </div>
        </section>
      </el-tab-pane>

      <el-tab-pane :label="`薄弱考点 ${weakStates.length ? `(${weakStates.length})` : ''}`" name="weak">
        <section class="card">
          <div class="section-heading compact">
            <div>
              <h3>薄弱考点</h3>
              <p class="section-subtitle">“证据不足”表示还需要更多独立作答，不等于不会。</p>
            </div>
          </div>
          <div v-if="weakStates.length" class="state-list">
            <article v-for="item in weakStates" :key="item.kp_id" class="state-row">
              <div class="state-main">
                <span class="chapter">{{ kpChapter(item.kp_id) }}</span>
                <h4>{{ kpName(item.kp_id) }}</h4>
                <p>{{ item.explanation || '建议通过不同题型的新题补充独立证据。' }}</p>
              </div>
              <div class="state-metrics" aria-label="考点证据">
                <span><strong>{{ item.evidence_count }}</strong> 条证据</span>
                <span><strong>{{ item.independent_family_count }}</strong> 个题族</span>
                <span><strong>{{ item.time_span_days }}</strong> 天跨度</span>
              </div>
              <el-tag :type="stateTagType(item.state)" effect="light">{{ stateLabel(item.state) }}</el-tag>
              <el-button type="primary" plain @click="practiceFor(item.kp_id)">立即练习</el-button>
            </article>
          </div>
          <div v-else class="empty-state compact-empty">
            <h4>暂无薄弱考点</h4>
            <p>完成更多练习后，这里会按证据自动更新。</p>
          </div>
        </section>

        <section class="card">
          <h3>全部考点状态</h3>
          <div class="state-chip-list">
            <div v-for="item in states.items" :key="item.kp_id" class="state-chip-row">
              <span>{{ kpName(item.kp_id) }}</span>
              <el-tag size="small" :type="stateTagType(item.state)" effect="plain">{{ stateLabel(item.state) }}</el-tag>
            </div>
            <p v-if="!states.items.length" class="muted">还没有可展示的知识状态。</p>
          </div>
        </section>
      </el-tab-pane>

      <el-tab-pane label="学习趋势" name="trend">
        <section class="card trend-card">
          <div class="section-heading compact">
            <div>
              <h3>历次练习得分率</h3>
              <p class="section-subtitle">不同试卷难度不同，折线只用于观察，不直接等同于能力变化。</p>
            </div>
          </div>
          <div v-if="trendItems.length" ref="chartEl" class="trend-chart" role="img" aria-label="历次练习得分率折线图" />
          <div v-else class="empty-state compact-empty">
            <h4>暂无趋势数据</h4>
            <p>至少提交一次有分数的练习后，趋势图才会出现。</p>
          </div>
        </section>

        <section class="card">
          <h3>最近练习记录</h3>
          <div v-if="submittedAttempts.length" class="attempt-list">
            <article v-for="(item, index) in submittedAttempts.slice(0, 10)" :key="item.attempt_id" class="attempt-row">
              <span class="attempt-index">{{ index + 1 }}</span>
              <div>
                <strong>{{ formatDate(item.submitted_at || item.started_at, true) }}</strong>
                <p>{{ item.manual_pending_count ? `另有 ${item.manual_pending_count} 题等待核查` : '评分已完成' }}</p>
              </div>
              <strong class="attempt-score">{{ scoreText(item) }}</strong>
              <el-tag :type="diagnosisStatusType(item.diagnosis_status)" effect="plain">
                分析{{ diagnosisStatusLabel(item.diagnosis_status) }}
              </el-tag>
            </article>
          </div>
          <p v-else class="muted">暂无已提交的练习记录。</p>
          <p v-if="latestHistoryDiagnosis?.payload?.rule" class="evidence-note">
            证据说明：{{ latestHistoryDiagnosis.payload.rule }}
          </p>
        </section>
      </el-tab-pane>

      <el-tab-pane label="学习计划" name="plan">
        <section class="plan-layout">
          <div class="card plan-settings">
            <div class="section-heading compact">
              <div>
                <h3>安排今日学习</h3>
                <p class="section-subtitle">优先级：到期复测 → 错题订正 → 薄弱考点。</p>
              </div>
            </div>
            <el-form label-position="top">
              <div class="plan-form-row">
                <el-form-item label="考试日期">
                  <el-date-picker v-model="examDate" type="date" value-format="YYYY-MM-DD" :clearable="false" />
                </el-form-item>
                <el-form-item label="每日可用时间">
                  <el-input-number v-model="dailyMinutes" :min="10" :max="600" :step="10" />
                  <span class="unit">分钟</span>
                </el-form-item>
              </div>
              <el-button type="primary" size="large" :loading="planBusy" @click="generatePlan">
                {{ plan.plan ? '更新今日计划' : '生成今日计划' }}
              </el-button>
            </el-form>
            <p v-if="planNotice" class="plan-notice" aria-live="polite">{{ planNotice }}</p>
          </div>

          <aside class="plan-summary">
            <span class="summary-label">今日待办</span>
            <strong>{{ pendingPlanTasks.length }} 项</strong>
            <span>预计 {{ plan.total_minutes || 0 }} 分钟</span>
            <div v-if="plan.plan" class="plan-meta">
              <span>每日预算 {{ plan.plan.daily_minutes }} 分钟</span>
              <span>考试日期 {{ formatDate(plan.plan.exam_date) }}</span>
            </div>
          </aside>
        </section>

        <section class="card plan-tasks-card">
          <div class="section-heading compact">
            <div>
              <h3>今日任务</h3>
              <p class="section-subtitle">每项任务都有明确的完成标准。</p>
            </div>
            <span class="completion-count">已完成 {{ completedPlanTasks.length }} / {{ plan.tasks.length }}</span>
          </div>
          <div v-if="pendingPlanTasks.length" class="plan-task-list">
            <article v-for="task in pendingPlanTasks" :key="task.task_id" class="plan-task-row">
              <div class="task-time">{{ task.minutes }}<small>分钟</small></div>
              <div class="task-content">
                <div class="tag-line">
                  <el-tag size="small" effect="plain">{{ planKindLabel(task.kind) }}</el-tag>
                  <span v-if="task.due_date" class="muted small">{{ formatDate(task.due_date) }}</span>
                </div>
                <h4>{{ task.title || kpName(task.kp_id) }}</h4>
                <p v-if="task.completion_criteria">完成标准：{{ task.completion_criteria }}</p>
                <p v-if="task.rationale" class="task-reason">安排原因：{{ task.rationale }}</p>
              </div>
              <div class="task-actions">
                <el-button v-if="task.kp_id" text type="primary" @click="practiceFor(task.kp_id)">去练习</el-button>
                <el-button plain @click="setPlanTaskStatus(task, 'completed')">标记完成</el-button>
              </div>
            </article>
          </div>
          <div v-else class="empty-state compact-empty">
            <h4>{{ plan.plan ? '今日任务已全部完成' : '还没有学习计划' }}</h4>
            <p>{{ plan.plan ? '做得不错，可以去练习中心自由巩固。' : '设置时间后生成计划，系统会自动安排优先级。' }}</p>
          </div>

          <details v-if="completedPlanTasks.length" class="completed-tasks">
            <summary>已完成任务（{{ completedPlanTasks.length }}）</summary>
            <article v-for="task in completedPlanTasks" :key="task.task_id" class="completed-task-row">
              <span>✓</span>
              <div>
                <strong>{{ task.title }}</strong>
                <p>{{ task.minutes }} 分钟 · {{ planStatusLabel(task.status) }}</p>
              </div>
              <el-button text @click="setPlanTaskStatus(task, 'pending')">恢复</el-button>
            </article>
          </details>
        </section>
      </el-tab-pane>
    </el-tabs>

    <section class="technical-shell">
      <button type="button" class="technical-toggle" :aria-expanded="technicalOpen" @click="loadTechnicalDetails">
        <span>
          <strong>技术详情</strong>
          <small>用于课程答辩与问题排查，日常学习无需查看</small>
        </span>
        <span aria-hidden="true">{{ technicalOpen ? '收起' : '展开' }}</span>
      </button>
      <div v-if="technicalOpen" v-loading="technicalLoading" class="technical-content">
        <details>
          <summary>原始诊断记录（{{ diagnoses.total }}）</summary>
          <div v-if="diagnoses.items.length" class="technical-records">
            <details v-for="item in diagnoses.items" :key="item.diagnosis_id">
              <summary>{{ item.level }} · {{ formatDate(item.created_at, true) }}</summary>
              <pre>{{ JSON.stringify(item, null, 2) }}</pre>
            </details>
          </div>
          <p v-else class="muted">暂无诊断记录。</p>
        </details>

        <details>
          <summary>运行与后台任务（{{ runs.total + jobs.total }}）</summary>
          <pre>{{ JSON.stringify({ runs: runs.items, jobs: jobs.items }, null, 2) }}</pre>
        </details>

        <details>
          <summary>学习事件（{{ events.events.length }}）</summary>
          <pre>{{ JSON.stringify(events.events, null, 2) }}</pre>
        </details>

        <details>
          <summary>项目判定规则</summary>
          <pre>{{ JSON.stringify(config.project_rules || {}, null, 2) }}</pre>
        </details>
      </div>
    </section>
  </main>
</template>

<style scoped>
.reports-page { --report-accent: #4f46e5; color: #172033; }
.reports-head { align-items: center; }
.reports-head h2 { font-size: 30px; letter-spacing: -.02em; }
.reports-head p:not(.eyebrow) { font-size: 14px; }
.eyebrow { margin: 0 0 5px; color: var(--report-accent); font-size: 14px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
.report-tabs :deep(.el-tabs__header) { margin: 0 0 18px; }
.report-tabs :deep(.el-tabs__item) { height: 46px; padding: 0 22px; font-size: 15px; }
.report-tabs :deep(.el-tabs__item.is-active) { color: var(--report-accent); font-weight: 700; }
.report-tabs :deep(.el-tabs__active-bar) { background: var(--report-accent); height: 3px; border-radius: 3px; }
.diagnosis-notice { display: flex; gap: 10px; align-items: center; margin-bottom: 16px; padding: 11px 14px; border: 1px solid #dbe4f0; border-radius: 10px; background: #f8fafc; color: #334155; font-size: 14px; }
.diagnosis-notice.is-running, .diagnosis-notice.is-partial { border-color: #f3d7a5; background: #fffbeb; color: #7c4a10; }
.diagnosis-notice.is-success { border-color: #bbecd8; background: #f0fdf8; color: #166044; }
.diagnosis-notice.is-failed { border-color: #fecaca; background: #fff7f7; color: #991b1b; }
.error-hint { background: #fff7f7; border-color: #fecaca; color: #991b1b; }
.overview-hero { margin-bottom: 16px; padding: 22px; border: 1px solid #dedcfb; border-radius: 16px; background: linear-gradient(135deg, #fff 20%, #f5f3ff 100%); box-shadow: 0 10px 30px rgba(79, 70, 229, .06); }
.section-heading { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 16px; }
.section-heading.compact { margin-bottom: 14px; }
.section-heading h3 { margin: 0; font-size: 18px; }
.section-subtitle { margin: 4px 0 0; color: #64748b; font-size: 14px; line-height: 1.6; }
.text-action { min-height: 44px; border: 0; background: transparent; color: var(--report-accent); font: inherit; font-weight: 600; cursor: pointer; }
.text-action:focus-visible, .technical-toggle:focus-visible { outline: 3px solid rgba(79, 70, 229, .24); outline-offset: 2px; }
.weak-priority-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.weak-priority-card { display: flex; min-width: 0; padding: 16px; border: 1px solid #e5e7eb; border-radius: 13px; background: #fff; }
.rank { flex: none; display: grid; place-items: center; width: 30px; height: 30px; margin-right: 12px; border-radius: 9px; background: #eef2ff; color: #4338ca; font-weight: 800; }
.weak-card-main { display: flex; flex: 1; min-width: 0; flex-direction: column; }
.weak-card-title { display: flex; justify-content: space-between; gap: 8px; align-items: flex-start; }
.chapter { display: block; margin-bottom: 3px; color: #64748b; font-size: 14px; }
.weak-card-title h4, .state-main h4, .plan-task-row h4 { margin: 0; font-size: 15px; line-height: 1.45; }
.weak-priority-card p { flex: 1; margin: 10px 0 14px; color: #526072; font-size: 14px; line-height: 1.65; }
.weak-card-footer { display: flex; justify-content: space-between; gap: 8px; align-items: center; color: #64748b; font-size: 14px; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.summary-card { display: flex; min-height: 118px; padding: 16px; flex-direction: column; justify-content: center; gap: 5px; border: 1px solid #e4e8ef; border-radius: 12px; background: #fff; }
.summary-card strong { font-size: 27px; color: #111827; }
.summary-card .score-value { font-size: 22px; }
.summary-card > span:last-child { color: #64748b; font-size: 14px; }
.summary-label { color: #475569; font-size: 14px; font-weight: 600; }
.readable-card { max-width: none; }
.diagnosis-score { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.diagnosis-score span { color: #64748b; font-size: 14px; }
.diagnosis-score strong { font-size: 22px; }
.action-list { display: grid; gap: 8px; margin: 0; padding-left: 22px; color: #334155; line-height: 1.65; }
.inline-empty { display: flex; flex-direction: column; gap: 5px; color: #64748b; font-size: 14px; }
.empty-state { padding: 26px 16px; text-align: center; color: #64748b; }
.empty-state.compact-empty { padding: 32px 16px; }
.empty-state h4 { margin: 6px 0; color: #334155; font-size: 16px; }
.empty-state p { margin: 0 0 12px; font-size: 14px; }
.empty-icon { display: grid; place-items: center; width: 42px; height: 42px; margin: 0 auto 8px; border-radius: 50%; background: #dcfce7; color: #15803d; font-size: 20px; font-weight: 800; }
.state-list { display: grid; gap: 2px; }
.state-row { display: grid; grid-template-columns: minmax(220px, 1.5fr) minmax(210px, 1fr) auto auto; gap: 16px; align-items: center; padding: 15px 4px; border-top: 1px solid #edf0f4; }
.state-row:first-child { border-top: 0; }
.state-main p { margin: 4px 0 0; color: #64748b; font-size: 14px; line-height: 1.55; }
.state-metrics { display: flex; gap: 16px; color: #64748b; font-size: 14px; }
.state-metrics span { display: flex; flex-direction: column; }
.state-metrics strong { color: #1f2937; font-size: 17px; }
.state-chip-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.state-chip-row { display: flex; justify-content: space-between; gap: 8px; align-items: center; min-width: 0; padding: 9px 11px; border-radius: 9px; background: #f8fafc; font-size: 14px; }
.state-chip-row > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trend-chart { width: 100%; height: 360px; }
.attempt-list { display: grid; }
.attempt-row { display: grid; grid-template-columns: 34px 1fr auto auto; gap: 12px; align-items: center; padding: 13px 4px; border-top: 1px solid #edf0f4; }
.attempt-row:first-child { border-top: 0; }
.attempt-index { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 8px; background: #f1f5f9; color: #64748b; font-size: 14px; font-weight: 700; }
.attempt-row p { margin: 3px 0 0; color: #64748b; font-size: 14px; }
.attempt-score { font-size: 16px; }
.evidence-note { margin: 14px 0 0; padding: 10px 12px; border-radius: 8px; background: #f8fafc; color: #64748b; font-size: 14px; line-height: 1.6; }
.plan-layout { display: grid; grid-template-columns: minmax(0, 1fr) 260px; gap: 14px; }
.plan-settings { margin-bottom: 14px; }
.plan-form-row { display: grid; grid-template-columns: repeat(2, minmax(0, 250px)); gap: 16px; }
.plan-form-row :deep(.el-date-editor) { width: 100%; }
.unit { margin-left: 8px; color: #64748b; font-size: 14px; }
.plan-notice { margin: 12px 0 0; color: #475569; font-size: 14px; line-height: 1.6; }
.plan-summary { display: flex; min-height: 180px; padding: 22px; flex-direction: column; justify-content: center; border-radius: 14px; background: #312e81; color: #fff; }
.plan-summary .summary-label { color: #c7d2fe; }
.plan-summary > strong { margin: 4px 0; font-size: 32px; }
.plan-summary > span:not(.summary-label) { color: #e0e7ff; font-size: 14px; }
.plan-meta { display: flex; margin-top: 18px; padding-top: 14px; flex-direction: column; gap: 5px; border-top: 1px solid rgba(255,255,255,.18); color: #c7d2fe; font-size: 14px; }
.completion-count { color: #64748b; font-size: 14px; }
.plan-task-list { display: grid; }
.plan-task-row { display: grid; grid-template-columns: 64px minmax(0, 1fr) auto; gap: 15px; align-items: center; padding: 16px 2px; border-top: 1px solid #edf0f4; }
.plan-task-row:first-child { border-top: 0; }
.task-time { display: flex; height: 54px; flex-direction: column; align-items: center; justify-content: center; border-radius: 10px; background: #eef2ff; color: #4338ca; font-size: 20px; font-weight: 800; }
.task-time small { font-size: 14px; font-weight: 600; }
.task-content h4 { margin-top: 6px; }
.task-content p { margin: 5px 0 0; color: #526072; font-size: 14px; line-height: 1.55; }
.task-content .task-reason { color: #7a8699; font-size: 14px; }
.task-actions { display: flex; align-items: center; }
.completed-tasks { margin-top: 12px; padding-top: 12px; border-top: 1px solid #edf0f4; }
.completed-tasks summary { min-height: 44px; color: #526072; cursor: pointer; font-size: 14px; font-weight: 600; line-height: 44px; }
.completed-task-row { display: grid; grid-template-columns: 24px 1fr auto; gap: 8px; align-items: center; padding: 9px 8px; color: #64748b; }
.completed-task-row strong { color: #475569; font-size: 14px; text-decoration: line-through; }
.completed-task-row p { margin: 2px 0 0; font-size: 14px; }
.technical-shell { margin-top: 18px; overflow: hidden; border: 1px solid #e1e6ee; border-radius: 12px; background: #fff; }
.technical-toggle { display: flex; width: 100%; min-height: 64px; padding: 12px 16px; align-items: center; justify-content: space-between; border: 0; background: #fff; color: #334155; text-align: left; cursor: pointer; }
.technical-toggle > span:first-child { display: flex; flex-direction: column; gap: 3px; }
.technical-toggle small { color: #8490a2; font-size: 14px; font-weight: 400; }
.technical-content { padding: 0 16px 16px; border-top: 1px solid #edf0f4; }
.technical-content > details { padding: 10px 0; border-bottom: 1px solid #edf0f4; }
.technical-content summary { min-height: 36px; color: #475569; cursor: pointer; font-size: 14px; font-weight: 600; line-height: 36px; }
.technical-content pre { max-height: 380px; overflow: auto; padding: 12px; border-radius: 8px; background: #0f172a; color: #dbeafe; font-size: 14px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.technical-records { padding-left: 12px; }

@media (max-width: 1100px) {
  .weak-priority-grid { grid-template-columns: 1fr; }
  .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .state-row { grid-template-columns: minmax(220px, 1fr) auto auto; }
  .state-metrics { grid-column: 1 / -1; grid-row: 2; }
  .state-chip-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 700px) {
  .reports-head { align-items: stretch; }
  .reports-head :deep(.el-button) { width: 100%; min-height: 44px; }
  .reports-head h2 { font-size: 25px; }
  .report-tabs :deep(.el-tabs__nav-wrap) { overflow-x: auto; }
  .report-tabs :deep(.el-tabs__item) { padding: 0 15px; }
  .overview-hero { padding: 16px; }
  .section-heading { align-items: center; }
  .weak-card-footer { align-items: flex-start; flex-direction: column; }
  .weak-card-footer :deep(.el-button) { width: 100%; min-height: 44px; }
  .summary-grid { grid-template-columns: 1fr 1fr; gap: 8px; }
  .summary-card { min-height: 104px; padding: 13px; }
  .summary-card strong { font-size: 23px; }
  .state-row { grid-template-columns: 1fr auto; gap: 10px; }
  .state-row > :deep(.el-button) { grid-column: 1 / -1; width: 100%; min-height: 44px; }
  .state-metrics { grid-column: 1 / -1; flex-wrap: wrap; }
  .state-chip-list { grid-template-columns: 1fr; }
  .trend-chart { height: 300px; }
  .attempt-row { grid-template-columns: 32px 1fr auto; }
  .attempt-row :deep(.el-tag) { grid-column: 2 / -1; justify-self: start; }
  .plan-layout { grid-template-columns: 1fr; }
  .plan-summary { min-height: 150px; }
  .plan-form-row { grid-template-columns: 1fr; gap: 0; }
  .plan-settings :deep(.el-button--primary) { width: 100%; min-height: 44px; }
  .plan-task-row { grid-template-columns: 54px 1fr; align-items: start; }
  .task-actions { grid-column: 1 / -1; justify-content: flex-end; }
  .task-actions :deep(.el-button) { min-height: 44px; }
  .technical-toggle { min-height: 72px; }
}
.head-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.skill-lead { margin: 0 0 14px; color: #697181; font-size: 14px; line-height: 1.7; }
.layer-list { display: flex; flex-direction: column; gap: 12px; }
.layer-head { display: flex; align-items: center; justify-content: space-between; font-size: 14px; }
.layer-status { color: #6b7280; font-size: 14px; }
.layer-status.ls-掌握良好 { color: #047857; }
.layer-status.ls-需要重点提升 { color: #b91c1c; }
.layer-bar { height: 8px; margin: 6px 0 4px; border-radius: 999px; background: #eef0f4; overflow: hidden; }
.layer-bar span { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #6366f1, #818cf8); }
.layer-row small { color: #8b93a3; font-size: 14px; }
.skill-facts { display: flex; flex-wrap: wrap; gap: 8px 18px; margin-top: 16px; padding-top: 12px; border-top: 1px solid #eef0f4; color: #4b5563; font-size: 14px; }
.graph-trace { margin-top: 16px; padding-top: 12px; border-top: 1px solid #eef0f4; }
.trace-title { display: flex; align-items: center; gap: 8px; margin: 0 0 8px; color: #4338ca; font-size: 14px; font-weight: 600; }
.trace-engine { padding: 1px 8px; border-radius: 999px; background: #eef2ff; color: #4338ca; font-size: 14px; font-weight: 500; }
.trace-list { display: flex; flex-direction: column; gap: 6px; }
.trace-row { display: flex; align-items: center; gap: 10px; font-size: 14px; color: #4b5563; }
.trace-index { display: grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; background: #eef2ff; color: #4338ca; font-size: 14px; }
.trace-node { flex: 1; }
.trace-status { padding: 1px 8px; border-radius: 999px; background: #ecfdf5; color: #047857; font-size: 14px; }
.trace-status.ts-skipped, .trace-status.ts-no_data { background: #f4f6fb; color: #6b7280; }
.trace-status.ts-error, .trace-status.ts-failed { background: #fef2f2; color: #b91c1c; }
.skill-notes { margin: 12px 0 0; padding-left: 18px; color: #8b93a3; font-size: 14px; line-height: 1.7; }

</style>
