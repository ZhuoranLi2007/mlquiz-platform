<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { auth } from '../auth'
import {
  friendlyError, friendlyQuizCode, noTaskDescription,
} from '../utils/presentation'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const loadError = ref('')
const assignments = ref<any[]>([])
const feedback = ref<any>({ attempts: [], chapter_states: [], knowledge_states: [] })
const starting = ref('')
const showFeedback = computed(() => route.path.includes('feedback'))
const pending = computed(() => assignments.value.filter(
  a => a.status === 'assigned' || a.status === 'opened'))
const completed = computed(() => assignments.value.filter(a => a.status === 'submitted'))
const unavailable = computed(() => assignments.value.filter(a => a.status === 'expired'))
const examRecords = computed(() => feedback.value.exam_records || [])
const identity = computed(() => auth.principal.value)
const emptyTaskText = computed(() => noTaskDescription(identity.value))
const testIdentity = computed(() => identity.value?.identity_scope === 'test')

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
  }
  finally { loading.value = false }
}

async function openAssignment(item: any) {
  if (item.status === 'submitted' && item.attempt_id) {
    await router.push(`/exam/${item.attempt_id}?recipient=${item.recipient_id}`)
    return
  }
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

function scoreText(item: any) {
  if (item.absent) return '缺考'
  if (item.source_type === 'historical_import') {
    const reported = item.reported_score ?? '—'
    const regrade = item.regrade
    return regrade
      ? `上报 ${reported}；重评分 ${regrade.awarded_score}/${regrade.max_score}`
      : `上报 ${reported}；暂无可靠重评分`
  }
  return `${item.platform_score ?? '待评分'} / ${item.platform_max_score ?? '—'}`
}

function percent(value: unknown) {
  return value === null || value === undefined ? '—' : `${Math.round(Number(value) * 100)}%`
}
function displayQuizCode(value: unknown) {
  const code = String(value || '').trim()
  return code && !/^(paper|attempt|release)_[\w-]+$/i.test(code) ? code : ''
}
onMounted(load)
</script>

<template>
  <div v-loading="loading" class="student-page">
    <section class="student-hero">
      <div class="student-stats"><div><b>{{ pending.length }}</b><span>待完成</span></div><div><b>{{ completed.length }}</b><span>已提交</span></div><div><b>{{ examRecords.length }}</b><span>考试记录</span></div></div>
      <el-button class="refresh-tasks" :loading="loading" @click="load">刷新任务</el-button>
    </section>

    <template v-if="!showFeedback">
      <el-alert v-if="testIdentity" type="warning" :closable="false" show-icon
        title="当前是隔离的旧测试账号，不属于正式班，也不会收到发给 STU001～STU016 的任务。" />
      <el-alert v-if="loadError" type="error" :closable="false" show-icon>
        <template #title>任务加载失败：{{ loadError }}</template>
        <el-button size="small" @click="load">重新加载</el-button>
      </el-alert>
      <section class="task-section">
        <div class="section-title"><h3>待完成</h3><span>{{ pending.length }} 项</span></div>
        <div v-if="pending.length" class="task-grid"><article v-for="item in pending" :key="item.recipient_id" class="task-card"><div class="task-top"><span :class="['kind',item.release_type]">{{ item.release_type==='retest'?'针对性复测':'课堂测验' }}</span><small>{{ item.status==='opened'?'已开始':'新任务' }}</small></div><h3>{{ item.title }}</h3><p>{{ item.paper_title }}</p><div class="task-meta"><span>{{ item.due_at ? `截止 ${item.due_at.slice(0,10)}` : '不限截止时间' }}</span></div><el-button type="primary" :loading="starting===item.recipient_id" @click="openAssignment(item)">{{ item.status==='opened'?'继续作答':'开始答题' }}</el-button></article></div>
        <el-empty v-else-if="!loadError && !loading" :description="emptyTaskText" />
      </section>
      <section v-if="completed.length" class="task-section"><div class="section-title"><h3>已提交</h3><span>{{ completed.length }} 项</span></div><div class="completed-list"><article v-for="item in completed" :key="item.recipient_id"><div><b>{{ item.title }}</b><small>{{ item.submitted_at?.slice(0,19).replace('T',' ') }}</small></div><strong>{{ item.score ?? '待批改' }}<small v-if="item.max_score"> / {{ item.max_score }}</small></strong><span v-if="item.targeted_retest" :class="['retest-result', item.targeted_retest.status]">第{{ item.targeted_retest.round }}轮 · {{ item.targeted_retest.accuracy === null ? '待完整评分' : `${Math.round(Number(item.targeted_retest.accuracy) * 100)}%` }} · {{ item.targeted_retest.status === 'passed' ? '已达标' : item.targeted_retest.status === 'not_passed' ? '未达标' : '暂不能判断' }}</span><span v-else-if="item.ungraded_item_count">已提交 · {{ item.scored_item_count ? '部分评分' : '待补全评分' }}；未评分 {{ item.ungraded_item_count }} 题 / {{ item.ungraded_max_score }} 分，名义满分 {{ item.nominal_max_score }}</span><el-button text @click="openAssignment(item)">查看答卷</el-button></article></div></section>
      <section v-if="unavailable.length" class="task-section"><div class="section-title"><h3>已失效</h3><span>{{ unavailable.length }} 项</span></div><div class="completed-list"><article v-for="item in unavailable" :key="item.recipient_id"><div><b>{{ item.title }}</b><small>截止 {{ item.due_at?.slice(0,19).replace('T',' ') }}</small></div><span>未在截止时间前提交</span></article></div></section>
      <section class="task-section"><div class="section-title"><h3>考试记录</h3><span>{{ examRecords.length }} 项</span></div><div v-if="examRecords.length" class="exam-records"><article v-for="item in examRecords" :key="item.record_id" :class="{ absent:item.absent }"><div class="record-main"><div><span class="source-tag">{{ item.source_label }}</span><b>{{ displayQuizCode(item.quiz_code) ? `${displayQuizCode(item.quiz_code)} · ` : '' }}{{ item.title }}</b><small>{{ item.scheduled_at || item.submitted_at?.slice(0,10) || '时间未记录' }}</small></div><strong>{{ scoreText(item) }}</strong></div><div class="record-meta"><span v-for="chapter in item.chapters || []" :key="chapter.chapter_id">{{ chapter.title }}</span><span v-if="item.absent" class="absent-tag">缺考</span><span v-else>{{ item.answer_count }} 题</span><el-button v-if="item.attempt_id" link type="primary" @click="openExamRecord(item)">查看答卷</el-button></div></article></div><el-empty v-else-if="!loading" description="暂无考试记录" /></section>
    </template>

    <template v-else>
      <section class="feedback-layout">
        <div class="panel">
          <div class="section-title"><h3>章节学习情况</h3><span>随考试与复测更新</span></div>
          <div v-if="feedback.chapter_states?.length" class="state-list"><article v-for="state in feedback.chapter_states" :key="state.classroom_id + state.chapter_id"><span class="state-dot"></span><div><b>{{ state.title || state.chapter_id }}</b><p>最近得分率 {{ percent(state.latest_score_rate) }}；{{ state.observation }}</p></div><strong>{{ state.comparison_status }}</strong></article></div>
          <el-empty v-else description="完成一份可评分试卷后生成" />
        </div>
        <div class="report-list">
          <article v-for="item in feedback.attempts || []" :key="item.attempt_id" class="panel report-card">
            <div class="report-head"><div><span>{{ friendlyQuizCode(item.quiz_code) || '在线任务' }}</span><h3>{{ item.title }}</h3></div><strong>{{ item.score ?? '—' }}<small> / {{ item.max_score ?? '待批改' }}</small></strong></div>
            <p v-if="item.ungraded_item_count" class="uncertainty">已提交 · {{ item.scored_item_count ? '部分评分' : '待补全评分' }}：{{ item.ungraded_item_count }} 题尚未评分，不按零分计算；整卷名义满分 {{ item.nominal_max_score }} 分。</p>
            <div class="kp-chips"><span v-for="chapter in item.chapters || []" :key="chapter.chapter_id">{{ chapter.title || chapter.chapter_id }} · {{ percent(chapter.score_rate) }}</span></div>
            <div v-if="item.resources?.length" class="resource-list"><a v-for="(resource,index) in item.resources" :key="index" :href="resource.url || '#'" target="_blank" rel="noopener"><b>{{ resource.title }}</b><small>{{ resource.reason || '针对薄弱知识点推荐' }}</small></a></div>
            <div v-if="item.retest_comparison" class="comparison"><b>复测章节对照</b><span>{{ item.retest_comparison.comparable_chapters?.length || 0 }} 个章节有可比证据</span><p>{{ item.retest_comparison.conclusion_limit }}</p></div>
            <p v-for="note in item.uncertainties || []" :key="note" class="uncertainty">{{ note }}</p>
          </article>
          <el-empty v-if="!feedback.attempts?.length" description="暂无已提交答卷" />
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.student-page{display:grid;gap:24px}.student-hero{display:flex;justify-content:space-between;align-items:center;gap:18px;background:linear-gradient(135deg,#fff,#eef7f5);border:1px solid #dce9e6;border-radius:18px;padding:30px 34px}.student-hero h2{font-size:28px;margin:7px 0}.student-hero p{color:#71818d;margin:3px 0}.student-hero p:first-of-type{color:#287f78;font-weight:700}.refresh-tasks{flex:0 0 auto}.eyebrow{font-size:11px;letter-spacing:.15em;color:#287f78;font-weight:800;margin:0}.student-stats{display:flex;gap:12px}.student-stats div{min-width:92px;background:#fff;border:1px solid #dbe6e5;border-radius:12px;padding:14px;text-align:center}.student-stats b,.student-stats span{display:block}.student-stats b{font-size:27px;color:#176d68}.student-stats span{font-size:11px;color:#7d8b95}.section-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:13px}.section-title h3{margin:0}.section-title span{font-size:12px;color:#788a96}.task-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.task-card,.panel{background:#fff;border:1px solid #e0e7eb;border-radius:14px;padding:20px}.task-top,.task-meta{display:flex;justify-content:space-between;align-items:center}.task-top small,.task-meta{color:#82919d;font-size:11px}.kind{font-size:11px;border-radius:20px;padding:4px 8px;background:#eaf2f7;color:#315e79}.kind.retest{background:#fff0d8;color:#8c6120}.task-card h3{margin:20px 0 5px}.task-card>p{color:#71818d;min-height:38px}.task-meta{border-top:1px solid #edf1f3;padding:13px 0}.task-card .el-button{width:100%;margin-top:4px}.completed-list{background:#fff;border:1px solid #e0e7eb;border-radius:14px}.completed-list article{display:grid;grid-template-columns:1fr auto minmax(100px,auto) 100px;align-items:center;gap:20px;padding:15px 20px;border-bottom:1px solid #edf1f3}.completed-list article:last-child{border-bottom:0}.completed-list b,.completed-list small{display:block}.completed-list small{font-size:11px;color:#87949e;margin-top:3px}.completed-list strong{color:#176d68;font-size:20px}.completed-list strong small{display:inline}.retest-result{padding:5px 8px;border-radius:7px;background:#fff0d8;color:#8c6120;font-size:11px}.retest-result.passed{background:#e7f7ee;color:#14794f}.retest-result.not_passed{background:#ffedef;color:#c33b46}.feedback-layout{display:grid;grid-template-columns:340px 1fr;gap:16px;align-items:start}.state-list{display:grid}.state-list article{display:grid;grid-template-columns:12px 1fr auto;gap:10px;align-items:start;padding:13px 0;border-bottom:1px solid #edf1f3}.state-list p{font-size:11px;line-height:1.6;color:#7d8b96;margin:5px 0}.state-list strong{font-size:11px;color:#397872}.state-dot{width:9px;height:9px;margin-top:5px;border-radius:50%;background:#e1ad4b}.state-dot.需加强{background:#d6635a}.state-dot.初步通过{background:#3f9b7a}.report-list{display:grid;gap:13px}.report-head{display:flex;justify-content:space-between;align-items:start}.report-head span{font-size:11px;color:#258078}.report-head h3{margin:5px 0}.report-head strong{font-size:25px;color:#173f5f}.report-head small{font-size:13px;color:#7c8b96}.kp-chips{display:flex;flex-wrap:wrap;gap:6px;margin:13px 0}.kp-chips span{font-size:11px;background:#eff5f5;color:#386d6d;padding:5px 8px;border-radius:20px}.resource-list{display:grid;grid-template-columns:1fr 1fr;gap:8px}.resource-list a{border:1px solid #e2e8ec;border-radius:9px;padding:10px;color:#243a4a;text-decoration:none}.resource-list b,.resource-list small{display:block}.resource-list small{font-size:11px;color:#82909c;margin-top:4px}.comparison{background:#eef7f5;border-radius:9px;padding:12px;margin-top:12px}.comparison b,.comparison span{display:block}.comparison span{font-size:12px;color:#32746f;margin-top:3px}.comparison p,.uncertainty{font-size:11px;line-height:1.6;color:#72828e}.uncertainty{border-left:2px solid #dcb55b;padding-left:9px}
.exam-records{display:grid;gap:9px}.exam-records article{background:#fff;border:1px solid #e0e7eb;border-radius:12px;padding:15px 18px}.exam-records article.absent{background:#fafbfc;color:#778692}.record-main{display:flex;justify-content:space-between;align-items:start;gap:18px}.record-main b,.record-main small{display:block}.record-main b{margin-top:7px}.record-main small{font-size:11px;color:#87949e;margin-top:4px}.record-main strong{color:#176d68;font-size:16px;text-align:right}.source-tag,.record-meta span{display:inline-flex;font-size:10px;border-radius:18px;padding:4px 7px;background:#edf5f5;color:#326e6a}.record-meta{display:flex;align-items:center;flex-wrap:wrap;gap:7px;margin-top:11px}.record-meta .absent-tag{background:#fff1dc;color:#8b5f20}.record-meta .el-button{margin-left:auto}
@media(max-width:960px){.student-hero{align-items:flex-start;flex-wrap:wrap}.task-grid{grid-template-columns:1fr 1fr}.feedback-layout{grid-template-columns:1fr}}@media(max-width:620px){.student-hero{align-items:flex-start;flex-direction:column;gap:20px}.task-grid{grid-template-columns:1fr}.completed-list article{grid-template-columns:1fr auto}.completed-list .el-button{grid-column:1/-1}.resource-list{grid-template-columns:1fr}}
</style>
