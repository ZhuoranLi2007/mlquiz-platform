<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { Collection, Position, TrendCharts, WarningFilled } from '@element-plus/icons-vue'
import MathText from './MathText.vue'
import SourceQuestionContent from './SourceQuestionContent.vue'
import { friendlyQuizCode } from '../utils/presentation'
import { recommendedResourceLinks } from '../utils/resourceLinks'

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
const viewMode = ref<'all' | 'paper' | 'chapter'>('all')
const selectedPaper = ref('')
const selectedChapter = ref('')
const repeatedOnly = ref(false)

const stats = computed(() => props.result?.statistics || {})
const attempts = computed(() => props.result?.attempts || [])
const allWrongItems = computed(() => props.result?.wrong_items || [])
const analysis = computed(() => props.result?.ai_analysis || {})
const plan = computed(() => props.result?.retest_plan || {})
const weakConcepts = computed(() => {
  const aiRows = analysis.value?.concept_hypotheses || []
  return (aiRows.length ? aiRows : (props.result?.weak_concepts || [])).slice(0, 5)
})
const recommendations = computed(() => (analysis.value?.recommendations || [])
  .map((row: any) => typeof row === 'string' ? row : (row?.message || row?.advice || row?.action))
  .filter(Boolean).slice(0, 4))
const aiFailure = computed(() => analysis.value?.model_status
  ? 'AI 服务暂时不可用，当前显示基础分析。可点击页面顶部“运行诊断”重试。' : '')
const workflowResources = computed(() => props.intervention?.outputs?.recommendations?.result?.recommendations || [])
const resources = computed(() => recommendedResourceLinks(workflowResources.value, props.result?.resources || []))
const publishedRetest = computed(() => props.intervention?.outputs?.retest?.status === 'published')
const generatedCount = computed(() => props.intervention?.outputs?.generation?.result?.candidates?.length || 0)

const paperOptions = computed(() => attempts.value.map((row: any) => ({
  value: row.assessment_version_id,
  label: `${friendlyQuizCode(row.quiz_code) ? `${friendlyQuizCode(row.quiz_code)} · ` : ''}${row.title}`,
})))
const chapterOptions = computed(() => (props.result?.chapters || []).map((row: any) => ({
  value: row.chapter_id, label: row.title || row.chapter_id,
})))
const filteredWrongItems = computed(() => allWrongItems.value.filter((item: any) => {
  if (repeatedOnly.value && !item.repeated) return false
  const occurrences = item.occurrences || [item]
  if (viewMode.value === 'paper' && selectedPaper.value) {
    return occurrences.some((row: any) => row.assessment_version_id === selectedPaper.value)
  }
  if (viewMode.value === 'chapter' && selectedChapter.value) {
    return item.chapter_id === selectedChapter.value
  }
  return true
}))
const primaryWrongItems = computed(() => filteredWrongItems.value.slice(0, 6))
const otherWrongItems = computed(() => filteredWrongItems.value.slice(6))

const TYPE_LABELS: Record<string, string> = {
  single_choice: '单选题', multiple_choice: '多选题', multi_choice: '多选题',
  true_false: '判断题', numeric: '数值计算题', calculation: '计算题',
  short_answer: '简答题', term: '名词解释', fill_blank: '填空题',
  formula_fill: '公式填空', derivation: '推导题', comprehensive: '综合题', matching: '匹配题',
}

function percent(value: unknown) {
  return value === null || value === undefined ? '—' : `${Math.round(Number(value) * 100)}%`
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
function examLabel(row: any) {
  const code = friendlyQuizCode(row?.quiz_code)
  return code || (row?.exam_order ? `第${row.exam_order}次` : row?.title || '在线考试')
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
function evidenceRefs(row: any) {
  return row?.question_positions || row?.question_refs || []
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
  if (mode === 'paper' && !selectedPaper.value) selectedPaper.value = paperOptions.value[0]?.value || ''
  if (mode === 'chapter' && !selectedChapter.value) selectedChapter.value = chapterOptions.value[0]?.value || ''
}
function itemDomId(item: any) { return `history-question-${String(item?.repeat_group_key || item?.question_id || item?.position).replace(/[^\w-]/g, '-')}` }
function locateConcept(row: any) {
  const name = row?.name || row?.concept
  const item = allWrongItems.value.find((entry: any) =>
    (name && entry?.knowledge_point?.name === name)
    || (row?.chapter && entry?.chapter?.title === row.chapter))
  if (item) document.getElementById(itemDomId(item))?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function renderChart() {
  if (!chartHost.value) return
  if (!chart) chart = echarts.init(chartHost.value)
  const rows = attempts.value.filter((row: any) => row.score_rate !== null && row.score_rate !== undefined)
  chart.setOption({
    animation: false,
    color: ['#2563eb'],
    grid: { left: 42, right: 20, top: 46, bottom: 36 },
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        const row = rows[params.dataIndex] || {}
        return `<b>${row.title || '考试'}</b><br/>${row.source_label || ''}<br/>得分：${numberText(row.awarded_score)}/${numberText(row.max_score)}<br/>得分率：${percent(row.score_rate)}<br/>时间：${dateText(row.exam_time)}`
      },
    },
    legend: { top: 6, right: 8, data: ['历史课堂小测', '在线考试', '针对性复测'], textStyle: { fontSize: 10, color: '#718096' } },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: rows.map((row: any) => examLabel(row)),
      axisLabel: { color: '#718096', fontSize: 10 },
      axisLine: { lineStyle: { color: '#d8e2ef' } },
    },
    yAxis: {
      type: 'value', min: 0, max: 100, interval: 25,
      axisLabel: { formatter: '{value}%', color: '#718096', fontSize: 10 },
      splitLine: { lineStyle: { color: '#edf2f7' } },
    },
    series: [{
      name: '得分率', type: 'line', smooth: false, symbolSize: 8,
      lineStyle: { width: 2.5 }, areaStyle: { color: 'rgba(37,99,235,.08)' },
      data: rows.map((row: any) => ({
        value: Math.round(Number(row.score_rate) * 100),
        symbol: row.source_label === '针对性复测' ? 'diamond' : (row.source_label === '历史课堂小测' ? 'circle' : 'rect'),
        itemStyle: { color: row.source_label === '针对性复测' ? '#7c3aed' : (row.source_label === '历史课堂小测' ? '#2563eb' : '#0ea5e9') },
      })),
    }],
  }, true)
}
function resizeChart() { chart?.resize() }
watch(() => props.result?.attempts, async () => { await nextTick(); renderChart() }, { deep: true })
onMounted(() => { renderChart(); window.addEventListener('resize', resizeChart) })
onBeforeUnmount(() => { window.removeEventListener('resize', resizeChart); chart?.dispose(); chart = null })
</script>

<template>
  <div class="history-workbench">
    <section class="history-overview">
      <div class="metric-strip">
        <article class="metric-card blue"><span>平均得分率</span><strong>{{ percent(stats.average_score_rate) }}</strong><small>历次考试平均</small></article>
        <article class="metric-card violet"><span>参与考试次数</span><strong>{{ stats.participated_assessments || 0 }}<em>次</em></strong><small>有成绩的考试</small></article>
        <article class="metric-card cyan"><span>累计答题题次</span><strong>{{ stats.scored_item_events || 0 }}<em>题次</em></strong><small>已完成评分</small></article>
        <article class="metric-card red"><span>累计错误题次</span><strong>{{ stats.wrong_item_events || 0 }}<em>题次</em></strong><small>{{ stats.unique_wrong_question_count || 0 }} 道不同错题</small></article>
      </div>
      <article class="trend-card">
        <header><div><el-icon><TrendCharts /></el-icon><b>历次成绩趋势</b></div></header>
        <div v-if="attempts.some((row: any) => row.score_rate !== null)" ref="chartHost" class="trend-chart"></div>
        <p v-else class="chart-empty">当前统计范围没有可绘制的有效评分。</p>
        <footer>不同试卷难度可能不同，曲线仅表示成绩变化。</footer>
      </article>
    </section>

    <section class="history-body">
      <main class="wrong-column">
        <header class="wrong-toolbar">
          <div><h2>错题汇总</h2><span>错误题次 {{ stats.wrong_item_events || 0 }} · 不同错题 {{ stats.unique_wrong_question_count || 0 }}</span></div>
          <label><el-checkbox v-model="repeatedOnly">仅看反复出错内容（≥2次）</el-checkbox></label>
        </header>
        <div class="view-tabs">
          <button :class="{ active: viewMode === 'all' }" @click="setView('all')">全部错题</button>
          <button :class="{ active: viewMode === 'paper' }" @click="setView('paper')">按试卷查看</button>
          <button :class="{ active: viewMode === 'chapter' }" @click="setView('chapter')">按章节统计</button>
          <el-select v-if="viewMode === 'paper'" v-model="selectedPaper" size="small" placeholder="选择试卷">
            <el-option v-for="row in paperOptions" :key="row.value" :label="row.label" :value="row.value" />
          </el-select>
          <el-select v-if="viewMode === 'chapter'" v-model="selectedChapter" size="small" placeholder="选择章节">
            <el-option v-for="row in chapterOptions" :key="row.value" :label="row.label" :value="row.value" />
          </el-select>
        </div>

        <div v-if="primaryWrongItems.length" class="wrong-grid">
          <article v-for="item in primaryWrongItems" :id="itemDomId(item)" :key="item.repeat_group_key" class="wrong-card">
            <header>
              <div class="tags"><b :class="{ repeat: item.repeated }">{{ item.repeated ? '反复错题' : '错题' }}</b><span>{{ item.chapter?.title || '未映射章节' }}</span><span>{{ TYPE_LABELS[item.question_type] || item.question_type }}</span></div>
              <strong>共 {{ item.error_count }} 次出错</strong>
            </header>
            <div class="card-body">
              <p class="paper-line">第 {{ item.position }} 题 · {{ item.assessment_title }} · {{ item.source_label }}</p>
              <div class="stem"><MathText :text="item.stem" /></div>
              <SourceQuestionContent :question="item" />
              <div v-if="optionsFor(item).length" class="options-list">
                <div v-for="option in optionsFor(item)" :key="option.key" class="option-row" :class="optionClass(item, option.key)">
                  <b>{{ option.key }}</b><MathText :text="option.text || option.value || ''" />
                  <span v-if="correctOption(item, option.key)" class="correct-note">正确答案</span>
                  <span v-if="selected(item, option.key) && !correctOption(item, option.key)" class="wrong-note">学生选择</span>
                </div>
              </div>
              <div class="answer-compare"><div class="student-answer"><small>学生作答</small><MathText :text="answerText(item.response)" /></div><div class="correct-answer"><small>正确答案</small><MathText :text="answerText(correctPayload(item))" /></div></div>
              <div class="score-meta"><span>本题得分 <b>{{ numberText(item.awarded_score) }}/{{ numberText(item.max_score) }}</b></span><span>所属概念 <b>{{ item.knowledge_point?.name || '未映射' }}</b></span></div>
              <section class="explanation"><h4>参考解析</h4><MathText :text="item.answer_reference?.explanation || '该题暂未提供独立解析。'" /></section>
              <details v-if="item.occurrences?.length" class="occurrences">
                <summary>查看 {{ item.occurrences.length }} 次原始作答与评分</summary>
                <div v-for="(row, index) in item.occurrences" :key="`${row.attempt_id}-${index}`" class="occurrence-row"><div><b>{{ friendlyQuizCode(row.quiz_code) || row.assessment_title }} 第{{ row.position }}题</b><span>{{ row.assessment_title }} · {{ dateText(row.exam_time) }}</span></div><div><span>作答：{{ answerText(row.response) }}</span><b>{{ numberText(row.awarded_score) }}/{{ numberText(row.max_score) }} 分</b></div></div>
              </details>
            </div>
          </article>
        </div>
        <div v-else class="empty-card"><el-icon><WarningFilled /></el-icon><div><b>当前筛选下没有错题</b><span>可调整试卷、章节或反复错误筛选。</span></div></div>

        <details v-if="otherWrongItems.length" class="other-wrong">
          <summary>其他错题（共 {{ otherWrongItems.length }} 道）</summary>
          <div class="compact-list"><article v-for="item in otherWrongItems" :key="item.repeat_group_key"><b>{{ item.quiz_code }} 第{{ item.position }}题 · {{ item.chapter?.title }}</b><span>{{ item.stem }}</span><em>错 {{ item.error_count }} 次</em></article></div>
        </details>

        <details v-if="result.ungraded_items?.length" class="ungraded-area">
          <summary>待评分题目（{{ result.ungraded_items.length }} 题次）</summary>
          <article v-for="(item, index) in result.ungraded_items" :key="`${item.attempt_id}-${item.paper_item_id}-${index}`"><b>{{ friendlyQuizCode(item.quiz_code) || item.assessment_title }} 第{{ item.position }}题 · {{ item.assessment_title }}</b><span>尚未完成评分</span></article>
        </details>
      </main>

      <aside class="ai-column">
        <section class="ai-panel">
          <header><div><h2>学情分析</h2></div></header>
          <section class="summary-section"><h3>综合结论</h3><p class="ai-summary">{{ analysis.summary || '当前考试记录不足，暂时无法形成长期分析。' }}</p></section>
          <p v-if="aiFailure" class="ai-failure">{{ aiFailure }}</p>

          <section class="sidebar-section">
            <h3>薄弱概念</h3>
            <div v-if="weakConcepts.length" class="concept-list">
              <article v-for="(row, index) in weakConcepts" :key="index" role="button" tabindex="0" @click="locateConcept(row)" @keydown.enter="locateConcept(row)"><div><b>{{ row.name || row.concept }}</b><em>错 {{ row.error_events || 0 }} 题次</em></div><span>{{ row.chapter || '未映射章节' }}</span><small>{{ evidenceRefs(row).join('、') || '点击查看相关错题' }}</small><p>{{ row.evidence || row.analysis || '建议通过同类练习进一步确认。' }}</p></article>
            </div>
            <p v-else class="sidebar-empty">当前没有反复出现的薄弱概念。</p>
          </section>

          <section class="sidebar-section">
            <h3>学习建议</h3>
            <ol v-if="recommendations.length" class="advice-list"><li v-for="(row, index) in recommendations" :key="index">{{ row }}</li></ol>
            <p v-else class="sidebar-empty">暂无需要特别提示的学习建议。</p>
          </section>

          <section class="sidebar-section">
            <h3><el-icon><Collection /></el-icon>推荐学习资源</h3>
            <div v-if="resources.length" class="resource-list"><a v-for="resource in resources" :key="resource.resource_id || resource.url" class="diagnosis-resource-link" :href="resource.url" target="_blank" rel="noopener noreferrer"><b class="resource-title">{{ resource.title }}</b><span>{{ resourceType(resource) }} · {{ resource.reason || '关联薄弱概念' }}</span></a></div>
            <div v-else class="sidebar-empty">暂无相关资料。<el-button link type="primary" @click="router.push('/teacher/resources')">添加资源</el-button></div>
          </section>

          <section class="retest-section">
            <h3>针对性复测</h3>
            <p v-if="!plan.ready">{{ plan.reason || '暂无可复测的薄弱概念。' }}</p>
            <div v-if="plan.ready" class="plan-line"><b>{{ plan.chapters?.map((row: any) => row.title).join('、') || '当前薄弱章节' }}</b><span>{{ plan.item_count }} 题</span></div>
            <div v-if="generatedCount" class="workflow-note">已生成 {{ generatedCount }} 道题。</div>
            <div v-if="publishedRetest" class="published-note">复测已发布。</div>
            <el-button class="retest-button" type="primary" :icon="Position" :loading="runningAi" :disabled="!plan.ready" @click="emit('retest')">生成并发布复测</el-button>
            <el-button class="detail-button" plain :disabled="!attempts.length" @click="emit('openAttempt', attempts[attempts.length - 1]?.assessment_version_id)">查看单卷明细</el-button>
          </section>
        </section>
      </aside>
    </section>
  </div>
</template>

<style scoped>
.history-workbench{display:grid;min-width:0;gap:16px;color:#34445f}.history-overview{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(390px,1fr);gap:14px}.metric-strip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.metric-card{min-width:0;padding:16px 14px;border:1px solid #dfe7f2;border-radius:13px;background:#fff;box-shadow:0 4px 16px rgb(32 65 115 / 5%)}.metric-card span,.metric-card small{display:block}.metric-card span{color:#64748a;font-size:11.5px;font-weight:700}.metric-card strong{display:block;margin-top:10px;color:#17243d;font-size:25px}.metric-card em{margin-left:3px;color:#718096;font-size:11px;font-style:normal}.metric-card small{margin-top:7px;color:#8794a6;font-size:9.5px;line-height:1.4}.metric-card.blue{border-top:3px solid #2563eb}.metric-card.violet{border-top:3px solid #7c3aed}.metric-card.cyan{border-top:3px solid #0ea5e9}.metric-card.red{border-top:3px solid #ef5d68}.trend-card{overflow:hidden;border:1px solid #dfe7f2;border-radius:13px;background:#fff;box-shadow:0 4px 16px rgb(32 65 115 / 5%)}.trend-card>header{display:flex;align-items:center;justify-content:space-between;padding:12px 14px 0}.trend-card>header>div{display:flex;align-items:center;gap:7px;color:#20344f}.trend-card>header .el-icon{color:#2563eb}.trend-card>header span{color:#8390a1;font-size:9.5px}.trend-chart{height:190px}.trend-card>footer{padding:0 14px 10px;color:#8a96a7;font-size:9px}.chart-empty{display:grid;height:160px;place-items:center;color:#8b97a8;font-size:11px}.history-body{display:grid;grid-template-columns:minmax(0,7fr) minmax(310px,3fr);align-items:start;gap:16px}.wrong-column{min-width:0}.wrong-toolbar{display:flex;align-items:end;justify-content:space-between;gap:12px;margin:1px 0 10px}.wrong-toolbar>div{display:flex;align-items:baseline;gap:10px}.wrong-toolbar h2{margin:0;color:#17243d;font-size:18px}.wrong-toolbar>div span{color:#7f8c9f;font-size:10.5px}.wrong-toolbar label{font-size:11px}.view-tabs{display:flex;align-items:center;gap:8px;margin-bottom:12px;padding:7px;border:1px solid #e0e7f1;border-radius:11px;background:#fff}.view-tabs button{padding:8px 15px;border:0;border-radius:7px;background:#f5f7fa;color:#53647b;font-size:11px;font-weight:700;cursor:pointer}.view-tabs button.active{background:#2563eb;color:#fff;box-shadow:0 4px 10px rgb(37 99 235 / 18%)}.view-tabs :deep(.el-select){width:220px;margin-left:auto}.wrong-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start;gap:14px}.wrong-card{min-width:0;overflow:hidden;border:1px solid #f2c3c7;border-radius:13px;background:#fff;box-shadow:0 4px 15px rgb(52 69 98 / 5%)}.wrong-card>header{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:9px 12px;background:linear-gradient(90deg,#fff4f5,#fff)}.tags{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.tags b,.tags span{padding:4px 8px;border-radius:6px;font-size:10px}.tags b{background:#ef5d68;color:#fff}.tags b.repeat{background:#d83d49}.tags span{background:#edf3ff;color:#315fc7}.wrong-card>header strong{flex:none;color:#d13c47;font-size:11px}.card-body{padding:12px 13px}.paper-line{margin:0 0 8px;color:#78869a;font-size:10px}.stem{color:#17243d;font-size:13.5px;font-weight:650;line-height:1.72;overflow-wrap:anywhere}.stem :deep(.katex-display){overflow-x:auto}.options-list{display:grid;gap:6px;margin-top:10px}.option-row{display:grid;grid-template-columns:22px minmax(0,1fr) auto;align-items:center;gap:6px;padding:8px 9px;border:1px solid #e5eaf1;border-radius:8px;background:#fff;font-size:11.5px}.option-row.is-correct{border-color:#b9e2cc;background:#edf9f2}.option-row.is-wrong-picked{border-color:#f1c5c8;background:#fff0f1}.correct-note,.wrong-note{padding:2px 5px;border-radius:5px;font-size:9px;font-weight:700}.correct-note{background:#dff3e8;color:#148052}.wrong-note{background:#fbe0e2;color:#c33b46}.answer-compare{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:11px}.answer-compare>div{min-width:0;padding:9px 10px;border-radius:8px;font-size:11.5px;line-height:1.6;overflow-wrap:anywhere}.answer-compare small{display:block;margin-bottom:4px;font-weight:700}.student-answer{background:#fff0f1;color:#912f39}.correct-answer{background:#edf9f2;color:#176847}.score-meta{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:9px;padding:8px 0;border-bottom:1px solid #edf0f5;color:#7d8999;font-size:9.5px}.score-meta span+span{padding-left:8px;border-left:1px solid #e6ebf1}.score-meta b{display:block;margin-top:2px;color:#35475f;font-size:10.5px}.explanation{margin-top:10px;padding:10px 11px;border:1px solid #e4e9f1;border-radius:8px;background:#fbfcfe;color:#4a596e;font-size:11px;line-height:1.65}.explanation h4{margin:0 0 4px;color:#25364f;font-size:11px}.occurrences{margin-top:10px;border-top:1px solid #edf0f5}.occurrences summary{padding-top:9px;color:#315fc7;font-size:10px;font-weight:700;cursor:pointer}.occurrence-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:8px 0;border-bottom:1px dashed #e5eaf1;font-size:9.5px}.occurrence-row>div>*{display:block}.occurrence-row b{color:#33465f}.occurrence-row span{margin-top:2px;color:#7d899a}.empty-card{display:flex;align-items:center;gap:12px;padding:26px;border:1px dashed #cad5e4;border-radius:13px;background:#fff}.empty-card>.el-icon{color:#ea9b34;font-size:25px}.empty-card b,.empty-card span{display:block}.empty-card span{margin-top:4px;color:#7d899b;font-size:11px}.other-wrong,.ungraded-area{margin-top:14px;border:1px solid #dbe5f4;border-radius:12px;background:#fff}.other-wrong>summary,.ungraded-area>summary{padding:13px 15px;color:#2c4160;font-size:11.5px;font-weight:700;cursor:pointer}.compact-list,.ungraded-area{overflow:hidden}.compact-list article,.ungraded-area article{display:grid;grid-template-columns:180px minmax(0,1fr) auto;gap:10px;padding:10px 14px;border-top:1px solid #edf1f5;font-size:10.5px}.compact-list span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.compact-list em{color:#d54853;font-style:normal}.ungraded-area article{grid-template-columns:220px 1fr}.ungraded-area article span{color:#7d899b}.ai-column{min-width:0}.ai-panel{position:sticky;top:14px;overflow:hidden;border:1px solid #dce5f2;border-radius:14px;background:#fff;box-shadow:0 6px 20px rgb(37 67 108 / 7%)}.ai-panel>header{display:flex;align-items:center;gap:10px;padding:15px 16px;border-bottom:1px solid #e7edf5;background:linear-gradient(135deg,#f2f7ff,#fff)}.robot{display:grid;flex:none;width:38px;height:32px;place-items:center;border-radius:9px;background:#2563eb;color:#fff;font-size:11px;font-weight:800}.ai-panel h2{margin:0;color:#17243d;font-size:17px}.ai-panel>header p{margin:2px 0 0;color:#8491a3;font-size:10px}.ai-summary,.ai-pending{margin:12px 14px 0;padding:11px 12px;border:1px solid #d8e4fb;border-radius:9px;background:#f6f9ff;color:#31445f;font-size:11.5px;line-height:1.65}.ai-pending b,.ai-pending span{display:block}.ai-pending span{margin-top:4px;color:#718096;font-size:10px}.sidebar-section{margin:0 14px;padding:14px 0;border-bottom:1px solid #e8edf4}.sidebar-section h3{display:flex;align-items:center;gap:6px;margin:0 0 9px;color:#223650;font-size:13px}.sidebar-section h3>.el-icon{color:#2563eb}.concept-list{display:grid;gap:7px}.concept-list article{padding:9px 10px;border-left:3px solid #5b7df3;border-radius:7px;background:#f7f9fc}.concept-list article>div{display:flex;align-items:center;justify-content:space-between;gap:8px}.concept-list b{color:#263950;font-size:11.5px}.concept-list em{color:#d54853;font-size:9.5px;font-style:normal}.concept-list span,.concept-list small{display:block;margin-top:3px;color:#4970c5;font-size:9.5px}.concept-list small{color:#77869a}.concept-list p{margin:5px 0 0;color:#718097;font-size:10px;line-height:1.5}.advice-list{display:grid;gap:7px;margin:0;padding:0;list-style:none;counter-reset:advice}.advice-list li{position:relative;padding-left:25px;color:#516279;font-size:10.5px;line-height:1.6;counter-increment:advice}.advice-list li::before{position:absolute;top:0;left:0;display:grid;width:18px;height:18px;place-items:center;border-radius:50%;background:#eaf1ff;color:#315cf0;font-size:9px;font-weight:700;content:counter(advice)}.sidebar-empty{margin:0;color:#8591a2;font-size:10.5px;line-height:1.55}.resource-list{display:grid;gap:5px}.resource-list article{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px;border-radius:8px;background:#f7f9fc}.resource-list article>div{min-width:0}.resource-list b,.resource-list span{display:block}.resource-list b{overflow:hidden;color:#34465f;font-size:10.5px;text-overflow:ellipsis;white-space:nowrap}.resource-list span{margin-top:3px;color:#8390a1;font-size:9px;line-height:1.4}.retest-section{padding:14px;background:#f6f9ff}.retest-section h3{margin:0 0 7px;color:#223650;font-size:13px}.retest-section>p{margin:0 0 9px;color:#7b899a;font-size:10px;line-height:1.5}.plan-line{display:flex;justify-content:space-between;gap:8px;margin-bottom:9px;color:#52647d;font-size:10px}.plan-line b{color:#263b5b}.workflow-note,.published-note{margin-bottom:8px;padding:7px 8px;border-radius:6px;background:#eaf2ff;color:#436a9f;font-size:9.5px}.published-note{background:#e8f7ef;color:#197451}.retest-button,.detail-button{width:100%;margin:0}.detail-button{margin-top:7px}.ai-panel>footer{padding:10px 14px;border-top:1px solid #e6ebf3;color:#8995a5;font-size:9px;line-height:1.5}@media(max-width:1320px){.history-overview{grid-template-columns:1fr}.history-body{grid-template-columns:minmax(0,1fr) minmax(290px,.72fr)}.wrong-grid{grid-template-columns:1fr}.metric-strip{grid-template-columns:repeat(4,1fr)}}@media(max-width:930px){.history-body{grid-template-columns:1fr}.ai-panel{position:static}.ai-column{order:2}.metric-strip{grid-template-columns:1fr 1fr}}@media(max-width:640px){.metric-strip{grid-template-columns:1fr}.wrong-toolbar{align-items:flex-start;flex-direction:column}.view-tabs{align-items:stretch;flex-direction:column}.view-tabs :deep(.el-select){width:100%;margin-left:0}.answer-compare,.score-meta,.occurrence-row{grid-template-columns:1fr}.compact-list,.ungraded-area article{grid-template-columns:1fr}.trend-card>header{align-items:flex-start;flex-direction:column}}
.history-workbench{gap:14px}.metric-card{padding:13px 14px;border-color:var(--diag-border,#dfe7f2);border-radius:var(--diag-radius,13px);box-shadow:var(--diag-shadow,0 4px 16px rgb(32 65 115 / 5%))}.metric-card strong{margin-top:7px}.metric-card small{margin-top:5px}.trend-card{border-color:var(--diag-border,#dfe7f2);border-radius:var(--diag-radius,13px);box-shadow:var(--diag-shadow,0 4px 16px rgb(32 65 115 / 5%))}.summary-section{margin:0 14px;padding:13px 0;border-bottom:1px solid #e8edf4}.summary-section h3{margin:0 0 8px;color:#223650;font-size:13px}.summary-section .ai-summary{margin:0}.summary-section small{display:block;margin-top:7px;color:#7d899b;font-size:9.5px;line-height:1.5}.ai-failure{margin:10px 14px 0;padding:8px 9px;border-radius:7px;background:#fff6e6;color:#8a6422;font-size:10px;line-height:1.5}.concept-list article[role=button]{cursor:pointer;transition:.15s}.concept-list article[role=button]:hover,.concept-list article[role=button]:focus{outline:none;background:#eef4ff;box-shadow:0 0 0 2px #dce7ff}.wrong-card{scroll-margin-top:18px}
</style>
