<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Collection, DocumentChecked, Position, Trophy, TrendCharts, WarningFilled } from '@element-plus/icons-vue'
import AttemptEvidenceTable from './AttemptEvidenceTable.vue'
import TargetedRetestHistory from './TargetedRetestHistory.vue'
import DiagnosisAnalysisContent from './DiagnosisAnalysisContent.vue'
import { recommendedResourceLinks } from '../utils/resourceLinks'
import { analysisNotice } from '../utils/analysisNotice'

const props = defineProps<{ result: any; diagnosisReady: boolean; diagnosisMeta?: any; intervention?: any; loading?: boolean; runningAi?: boolean; studentLabel?: string; assessmentLabel?: string }>()
const emit = defineEmits<{ run: []; retest: [] }>()
const router = useRouter()
const showAudit = ref(false)
const showCompare = ref(false)
const summary = computed(() => props.result?.score_summary || {})
const comparison = computed(() => props.result?.class_comparison || {})
const analysis = computed(() => props.result?.ai_analysis || {})
const plan = computed(() => props.result?.retest_plan || {})
const workflowResources = computed(() => props.intervention?.outputs?.recommendations?.result?.recommendations || [])
const resources = computed(() => recommendedResourceLinks(workflowResources.value, props.result?.resources || []))
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
const aiFailure = computed(() => analysisNotice(analysis.value))

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
</script>

<template>
  <div v-if="result" class="single-workbench">
    <section class="result-overview" aria-label="本次诊断结果">
      <header class="result-heading">
        <h2>{{ studentLabel || '当前学生' }} · {{ assessmentLabel || '当前试卷' }}</h2>
        <div class="result-links">
          <button v-if="result.historical_reported_result || result.regraded_result || diagnosisMeta" type="button" :aria-expanded="showAudit" @click="showAudit = !showAudit">{{ showAudit ? '收起评分详情' : '查看评分详情' }}</button>
          <button type="button" :aria-expanded="showCompare" @click="showCompare = !showCompare">{{ showCompare ? '收起班级对照' : '查看班级对照' }}</button>
        </div>
      </header>
      <div v-if="showAudit" class="audit-detail">
        <p v-if="diagnosisMeta">最近分析时间：{{ diagnosisMeta.created_at }}</p>
        <p>原成绩：{{ numberText(result.historical_reported_result?.score) }}；当前评分：{{ numberText(result.regraded_result?.score) }}/{{ numberText(result.regraded_result?.max_score) }}。</p>
      </div>
      <div class="diagnosis-stats">
        <article class="diagnosis-stat"><el-icon><Trophy /></el-icon><div><span>本次成绩</span><strong>{{ scoreText(summary.awarded_score, summary.max_score) }}</strong><small>{{ Number(summary.max_score) > 0 ? `得分率 ${percent(summary.score_rate)}` : '历史满分缺失，不计算得分率' }}</small></div></article>
        <article class="diagnosis-stat"><el-icon><TrendCharts /></el-icon><div><span>班级排名</span><strong>{{ rankText() }}<small v-if="comparison.status === 'available'">/ {{ comparison.participant_count }} 人</small></strong><small>{{ comparison.status === 'available' ? '与同卷学生比较' : comparison.reason }}</small></div></article>
        <article class="diagnosis-stat"><el-icon><WarningFilled /></el-icon><div><span>错题数量</span><strong>{{ summary.wrong_items ?? 0 }}<small>/ {{ summary.scored_items ?? 0 }} 题</small></strong><small>{{ summary.ungraded_item_count ? `另有 ${summary.ungraded_item_count} 题待评分` : `已评分 ${summary.scored_items ?? 0} 题` }}</small></div></article>
        <article class="diagnosis-stat"><el-icon><DocumentChecked /></el-icon><div><span>参考情况</span><strong>{{ comparison.attendee_count ?? 0 }}<small>/ {{ comparison.class_size ?? 0 }} 人</small></strong><small>实际参加 / 正式班人数</small></div></article>
      </div>
      <div v-if="showCompare" class="class-compare">
        <div class="compare-stats"><div><span>学生得分</span><b>{{ numberText(comparison.selected_score) }}/{{ numberText(comparison.max_score) }}</b></div><div><span>班级平均分</span><b>{{ numberText(comparison.average_score) }}</b></div><div><span>班级最高分</span><b>{{ numberText(comparison.highest_score) }}</b></div><div><span>正式排名</span><b>{{ rankText() }}</b></div></div>
        <div class="question-rates"><span v-for="row in comparison.question_correct_rates || []" :key="row.paper_item_id">第{{ row.position }}题 <b>{{ percent(row.correct_rate) }}</b><small>{{ row.correct_count }}/{{ row.denominator }}</small></span></div>
      </div>
    </section>

    <section class="diagnosis-body">
      <div class="evidence-column">
        <section class="answers-panel" aria-label="作答诊断">
          <header><h2>作答诊断</h2></header>
          <AttemptEvidenceTable :result="result" section="problems" />
          <AttemptEvidenceTable :result="result" section="correct" />
        </section>
        <TargetedRetestHistory :history="retestHistory" :original-summary="summary" :empty-text="plan.ready ? '诊断确认复测目标后，可从右侧生成并发布针对性新题。' : (plan.reason || '当前没有适合生成复测的错题。')" />
      </div>

      <aside class="insight-sidebar">
        <section class="ai-panel">
          <header><h2>学情分析</h2></header>
          <DiagnosisAnalysisContent :analysis="analysis" scope="student_attempt" />
          <p v-if="aiFailure" class="ai-failure">{{ aiFailure }}</p>

          <div class="sidebar-section resource-section">
            <h3><el-icon><Collection /></el-icon>推荐学习资源</h3>
            <div v-if="resources.length" class="resource-list">
              <a v-for="resource in resources" :key="resource.resource_id || resource.url || resource.title"
                class="diagnosis-resource-link" :href="resource.url" target="_blank" rel="noopener noreferrer">
                <b class="resource-title">{{ resource.title }}</b>
                <span>{{ resourceType(resource) }} · {{ resource.reason || '关联当前错题概念' }}</span>
              </a>
            </div>
            <div v-else class="sidebar-empty">暂无相关资料。<button type="button" class="resource-action" @click="router.push('/teacher/resources')">添加资源</button></div>
          </div>

          <div class="retest-section">
            <h3>复测建议</h3>
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

<style scoped src="../styles/singleAttemptWorkbench.css"></style>
