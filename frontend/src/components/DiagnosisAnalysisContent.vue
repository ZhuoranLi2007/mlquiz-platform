<script setup lang="ts">
import { computed, ref } from 'vue'
import AnalysisRichText from './AnalysisRichText.vue'

const props = defineProps<{
  analysis?: Record<string, any>
  scope: 'student_attempt' | 'student_history' | 'class_assessment' | 'student_feedback'
  locatable?: boolean
}>()
const emit = defineEmits<{ locate: [row: any] }>()

const expanded = ref(false)
const allConcepts = computed(() => props.analysis?.concepts || props.analysis?.concept_hypotheses || [])
const concepts = computed(() => expanded.value ? allConcepts.value : allConcepts.value.slice(0, 3))
const plan = computed(() => (props.analysis?.learning_plan || []).slice(0, expanded.value ? undefined : 3))
const recommendations = computed(() => (props.analysis?.recommendations || []).slice(0, 3))
const evidence = computed(() => (props.analysis?.evidence_details || []).slice(0, 6))
const retestText = computed(() => String(props.analysis?.retest_summary?.text || ''))
const classMode = computed(() => props.scope === 'class_assessment')

function refs(row: any) {
  if (Array.isArray(row.question_refs) && row.question_refs.length) return row.question_refs.join('、')
  return (row.question_positions || []).map((position: number) => `第${position}题`).join('、')
}
</script>

<template>
  <div class="diagnosis-analysis-content">
    <section class="analysis-section">
      <h3>综合结论</h3>
      <p v-if="analysis?.context?.label" class="analysis-period">{{ analysis.context.label }}</p>
      <p class="analysis-summary"><AnalysisRichText :text="analysis?.summary || '当前作答信息不足，暂时无法形成分析。'" /></p>
    </section>

    <section class="analysis-section">
      <h3>{{ classMode ? '共性问题' : '薄弱概念' }}</h3>
      <div v-if="concepts.length" class="analysis-concepts">
        <component :is="locatable ? 'button' : 'div'" v-for="(row, index) in concepts"
          :key="`${row.name}-${index}`" :type="locatable ? 'button' : undefined"
          class="analysis-concept" @click="locatable && emit('locate', row)">
          <b><span v-if="row.priority" class="priority-number">{{ row.priority }}</span>{{ row.name || row.concept }}</b>
          <small>{{ row.chapter ? `${row.chapter} · ` : '' }}{{ refs(row) }}</small>
          <small v-if="row.priority_reason" class="priority-reason">{{ row.priority_reason }}</small>
          <p v-if="row.observation"><strong>作答依据：</strong><AnalysisRichText :text="row.observation" /></p>
          <p v-if="row.analysis"><strong>{{ row.analysis_source === 'model' ? 'AI 解读：' : '复习重点：' }}</strong><AnalysisRichText :text="row.analysis" /></p>
        </component>
      </div>
      <p v-else class="analysis-empty">{{ classMode ? '本次没有可归纳的班级共性错题。' : '当前没有明确的薄弱概念。' }}</p>
      <button v-if="allConcepts.length > 3" type="button" class="show-more" @click="expanded=!expanded">{{ expanded ? '收起其他重点' : `查看其余 ${allConcepts.length - 3} 个重点` }}</button>
    </section>

    <section v-if="plan.length" class="analysis-section">
      <h3>{{ classMode ? '按优先级安排讲评' : '按顺序复习与自测' }}</h3>
      <ol class="analysis-advice study-plan"><li v-for="row in plan" :key="row.kp_id || row.name">
        <b>{{ row.name }}</b>
        <p><AnalysisRichText :text="row.action" /></p>
        <p class="success-check"><strong>完成标准：</strong><AnalysisRichText :text="row.success_check" /></p>
      </li></ol>

    </section>
    <section v-else-if="recommendations.length" class="analysis-section">
      <h3>{{ classMode ? '教学建议' : '学习建议' }}</h3>
      <ol class="analysis-advice">
        <li v-for="(row, index) in recommendations" :key="index"><AnalysisRichText :text="String(row)" /></li>
      </ol>
    </section>

    <section v-if="retestText" class="analysis-section">
      <h3>复测结论</h3>
      <p class="analysis-retest"><AnalysisRichText :text="retestText" /></p>
    </section>

    <details v-if="evidence.length" class="analysis-evidence">
      <summary>查看题目依据</summary>
      <ul><li v-for="(row, index) in evidence" :key="index"><b>{{ row.ref }}</b> {{ row.observation }}</li></ul>
    </details>
  </div>
</template>

<style scoped>
.diagnosis-analysis-content{min-width:0;color:#43546c}
.analysis-section{margin:0 14px;padding:14px 0;border-bottom:1px solid #e8edf4}
.analysis-section h3{margin:0 0 9px;color:#223650;font-size:16px;font-weight:600;line-height:1.4}
.analysis-summary,.analysis-retest,.analysis-concept p{margin:0;font-size:14px;font-weight:400;line-height:1.6}
.analysis-concepts{display:grid;gap:14px}
.analysis-concept{display:block;min-width:0;width:100%;padding:0;border:0;background:transparent;color:inherit;text-align:left}
.analysis-concept:is(button){cursor:pointer}
.analysis-concept:is(button):hover b{color:#315fc0}
.analysis-concept:is(button):focus-visible{outline:2px solid #315fc0;outline-offset:3px;border-radius:3px}
.analysis-concept b{display:block;color:#263950;font-size:14px;font-weight:600;line-height:1.45}
.analysis-concept small{display:block;margin:3px 0 5px;color:#718096;font-size:14px;font-weight:400;line-height:1.45}
.analysis-concept p+p{margin-top:3px}
.analysis-advice{display:grid;gap:8px;margin:0;padding-left:21px}
.analysis-advice li{padding-left:2px;font-size:14px;font-weight:400;line-height:1.6}
.analysis-empty{margin:0;color:#8793a3;font-size:14px;line-height:1.55}
.analysis-evidence{margin:0 14px;padding:12px 0;color:#596982;font-size:14px}
.analysis-evidence summary{cursor:pointer;color:#315fc0}
.analysis-evidence ul{display:grid;gap:6px;margin:9px 0 0;padding-left:18px;line-height:1.55}
.analysis-evidence b{font-weight:600}
.analysis-period{font-size:12px;color:#64748b;margin:0 0 9px;line-height:1.6}
.priority-number{display:inline-block;margin-right:6px;color:#315fc0}.priority-reason{color:#80531c!important}
.analysis-concept strong{font-weight:600}.show-more{border:0;background:none;color:#315fc0;padding:12px 0 0;cursor:pointer}
.study-plan small{display:block;color:#64748b;margin:3px 0}.study-plan p{margin:6px 0}.success-check{color:#365b49}.study-time-note{color:#64748b;font-size:12px;line-height:1.6;margin:12px 0 0}
</style>
