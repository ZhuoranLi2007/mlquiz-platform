<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { Collection, DocumentChecked, Position, Trophy, TrendCharts, WarningFilled } from '@element-plus/icons-vue'
import AttemptEvidenceTable from './AttemptEvidenceTable.vue'
import TargetedRetestHistory from './TargetedRetestHistory.vue'

const props = defineProps<{ result: any; diagnosisReady: boolean; diagnosisMeta?: any; intervention?: any; loading?: boolean; runningAi?: boolean }>()
const emit = defineEmits<{ run: []; retest: [] }>()
const router = useRouter()
const summary = computed(() => props.result?.score_summary || {})
const comparison = computed(() => props.result?.class_comparison || {})
const analysis = computed(() => props.result?.ai_analysis || {})
const plan = computed(() => props.result?.retest_plan || {})
const workflowResources = computed(() => props.intervention?.outputs?.recommendations?.result?.recommendations || [])
const resources = computed(() => workflowResources.value.length ? workflowResources.value : (props.result?.resources || []))
const generatedCandidates = computed(() => props.intervention?.outputs?.generation?.result?.candidates || [])
const publishedRetest = computed(() => props.intervention?.outputs?.retest?.status === 'published')
const retestFailure = computed(() => publishedRetest.value ? '' : (props.intervention?.errors || []).join('；'))
const retestHistory = computed(() => props.result?.retest_history || [])
const latestRetest = computed(() => retestHistory.value[retestHistory.value.length - 1] || null)
const retestButtonLabel = computed(() => {
  if (latestRetest.value?.status === 'not_passed') return '再次生成复测'
  if (latestRetest.value?.status === 'passed') return '主动再次复测'
  return '生成并发布复测'
})
const conceptHypotheses = computed(() => (analysis.value?.concept_hypotheses || []).filter((row: any) => row?.name || row?.concept))
const recommendations = computed(() => (analysis.value?.recommendations || []).map((row: any) => typeof row === 'string' ? row : (row?.message || row?.advice || row?.action)).filter(Boolean).slice(0, 4))
const aiFailure = computed(() => analysis.value?.model_status
  ? 'AI 服务暂时不可用，当前显示基础分析。可点击页面顶部“运行诊断”重试。' : '')

function percent(value: unknown) { return value === null || value === undefined ? '—' : `${Math.round(Number(value) * 100)}%` }
function numberText(value: unknown) {
  if (value === null || value === undefined) return '—'
  const number = Number(value); return Number.isInteger(number) ? String(number) : number.toFixed(1)
}
function scoreText(score: unknown, maxScore: unknown) {
  return Number(maxScore) > 0
    ? `${numberText(score)}/${numberText(maxScore)}`
    : `${numberText(score)} 分（历史满分缺失）`
}
function evidenceText(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => String(item || '').trim()).filter(Boolean).join('；')
  if (typeof value !== 'string') return value ? String(value) : ''
  const text = value.trim()
  if (!text.startsWith('[')) return text
  try {
    const parsed = JSON.parse(text)
    return Array.isArray(parsed) ? parsed.map((item) => String(item || '').trim()).filter(Boolean).join('；') : text
  } catch { return text }
}
function rankText() { return comparison.value.status === 'available' ? `第 ${comparison.value.rank} 名` : '暂无排名' }
function typeLabel(type: string) {
  const labels: Record<string, string> = { single_choice: '单选题', multiple_choice: '多选题', multi_choice: '多选题', true_false: '判断题', fill_blank: '填空题', numeric: '数值题', short_answer: '简答题', term: '名词解释' }
  return labels[type] || type || '自动匹配题型'
}
function resourceType(resource: any) {
  const value = String(resource.content_type || resource.source_type || resource.platform || '').toLowerCase()
  if (value.includes('video') || value.includes('bilibili')) return '视频'
  if (value.includes('ppt') || value.includes('slide')) return '课件'
  if (value.includes('article')) return '文章'
  return resource.platform || '学习资料'
}
function openResource(resource: any) { if (resource?.url) window.open(resource.url, '_blank', 'noopener,noreferrer') }
function positions(row: any) { return (row?.question_positions || []).map((n: any) => `第${n}题`).join('、') || '未定位题号' }
</script>

<template>
  <div v-if="result" class="single-workbench">
    <details v-if="result.historical_reported_result || result.regraded_result || diagnosisMeta" class="audit-detail">
      <summary>查看评分详情</summary>
      <p v-if="diagnosisMeta">最近分析时间：{{ diagnosisMeta.created_at }}</p>
      <p>原成绩：{{ numberText(result.historical_reported_result?.score) }}；当前评分：{{ numberText(result.regraded_result?.score) }}/{{ numberText(result.regraded_result?.max_score) }}。</p>
    </details>

    <section class="metrics-grid">
      <article class="metric-card score"><el-icon><Trophy /></el-icon><div><span>本次成绩</span><strong>{{ scoreText(summary.awarded_score, summary.max_score) }}</strong><p>{{ Number(summary.max_score) > 0 ? `得分率 ${percent(summary.score_rate)}` : '历史满分缺失，不计算得分率' }}</p></div></article>
      <article class="metric-card rank"><el-icon><TrendCharts /></el-icon><div><span>班级排名</span><strong>{{ rankText() }}<small v-if="comparison.status === 'available'">/{{ comparison.participant_count }} 人</small></strong><p>{{ comparison.status === 'available' ? '与同卷学生比较' : comparison.reason }}</p></div></article>
      <article class="metric-card wrong"><el-icon><WarningFilled /></el-icon><div><span>错题数量</span><strong>{{ summary.wrong_items ?? 0 }}<small>/{{ summary.scored_items ?? 0 }} 题</small></strong><p>{{ summary.ungraded_item_count ? `另有 ${summary.ungraded_item_count} 题待评分` : `已评分 ${summary.scored_items ?? 0} 题` }}</p></div></article>
      <article class="metric-card attend"><el-icon><DocumentChecked /></el-icon><div><span>参考情况</span><strong>{{ comparison.attendee_count ?? 0 }}<small>/{{ comparison.class_size ?? 0 }} 人</small></strong><p>实际参加 / 正式班人数</p></div></article>
    </section>

    <details class="class-compare">
      <summary><span><el-icon><TrendCharts /></el-icon><b>查看班级对照</b></span></summary>
      <div class="compare-stats"><div><span>学生得分</span><b>{{ numberText(comparison.selected_score) }}/{{ numberText(comparison.max_score) }}</b></div><div><span>班级平均分</span><b>{{ numberText(comparison.average_score) }}</b></div><div><span>班级最高分</span><b>{{ numberText(comparison.highest_score) }}</b></div><div><span>正式排名</span><b>{{ rankText() }}</b></div></div>
      <div class="question-rates"><span v-for="row in comparison.question_correct_rates || []" :key="row.paper_item_id">第{{ row.position }}题 <b>{{ percent(row.correct_rate) }}</b><small>{{ row.correct_count }}/{{ row.denominator }}</small></span></div>
    </details>

    <section class="diagnosis-body">
      <div class="evidence-column">
        <AttemptEvidenceTable :result="result" section="problems" />
        <AttemptEvidenceTable :result="result" section="correct" />
        <TargetedRetestHistory :history="retestHistory" :original-summary="summary" />
      </div>

      <aside class="insight-sidebar">
        <section class="ai-panel">
          <header><div><h2>学情分析</h2></div></header>
          <section class="summary-section"><h3>综合结论</h3><p class="ai-summary">{{ analysis.summary || '当前答卷信息不足，暂时无法形成分析。' }}</p></section>
          <div v-if="latestRetest" :class="['mastery-status', latestRetest.status]">
            <b>{{ latestRetest.status === 'passed' ? '复测已达标' : latestRetest.status === 'not_passed' ? '尚未达标' : latestRetest.status === 'scoring_pending' ? '等待完整评分' : '复测进行中' }}</b>
            <span v-if="latestRetest.result_determinable">第{{ latestRetest.round }}轮 · {{ latestRetest.correct_count }}/{{ latestRetest.question_count }}题 · {{ percent(latestRetest.accuracy) }}</span>
            <span v-else>第{{ latestRetest.round }}轮 · {{ latestRetest.question_count }}题</span>
          </div>
          <p v-if="aiFailure" class="ai-failure">{{ aiFailure }}</p>

          <div class="sidebar-section">
            <h3>薄弱概念</h3>
            <div v-if="conceptHypotheses.length" class="concept-list">
              <article v-for="(row, index) in conceptHypotheses" :key="index">
                <b>{{ row.name || row.concept }}</b>
                <span>{{ row.chapter ? `${row.chapter} · ` : '' }}{{ positions(row) }}</span>
                <p v-if="row.evidence || row.hypothesis">{{ evidenceText(row.evidence || row.hypothesis) }}</p>
              </article>
            </div>
            <p v-else class="sidebar-empty">本次没有需要重点提示的薄弱概念。</p>
          </div>

          <div class="sidebar-section">
            <h3>学习建议</h3>
            <ol v-if="recommendations.length" class="advice-list"><li v-for="(row, index) in recommendations" :key="index">{{ row }}</li></ol>
            <p v-else class="sidebar-empty">暂无需要特别提示的学习建议。</p>
          </div>

          <div class="sidebar-section resource-section">
            <h3><el-icon><Collection /></el-icon>推荐学习资源</h3>
            <div v-if="resources.length" class="resource-list">
              <article v-for="resource in resources.slice(0, 3)" :key="resource.resource_id || resource.url || resource.title">
                <div><b>{{ resource.title }}</b><span>{{ resourceType(resource) }} · {{ resource.reason || '关联当前错题概念' }}</span></div>
                <el-button link type="primary" :disabled="!resource.url" @click="openResource(resource)">查看</el-button>
              </article>
            </div>
            <div v-else class="sidebar-empty">暂无相关资料。<el-button link type="primary" @click="router.push('/teacher/resources')">添加资源</el-button></div>
          </div>

          <div class="retest-section">
            <h3>针对性复测</h3>
            <div v-if="plan.ready" class="plan-summary"><span>{{ plan.chapters?.map((item: any) => item.title).join('、') || '当前错题章节' }}</span><span>{{ plan.item_count }} 题 · {{ plan.suggested_types?.map(typeLabel).join('、') || '自动匹配题型' }}</span></div>
            <p v-else>{{ plan.reason || '当前没有适合生成复测的错题。' }}</p>
            <div v-if="generatedCandidates.length" class="candidate-note">已生成 {{ generatedCandidates.length }} 道候选题，校验通过后发布。</div>
            <div v-if="publishedRetest" class="published-note">复测已发布，提交后将回写章节表现。</div>
            <div v-else-if="retestFailure" class="validation-note">{{ retestFailure }}</div>
            <el-button class="retest-button" type="primary" :plain="latestRetest?.status === 'passed'" :icon="Position" :loading="runningAi" :disabled="!plan.ready" @click="emit('retest')">{{ retestButtonLabel }}</el-button>
          </div>
        </section>
      </aside>
    </section>
  </div>
</template>

<style scoped>
.single-workbench{display:grid;min-width:0;gap:14px;color:#34445f}.audit-detail{padding:9px 13px;border:1px solid var(--diag-border,#dfe6f0);border-radius:10px;background:#f8faff;font-size:11px}.audit-detail summary{cursor:pointer;color:#58708f;font-weight:700}.audit-detail p{margin:7px 0 0;color:#728096}.metrics-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric-card{display:flex;min-width:0;align-items:center;gap:11px;padding:13px 14px;border:1px solid var(--diag-border,#dfe7f2);border-radius:var(--diag-radius,13px);background:#fff;box-shadow:var(--diag-shadow,0 3px 13px rgb(40 69 112 / 4%))}.metric-card>.el-icon{display:grid;flex:none;width:42px;height:42px;place-items:center;border-radius:11px;font-size:21px}.metric-card.score>.el-icon{background:#eaf2ff;color:#315cf0}.metric-card.rank>.el-icon{background:#f1edff;color:#7256dc}.metric-card.wrong>.el-icon{background:#fff0f1;color:#df4a55}.metric-card.attend>.el-icon{background:#eaf8f7;color:#15877f}.metric-card div{min-width:0}.metric-card span{display:block;color:#64748a;font-size:11.5px;font-weight:650}.metric-card strong{display:block;margin-top:2px;color:#17243d;font-size:23px;white-space:nowrap}.metric-card strong small{margin-left:3px;color:#68778d;font-size:12px}.metric-card p{margin:3px 0 0;color:#8290a3;font-size:10px;line-height:1.35;display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:2}.class-compare{border:1px solid var(--diag-border,#dfe7f3);border-radius:var(--diag-radius,12px);background:#fff}.class-compare summary{display:flex;align-items:center;justify-content:space-between;padding:11px 14px;cursor:pointer}.class-compare summary span{display:flex;align-items:center;gap:8px;color:#2f4260}.compare-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;padding:0 15px 10px}.compare-stats div{padding:10px;border-radius:9px;background:#f5f8fc}.compare-stats span,.compare-stats b{display:block}.compare-stats span{color:#7a879a;font-size:10px}.compare-stats b{margin-top:3px;color:#21344f;font-size:16px}.question-rates{display:flex;flex-wrap:wrap;gap:7px;padding:0 15px 14px}.question-rates span{padding:6px 9px;border-radius:7px;background:#eef4ff;color:#48617f;font-size:10.5px}.question-rates b{margin-left:4px;color:#245fd3}.question-rates small{margin-left:4px;color:#8996a7}
.diagnosis-body{display:grid;grid-template-columns:minmax(0,7fr) minmax(300px,3fr);align-items:start;gap:15px;min-width:0}.evidence-column{display:grid;gap:14px;min-width:0;container-type:inline-size}.insight-sidebar{min-width:0}.ai-panel{position:sticky;top:15px;overflow:hidden;border:1px solid var(--diag-border,#dce5f2);border-radius:14px;background:#fff;box-shadow:0 6px 20px rgb(37 67 108 / 7%)}.ai-panel>header{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid #e7edf5;background:linear-gradient(135deg,#f2f7ff,#fff)}.robot{display:grid;flex:none;width:36px;height:31px;place-items:center;border-radius:9px;background:#315cf0;color:#fff;font-size:11px;font-weight:800;box-shadow:0 4px 10px rgb(49 92 240 / 20%)}.ai-panel h2{margin:0;color:#17243d;font-size:17px}.summary-section{margin:0 14px;padding:13px 0;border-bottom:1px solid #e8edf4}.summary-section h3,.retest-section h3{margin:0 0 8px;color:#223650;font-size:13px}.ai-summary{margin:0;padding:10px 11px;border:1px solid #d8e4fb;border-radius:9px;background:#f6f9ff;color:#31445f;font-size:12px;line-height:1.65}.summary-section small{display:block;margin-top:7px;color:#7d899b;font-size:9.5px;line-height:1.5}.ai-failure{margin:10px 14px 0;padding:8px 9px;border-radius:7px;background:#fff6e6;color:#8a6422;font-size:10px;line-height:1.5}.mastery-status{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:10px 14px 0;padding:8px 10px;border-radius:8px;background:#eef4ff;color:#315fca;font-size:10px}.mastery-status.passed{background:#e8f7ef;color:#14794f}.mastery-status.not_passed{background:#ffedef;color:#c33b46}.mastery-status span{font-size:9px}.sidebar-section{margin:0 14px;padding:13px 0;border-bottom:1px solid #e8edf4}.sidebar-section h3{display:flex;align-items:center;gap:6px;margin:0 0 9px;color:#223650;font-size:13px}.sidebar-section h3>.el-icon{color:#315cf0}.concept-list{display:grid;gap:7px}.concept-list article{padding:9px 10px;border-left:3px solid #5b7df3;border-radius:7px;background:#f7f9fc}.concept-list b,.concept-list span{display:block}.concept-list b{color:#263950;font-size:11.5px}.concept-list span{margin-top:3px;color:#4970c5;font-size:10px}.concept-list p{margin:5px 0 0;color:#718097;font-size:10.5px;line-height:1.5}.advice-list{display:grid;gap:7px;margin:0;padding:0;list-style:none;counter-reset:advice}.advice-list li{position:relative;padding-left:25px;color:#516279;font-size:11px;line-height:1.6;counter-increment:advice}.advice-list li::before{position:absolute;top:0;left:0;display:grid;width:18px;height:18px;place-items:center;border-radius:50%;background:#eaf1ff;color:#315cf0;font-size:9px;font-weight:700;content:counter(advice)}.sidebar-empty{margin:0;color:#8591a2;font-size:10.5px;line-height:1.55}.resource-list{display:grid;gap:5px}.resource-list article{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px;border-radius:8px;background:#f7f9fc}.resource-list article>div{min-width:0}.resource-list b,.resource-list span{display:block}.resource-list b{overflow:hidden;color:#34465f;font-size:11px;text-overflow:ellipsis;white-space:nowrap}.resource-list span{margin-top:3px;color:#8390a1;font-size:9.5px;line-height:1.4}.resource-list .el-button{flex:none}.retest-section{padding:14px;background:#f6f9ff}.plan-summary{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:9px}.plan-summary span{color:#52647d;font-size:10px;line-height:1.45}.plan-summary span:first-child{color:#263b5b;font-weight:700}.retest-section>p{margin:0 0 9px;color:#7b899a;font-size:10.5px}.candidate-note,.published-note{margin-bottom:8px;padding:7px 8px;border-radius:6px;background:#eaf2ff;color:#436a9f;font-size:9.5px}.published-note{background:#e8f7ef;color:#197451}.retest-button{width:100%}@media(max-width:1320px){.diagnosis-body{grid-template-columns:minmax(0,1fr) minmax(290px,.72fr)}.metrics-grid{grid-template-columns:1fr 1fr}}@media(max-width:900px){.diagnosis-body{grid-template-columns:1fr}.ai-panel{position:static}.insight-sidebar{order:2}}@media(max-width:700px){.metrics-grid,.compare-stats{grid-template-columns:1fr}.class-compare summary{align-items:flex-start;flex-direction:column}.question-rates{display:grid;grid-template-columns:1fr 1fr}}
.validation-note{margin-bottom:8px;padding:7px 8px;border-radius:6px;background:#fff4e5;color:#906421;font-size:9.5px;line-height:1.45}
</style>
