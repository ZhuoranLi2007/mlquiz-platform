<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import StudentReviewQuestion from './StudentReviewQuestion.vue'
import { friendlyError } from '../utils/presentation'
import { itemResult, type ReviewItem } from '../utils/studentAttemptReview'

const props = defineProps<{ attemptId: string; targetedRetest?: any }>()
const router = useRouter()
const review = ref<any>(null)
const loading = ref(true)
const error = ref('')
const items = computed<ReviewItem[]>(() => review.value?.items || [])
const wrong = computed(() => items.value.filter(item => itemResult(item) === 'wrong'))
const correct = computed(() => items.value.filter(item => itemResult(item) === 'correct'))
const ungraded = computed(() => items.value.filter(item => itemResult(item) === 'ungraded'))
const fullyGraded = computed(() => Boolean(review.value && !review.value.ungraded_item_count))
const scorePercent = computed(() => {
  const score = Number(review.value?.score)
  const maximum = Number(review.value?.max_score)
  return fullyGraded.value && Number.isFinite(score) && maximum > 0
    ? Math.round(score / maximum * 100) : null
})
const feedbackUrl = computed(() => ({
  path: '/student/feedback',
  query: { attempt: review.value?.retest_of_attempt_id || props.attemptId },
}))

function numberText(value: unknown) {
  return value == null ? '—' : String(Number(Number(value).toFixed(4)))
}
function retestPercent() {
  const accuracy = Number(props.targetedRetest?.accuracy)
  return Number.isFinite(accuracy) ? `${Math.round(accuracy * 100)}%` : ''
}
async function loadReview() {
  loading.value = true
  error.value = ''
  try {
    review.value = await api.get(`/student/attempts/${props.attemptId}/review`)
  } catch (reason) {
    error.value = friendlyError(reason, '答卷读取失败')
  } finally {
    loading.value = false
  }
}
function focusQuestion(position: number) {
  const target = document.getElementById(`result-question-${position}`)
  if (!target) return
  const group = target.closest('details') as HTMLDetailsElement | null
  if (group) group.open = true
  target.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
onMounted(loadReview)
</script>

<template>
  <div class="student-result" v-loading="loading">
    <header class="result-header">
      <button type="button" class="back" @click="router.push('/student')">← 返回我的任务</button>
      <h1>答卷结果</h1>
      <span v-if="review?.mode === 'retest'" class="retest-label">针对性复测<span v-if="targetedRetest?.round"> · 第 {{ targetedRetest.round }} 轮</span></span>
    </header>
    <p v-if="error" class="error" role="alert">{{ error }} <button type="button" @click="loadReview">重试</button></p>
    <template v-if="review">
      <section class="summary" aria-label="本次成绩">
        <div><span>本次成绩</span><strong>{{ numberText(review.score) }}<small> / {{ numberText(review.max_score) }} 分</small></strong></div>
        <div><span>{{ fullyGraded ? '得分率' : '评分进度' }}</span><strong>{{ fullyGraded ? `${scorePercent ?? '—'}%` : `${review.scored_item_count} / ${items.length} 题` }}</strong></div>
        <div><span>错题</span><strong>{{ wrong.length }}<small> 题</small></strong></div>
        <div><span>已答对</span><strong>{{ correct.length }}<small> 题</small></strong></div>
      </section>
      <p v-if="ungraded.length" class="grading-note">另有 {{ ungraded.length }} 题暂无可靠评分，未计入上方成绩，也不按答错处理。</p>
      <p v-if="review.mode === 'retest' && targetedRetest" class="retest-verdict" :class="targetedRetest.status">
        本轮复测{{ targetedRetest.status === 'passed' ? '已达标' : targetedRetest.status === 'not_passed' ? '未达标' : '结果待确认' }}<template v-if="retestPercent()"> · 正确率 {{ retestPercent() }}</template><template v-if="targetedRetest.status === 'not_passed'"> · 达标线 80%</template>
      </p>
      <p class="legend">绿色：参考答案（已选时为选对）　橙色：漏选　红色：错选</p>

      <section class="result-group" aria-label="错题列表">
        <div class="section-heading"><h2>错题</h2><span>{{ wrong.length }} 题</span></div>
        <p v-if="!wrong.length" class="empty">本次没有判错的题目。</p>
        <div v-else class="question-columns">
          <StudentReviewQuestion v-for="item in wrong" :key="item.paper_item_id" :item="item" />
        </div>
      </section>

      <details v-if="correct.length" class="group-details">
        <summary>已答对题目 <span>{{ correct.length }} 题，点击展开</span></summary>
        <div class="question-columns"><StudentReviewQuestion v-for="item in correct" :key="item.paper_item_id" :item="item" /></div>
      </details>
      <details v-if="ungraded.length" class="group-details">
        <summary>暂无可靠评分 <span>{{ ungraded.length }} 题，点击展开</span></summary>
        <div class="question-columns"><StudentReviewQuestion v-for="item in ungraded" :key="item.paper_item_id" :item="item" /></div>
      </details>

      <nav class="footer-actions">
        <button type="button" class="primary" @click="router.push(feedbackUrl)">{{ review.mode === 'retest' ? '返回原始学习反馈' : '查看学习反馈' }}</button>
        <button type="button" @click="router.push('/student')">返回我的任务</button>
      </nav>
      <nav class="question-jump" aria-label="题目定位">
        <span>跳转题目</span><button v-for="item in items" :key="item.paper_item_id" type="button" :class="itemResult(item)" @click="focusQuestion(item.position)">{{ item.position }}</button>
      </nav>
    </template>
  </div>
</template>

<style scoped>
.student-result{max-width:1360px;margin:auto;padding:24px 26px 64px;color:#1e293b}.result-header{display:flex;align-items:center;gap:18px;margin-bottom:20px}.result-header h1{margin:0;font-size:24px;font-weight:700}.back,.footer-actions button{border:1px solid #dce4f0;border-radius:9px;background:#fff;color:#2558d4;padding:10px 14px;cursor:pointer;font-size:14px}.retest-label{color:#8c5d19;background:#fff5e6;padding:5px 9px;border-radius:7px;font-size:14px}.summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:18px}.summary>div{display:grid;gap:7px;padding:18px 20px;border:1px solid #e2e8f3;border-radius:12px;background:#fff}.summary span{font-size:14px;color:#64748b}.summary strong{font-size:30px;line-height:1.2;font-weight:700;font-variant-numeric:tabular-nums}.summary small{font-size:15px;font-weight:400;color:#475569}.grading-note,.retest-verdict,.legend{font-size:14px;line-height:1.5}.grading-note{color:#9a5a11}.retest-verdict{padding:10px 14px;border:1px solid #e2e8f3;border-radius:8px;background:#fff}.retest-verdict.passed{color:#28855a}.retest-verdict.not_passed{color:#c94e56}.legend{color:#64748b;margin:6px 0 18px}.section-heading{display:flex;align-items:baseline;gap:10px;margin:0 0 12px}.section-heading h2{font-size:19px;font-weight:600;margin:0}.section-heading span,.group-details>summary span{font-size:14px;color:#64748b}.question-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}.group-details{margin-top:16px}.group-details>summary{padding:17px 18px;border:1px solid #e2e8f3;border-radius:10px;background:#fff;font-size:18px;font-weight:600;cursor:pointer}.group-details>summary span{font-weight:400;margin-left:8px}.group-details>.question-columns{margin-top:14px}.empty{padding:22px;border:1px solid #e2e8f3;border-radius:10px;background:#fff;color:#475569}.footer-actions{display:flex;gap:10px;margin-top:24px}.footer-actions .primary{background:#2858d8;border-color:#2858d8;color:#fff}.question-jump{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin-top:20px;font-size:14px;color:#64748b}.question-jump button{width:33px;height:33px;border:1px solid #dce4f0;border-radius:7px;background:#fff;color:#475569;cursor:pointer}.question-jump button.wrong{color:#c94e56}.question-jump button.correct{color:#28855a}.error{padding:12px;background:#fff5f5;color:#b33e47}.error button{border:0;background:transparent;text-decoration:underline;cursor:pointer;color:inherit}@media(max-width:1050px){.question-columns{grid-template-columns:1fr}}@media(max-width:680px){.student-result{padding:18px 14px 42px}.result-header{flex-wrap:wrap}.summary{grid-template-columns:repeat(2,minmax(0,1fr))}.summary>div{padding:14px}}
.summary strong{font-size:var(--font-size-metric)}
.question-columns:has(> :only-child){grid-template-columns:minmax(0,980px)}
</style>
