<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  Aim, Check, CircleCheck, CircleClose, Clock, Collection,
  DataAnalysis, Reading, RefreshRight, Trophy, View,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRoute } from 'vue-router'
import { api } from '../api/client'
import AttemptEvidenceTable from '../components/AttemptEvidenceTable.vue'
import DiagnosisAnalysisContent from '../components/DiagnosisAnalysisContent.vue'
import LearningResourceList from '../components/LearningResourceList.vue'
import { friendlyError } from '../utils/presentation'
import { recommendedResourceLinks } from '../utils/resourceLinks'

const loading = ref(true)
const route = useRoute()
const detailLoading = ref(false)
const loadError = ref('')
const attempts = ref<any[]>([])
const selectedAttemptId = ref('')
const detail = ref<any>(null)

const score = computed(() => detail.value?.score_summary || {})
const comparison = computed(() => detail.value?.class_comparison || {})
const retests = computed(() => detail.value?.retest_history || [])
const latestRetest = computed(() => retests.value.at(-1) || null)
const sideResources = computed(() => recommendedResourceLinks(detail.value?.resources || []))
const scoreRate = computed(() => {
  if (score.value.score_rate !== null && score.value.score_rate !== undefined) return Number(score.value.score_rate)
  const max = Number(score.value.max_score || 0)
  return max > 0 ? Number(score.value.awarded_score || 0) / max : null
})
const rankText = computed(() => comparison.value.rank
  ? `第${comparison.value.rank}名 / ${comparison.value.participant_count}人`
  : '暂无排名')
const retestState = computed(() => {
  const row = latestRetest.value
  if (!row) return { label: '尚未复测', tone: 'empty', note: '等待教师布置复测' }
  if (row.status === 'passed') return { label: '已达标', tone: 'passed', note: `第${row.round}轮 · ${percent(row.accuracy)}` }
  if (row.status === 'not_passed') return { label: '未达标', tone: 'failed', note: `第${row.round}轮 · ${percent(row.accuracy)}` }
  return { label: '待完成', tone: 'pending', note: `第${row.round}轮复测` }
})

function numberText(value: unknown) {
  if (value === null || value === undefined) return '—'
  const number = Number(value)
  return Number.isInteger(number) ? String(number) : number.toFixed(1)
}
function percent(value: unknown) {
  return value === null || value === undefined ? '—' : `${Math.round(Number(value) * 100)}%`
}
function dateText(value: string) {
  if (!value) return ''
  return value.slice(0, 10)
}
function attemptLabel(row: any) {
  const code = String(row.quiz_code || '')
  const prefix = code && !/^(paper|attempt|release)_[\w-]+$/i.test(code) ? `${code} · ` : ''
  const date = dateText(row.scheduled_at || row.submitted_at)
  const scoreLabel = row.score !== null && row.score !== undefined && row.max_score
    ? `${numberText(row.score)}/${numberText(row.max_score)}${row.ungraded_item_count
        ? `（${row.scored_item_count}/${Number(row.scored_item_count || 0) + Number(row.ungraded_item_count)}题）`
        : ''}` : '暂无成绩'
  return `${prefix}${row.title}${date ? ` · ${date}` : ''} · ${scoreLabel}`
}
function roundStatus(row: any) {
  if (row.status === 'passed') return { label: '已达标', tone: 'passed' }
  if (row.status === 'not_passed') return { label: '未达标', tone: 'failed' }
  return { label: '待完成', tone: 'pending' }
}
function roundConclusion(row: any) {
  if (row.status === 'passed') return '本轮复测已达标。'
  if (row.status === 'not_passed') return '本轮复测未达标，建议继续练习。'
  return '本轮复测等待完成。'
}

async function loadDetail() {
  if (!selectedAttemptId.value) { detail.value = null; return }
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await api.get(`/student/feedback/${selectedAttemptId.value}`)
  } catch (error) {
    ElMessage.error(friendlyError(error, '反馈详情加载失败'))
  } finally { detailLoading.value = false }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const result: any = await api.get('/student/feedback')
    attempts.value = result.attempts || []
    const requestedAttempt = String(route.query.attempt || '')
    if (attempts.value.some(row => row.attempt_id === requestedAttempt)) {
      selectedAttemptId.value = requestedAttempt
    }
    if (!attempts.value.some(row => row.attempt_id === selectedAttemptId.value)) {
      selectedAttemptId.value = attempts.value[0]?.attempt_id || ''
    }
    await loadDetail()
  } catch (error) {
    loadError.value = friendlyError(error, '学习反馈加载失败')
  } finally { loading.value = false }
}

async function scrollToRetests() {
  await nextTick()
  document.getElementById('targeted-retests')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="student-feedback-page">
    <section class="selector-card">
      <el-select v-model="selectedAttemptId" filterable placeholder="选择已完成试卷"
        aria-label="选择试卷" @change="loadDetail">
        <el-option v-for="row in attempts" :key="row.attempt_id"
          :label="attemptLabel(row)" :value="row.attempt_id" />
      </el-select>
      <el-button type="primary" :icon="View" :loading="detailLoading" :disabled="!selectedAttemptId" @click="loadDetail">查看反馈</el-button>
      <el-button :icon="Clock" :disabled="!detail" @click="scrollToRetests">查看复测记录</el-button>
    </section>

    <el-alert v-if="loadError" type="error" :closable="false" show-icon>
      <template #title>{{ loadError }}</template>
      <el-button size="small" @click="load">重新加载</el-button>
    </el-alert>
    <el-empty v-else-if="!loading && !attempts.length" description="完成一份可评分试卷后即可查看学习反馈" />

    <template v-if="detail">
      <section class="metric-grid">
        <article class="metric-card score-card"><span class="metric-icon"><el-icon><Trophy /></el-icon></span><div><small>本次成绩</small><b>{{ numberText(score.awarded_score) }}<em>/ {{ numberText(score.max_score) }}</em></b><p>得分率 {{ percent(scoreRate) }}</p></div></article>
        <article class="metric-card rank-card"><span class="metric-icon"><el-icon><DataAnalysis /></el-icon></span><div><small>班级排名</small><b>{{ rankText }}</b><p>{{ comparison.rank ? '同卷成绩对比' : '本卷暂无可比排名' }}</p></div></article>
        <article class="metric-card wrong-card"><span class="metric-icon"><el-icon><CircleClose /></el-icon></span><div><small>错题数量</small><b>{{ score.wrong_items ?? 0 }}<em>/ {{ score.scored_items ?? 0 }} 题</em></b><p>已评分题目</p></div></article>
        <article class="metric-card retest-card" :class="retestState.tone"><span class="metric-icon"><el-icon><RefreshRight /></el-icon></span><div><small>复测状态</small><b>{{ retestState.label }}</b><p>{{ retestState.note }}</p></div></article>
      </section>

      <section class="feedback-layout">
        <main class="feedback-main" v-loading="detailLoading">
          <AttemptEvidenceTable :result="detail" />
          <AttemptEvidenceTable :result="detail" section="correct" />

          <section id="targeted-retests" class="retest-panel">
            <header class="section-heading"><div><i></i><h2>针对性复测</h2></div><span>达标线 {{ percent(detail.retest_threshold) }}</span></header>
            <div class="retest-progress">
              <span>原卷 <b>{{ percent(scoreRate) }}</b></span>
              <template v-for="row in retests" :key="row.round"><i>→</i><span :class="roundStatus(row).tone">第{{ row.round }}轮 <b>{{ row.accuracy === null || row.accuracy === undefined ? '待完成' : percent(row.accuracy) }}</b></span></template>
            </div>
            <div v-if="!retests.length" class="retest-empty"><el-icon><Clock /></el-icon><div><b>尚未安排针对性复测</b></div></div>
            <div v-else class="round-list">
              <details v-for="(row,index) in retests" :key="row.round" :open="index === retests.length - 1" class="round-card">
                <summary><span>第 {{ row.round }} 轮复测</span><b :class="roundStatus(row).tone">{{ row.accuracy === null || row.accuracy === undefined ? '待完成' : `${row.correct_count}/${row.question_count} · ${percent(row.accuracy)} · ${roundStatus(row).label}` }}</b></summary>
                <div class="round-body">
                  <p :class="['round-conclusion', roundStatus(row).tone]">{{ roundConclusion(row) }}</p>
                  <AttemptEvidenceTable v-if="row.wrong_items?.length" :result="{ items: row.wrong_items }" heading="本轮复测错题" />
                  <div v-else-if="row.status === 'passed'" class="round-clean"><el-icon><CircleCheck /></el-icon>本轮题目全部答对。</div>
                </div>
              </details>
            </div>
          </section>
        </main>

        <aside class="analysis-panel">
          <header><span><el-icon><DataAnalysis /></el-icon></span><div><h2>学情分析</h2></div></header>
          <DiagnosisAnalysisContent :analysis="detail.analysis" scope="student_feedback" />
          <section class="side-section"><h3><el-icon><Collection /></el-icon>推荐学习资源</h3><LearningResourceList :resources="sideResources" /></section>
          <section class="side-section retest-side"><h3><el-icon><RefreshRight /></el-icon>针对性复测</h3><div :class="['retest-result', retestState.tone]"><el-icon><Check v-if="retestState.tone === 'passed'" /><Clock v-else /></el-icon><div><b>{{ retestState.label }}</b><span>{{ latestRetest ? `第${latestRetest.round}轮 · ${latestRetest.accuracy === null || latestRetest.accuracy === undefined ? '等待完成' : percent(latestRetest.accuracy)}` : '等待教师布置复测' }}</span></div></div></section>
        </aside>
      </section>
    </template>
  </div>
</template>

<style scoped>
.student-feedback-page{display:grid;min-width:0;gap:14px;color:#34445f}.selector-card{display:flex;align-items:center;gap:12px;padding:12px 14px;border:1px solid #dce5f2;border-radius:13px;background:#fff;box-shadow:0 4px 16px rgb(32 65 115 / 5%)}.selector-card :deep(.el-select){min-width:0;flex:1}.selector-card :deep(.el-select__wrapper){min-height:39px}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric-card{display:flex;min-width:0;align-items:center;gap:12px;padding:15px 16px;border:1px solid #dfe7f2;border-radius:13px;background:#fff;box-shadow:0 4px 16px rgb(32 65 115 / 5%)}.metric-icon{display:grid;flex:none;width:43px;height:43px;place-items:center;border-radius:50%;background:#eaf1ff;color:#315cf0;font-size:21px}.metric-card>div{min-width:0}.metric-card small,.metric-card b,.metric-card p{display:block}.metric-card small{color:#53647c;font-size:14px;font-weight:700}.metric-card b{margin-top:5px;overflow:hidden;color:#17243d;font-size:22px;text-overflow:ellipsis;white-space:nowrap}.metric-card em{margin-left:3px;color:#718096;font-size:14px;font-style:normal}.metric-card p{margin:4px 0 0;color:#8390a2;font-size:14px}.wrong-card{background:#fff7f7}.wrong-card .metric-icon{background:#ffe7e9;color:#e34a55}.retest-card{background:#fffaf2}.retest-card .metric-icon{background:#fff0d9;color:#d78920}.retest-card.passed{background:#f1fbf5}.retest-card.passed .metric-icon{background:#dcf5e7;color:#18935f}.feedback-layout{display:grid;grid-template-columns:minmax(0,7fr) minmax(300px,3fr);align-items:start;gap:16px}.feedback-main{display:grid;min-width:0;gap:14px}.analysis-panel{position:sticky;top:91px;overflow:hidden;border:1px solid #dbe5f3;border-radius:14px;background:#fff;box-shadow:0 6px 20px rgb(37 67 108 / 7%)}.analysis-panel>header{display:flex;align-items:center;gap:11px;padding:15px 16px;border-bottom:1px solid #e8edf4}.analysis-panel>header>span{display:grid;width:36px;height:34px;place-items:center;border-radius:9px;background:#315cf0;color:#fff;font-size:19px}.analysis-panel h2{margin:0;color:#17243d;font-size:18px}.analysis-panel header small{display:inline-block;margin-top:3px;padding:2px 6px;border-radius:999px;background:#edf3ff;color:#315fc0;font-size:14px}.analysis-summary{margin:12px 14px 0;padding:11px 12px;border:1px solid #d8e4fb;border-radius:9px;background:#f6f9ff}.analysis-summary h3,.side-section h3{margin:0;color:#223650;font-size:14px}.analysis-summary p{margin:6px 0 0;color:#43546c;font-size:14px;line-height:1.7}.side-section{margin:0 14px;padding:14px 0;border-bottom:1px solid #e8edf4}.side-section:last-child{border-bottom:0}.side-section h3{display:flex;align-items:center;gap:6px;margin-bottom:9px}.side-section h3>.el-icon{color:#315cf0}.concept-list{display:grid;gap:7px}.concept-list article{padding:9px 10px;border-left:3px solid #5a7df1;border-radius:7px;background:#f7f9fc}.concept-list b,.concept-list span{display:block}.concept-list b{color:#253850;font-size:14px}.concept-list span{margin-top:3px;color:#4770c5;font-size:14px}.concept-list p{margin:5px 0 0;color:#718097;font-size:14px;line-height:1.55}.advice-list{display:grid;gap:8px;margin:0;padding:0;list-style:none;counter-reset:advice}.advice-list li{position:relative;padding-left:25px;color:#516279;font-size:14px;line-height:1.65;counter-increment:advice}.advice-list li::before{position:absolute;top:0;left:0;display:grid;width:18px;height:18px;place-items:center;border-radius:50%;background:#eaf1ff;color:#315cf0;font-size:14px;font-weight:700;content:counter(advice)}.side-empty{margin:0;color:#8793a3;font-size:14px;line-height:1.55}.resource-list{display:grid;gap:6px}.resource-list a{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 9px;border:1px solid #e4eaf2;border-radius:8px;background:#fafbfe;color:inherit;text-decoration:none}.resource-list a:hover{border-color:#b9caef;background:#f4f7ff}.resource-list a>div{min-width:0}.resource-list b,.resource-list span{display:block}.resource-list b{overflow:hidden;color:#34465f;font-size:14px;text-overflow:ellipsis;white-space:nowrap}.resource-list span{margin-top:3px;color:#8390a1;font-size:14px;line-height:1.45}.resource-list em{flex:none;color:#315cf0;font-size:14px;font-style:normal}.retest-result{display:flex;align-items:center;gap:9px;padding:10px;border-radius:8px;background:#f3f5f8;color:#66758a}.retest-result>.el-icon{font-size:20px}.retest-result b,.retest-result span{display:block}.retest-result b{font-size:14px}.retest-result span{margin-top:2px;font-size:14px}.retest-result.passed{background:#e9f8ef;color:#177b50}.retest-result.failed{background:#fff0f1;color:#bd3c47}.retest-result.pending{background:#fff5e6;color:#9a681f}.retest-panel{scroll-margin-top:94px;padding:15px;border:1px solid #dce5f2;border-radius:14px;background:#fff;box-shadow:0 4px 16px rgb(32 65 115 / 5%)}.section-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.section-heading>div{display:flex;align-items:center;gap:9px}.section-heading i{width:4px;height:24px;border-radius:3px;background:#315cf0}.section-heading h2{margin:0;color:#17243d;font-size:18px}.section-heading>span{color:#718096;font-size:14px}.retest-progress{display:flex;align-items:center;gap:10px;margin-top:13px;overflow-x:auto}.retest-progress>span{flex:none;min-width:125px;padding:8px 13px;border-radius:999px;background:#f1f4f8;color:#52647c;font-size:14px;text-align:center}.retest-progress>span b{margin-left:5px;color:#263950}.retest-progress>span.passed{background:#e6f8ed;color:#18794f}.retest-progress>span.failed{background:#fff0f1;color:#b83b46}.retest-progress>span.pending{background:#fff5e5;color:#93611b}.retest-progress>i{color:#7d8ba0;font-style:normal}.retest-empty{display:flex;align-items:center;gap:12px;margin-top:13px;padding:18px;border:1px dashed #cbd7e7;border-radius:10px;background:#f9fbfe}.retest-empty>.el-icon{color:#6c87bd;font-size:24px}.retest-empty b,.retest-empty span{display:block}.retest-empty b{color:#34475f;font-size:14px}.retest-empty span{margin-top:4px;color:#7d899a;font-size:14px}.round-list{display:grid;gap:10px;margin-top:13px}.round-card{overflow:hidden;border:1px solid #dde5f1;border-radius:10px}.round-card>summary{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 13px;background:#f8faff;color:#34465f;font-size:14px;cursor:pointer}.round-card>summary b{font-size:14px}.round-card>summary b.passed{color:#178054}.round-card>summary b.failed{color:#c23d48}.round-card>summary b.pending{color:#98651d}.round-body{padding:12px}.round-conclusion{margin:0 0 10px;padding:8px 10px;border-radius:7px;background:#fff5e5;color:#8f5f1d;font-size:14px}.round-conclusion.passed{background:#eaf8ef;color:#167a4e}.round-conclusion.failed{background:#fff0f1;color:#b83c47}.round-clean{display:flex;align-items:center;gap:7px;padding:14px;border-radius:8px;background:#edf9f2;color:#16784f;font-size:14px}.round-clean>.el-icon{font-size:18px}@media(max-width:1280px){.feedback-layout{grid-template-columns:minmax(0,1fr) minmax(285px,.65fr)}.metric-card{padding:13px 12px}.metric-icon{width:38px;height:38px}.metric-card b{font-size:18px}}@media(max-width:980px){.feedback-layout{grid-template-columns:1fr}.analysis-panel{position:static}.metric-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:680px){.selector-card{align-items:stretch;flex-direction:column}.selector-card .el-button{width:100%;margin:0}.metric-grid{grid-template-columns:1fr}.metric-card{min-height:78px}.section-heading{align-items:flex-start;flex-direction:column}.retest-progress{align-items:stretch;flex-direction:column}.retest-progress>i{transform:rotate(90deg);text-align:center}.retest-progress>span{width:100%}.round-card>summary{align-items:flex-start;flex-direction:column}}
/* 学生端可读性优化：字号更清晰，首屏层级更紧凑。 */
.student-feedback-page{gap:12px;color:#2f4058}
.selector-card{gap:10px;padding:10px 12px}
.selector-card :deep(.el-select__wrapper){min-height:42px;font-size:14px}
.selector-card :deep(.el-button){min-height:40px;font-size:14px;font-weight:var(--font-weight-semibold)}
.metric-grid{gap:10px}
.metric-card{min-height:88px;gap:11px;padding:13px 14px}
.metric-icon{width:44px;height:44px;font-size:22px}
.metric-card small{font-size:14px;font-weight:var(--font-weight-semibold)}
.metric-card b{margin-top:4px;overflow:visible;font-size:26px;font-weight:var(--font-weight-bold);font-variant-numeric:tabular-nums;text-overflow:clip;white-space:normal;overflow-wrap:anywhere}
.metric-card em{font-size:14px;font-weight:var(--font-weight-normal)}
.metric-card p{margin-top:3px;font-size:14px;line-height:1.35}
.feedback-layout{gap:14px}
.feedback-main{gap:12px}
.analysis-panel>header{gap:12px;padding:14px 15px}
.analysis-panel>header>span{width:38px;height:38px;font-size:21px}
.analysis-panel h2{font-size:var(--font-size-section);font-weight:var(--font-weight-semibold)}
.analysis-panel header small{margin-top:2px;font-size:14px}
.analysis-summary{margin:10px 13px 0;padding:11px 12px}
.analysis-summary h3,.side-section h3{font-size:16px;font-weight:var(--font-weight-semibold)}
.analysis-summary p{margin-top:7px;font-size:14px;line-height:1.65}
.side-section{margin:0 13px;padding:12px 0}
.side-section h3{margin-bottom:9px}
.concept-list{gap:7px}
.concept-list article{padding:9px 10px}
.concept-list b{font-size:14px}
.concept-list span{font-size:14px;font-weight:var(--font-weight-semibold)}
.concept-list p{margin-top:4px;font-size:14px;line-height:1.6}
.advice-list{gap:9px}
.advice-list li{padding-left:28px;font-size:14px;line-height:1.6}
.advice-list li::before{width:20px;height:20px;font-size:14px}
.side-empty{font-size:14px}
.resource-list a{padding:9px 10px}
.resource-list b{font-size:14px}
.resource-list span{font-size:14px}
.resource-list em{font-size:14px;font-weight:var(--font-weight-semibold)}
.retest-result{padding:10px 11px}
.retest-result b{font-size:14px}
.retest-result span{font-size:14px}
.retest-panel{padding:14px}
.section-heading h2{font-size:var(--font-size-section);font-weight:var(--font-weight-semibold)}
.section-heading>span,.retest-progress>span,.retest-empty span,.round-card>summary,.round-conclusion,.round-clean{font-size:14px}
.retest-empty b{font-size:14px}
@media(max-width:1280px){.metric-card b{font-size:22px}.metric-card small{font-size:14px}}
</style>
