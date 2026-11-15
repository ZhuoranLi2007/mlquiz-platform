<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Aim, Calendar, CircleCheck, Clock, Collection, DataAnalysis,
  Document, Reading, RefreshRight, Tickets, VideoPlay,
} from '@element-plus/icons-vue'
import { api } from '../api/client'
import { auth } from '../auth'
import { friendlyError, noTaskDescription } from '../utils/presentation'
import { averageCompleteScoreRate, effectiveRecordScore } from '../utils/studentTaskOverview'

const router = useRouter()
const loading = ref(true)
const loadError = ref('')
const assignments = ref<any[]>([])
const feedback = ref<any>({ exam_records: [] })
const starting = ref('')
const taskType = ref('all')
const taskSearch = ref('')
const showAllCompleted = ref(false)
const showAllRecords = ref(false)

const pending = computed(() => assignments.value
  .filter(item => item.status === 'assigned' || item.status === 'opened')
  .sort((a, b) => String(b.published_at || '').localeCompare(String(a.published_at || ''))))
const visiblePending = computed(() => pending.value.filter(item =>
  (taskType.value === 'all' || item.release_type === taskType.value)
  && `${taskTitle(item)} ${item.instructions || ''}`.toLowerCase().includes(taskSearch.value.trim().toLowerCase())))
const completed = computed(() => assignments.value.filter(item => item.status === 'submitted')
  .sort((a, b) => String(b.submitted_at || '').localeCompare(String(a.submitted_at || ''))))
const visibleCompleted = computed(() => showAllCompleted.value ? completed.value : completed.value.slice(0, 3))
const unavailable = computed(() => assignments.value.filter(item => item.status === 'expired'))
const examRecords = computed<any[]>(() => feedback.value.exam_records || [])
const visibleRecords = computed(() => showAllRecords.value ? examRecords.value : examRecords.value.slice(0, 6))
const average = computed(() => averageCompleteScoreRate(examRecords.value))
const latestRetest = computed(() => assignments.value.filter(item => item.release_type === 'retest')
  .sort((a, b) => String(b.submitted_at || b.published_at || '').localeCompare(String(a.submitted_at || a.published_at || '')))[0] || null)
const testIdentity = computed(() => auth.principal.value?.identity_scope === 'test')

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [tasks, reports] = await Promise.all([
      api.get('/student/assignments'), api.get('/student/feedback'),
    ]) as any[]
    assignments.value = tasks.items || []
    feedback.value = reports
  } catch (error) {
    loadError.value = friendlyError(error, '学生端数据加载失败')
    ElMessage.error(loadError.value)
  } finally { loading.value = false }
}

async function openAssignment(item: any) {
  if (item.status === 'submitted') {
    if (item.attempt_id) await router.push(`/exam/${item.attempt_id}?recipient=${item.recipient_id}`)
    return
  }
  if (!['assigned', 'opened'].includes(item.status)) return
  starting.value = item.recipient_id
  try {
    const attempt: any = await api.post(`/student/assignments/${item.recipient_id}/start`)
    await router.push(`/exam/${attempt.attempt_id}?recipient=${item.recipient_id}`)
  } catch (error) { ElMessage.error(friendlyError(error, '无法开始答题')) }
  finally { starting.value = '' }
}

async function openExamRecord(item: any) {
  if (item.attempt_id) await router.push(`/exam/${item.attempt_id}`)
}

function taskTitle(item: any) {
  const releaseTitle = String(item.title || '').trim()
  const paperTitle = String(item.paper_title || '').trim()
  if (item.release_type === 'retest' && releaseTitle === '针对性复测' && item.source_attempt_id) {
    const source = examRecords.value.find(record => record.attempt_id === item.source_attempt_id)
    if (source?.title) return `${source.title} · 复测`
  }
  if (!releaseTitle || ['课堂测验', '针对性复测'].includes(releaseTitle)) return paperTitle || releaseTitle || '未命名任务'
  return releaseTitle
}
function taskDescription(item: any) {
  const description = String(item.instructions || '').trim()
  if (description && description !== taskTitle(item) && description !== String(item.paper_title || '').trim())
    return description
  return item.release_type === 'retest'
    ? '完成针对性练习，查看本轮复测结果。'
    : '完成本次课堂测验并提交答卷。'
}
function taskMinutes(item: any) {
  const minutes = Number(item.estimated_minutes)
  return Number.isFinite(minutes) && minutes > 0 ? Math.max(1, Math.round(minutes)) : null
}
function dateTime(value: unknown) {
  if (!value) return ''
  const date = new Date(String(value))
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date).replace(/\//g, '-')
}
function scoreText(score: unknown, max: unknown) {
  return score === null || score === undefined || max === null || max === undefined
    ? '暂无成绩' : `${score} / ${max}`
}
function recordScoreText(item: any) {
  if (item.absent) return '缺考'
  const result = effectiveRecordScore(item)
  if (result) return `${result.score} / ${result.max}${result.complete ? '' : ' · 部分题目'}`
  if (item.source_type === 'historical_import' && item.reported_score !== null && item.reported_score !== undefined)
    return `原始记录 ${item.reported_score}`
  return '暂无成绩'
}
function recordTime(item: any) {
  if (item.source_type === 'historical_import') {
    const code = String(item.quiz_code || '').trim()
    return /^\d+(?:\.\d+)?$/.test(code) ? `小测 ${code}` : '历史小测'
  }
  return dateTime(item.submitted_at || item.scheduled_at) || '时间未记录'
}
function recordChapters(item: any) {
  const names = (item.chapters || []).map((row: any) => String(row.title || '').trim()).filter(Boolean)
  return names.length ? `${names.slice(0, 2).join('、')}${names.length > 2 ? ` 等${names.length}章` : ''}` : '—'
}
function chapterNames(item: any): string[] {
  return (item.chapters || []).map((row: any) => String(row.title || '').trim()).filter(Boolean)
}
function recordStatus(item: any) {
  if (item.absent) return { label: '缺考', tone: 'absent' }
  const scored = effectiveRecordScore(item)
  if (scored) return scored.complete
    ? { label: '已完成', tone: 'complete' }
    : { label: '部分评分', tone: 'partial' }
  if (item.source_type === 'historical_import' && item.reported_score != null)
    return { label: '原始记录', tone: 'recorded' }
  return { label: '未评分', tone: 'ungraded' }
}
function retestText(item: any) {
  if (!item) return '暂无记录'
  if (item.status !== 'submitted') return item.status === 'opened' ? '进行中' : '待完成'
  const status = item.targeted_retest?.status
  if (status === 'passed') return '已达标'
  if (status === 'not_passed') return '未达标'
  return scoreText(item.score, item.max_score)
}
function scrollToRecords() { document.getElementById('exam-records')?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="student-tasks-page">
    <el-alert v-if="testIdentity" type="warning" :closable="false" title="当前为旧测试账号，不属于正式班。" />
    <el-alert v-if="loadError" type="error" :closable="false">
      <template #title>任务加载失败：{{ loadError }}</template>
      <el-button size="small" @click="load">重新加载</el-button>
    </el-alert>

    <section class="overview-grid" aria-label="任务概况">
      <div class="overview-card tone-blue"><span class="stat-icon"><el-icon><Tickets /></el-icon></span><div class="stat-copy"><span>待完成</span><strong>{{ pending.length }}<small>项</small></strong></div></div>
      <div class="overview-card tone-green"><span class="stat-icon"><el-icon><CircleCheck /></el-icon></span><div class="stat-copy"><span>已提交</span><strong>{{ completed.length }}<small>项</small></strong></div></div>
      <div class="overview-card tone-purple"><span class="stat-icon"><el-icon><Document /></el-icon></span><div class="stat-copy"><span>考试记录</span><strong>{{ examRecords.length }}<small>次</small></strong></div></div>
      <div class="overview-card tone-orange"><span class="stat-icon"><el-icon><DataAnalysis /></el-icon></span><div class="stat-copy"><span>平均得分率</span><strong>{{ average.rate === null ? '—' : `${Math.round(average.rate * 100)}%` }}</strong><small>{{ average.count ? `${average.count} 次完整评分` : '暂无完整评分' }}</small></div></div>
      <div class="overview-card tone-cyan retest-stat"><span class="stat-icon"><el-icon><Aim /></el-icon></span><div class="stat-copy"><span>最近复测</span><strong>{{ retestText(latestRetest) }}</strong><small v-if="latestRetest">{{ taskTitle(latestRetest) }}</small><small v-else>暂无复测任务</small></div></div>
    </section>

    <section class="pending-section" aria-labelledby="pending-heading">
      <div class="section-heading">
        <div class="heading-copy"><h2 id="pending-heading">待完成任务</h2><span>共 {{ pending.length }} 项</span></div>
        <div class="task-tools">
          <el-select v-model="taskType" aria-label="筛选任务类型" class="type-filter">
            <el-option label="全部类型" value="all" /><el-option label="课堂测验" value="exam" /><el-option label="针对性复测" value="retest" />
          </el-select>
          <el-input v-model="taskSearch" aria-label="搜索任务标题" clearable placeholder="搜索任务标题" class="task-search" />
          <el-button :icon="RefreshRight" :loading="loading" @click="load">刷新</el-button>
        </div>
      </div>
      <div v-if="visiblePending.length" class="task-grid">
        <article v-for="item in visiblePending" :key="item.recipient_id" class="task-card" :class="item.release_type === 'retest' ? 'retest-card' : 'exam-card'">
          <div class="task-card-top"><span class="task-kind" :class="item.release_type === 'retest' ? 'retest' : 'exam'"><el-icon><component :is="item.release_type === 'retest' ? Aim : Reading" /></el-icon>{{ item.release_type === 'retest' ? '针对性复测' : '课堂测验' }}</span><span class="status-pill" :class="item.status === 'opened' ? 'in-progress' : 'new'">{{ item.status === 'opened' ? '进行中' : '新任务' }}</span></div>
          <h3>{{ taskTitle(item) }}</h3>
          <p class="task-description">{{ taskDescription(item) }}</p>
          <div class="task-card-bottom">
            <div class="task-facts"><span><el-icon><Calendar /></el-icon>{{ item.due_at ? `截止 ${dateTime(item.due_at) || '时间未记录'}` : '不限截止时间' }}</span><span v-if="item.item_count"><el-icon><Collection /></el-icon>共 {{ item.item_count }} 题</span><span v-if="taskMinutes(item)"><el-icon><Clock /></el-icon>预计约 {{ taskMinutes(item) }} 分钟</span></div>
            <el-button type="primary" :icon="VideoPlay" :loading="starting === item.recipient_id" @click="openAssignment(item)">{{ item.status === 'opened' ? '继续答题' : '开始答题' }}</el-button>
          </div>
        </article>
      </div>
      <div v-else-if="!loading && !loadError" class="empty-state">{{ pending.length ? '没有符合筛选条件的任务' : noTaskDescription(auth.principal.value) }}</div>
    </section>

    <div class="records-layout">
      <section class="records-panel recent-panel" aria-labelledby="recent-heading">
        <div class="panel-heading"><div class="heading-copy"><el-icon class="panel-icon"><Document /></el-icon><h2 id="recent-heading">最近提交</h2><span>共 {{ completed.length }} 项</span></div><button type="button" class="text-action" @click="completed.length > 3 ? showAllCompleted = !showAllCompleted : scrollToRecords()">{{ completed.length > 3 ? (showAllCompleted ? '收起' : '查看更多') : '查看考试记录' }}</button></div>
        <div v-if="completed.length" class="recent-list">
          <article v-for="item in visibleCompleted" :key="item.recipient_id" class="recent-row">
            <div class="recent-main"><strong>{{ taskTitle(item) }}</strong><small><el-icon><Calendar /></el-icon>{{ dateTime(item.submitted_at) || '提交时间未记录' }}<span class="recent-type">{{ item.release_type === 'retest' ? '针对性复测' : '在线测验' }}</span></small><small v-if="item.release_type === 'retest' && item.targeted_retest">{{ retestText(item) }}</small></div>
            <b>{{ scoreText(item.score, item.max_score) }}</b>
            <el-button plain type="primary" :disabled="!item.attempt_id" @click="openAssignment(item)">查看答卷</el-button>
          </article>
        </div>
        <div v-else-if="!loading" class="empty-state compact">暂无已提交任务</div>
      </section>

      <section id="exam-records" class="records-panel history-panel" aria-labelledby="history-heading">
        <div class="panel-heading"><div class="heading-copy"><el-icon class="panel-icon"><Collection /></el-icon><h2 id="history-heading">考试记录</h2><span>共 {{ examRecords.length }} 次</span></div><button v-if="examRecords.length > 6" type="button" class="text-action" @click="showAllRecords = !showAllRecords">{{ showAllRecords ? '收起' : '查看全部' }}</button></div>
        <div v-if="examRecords.length" class="history-table-wrap"><table class="history-table"><thead><tr><th>试卷标题</th><th>考试时间 / 顺序</th><th>所属章节</th><th>得分</th><th>状态</th><th>操作</th></tr></thead><tbody>
          <tr v-for="item in visibleRecords" :key="item.record_id"><td data-label="试卷标题"><span class="record-title">{{ item.title }}</span><small v-if="item.source_type === 'online_retest'" class="record-type">复测</small></td><td data-label="考试时间 / 顺序">{{ recordTime(item) }}</td><td data-label="所属章节" class="chapter-cell" :title="recordChapters(item)"><template v-if="chapterNames(item).length"><span v-for="(name,index) in chapterNames(item).slice(0,2)" :key="index" class="chapter-chip">{{ name }}</span><small v-if="chapterNames(item).length > 2">+{{ chapterNames(item).length - 2 }}</small></template><span v-else>—</span></td><td data-label="得分" class="score-cell">{{ recordScoreText(item) }}</td><td data-label="状态"><span class="record-status" :class="recordStatus(item).tone">{{ recordStatus(item).label }}</span></td><td data-label="操作"><el-button v-if="item.attempt_id" plain type="primary" @click="openExamRecord(item)">查看答卷</el-button><span v-else class="no-action">—</span></td></tr>
        </tbody></table></div>
        <div v-else-if="!loading" class="empty-state compact">暂无考试记录</div>
      </section>
    </div>

    <details v-if="unavailable.length" class="expired-tasks"><summary>已失效任务（{{ unavailable.length }}）</summary><div v-for="item in unavailable" :key="item.recipient_id">{{ taskTitle(item) }}<span>{{ item.due_at ? `截止 ${dateTime(item.due_at)}` : '已失效' }}</span></div></details>
  </div>
</template>

<style scoped src="../styles/studentPortal.css"></style>
