<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import AttemptEvidenceTable from '../components/AttemptEvidenceTable.vue'
import LearningResourceList from '../components/LearningResourceList.vue'
import MathText from '../components/MathText.vue'
import WorkbenchPage from '../components/WorkbenchPage.vue'
import { friendlyError } from '../utils/presentation'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const detailLoading = ref(false)
const loadError = ref('')
const attempts = ref<any[]>([])
const selectedAttemptId = ref('')
const detail = ref<any>(null)
const view = ref<'diagnosis' | 'questions'>('diagnosis')
const selectedConceptId = ref('')
const rememberedConcepts = new Map<string, string>()
const showRetests = ref(false)
const opening = ref('')
const questionScroll = ref<HTMLElement>()
const conceptScroll = ref<HTMLElement>()
let requestSequence = 0
const workbench = computed(() => detail.value?.diagnosis_workbench)
const concepts = computed<any[]>(() => workbench.value?.concepts || [])
const current = computed(() => concepts.value.find(c => c.id === selectedConceptId.value) || concepts.value[0])
const score = computed(() => detail.value?.score_summary || {})
const stats = computed(() => workbench.value?.statistics || {})
const retests = computed<any[]>(() => detail.value?.retest_history || [])
const latestRetest = computed(() => retests.value.at(-1))
const wrongItems = computed<any[]>(() => (detail.value?.items || []).filter((r:any) => r.evidence_status === 'scored' && r.correct === false))
const retestState = computed(() => {
  const row = latestRetest.value
  if (!row) return '尚未复测'
  if (row.status === 'passed') return '本轮达标'
  if (row.status === 'not_passed') return '本轮未达标'
  if (row.submitted_at) return '等待评分'
  return '等待作答'
})
function numberText(value: unknown) {
  if (value === null || value === undefined) return '—'
  return Number.isInteger(Number(value)) ? String(value) : Number(value).toFixed(1)
}
function percent(value: unknown) { return value == null ? '—' : `${Math.round(Number(value) * 100)}%` }
function changeText(value: number | null) {
  if (value == null) return '完整评分不足，暂不比较'
  const points = Math.round(value * 100)
  return points === 0 ? '答对比例与原卷相同' : `答对比例${points > 0 ? '增加' : '减少'} ${Math.abs(points)} 个百分点`
}
function attemptLabel(row:any) {
  const date = String(row.scheduled_at || row.submitted_at || '').slice(0, 10)
  return `${row.title}${date ? ` · ${date}` : ''} · ${numberText(row.score)}/${numberText(row.max_score)}`
}
async function loadDetail() {
  const sequence = ++requestSequence
  const attemptId = selectedAttemptId.value
  if (detail.value?.attempt_id) rememberedConcepts.set(detail.value.attempt_id, selectedConceptId.value)
  detail.value = null
  loadError.value = ''
  if (!attemptId) { detailLoading.value = false; return }
  detailLoading.value = true
  try {
    const result:any = await api.get(`/student/feedback/${attemptId}`)
    if (sequence !== requestSequence) return
    detail.value = result
    selectedConceptId.value = rememberedConcepts.get(attemptId) || result.diagnosis_workbench?.concepts?.[0]?.id || ''
  } catch (error) {
    if (sequence === requestSequence) loadError.value = friendlyError(error, '反馈详情加载失败')
  } finally { if (sequence === requestSequence) detailLoading.value = false }
}
async function load() {
  loading.value = true
  try {
    const result:any = await api.get('/student/feedback')
    attempts.value = result.attempts || []
    const requested = String(route.query.attempt || '')
    selectedAttemptId.value = attempts.value.some(r => r.attempt_id === requested) ? requested : attempts.value[0]?.attempt_id || ''
    await loadDetail()
  } catch (error) { loadError.value = friendlyError(error, '学习反馈加载失败') }
  finally { loading.value = false }
}
function selectConcept(id:string) {
  selectedConceptId.value = id
  conceptScroll.value?.scrollTo({ top: 0 })
}
async function locate(position:number) {
  view.value = 'questions'
  await nextTick()
  const card = questionScroll.value?.querySelector<HTMLElement>(`#feedback-question-${position}`)
  card?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  if (card) { card.tabIndex = -1; card.focus({ preventScroll: true }) }
}
async function openRetest(row:any) {
  if (opening.value) return
  opening.value = row.release_id
  try {
    const result:any = await api.get('/student/assignments')
    const task = (result.items || []).find((t:any) => t.release_id === row.release_id)
    if (!task) throw new Error('当前账号未找到此复测任务，请刷新反馈或联系教师。')
    if (task.status === 'submitted' && task.attempt_id) {
      await router.push(`/exam/${task.attempt_id}?recipient=${task.recipient_id}`)
    } else if (['assigned', 'opened'].includes(task.status)) {
      const attempt:any = await api.post(`/student/assignments/${task.recipient_id}/start`)
      await router.push(`/exam/${attempt.attempt_id}?recipient=${task.recipient_id}`)
    } else { ElMessage.warning('此复测任务目前不可作答，请在“我的任务”中查看状态。') }
  } catch (error) { ElMessage.error(friendlyError(error, '复测任务打开失败')) }
  finally { opening.value = '' }
}
onMounted(load)
</script>

<template>
  <WorkbenchPage v-loading="loading || detailLoading" :scroll="false" class="student-feedback-page">
    <template #header>
      <div class="feedback-heading">
        <div><p class="eyebrow">学生端</p><h1>学习反馈</h1></div>
        <div class="workbench-toolbar">
          <el-select v-model="selectedAttemptId" filterable placeholder="选择已完成试卷" aria-label="选择试卷" @change="loadDetail">
            <el-option v-for="row in attempts" :key="row.attempt_id" :label="attemptLabel(row)" :value="row.attempt_id" />
          </el-select>
          <el-button :disabled="!selectedAttemptId" :loading="detailLoading" @click="loadDetail">刷新反馈</el-button>
          <el-button :disabled="!detail" @click="showRetests=true">查看复测记录</el-button>
        </div>
      </div>
      <div v-if="detail" class="workbench-metrics">
        <article class="workbench-metric"><div><small>本次成绩</small><strong>{{ numberText(score.awarded_score) }} <em>/ {{ numberText(score.max_score) }}</em></strong><small>{{ detail.class_comparison?.rank ? `班级第 ${detail.class_comparison.rank} / ${detail.class_comparison.participant_count} 名` : '本卷暂无可比排名' }}</small></div></article>
        <article class="workbench-metric"><div><small>错题数量</small><strong>{{ stats.wrong_count ?? '—' }} <em>/ {{ stats.scored_count ?? '—' }} 已评分题</em></strong><small>{{ stats.ungraded_count ? `${stats.ungraded_count} 题待评分` : '按题去重统计' }}</small></div></article>
        <article class="workbench-metric"><div><small>薄弱知识点</small><strong>{{ workbench?.current_weak_concept_count ?? workbench?.weak_concept_count ?? '—' }}</strong><small>原卷 {{ workbench?.weak_concept_count ?? '—' }} 个 · 结合已测概念更新</small></div></article>
        <article class="workbench-metric"><div><small>复测状态</small><strong class="status-text">{{ retestState }}</strong><small>{{ latestRetest ? `第 ${latestRetest.round} 轮 · ${percent(latestRetest.accuracy)}` : '当前原卷尚未安排复测' }}</small></div></article>
      </div>
      <div class="view-switch" role="tablist" aria-label="反馈视图">
        <button id="diagnosis-tab" type="button" role="tab" aria-controls="diagnosis-view" :aria-selected="view==='diagnosis'" @click="view='diagnosis'">学情诊断</button>
        <button id="questions-tab" type="button" role="tab" aria-controls="questions-view" :aria-selected="view==='questions'" @click="view='questions'">错题详情</button>
        <span v-if="detail">{{ detail.assessment.title }}</span>
      </div>
    </template>
    <el-alert v-if="loadError" type="error" :closable="false" show-icon><template #title>{{ loadError }}</template><el-button @click="load">重新加载</el-button></el-alert>
    <el-empty v-else-if="!loading && !detailLoading && !detail" description="完成一份试卷后即可查看学习反馈" />
    <template v-if="detail">
      <section v-show="view==='diagnosis'" id="diagnosis-view" role="tabpanel" aria-labelledby="diagnosis-tab" class="diagnosis-layout">
        <aside class="overview workbench-scroll" tabindex="0" aria-label="诊断总览与知识点列表">
          <h2>本卷诊断</h2><p>{{ workbench?.summary || '诊断数据暂不可用，请刷新反馈。' }}</p>
          <p class="scope-note">{{ workbench?.scope_note }}</p>
          <div class="concept-list">
            <button v-for="concept in concepts" :key="concept.id" type="button" :class="{ selected: current?.id===concept.id }" :aria-pressed="current?.id===concept.id" @click="selectConcept(concept.id)">
              <b>{{ concept.name }}</b>
              <span>{{ concept.statistics.wrong_count }} 道错题 / {{ concept.statistics.scored_count }} 道已评分题 · 错误比例 {{ percent(concept.statistics.error_rate) }}</span>
              <span>{{ concept.chapter }}</span>
              <span>{{ concept.priority ? `复习顺序 ${concept.priority}` : (concept.statistics.ungraded_count ? '待评分' : '原卷相关题已答对') }} · {{ concept.retest_state }}</span>
            </button>
          </div>
        </aside>
        <article ref="conceptScroll" class="concept-detail workbench-scroll" tabindex="0" aria-label="当前知识点详细诊断">
          <template v-if="current">
            <header><h2>{{ current.name }}</h2><p>{{ current.chapter }} · 涉及原卷第 {{ current.question_positions.join('、') }} 题</p></header>
            <section class="detail-section"><h3>具体错误</h3>
              <div v-if="current.errors.length" class="question-links"><button v-for="item in current.errors" :key="item.position" type="button" @click="locate(item.position)">定位第 {{ item.position }} 题 ↗</button></div>
              <AttemptEvidenceTable v-if="current.errors.length" :result="{items: current.errors}" :id-prefix="`concept-${current.id}`" heading="原卷作答依据" />
              <p v-else>{{ current.statistics.ungraded_count ? '本概念有题目待评分，暂不判断掌握情况。' : '本卷该概念的已评分题目没有错答；不据此推断其他题目表现。' }}</p>
            </section>
            <section class="detail-section"><h3>可能的错因</h3>
              <div v-for="(hypothesis,index) in current.hypotheses" :key="index" class="hypothesis"><small>推断 · 依据第 {{ hypothesis.question_positions.join('、') }} 题</small><p><MathText :text="hypothesis.text" /></p></div>
              <p class="scope-note">{{ current.evidence_limit }}</p>
            </section>
            <section class="detail-section"><h3>概念纠正与自测</h3>
              <div v-for="(correction,index) in current.corrections" :key="index" class="correction"><small>{{ correction.source }}</small><p><MathText :text="correction.text" /></p></div>
              <p v-if="!current.corrections.length">现有记录缺少可核对的解释，请结合原题参考答案与下方课程资料复习，再通过新题确认。</p>
              <p><b>练习任务：</b><MathText :text="current.action" /></p><p><b>检查标准：</b><MathText :text="current.success_check" /></p>
            </section>
            <section class="detail-section"><h3>对应课程资料</h3><LearningResourceList :key="current.id" :resources="current.resources" /></section>
            <section class="detail-section"><h3>本知识点专项复测</h3>
              <p>原卷答对比例 {{ percent(current.statistics.accuracy) }}（{{ current.statistics.correct_count }}/{{ current.statistics.scored_count }} 题）。以下只比较本学生、本原卷和当前概念。</p>
              <p v-if="!current.retests.length">当前原卷的此知识点尚未复测，等待教师布置。</p>
              <article v-for="round in current.retests" :key="round.round" class="concept-round">
                <h4>第 {{ round.round }} 轮 · {{ round.label }}</h4>
                <p v-if="round.scored_count">答对 {{ round.correct_count }}/{{ round.scored_count }} 题 · {{ percent(round.accuracy) }}<span v-if="round.ungraded_count"> · {{ round.ungraded_count }} 题待评分</span></p>
                <p v-if="round.submitted_at">{{ changeText(round.accuracy_change) }}</p>
                <p v-if="round.release_title">任务：{{ round.release_title }}</p>
                <AttemptEvidenceTable v-if="round.wrong_items?.length" :result="{items: round.wrong_items}" :id-prefix="`concept-retest-${current.id}-${round.round}`" heading="本概念的复测错题" />
                <el-button v-if="round.release_id" :loading="opening===round.release_id" @click="openRetest(round)">{{ round.submitted_at ? '查看本轮答卷' : '打开本轮复测' }}</el-button>
              </article>
            </section>
          </template>
          <el-empty v-else description="暂无知识点证据，请先完成有效作答与评分" />
        </article>
      </section>
      <section v-show="view==='questions'" id="questions-view" role="tabpanel" aria-labelledby="questions-tab" class="questions-layout">
        <nav v-if="wrongItems.length" class="question-links" aria-label="错题题号导航"><span>定位错题：</span><button v-for="item in wrongItems" :key="item.position" type="button" @click="locate(item.position)">第 {{ item.position }} 题</button></nav>
        <div ref="questionScroll" class="question-scroll workbench-scroll" tabindex="0" aria-label="错题列表">
          <AttemptEvidenceTable :result="detail" id-prefix="feedback-question" />
          <AttemptEvidenceTable :result="detail" section="correct" />
        </div>
      </section>
    </template>
    <el-drawer v-model="showRetests" title="当前原卷的复测记录" size="min(920px, 96vw)">
      <template v-if="detail">
        <p>{{ detail.assessment.title }} · 原卷答对比例 {{ percent(stats.accuracy) }}</p>
        <p class="scope-note">整轮达标线 {{ percent(detail.retest_threshold) }}；知识点是否仍有错题，请查看知识点详情。未覆盖和待评分不算通过。</p>
        <el-empty v-if="!retests.length" description="当前原卷尚未安排复测，等待教师布置" />
        <details v-for="(round,index) in retests" :key="round.round" :open="index===retests.length-1" class="history-round">
          <summary>第 {{ round.round }} 轮 · {{ round.submitted_at ? (round.result_determinable ? `${round.correct_count}/${round.question_count} · ${percent(round.accuracy)}` : '等待评分') : '等待作答' }}</summary>
          <p>{{ round.status==='passed' ? '本轮达到发布时设定的达标线。' : round.status==='not_passed' ? '本轮未达到设定的达标线。' : '完成作答并取得有效评分后更新结果。' }}</p>
          <p v-if="round.untested_kp_ids?.length">本轮仍有未覆盖知识点，不能据此判断已掌握。</p>
          <AttemptEvidenceTable v-if="round.wrong_items?.length" :result="{items: round.wrong_items}" :id-prefix="`round-${round.round}`" heading="本轮错题" />
          <el-button v-if="round.release_id" :loading="opening===round.release_id" @click="openRetest(round)">{{ round.submitted_at ? '查看本轮答卷' : '打开本轮复测' }}</el-button>
        </details>
      </template>
      <template #footer><el-button @click="showRetests=false">关闭记录</el-button></template>
    </el-drawer>
  </WorkbenchPage>
</template>

<style scoped>
.student-feedback-page{color:#34445f}.feedback-heading{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}.feedback-heading h1{margin:0;font-size:26px;color:#17243d}.eyebrow{margin:0 0 4px;font-size:14px;color:#557198}
.workbench-metric{height:86px}.workbench-metric em{font-size:14px;font-style:normal;font-weight:400}.workbench-metric .status-text{font-size:21px}.workbench-metric small{line-height:1.45}
.view-switch{display:flex;align-items:center;gap:8px;border-bottom:1px solid #dce5f2;padding-bottom:8px;min-width:0}.view-switch button,.question-links button{border:1px solid #dce5f2;background:white;color:#35547e;border-radius:8px;padding:9px 15px;font:inherit;cursor:pointer}.view-switch button[aria-selected=true]{background:#e9f0fa;border-color:#b9ccec;color:#264f9e;font-weight:600}.view-switch>span{margin-left:auto;color:#64748b;overflow-wrap:anywhere}
.diagnosis-layout{display:grid;grid-template-columns:minmax(0,35fr) minmax(0,65fr);gap:14px;height:100%;min-height:0}.overview,.concept-detail{background:#fff;border:1px solid #dce5f2;border-radius:12px;padding:18px}.overview h2,.concept-detail h2{margin:0 0 10px;font-size:20px;color:#223650}.overview p,.concept-detail p{font-size:14px;line-height:1.7;margin:8px 0}.scope-note{color:#64748b;font-size:14px;line-height:1.65}.concept-list{display:grid;gap:10px;margin-top:18px}.concept-list button{display:grid;gap:7px;min-width:0;padding:13px;text-align:left;border:1px solid #dce5f2;border-radius:9px;background:#fafcff;font:inherit;color:#34445f;cursor:pointer}.concept-list button.selected{border-color:#7d9bc9;background:#edf4ff}.concept-list b{font-size:16px}.concept-list span{font-size:14px;line-height:1.5}.detail-section{padding:18px 0;border-top:1px solid #e5ebf4}.detail-section h3{margin:0 0 12px;font-size:17px;color:#223650}.concept-detail>header{padding-bottom:14px}.concept-detail :deep(.question-list){grid-template-columns:1fr}.concept-detail :deep(.section-title){display:none}.concept-detail :deep(.question-card){box-shadow:none}.hypothesis,.correction{padding:12px;background:#f6f9fd;border-radius:8px;margin-bottom:10px}.hypothesis small,.correction small{font-size:14px;color:#65758b}.concept-round{border:1px solid #e1e7ef;border-radius:8px;margin:12px 0;padding:12px}.concept-round h4{margin:0;font-size:15px}.question-links{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px}.question-links button{padding:6px 10px;font-size:14px}.questions-layout{height:100%;min-height:0;display:flex;flex-direction:column}.questions-layout>nav{flex:none;max-height:25%;overflow:auto}.question-scroll{flex:1;display:flex;flex-direction:column;gap:14px;padding:0 4px 12px}.question-scroll>*{flex:none}.history-round{margin:15px 0;padding:14px;border:1px solid #dce5f2;border-radius:8px}.history-round>summary{cursor:pointer;font-size:16px;font-weight:600}.history-round>p{line-height:1.7}
button:focus-visible{outline:2px solid #315fc0;outline-offset:2px}
@media(max-width:900px){.feedback-heading .workbench-toolbar{width:100%}.view-switch>span{display:none}.diagnosis-layout{grid-template-columns:minmax(230px,35fr) minmax(0,65fr)}}
@media(max-width:650px){.diagnosis-layout{grid-template-columns:1fr;grid-template-rows:minmax(110px,35%) minmax(0,1fr);gap:8px}.overview,.concept-detail{padding:12px}.workbench-metric{height:auto;min-height:84px}.feedback-heading h1{font-size:24px}}
</style>
