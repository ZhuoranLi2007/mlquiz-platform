<script setup lang="ts">
import WorkbenchPage from "./WorkbenchPage.vue"
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { Collection, Position, WarningFilled } from '@element-plus/icons-vue'
import MathText from './MathText.vue'
import SourceQuestionContent from './SourceQuestionContent.vue'
import DiagnosisAnalysisContent from './DiagnosisAnalysisContent.vue'
import LearningResourceList from './LearningResourceList.vue'
import { recommendedResourceLinks } from '../utils/resourceLinks'
import { analysisNotice } from '../utils/analysisNotice'

const props = defineProps<{
  result: any
  diagnosisReady: boolean
  intervention?: any
  runningAi?: boolean
}>()
const emit = defineEmits<{ retest: [] }>()
const router = useRouter()
const distributionHost = ref<HTMLElement | null>(null)
const accuracyHost = ref<HTMLElement | null>(null)
let distributionChart: echarts.ECharts | null = null
let accuracyChart: echarts.ECharts | null = null

const overview = computed(() => props.result?.score_overview || {})
const participation = computed(() => props.result?.participation || {})
const topErrors = computed(() => props.result?.high_frequency_errors || [])
const analysis = computed(() => props.result?.ai_analysis || {})
const plan = computed(() => props.result?.retest_plan || {})
const aiFailure = computed(() => analysisNotice(analysis.value))
const resources = computed(() => recommendedResourceLinks(props.result?.resources || []))
const publishedRetest = computed(() => props.intervention?.outputs?.retest?.status === 'published')
const generatedCount = computed(() => props.intervention?.outputs?.generation?.result?.candidates?.length || 0)

const TYPE_LABELS: Record<string, string> = {
  single_choice: '单选题', multiple_choice: '多选题', multi_choice: '多选题',
  true_false: '判断题', numeric: '数值题', calculation: '计算题',
  short_answer: '简答题', term: '名词解释', fill_blank: '填空题',
  formula_fill: '公式填空', derivation: '推导题', comprehensive: '综合题', matching: '匹配题',
}
function percent(value: unknown) { return value === null || value === undefined ? '—' : `${Math.round(Number(value) * 1000) / 10}%` }
function numberText(value: unknown) {
  if (value === null || value === undefined) return '—'
  const n = Number(value); return Number.isInteger(n) ? String(n) : n.toFixed(1)
}
function answerValue(value: any): any {
  if (value === null || value === undefined || value === '') return null
  if (typeof value !== 'object' || Array.isArray(value)) return value
  for (const key of ['selected_keys', 'keys', 'key', 'values', 'parts', 'value', 'answer', 'reference_answer', 'text']) {
    if (value[key] !== undefined && value[key] !== null && value[key] !== '') return value[key]
  }
  return value
}
function answerText(input: any): string {
  const value = answerValue(input)
  if (value === null || value === undefined || value === '') return '未提供'
  if (typeof value === 'boolean') return value ? '正确' : '错误'
  if (Array.isArray(value)) return value.map(answerText).join('、')
  if (typeof value === 'object') return Object.values(value).map(answerText).join('、')
  return String(value)
}
function answerKeys(input: any): string[] {
  const value = answerValue(input)
  return (Array.isArray(value) ? value : [value]).map((entry: any) =>
    String(entry && typeof entry === 'object' ? (entry.key ?? entry.value ?? '') : (entry ?? '')).trim().toUpperCase()).filter(Boolean)
}
function optionsFor(item: any): any[] {
  const options = item?.options || []
  if (Array.isArray(options)) return options.map((row: any, index: number) =>
    typeof row === 'object' ? row : { key: String.fromCharCode(65 + index), text: String(row) })
  return Object.entries(options).map(([key, value]) => ({ key, text: value }))
}
function isCorrect(item: any, key: string) { return answerKeys(item?.answer_reference?.payload).includes(String(key).toUpperCase()) }
function isFrequentWrong(item: any, key: string) {
  return (item?.common_wrong_answers || []).some((row: any) =>
    String(row.answer || '').split(',').map((v: string) => v.trim().toUpperCase()).includes(String(key).toUpperCase())) && !isCorrect(item, key)
}
function optionClass(item: any, key: string) { return { correct: isCorrect(item, key), wrong: isFrequentWrong(item, key) } }
function originalOptions(item: any): any[] {
  const original = item?.original_question
  if (!original) return []
  const options = original.options?.length ? original.options : original.source_options
  if (Array.isArray(options)) return options.map((row: any, index: number) =>
    typeof row === 'object' ? row : { key: String.fromCharCode(65 + index), text: String(row) })
  return Object.entries(options || {}).map(([key, value]) => ({ key, text: value }))
}
function referenceLabel(item: any) {
  if (item?.scoring_reference_status !== 'available') return '参考答案待核对'
  return item?.answer_reference?.authority_label || '参考答案'
}
function resourceType(resource: any) {
  const value = String(resource?.content_type || resource?.source_type || resource?.platform || '').toLowerCase()
  if (value.includes('video') || value.includes('bilibili')) return '视频'
  if (value.includes('ppt') || value.includes('slide')) return '课件'
  if (value.includes('article')) return '文章'
  return resource?.platform || '学习资料'
}
function locateQuestion(row: any) {
  const position = (row?.question_positions || [])[0]
  if (position !== undefined) document.getElementById(`class-question-${position}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function renderCharts() {
  const bins = props.result?.score_distribution?.comparable_bins || props.result?.score_distribution?.bins || []
  if (distributionHost.value) {
    if (!distributionChart) distributionChart = echarts.init(distributionHost.value)
    distributionChart.setOption({
      animation: false,
      grid: { left: 48, right: 14, top: 30, bottom: 42 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { fontSize: 14 }, formatter: (rows: any) => `${rows[0].axisValue} 分：${rows[0].value} 人` },
      xAxis: { type: 'category', data: bins.map((row: any) => row.label), axisLabel: { fontSize: 14, color: '#475569', showMaxLabel: true, interval: distributionLabelInterval(bins.length, distributionHost.value.clientWidth) }, axisLine: { lineStyle: { color: '#d9e2ef' } } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 14, color: '#475569' }, splitLine: { lineStyle: { color: '#edf2f7' } } },
      series: [{ type: 'bar', data: bins.map((row: any) => row.count), barMaxWidth: 34, itemStyle: { color: '#4f8df7', borderRadius: [5, 5, 0, 0] }, label: { show: true, position: 'top', fontSize: 14, color: '#1e293b' } }],
    }, true)
  }
  const questions = props.result?.question_analysis || []
  if (accuracyHost.value) {
    if (!accuracyChart) accuracyChart = echarts.init(accuracyHost.value)
    accuracyChart.setOption({
      animation: false,
      grid: { left: 54, right: 14, top: 30, bottom: questions.length > 10 ? 54 : 42 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, textStyle: { fontSize: 14 }, formatter: (rows: any) => {
        const item = questions[rows[0].dataIndex] || {}
        return `第${item.position}题<br/>正确：${item.correct_count}/${item.denominator}<br/>正确率：${percent(item.correct_rate)}`
      } },
      dataZoom: questions.length > 10 ? [{ type: 'inside' }, { type: 'slider', height: 12, bottom: 2 }] : [],
      xAxis: { type: 'category', data: questions.map((row: any) => `第${row.position}题`), axisLabel: { fontSize: 14, color: '#475569', hideOverlap: true }, axisLine: { lineStyle: { color: '#d9e2ef' } } },
      yAxis: { type: 'value', min: 0, max: 100, interval: 25, axisLabel: { formatter: '{value}%', fontSize: 14, color: '#475569' }, splitLine: { lineStyle: { color: '#edf2f7' } } },
      series: [{ type: 'bar', barMaxWidth: 32, data: questions.map((row: any) => {
        const value = Math.round(Number(row.correct_rate || 0) * 1000) / 10
        return { value, itemStyle: { color: value < 60 ? '#f39a9f' : (value < 75 ? '#8ab0f8' : '#4f8df7'), borderRadius: [5, 5, 0, 0] } }
      }), label: { show: true, position: 'top', formatter: '{c}%', fontSize: 14, color: '#1e293b' } }],
    }, true)
  }
}
function distributionLabelInterval(count: number, width: number) {
  const visibleLabels = Math.max(2, Math.floor((width - 52) / 62))
  return Math.max(0, Math.ceil(count / visibleLabels) - 1)
}
function resizeCharts() {
  distributionChart?.resize()
  distributionChart?.setOption({ xAxis: { axisLabel: { interval: distributionLabelInterval((props.result?.score_distribution?.comparable_bins || props.result?.score_distribution?.bins || []).length, distributionHost.value?.clientWidth || 0) } } })
  accuracyChart?.resize()
}
watch(() => props.result, async () => { await nextTick(); renderCharts() }, { deep: true })
onMounted(() => { renderCharts(); window.addEventListener('resize', resizeCharts) })
onBeforeUnmount(() => { window.removeEventListener('resize', resizeCharts); distributionChart?.dispose(); accuracyChart?.dispose() })
</script>

<template>
  <WorkbenchPage :scroll="false" class="class-workbench">
    <template #header>
    <section class="class-overview">
      <div class="class-metrics">
        <article class="metric primary"><span>平均分</span><b>{{ numberText(overview.average_score) }}<small>分</small></b></article>
        <article class="metric good"><span>最高分</span><b>{{ numberText(overview.highest_score) }}<small>分</small></b></article>
        <article class="metric bad"><span>最低分</span><b>{{ numberText(overview.lowest_score) }}<small>分</small></b></article>
        <article class="metric people"><span>参考人数</span><b>{{ overview.comparable_student_count ?? participation.scored ?? 0 }}<small>/{{ participation.enrolled || 0 }} 人</small></b></article>
      </div>

    </section>

    </template>
    <section class="class-body">
      <main class="errors-column">
      <div class="class-charts">
        <article class="chart-card"><header><b>成绩分布</b><span>{{ numberText(overview.max_score) }} 分 · {{ overview.question_count || 0 }} 题</span></header><div ref="distributionHost" class="chart"></div></article>
        <article class="chart-card"><header><b>各题正确率</b><span>低于 60% 重点标记</span></header><div ref="accuracyHost" class="chart"></div></article>
      </div>
        <header class="section-title"><div><el-icon><WarningFilled /></el-icon><h2>高频错题（Top 5）</h2></div></header>
        <div v-if="topErrors.length" class="error-grid">
          <article v-for="item in topErrors" :id="`class-question-${item.position}`" :key="item.paper_item_id" class="error-card">
            <header><div><b>第{{ item.position }}题</b><span>{{ TYPE_LABELS[item.question_type] || item.question_type }}</span><span>{{ item.chapter?.title || '未映射章节' }}</span></div><strong>错误 {{ item.incorrect_count }}/{{ item.denominator }} · 正确率 {{ percent(item.correct_rate) }}</strong></header>
            <div class="card-body">
              <div v-if="item.is_scoring_revision" class="revision-label">修订版（评分依据）</div>
              <div v-if="item.scoring_reference_status !== 'available'" class="reference-warning">{{ item.answer_reference?.reason || '评分答案待核对' }}</div>
              <div class="stem"><MathText :text="item.stem" /></div>
              <div v-if="optionsFor(item).length" class="options-list"><div v-for="option in optionsFor(item)" :key="option.key" class="option-row" :class="optionClass(item, option.key)"><b>{{ option.key }}</b><MathText :text="option.text || option.value || ''" /><span v-if="isCorrect(item, option.key)" class="correct-note">正确答案</span><span v-if="isFrequentWrong(item, option.key)" class="wrong-note">常见误选</span></div></div>
              <div class="answer-summary"><div class="wrong-answer"><span>常见错误作答</span><b>{{ item.high_frequency_wrong_answer || '错误作答分散' }}</b><small v-if="item.answer_mapping_counts?.unmapped">另有 {{ item.answer_mapping_counts.unmapped }} 份作答的选项对应待核对</small></div><div class="right-answer"><span>{{ referenceLabel(item) }}</span><b>{{ item.scoring_reference_status === 'available' ? answerText(item.answer_reference?.payload) : '待核对' }}</b></div></div>
              <section v-if="item.scoring_reference_status === 'available'" class="explanation"><h4>参考解析 <small v-if="item.answer_review_status === 'disagreed'">独立复核有分歧，需教师核对</small><small v-else-if="item.answer_review_status === 'unavailable'">独立复核未完成，需教师核对</small></h4><MathText :text="item.answer_reference?.explanation || '该题暂未提供独立解析。'" /></section>
              <details v-if="item.original_question" class="original-question"><summary>查看原始题目</summary><div class="original-stem"><SourceQuestionContent :question="item.original_question" /></div><div v-if="originalOptions(item).length" class="original-options"><div v-for="option in originalOptions(item)" :key="option.key"><b>{{ option.key }}</b><MathText :text="option.text || option.value || ''" /></div></div></details>
            </div>
          </article>
        </div>
        <div v-else class="empty-card"><b>当前试卷暂无高频错题</b><span>全部答对或暂无已评分作答。</span></div>
      </main>

      <aside class="ai-column">
        <section class="ai-panel">
          <header><div><h2>班级学情分析</h2></div></header>
          <DiagnosisAnalysisContent :analysis="analysis" scope="class_assessment" locatable @locate="locateQuestion" />
          <p v-if="aiFailure" class="ai-failure">{{ aiFailure }}</p>
          <section class="side-section"><h3><el-icon><Collection /></el-icon>推荐学习资源</h3><LearningResourceList :resources="resources" /></section>

          <section class="retest-section"><h3>班级复测</h3><p>优先从题库选择符合班级薄弱知识点的新题，缺题时由 AI 补充，校验后发布。</p><p v-if="!plan.ready">{{ plan.reason }}</p><div v-if="generatedCount" class="workflow-note">已生成 {{ generatedCount }} 道候选题，自动筛选并核验后发布。</div><div v-if="publishedRetest" class="published-note">班级复测已发布。</div><el-button type="primary" class="retest-button" :icon="Position" :loading="runningAi" :disabled="!plan.ready" @click="emit('retest')">生成并发布班级复测</el-button></section>
        </section>
      </aside>
    </section>
  </WorkbenchPage>
</template>

<style scoped>
.class-workbench{display:grid;min-width:0;gap:15px;color:#34445f}.overview-shell{display:grid;grid-template-columns:minmax(310px,.9fr) minmax(330px,1fr) minmax(360px,1.2fr);overflow:hidden;border:1px solid #dce5f2;border-radius:14px;background:#fff;box-shadow:0 5px 18px rgb(37 67 108 / 6%)}.overview-shell>article+article{border-left:1px solid #e7edf5}.overview-card,.chart-card{min-width:0;padding:14px}.overview-card>header,.chart-card>header{display:flex;align-items:center;gap:7px;color:#223650}.overview-card>header .el-icon{color:#2563eb;font-size:19px}.overview-card>header span,.chart-card>header span{margin-left:auto;color:#8491a3;font-size:14px}.score-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:13px}.score-metrics>div{padding:10px;border-radius:9px}.score-metrics span,.score-metrics b{display:block}.score-metrics span{font-size:14px}.score-metrics b{margin-top:3px;font-size:20px}.score-metrics small{margin-left:2px;font-size:14px}.score-metrics .primary{background:#eef4ff;color:#2458c8}.score-metrics .good{background:#edf9f2;color:#148052}.score-metrics .bad{background:#fff0f1;color:#c83d48}.minor-metrics{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:9px}.minor-metrics>div{padding:9px;border-radius:8px;background:#f6f8fb}.minor-metrics span,.minor-metrics b{display:block}.minor-metrics span{color:#7a8799;font-size:14px}.minor-metrics b{margin-top:3px;color:#25384f;font-size:14px}.overview-card>p{margin:8px 0 0;color:#8793a3;font-size:14px;line-height:1.4}.chart-card>header b{font-size:14px}.chart{height:170px}.class-body{display:grid;grid-template-columns:minmax(0,7fr) minmax(310px,3fr);align-items:start;gap:15px}.errors-column{min-width:0}.section-title{display:flex;align-items:end;justify-content:space-between;gap:10px;margin-bottom:10px}.section-title>div{display:flex;align-items:center;gap:7px}.section-title .el-icon{color:#e94f5b;font-size:18px}.section-title h2{margin:0;color:#17243d;font-size:18px}.section-title p{margin:0;color:#7d899b;font-size:14px}.error-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start;gap:12px}.error-card{min-width:0;overflow:hidden;border:1px solid #dce5f2;border-radius:13px;background:#fff;box-shadow:0 4px 14px rgb(52 69 98 / 4%)}.error-card>header{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:9px 11px;background:#f8faff}.error-card>header>div{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.error-card>header b,.error-card>header span{padding:4px 7px;border-radius:6px;font-size:14px}.error-card>header b{background:#4f8df7;color:#fff}.error-card>header span{background:#edf3ff;color:#315fc7}.error-card>header strong{flex:none;color:#d5414c;font-size:14px}.card-body{padding:12px}.stem{color:#17243d;font-size:14px;font-weight:650;line-height:1.7;overflow-wrap:anywhere}.stem :deep(.katex-display){overflow-x:auto}.options-list{display:grid;gap:6px;margin-top:9px}.option-row{display:grid;grid-template-columns:22px minmax(0,1fr) auto;align-items:center;gap:6px;padding:7px 8px;border:1px solid #e5eaf1;border-radius:8px;background:#fff;font-size:14px}.option-row.correct{border-color:#b9e2cc;background:#edf9f2}.option-row.wrong{border-color:#f1c5c8;background:#fff0f1}.correct-note,.wrong-note{padding:2px 5px;border-radius:5px;font-size:14px;font-weight:700}.correct-note{background:#dff3e8;color:#148052}.wrong-note{background:#fbe0e2;color:#c33b46}.answer-summary{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:9px}.answer-summary>div{padding:8px 9px;border-radius:8px}.answer-summary span,.answer-summary b{display:block}.answer-summary span{font-size:14px}.answer-summary b{margin-top:3px;font-size:14px}.wrong-answer{background:#fff0f1;color:#9b303b}.right-answer{background:#edf9f2;color:#176847}.explanation{margin-top:9px;padding:9px 10px;border:1px solid #e4e9f1;border-radius:8px;background:#fbfcfe;color:#4a596e;font-size:14px;line-height:1.6}.explanation h4{margin:0 0 4px;color:#25364f;font-size:14px}.empty-card{padding:30px;border:1px dashed #cbd7e6;border-radius:13px;background:#fff;text-align:center}.empty-card b,.empty-card span{display:block}.empty-card span{margin-top:4px;color:#7d899b;font-size:14px}.ai-column{min-width:0}.ai-panel{position:sticky;top:14px;overflow:hidden;border:1px solid #d9e5f5;border-radius:14px;background:#fff;box-shadow:0 6px 20px rgb(37 67 108 / 7%)}.ai-panel>header{display:flex;align-items:center;gap:10px;padding:15px 16px;border-bottom:1px solid #e7edf5;background:linear-gradient(135deg,#edf5ff,#fff)}.spark{display:grid;width:38px;height:32px;place-items:center;border-radius:9px;background:#2563eb;color:#fff;font-size:14px;font-weight:800}.ai-panel h2{margin:0;color:#17243d;font-size:17px}.ai-panel>header p{margin:2px 0 0;color:#8491a3;font-size:14px}.summary,.pending{margin:12px 14px 0;padding:10px 11px;border:1px solid #d8e4fb;border-radius:9px;background:#f6f9ff;color:#31445f;font-size:14px;line-height:1.6}.pending b,.pending span{display:block}.pending span{margin-top:4px;color:#718096;font-size:14px}.side-section{margin:0 14px;padding:13px 0;border-bottom:1px solid #e8edf4}.side-section h3{display:flex;align-items:center;gap:6px;margin:0 0 8px;color:#223650;font-size:14px}.side-section h3 .el-icon{color:#2563eb}.side-section ul,.side-section ol{display:grid;gap:7px;margin:0;padding-left:18px}.side-section li{color:#526279;font-size:14px;line-height:1.5}.side-section li b,.side-section li span{display:block}.side-section li span{margin-top:2px;color:#7a8799;font-size:14px}.concepts{display:flex;flex-wrap:wrap;gap:6px}.concepts article{padding:7px 8px;border-radius:8px;background:#eef4ff}.concepts article>div{display:flex;align-items:center;gap:7px}.concepts b{color:#315fc7;font-size:14px}.concepts em{color:#7384a1;font-size:14px;font-style:normal}.concepts span{display:block;margin-top:2px;color:#748399;font-size:14px}.side-empty{margin:0;color:#8793a3;font-size:14px;line-height:1.5}.resources{display:grid;gap:5px}.resources article{display:flex;align-items:center;justify-content:space-between;gap:7px;padding:7px;border-radius:8px;background:#f7f9fc}.resources article>div{min-width:0}.resources b,.resources span{display:block}.resources b{overflow:hidden;color:#34465f;font-size:14px;text-overflow:ellipsis;white-space:nowrap}.resources span{margin-top:2px;color:#8390a1;font-size:14px}.retest-section{padding:14px;background:#f6f9ff}.retest-section h3{margin:0 0 6px;color:#223650;font-size:14px}.retest-section p{margin:0 0 8px;color:#7b899a;font-size:14px;line-height:1.5}.workflow-note,.published-note{margin-bottom:7px;padding:7px;border-radius:6px;background:#eaf2ff;color:#436a9f;font-size:14px}.published-note{background:#e8f7ef;color:#197451}.retest-button{width:100%}.ai-panel>footer{padding:9px 14px;border-top:1px solid #e6ebf3;color:#8995a5;font-size:14px;line-height:1.5}@media(max-width:1350px){.overview-shell{grid-template-columns:1fr 1fr}.overview-card{grid-row:span 2}.accuracy-card{border-top:1px solid #e7edf5}.class-body{grid-template-columns:minmax(0,1fr) minmax(290px,.72fr)}.error-grid{grid-template-columns:1fr}}@media(max-width:920px){.overview-shell,.class-body{grid-template-columns:1fr}.overview-shell>article+article{border-top:1px solid #e7edf5;border-left:0}.ai-panel{position:static}.ai-column{order:2}.error-grid{grid-template-columns:1fr}}@media(max-width:600px){.score-metrics,.minor-metrics,.answer-summary{grid-template-columns:1fr}.section-title{align-items:flex-start;flex-direction:column}.option-row{grid-template-columns:22px minmax(0,1fr)}.option-row span{grid-column:2;justify-self:start}}
.class-overview{display:grid;gap:12px}.class-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric{min-width:0;padding:13px 15px;border:1px solid var(--diag-border,#dce5f2);border-radius:var(--diag-radius,13px);background:#fff;box-shadow:var(--diag-shadow,0 4px 16px rgb(32 65 115 / 5%))}.metric span,.metric b{display:block}.metric span{color:#64748a;font-size:14px;font-weight:700}.metric b{margin-top:5px;color:#17243d;font-size:24px}.metric small{margin-left:4px;color:#718096;font-size:14px}.metric.primary{border-top:3px solid #2563eb}.metric.good{border-top:3px solid #18a168}.metric.bad{border-top:3px solid #ef5d68}.metric.people{border-top:3px solid #7c3aed}.class-charts{display:grid;grid-template-columns:1fr 1fr;gap:12px}.chart-card{padding:13px 14px;border:1px solid var(--diag-border,#dce5f2);border-radius:var(--diag-radius,13px);background:#fff;box-shadow:var(--diag-shadow,0 4px 16px rgb(32 65 115 / 5%))}.chart-card>header{display:flex;align-items:center;justify-content:space-between;color:#223650}.chart-card>header b{font-size:14px}.chart-card>header span{color:#8491a3;font-size:14px}.summary-section{margin:0 14px;padding:13px 0;border-bottom:1px solid #e8edf4}.summary-section h3{margin:0 0 8px;color:#223650;font-size:14px}.summary-section .summary{margin:0}.summary-section small{display:block;margin-top:7px;color:#7d899b;font-size:14px;line-height:1.5}.ai-failure{margin:10px 14px 0;padding:8px 9px;border-radius:7px;background:#fff6e6;color:#8a6422;font-size:14px;line-height:1.5}.side-section li[role=button],.concepts article[role=button]{cursor:pointer;transition:.15s}.side-section li[role=button]:hover,.side-section li[role=button]:focus,.concepts article[role=button]:hover,.concepts article[role=button]:focus{outline:none;background:#e7f0ff}.concepts article{display:grid;gap:2px}.error-card{scroll-margin-top:18px}@media(max-width:920px){.class-charts{grid-template-columns:1fr}.class-metrics{grid-template-columns:1fr 1fr}}@media(max-width:600px){.class-metrics{grid-template-columns:1fr}}
.revision-label{margin-bottom:7px;color:#43658e;font-size:14px;font-weight:600}.reference-warning{margin-bottom:8px;color:#996824;font-size:14px;line-height:1.5}.answer-summary small{display:block;margin-top:5px;color:#85636a;font-size:14px;line-height:1.4}.explanation h4 small{margin-left:6px;color:#9a6926;font-size:14px;font-weight:400}.original-question{margin-top:10px;padding-top:9px;border-top:1px solid #e5eaf1;color:#56677d;font-size:14px;line-height:1.6}.original-question summary{width:max-content;max-width:100%;color:#315fc7;cursor:pointer}.original-stem{margin-top:8px;overflow-wrap:anywhere}.original-options{display:grid;gap:5px;margin-top:7px}.original-options>div{display:grid;grid-template-columns:20px minmax(0,1fr);gap:5px;overflow-wrap:anywhere}
.metric.primary,.metric.good,.metric.bad,.metric.people{border-top:1px solid var(--diag-border,#dce5f2)}
.metric span{color:var(--text-secondary);font-weight:600}
.metric b{font-size:var(--font-size-metric);font-weight:700;font-variant-numeric:tabular-nums}
.metric small{font-weight:400}
.chart-card>header b{font-size:16px;font-weight:600}
.stem{font-size:var(--reading-size);font-weight:500;line-height:var(--reading-line)}
.ai-panel>header{background:#fff}
.ai-panel h2{font-size:var(--font-size-section);font-weight:600}
</style>

<style scoped>
.class-workbench.workbench-page{display:flex;gap:12px}.class-metrics{grid-template-columns:repeat(4,minmax(0,260px))}.class-metrics .metric{min-height:84px;padding:10px 14px}.class-body{height:100%;min-height:0;align-items:stretch}.errors-column,.ai-column{min-height:0;overflow:auto}.ai-panel{position:static}.class-charts{margin-bottom:14px}.chart{height:180px}@media(max-width:700px){.class-metrics{grid-template-columns:1fr 1fr}.class-body{grid-template-columns:1fr;grid-template-rows:1fr 1fr}}
</style>
