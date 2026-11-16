<script setup lang="ts">
import MathText from './MathText.vue'
import AuthenticatedImage from './AuthenticatedImage.vue'
import SourceQuestionContent from './SourceQuestionContent.vue'
import { questionTypeLabel } from '../utils/presentation'
import {
  choiceMark, mathReadyAnswer, readableAnswer, referenceAnswer, studentAnswer, truthMark,
  type ReviewItem,
} from '../utils/studentAttemptReview'

const props = defineProps<{ item: ReviewItem }>()
const item = props.item
function numberText(value: unknown) {
  return value == null ? '—' : String(Number(Number(value).toFixed(4)))
}
function optionsFor(value: any): Array<{ key: string; text: string; review_media?: any[] }> {
  const source = value?.options?.length ? value.options : value?.source_options
  if (Array.isArray(source)) return source.map((entry: any, index: number) =>
    typeof entry === 'object' && entry !== null
      ? { key: String(entry.key || String.fromCharCode(65 + index)), text: String(entry.text || entry.value || ''), review_media: entry.review_media || [] }
      : { key: String.fromCharCode(65 + index), text: String(entry) })
  return Object.entries(source || {}).map(([key, text]) => ({ key, text: String(text || '') }))
}
function rubricText(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(readableAnswer).filter(x => x !== '未作答').join('；')
  if (value && typeof value === 'object') {
    const row = value as Record<string, unknown>
    return rubricText(row.points || row.criteria || row.items)
  }
  return ''
}
</script>

<template>
  <article :id="`result-question-${item.position}`" class="result-question">
    <header class="question-head">
      <div><b>第 {{ item.position }} 题</b><span>{{ questionTypeLabel(item.question_type) }}</span><span v-if="item.is_scoring_revision">修订版（评分依据）</span></div>
      <strong v-if="item.score">{{ numberText(item.score.awarded_score) }} / {{ numberText(item.score.max_score) }} 分</strong>
      <strong v-else>待评分</strong>
    </header>
    <div class="question-body">
      <div class="stem"><MathText v-if="item.is_scoring_revision" :text="item.stem" /><SourceQuestionContent v-else :question="item" :fallback="item.stem" /></div>
      <p v-if="item.selection_status === 'unmapped'" class="notice">历史作答的选项对应待核对，下方保留学生原文。</p>
      <p v-if="!item.reference_available && item.score" class="notice">参考答案或评分版本待核对，本题不标记选项正误。</p>
      <div v-if="['single_choice','multi_choice','multiple_choice'].includes(item.question_type)" class="options">
        <div v-for="option in optionsFor(item)" :key="option.key" class="option" :class="item.score ? choiceMark(item, option.key).tone : ''">
          <b>{{ option.key }}</b>
          <div class="option-text">
            <MathText v-if="item.is_scoring_revision" :text="option.text" />
            <SourceQuestionContent v-else :question="item" part="option" :option-key="option.key" :fallback="option.text" />
            <AuthenticatedImage v-for="image in option.review_media || []" :key="image.url" class="review-media" :src="image.url" :alt="image.alt_text" />
          </div>
          <span v-if="item.score && choiceMark(item, option.key).label" class="option-note">{{ choiceMark(item, option.key).label }}</span>
        </div>
      </div>
      <div v-else-if="item.question_type === 'true_false'" class="options">
        <div v-for="choice in [{ value: true, label: '正确' }, { value: false, label: '错误' }]" :key="choice.label" class="option" :class="item.score ? truthMark(item, choice.value).tone : ''">
          <b>{{ choice.label }}</b><span v-if="item.score && truthMark(item, choice.value).label" class="option-note">{{ truthMark(item, choice.value).label }}</span>
        </div>
      </div>
      <div class="answer-line"><span>你的答案：<MathText :text="mathReadyAnswer(studentAnswer(item))" /></span><span v-if="item.score || item.reference_available">参考答案：<MathText :text="mathReadyAnswer(referenceAnswer(item))" /></span></div>
      <details v-if="item.answer_reference?.explanation && item.reference_available" class="detail"><summary>参考解析</summary><MathText :text="item.answer_reference.explanation" /></details>
      <details v-if="rubricText(item.answer_reference?.rubric)" class="detail"><summary>评分要点</summary><MathText :text="rubricText(item.answer_reference?.rubric)" /></details>
      <details v-if="item.original_question" class="detail"><summary>查看原始题目</summary><SourceQuestionContent :question="item.original_question" :fallback="item.original_question.stem" /></details>
    </div>
  </article>
</template>

<style scoped>
.result-question{min-width:0;border:1px solid #e2e8f3;border-radius:12px;background:#fff;overflow:hidden}.question-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:13px 16px;border-bottom:1px solid #edf0f5}.question-head>div{display:flex;flex-wrap:wrap;align-items:center;gap:10px}.question-head b{font-size:16px}.question-head span{font-size:14px;color:#64748b}.question-head strong{font-size:15px;font-weight:600;white-space:nowrap}.question-body{padding:16px}.stem{margin-bottom:15px;font-size:16px;font-weight:600;line-height:1.6;overflow-wrap:anywhere}.options{display:grid;gap:8px}.option{display:grid;grid-template-columns:22px minmax(0,1fr) auto;align-items:start;gap:8px;padding:10px 11px;border:1px solid #e2e8f3;border-radius:8px;background:#fff;font-size:15px;line-height:1.55}.option>b{color:#243855}.option-text{min-width:0;overflow-wrap:anywhere}.option-note{font-size:14px;color:#475569;white-space:nowrap}.option.correct,.option.reference{border-color:#9cd9b5;background:#f1f8f3}.option.correct .option-note,.option.reference .option-note{color:#28855a}.option.wrong{border-color:#e8a7ad;background:#fff5f5}.option.wrong .option-note{color:#c94e56}.option.missed{border-color:#e9c88f;background:#fff8eb}.option.missed .option-note{color:#9a5a11}.answer-line{display:flex;flex-wrap:wrap;gap:7px 18px;margin-top:13px;font-size:14px;line-height:1.6;color:#475569;overflow-wrap:anywhere}.answer-line>span{min-width:0}.notice{font-size:14px;line-height:1.5;color:#9a5a11;margin:0 0 10px}.detail{border-top:1px solid #edf0f5;margin-top:13px;padding-top:10px;font-size:14px;line-height:1.6;color:#475569}.detail summary{color:#2558d4;cursor:pointer;width:max-content;max-width:100%}.detail :deep(.katex-display),.stem :deep(.katex-display){overflow-x:auto;max-width:100%}.review-media{display:block;max-width:100%;max-height:70px;object-fit:contain;object-position:left;margin-top:5px}@media(max-width:680px){.option{grid-template-columns:22px minmax(0,1fr)}.option-note{grid-column:2;white-space:normal}}
.stem{font-weight:500;line-height:1.7}
</style>
