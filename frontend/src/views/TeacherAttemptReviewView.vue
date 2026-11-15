<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import MathText from '../components/MathText.vue'
import SourceQuestionContent from '../components/SourceQuestionContent.vue'
import { formatAnswer, friendlyError, questionTypeLabel } from '../utils/presentation'

const route = useRoute()
const router = useRouter()
const review = ref<any>(null)
const loading = ref(true)
const error = ref('')
const items = computed<any[]>(() => review.value?.items || [])

function numberText(value: unknown): string {
  if (value === null || value === undefined) return '待评分'
  return String(Number(Number(value).toFixed(4)))
}
function elapsedText(value: unknown): string {
  if (value === null || value === undefined) return '用时未记录'
  const seconds = Math.floor(Number(value) / 1000)
  const minutes = Math.floor(seconds / 60)
  return minutes >= 60
    ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}
function submittedText(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date).replaceAll('/', '-')
}
function optionsFor(value: any): any[] {
  const source = value?.options?.length ? value.options : value?.source_options
  if (Array.isArray(source)) return source.map((row: any, index: number) =>
    typeof row === 'object' ? row : { key: String.fromCharCode(65 + index), text: String(row) })
  return Object.entries(source || {}).map(([key, text]) => ({ key, text }))
}
function answerTone(selected: boolean, correct: boolean, comparable: boolean): string {
  if (!comparable) return ''
  if (selected && correct) return 'correct'
  if (correct) return 'missed'
  if (selected) return 'wrong'
  return ''
}
function answerNotes(selected: boolean, correct: boolean, comparable: boolean): string[] {
  if (comparable && selected && correct) return ['学生选择', '参考答案']
  if (comparable && correct) return ['漏选', '参考答案']
  if (comparable && selected) return ['学生错选']
  if (selected) return ['学生选择']
  return correct ? ['参考答案'] : []
}
function optionTone(item: any, key: string): string {
  const selected = item.selection_status === 'reliable' && item.selected_keys?.includes(key)
  const correct = item.reference_available && item.reference_keys?.includes(key)
  return answerTone(Boolean(selected), Boolean(correct),
    item.selection_status === 'reliable' && item.reference_available)
}
function optionNotes(item: any, key: string): string[] {
  return answerNotes(item.selection_status === 'reliable' && item.selected_keys?.includes(key),
    item.reference_available && item.reference_keys?.includes(key),
    item.selection_status === 'reliable' && item.reference_available)
}
function truthTone(item: any, value: boolean): string {
  const selected = item.student_boolean === value
  const correct = item.reference_available && item.reference_boolean === value
  return answerTone(selected, correct, item.student_boolean !== null &&
    item.reference_available && item.reference_boolean !== null)
}
function truthNotes(item: any, value: boolean): string[] {
  return answerNotes(item.student_boolean === value,
    item.reference_available && item.reference_boolean === value,
    item.student_boolean !== null && item.reference_available && item.reference_boolean !== null)
}
function hasReference(item: any) { return item.reference_available && item.answer_reference?.payload }
function responseText(value: any): string {
  if (!value || typeof value !== 'object') return formatAnswer(value)
  if (Array.isArray(value.texts)) return value.texts.map(String).join('；')
  if (typeof value.text === 'string') return value.text
  if (typeof value.raw_text === 'string') return value.raw_text
  return formatAnswer(value)
}

onMounted(async () => {
  try {
    review.value = await api.get(`/teacher/attempts/${String(route.params.attemptId)}/review`)
  } catch (reason) {
    error.value = friendlyError(reason, '答卷读取失败')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main v-loading="loading" class="review-page">
    <header class="review-header">
      <button type="button" class="back-button" @click="router.push('/teacher')">← 返回教师端</button>
      <div><p>教师阅卷</p><h1>查看答卷</h1></div>
    </header>
    <div v-if="error" class="error" role="alert">{{ error }}</div>
    <template v-if="review">
      <section class="overview" aria-label="答卷概览">
        <div><span>成绩</span><strong>{{ numberText(review.score) }} <small>/ {{ numberText(review.max_score) }} 分</small></strong><small v-if="review.score_source === 'source_record'">原始记录成绩</small></div>
        <div><span>答卷用时</span><strong>{{ elapsedText(review.display_elapsed_ms) }}</strong><small v-if="review.elapsed_source === 'historical_display'">历史展示用时</small></div>
        <div><span>题目</span><strong>{{ items.length }} <small>题</small></strong><small v-if="submittedText(review.submitted_at)">{{ submittedText(review.submitted_at) }} 交卷</small></div>
      </section>
      <div class="review-intro">绿色为选对，橙色为漏选，红色为错选；答案待核对的题目不作正误着色。</div>
      <section class="question-list" aria-label="答卷题目">
        <article v-for="item in items" :id="`review-question-${item.position}`" :key="item.paper_item_id" class="review-question">
          <header class="question-head"><div><b>第 {{ item.position }} 题</b><span>{{ questionTypeLabel(item.question_type) }}</span><span v-if="item.is_scoring_revision">修订版（评分依据）</span></div><strong v-if="item.score">{{ numberText(item.score.awarded_score) }} / {{ numberText(item.score.max_score) }} 分</strong><strong v-else>待评分</strong></header>
          <div class="question-body">
            <div class="stem"><MathText v-if="item.is_scoring_revision" :text="item.stem" /><SourceQuestionContent v-else :question="item" :fallback="item.stem" /></div>
            <p v-if="!item.reference_available" class="notice">{{ item.answer_reference?.reason || '参考答案或评分版本待核对，本题不标记正确选项。' }}</p>
            <p v-if="item.selection_status === 'unmapped'" class="notice">已作答，选项对应待核对。</p>
            <div v-if="['single_choice','multi_choice','multiple_choice'].includes(item.question_type)" class="options">
              <div v-for="option in optionsFor(item)" :key="option.key" class="option" :class="optionTone(item, option.key)">
                <b>{{ option.key }}</b><span class="option-text"><MathText v-if="item.is_scoring_revision" :text="option.text || option.value || ''" /><SourceQuestionContent v-else :question="item" part="option" :option-key="option.key" :fallback="option.text || option.value || ''" /></span>
                <span v-if="optionNotes(item, option.key).length" class="option-note">{{ optionNotes(item, option.key).join(' · ') }}</span>
              </div>
            </div>
            <div v-else-if="item.question_type === 'true_false' && item.reference_boolean !== null" class="options">
              <div v-for="choice in [{ value: true, label: '正确' }, { value: false, label: '错误' }]" :key="choice.label" class="option" :class="truthTone(item, choice.value)"><b>{{ choice.label }}</b><span class="option-note">{{ truthNotes(item, choice.value).join(' · ') }}</span></div>
            </div>
            <div v-else class="answer-pair"><div><span>学生作答</span><MathText :text="responseText(item.student_response)" /></div><div><span>{{ item.answer_reference?.authority_label || '参考答案' }}</span><MathText :text="hasReference(item) ? formatAnswer(item.answer_reference.payload) : '待核对'" /></div></div>
            <details v-if="item.selection_status === 'unmapped'" class="detail"><summary>查看原始作答</summary><MathText :text="responseText(item.student_response)" /></details>
            <details v-if="item.answer_reference?.explanation && item.reference_available" class="detail"><summary>查看参考解析</summary><MathText :text="item.answer_reference.explanation" /></details>
            <details v-if="item.original_question" class="detail"><summary>查看原始题目</summary><div class="original-stem"><SourceQuestionContent :question="item.original_question" :fallback="item.original_question.stem" /></div><div v-for="option in optionsFor(item.original_question)" :key="option.key" class="original-option"><b>{{ option.key }}</b><MathText :text="option.text || option.value || ''" /></div></details>
          </div>
        </article>
      </section>
    </template>
  </main>
</template>

<style scoped>
.review-page{max-width:1120px;margin:0 auto;padding:28px 24px 60px;color:#202938}.review-header{display:flex;align-items:center;gap:22px;margin-bottom:20px}.review-header p{margin:0;color:#596579;font-size:14px}.review-header h1{margin:3px 0 0;font-size:24px;font-weight:600}.back-button{padding:9px 12px;border:1px solid #dfe4eb;border-radius:8px;background:#fff;color:#3159c9;cursor:pointer}.overview{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:19px 22px;border:1px solid #e1e6ed;border-radius:12px;background:#fff}.overview>div{display:grid;align-content:start;gap:5px}.overview span{color:#596579;font-size:14px}.overview strong{font-size:25px;font-weight:600;font-variant-numeric:tabular-nums}.overview small{color:#8791a0;font-size:14px;font-weight:400}.review-intro{margin:15px 0 12px;color:#596579;font-size:14px}.question-list{display:grid;gap:14px}.review-question{overflow:hidden;border:1px solid #e1e6ed;border-radius:12px;background:#fff}.question-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 18px;border-bottom:1px solid #edf0f4}.question-head>div{display:flex;align-items:center;flex-wrap:wrap;gap:12px}.question-head b{font-size:15px}.question-head span{color:#758195;font-size:14px}.question-head strong{font-size:15px;font-weight:600;white-space:nowrap}.question-body{padding:18px}.stem{margin-bottom:17px;font-size:16px;font-weight:600;line-height:1.6;overflow-wrap:anywhere}.options{display:grid;gap:8px}.option{display:grid;grid-template-columns:24px minmax(0,1fr) auto;align-items:start;gap:10px;min-height:46px;padding:11px 13px;border:1px solid #e6e8ec;border-radius:8px;font-size:14px;line-height:1.6}.option>b{color:#34445f}.option-text{min-width:0;overflow-wrap:anywhere}.option-note{color:#596579;font-size:14px;white-space:nowrap}.option.correct{border-color:#9cd9b5;background:#f1f8f3}.option.correct .option-note{color:#28855a}.option.wrong{border-color:#e8a7ad;background:#fff5f5}.option.wrong .option-note{color:#c94e56}.option.selected{border-color:#b9c9e8;background:#f5f7fc}.notice{margin:0 0 12px;color:#966a25;font-size:14px;line-height:1.5}.answer-pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}.answer-pair>div{min-width:0;padding:12px;border:1px solid #e6e8ec;border-radius:8px;line-height:1.6;overflow-wrap:anywhere}.answer-pair span{display:block;margin-bottom:5px;color:#596579;font-size:14px}.detail{margin-top:15px;padding-top:11px;border-top:1px solid #edf0f4;color:#48576b;font-size:14px;line-height:1.6}.detail summary{width:max-content;max-width:100%;color:#3159c9;cursor:pointer}.detail :deep(.katex-display){max-width:100%;overflow-x:auto}.original-stem{margin:11px 0 8px}.original-option{display:grid;grid-template-columns:22px 1fr;gap:8px;margin:5px 0}.error{padding:12px;border:1px solid #e8a7ad;border-radius:8px;color:#b33e47}@media(max-width:700px){.review-page{padding:18px 14px 40px}.overview{grid-template-columns:1fr 1fr;padding:15px}.question-body{padding:14px}.option{grid-template-columns:20px 1fr}.option-note{grid-column:2;white-space:normal}.answer-pair{grid-template-columns:1fr}}
.option.missed{border-color:#e9c88f;background:#fff8eb}.option.missed .option-note{color:#9a5a11}
</style>
