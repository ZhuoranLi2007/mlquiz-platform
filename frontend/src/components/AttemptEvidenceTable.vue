<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowDown, CircleCheck } from '@element-plus/icons-vue'
import MathText from './MathText.vue'
import SourceQuestionContent from './SourceQuestionContent.vue'

const props = withDefaults(defineProps<{
  result: any
  section?: 'problems' | 'correct'
  heading?: string
}>(), { section: 'problems', heading: '错题详情' })
const correctOpen = ref(false)
const allItems = computed(() => [...(props.result?.items || [])].sort((a: any, b: any) => Number(a.position) - Number(b.position)))
const wrongItems = computed(() => allItems.value.filter((item: any) => item.evidence_status === 'scored' && item.correct === false))
const ungradedItems = computed(() => allItems.value.filter((item: any) => item.evidence_status !== 'scored'))
const correctItems = computed(() => allItems.value.filter((item: any) => item.evidence_status === 'scored' && item.correct === true))

const TYPE_LABELS: Record<string, string> = {
  single_choice: '单选题', multiple_choice: '多选题', multi_choice: '多选题',
  true_false: '判断题', numeric: '数值计算题', calculation: '计算题',
  short_answer: '简答题', term: '名词解释', fill_blank: '填空题',
  formula_fill: '公式填空', derivation: '推导题', comprehensive: '综合题',
  matching: '匹配题',
}

function answerValue(value: any): any {
  if (value === null || value === undefined || value === '') return null
  if (typeof value !== 'object' || Array.isArray(value)) return value
  for (const key of ['selected_keys', 'keys', 'key', 'texts', 'values', 'parts', 'value', 'answer', 'reference_answer', 'raw_text', 'text']) {
    if (value[key] !== undefined && value[key] !== null && value[key] !== '') return value[key]
  }
  return value
}
function answerText(input: any): string {
  const value = answerValue(input)
  if (value === null || value === undefined || value === '') return '未作答'
  if (typeof value === 'boolean') return value ? '正确' : '错误'
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map((entry, index) => {
    if (entry && typeof entry === 'object') {
      const label = entry.label || entry.name || entry.key || `第 ${index + 1} 项`
      return `${label}：${answerText(entry.value ?? entry.answer ?? entry.accepted ?? entry)}`
    }
    return answerText(entry)
  }).join('；')
  return Object.entries(value).map(([key, entry]) => `${key}：${answerText(entry)}`).join('；')
}
function answerKeys(input: any): string[] {
  const value = answerValue(input)
  const rows = Array.isArray(value) ? value : [value]
  return rows.map((entry: any) => String(entry && typeof entry === 'object' ? (entry.key ?? entry.value ?? '') : (entry ?? '')).trim().toUpperCase()).filter(Boolean)
}
function correctPayload(item: any) { return item?.answer_reference?.payload ?? null }
function optionsFor(item: any): any[] {
  const options = (item?.options?.length ? item.options : item?.source_options) || []
  if (Array.isArray(options)) return options.map((option: any, index: number) =>
    typeof option === 'object' ? option : { key: String.fromCharCode(65 + index), text: String(option) })
  return Object.entries(options).map(([key, value]) => ({ key, text: value }))
}
function selectedKeys(item: any): string[] {
  const response = item?.response || {}
  const direct = response.selected_keys ?? response.keys ?? response.key
  if (direct !== undefined && direct !== null) return answerKeys(direct)
  const rawRows = Array.isArray(response.texts) ? response.texts
    : typeof response.raw_text === 'string' ? response.raw_text.split('┋') : []
  const options = optionsFor(item)
  const keys: string[] = rawRows.map((entry: any): string => {
    const text = String(entry || '').trim()
    const leading = text.match(/^\(?([A-Z])(?:\s|[）).、:：])/i)
    if (leading) return leading[1].toUpperCase()
    const normalized = text.replace(/^\(?[A-Z](?:\s|[）).、:：])+\s*/i, '').replace(/[。\s]+$/g, '')
    const match = options.find((option: any) =>
      String(option.text || option.value || '').replace(/[。\s]+$/g, '') === normalized)
    return match ? String(match.key).toUpperCase() : ''
  }).filter(Boolean)
  return [...new Set<string>(keys)]
}
function selected(item: any, key: string) { return selectedKeys(item).includes(String(key).toUpperCase()) }
function isCorrectOption(item: any, key: string) { return answerKeys(correctPayload(item)).includes(String(key).toUpperCase()) }
function optionClass(item: any, key: string) {
  const picked = selected(item, key); const correct = isCorrectOption(item, key)
  return { 'is-correct': correct, 'is-wrong-picked': picked && !correct, 'is-picked': picked }
}
function numberText(value: unknown) {
  if (value === null || value === undefined) return '—'
  const number = Number(value); return Number.isInteger(number) ? String(number) : number.toFixed(1)
}
function scoreText(item: any) {
  if (item.evidence_status !== 'scored') return '—'
  if (!(Number(item.max_score) > 0)) return item.correct ? '已判对 · 历史分值缺失' : '已判错 · 历史分值缺失'
  return `${numberText(item.awarded_score)}/${numberText(item.max_score)} 分`
}
function rubricRows(item: any): any[] {
  const rubric = item?.answer_reference?.rubric
  if (!rubric) return []
  if (Array.isArray(rubric)) return rubric
  return rubric.points || rubric.key_points || rubric.criteria || []
}
function explanationText(item: any): string {
  return String(item?.answer_reference?.explanation || '').trim()
}
function toleranceText(item: any) {
  const payload = correctPayload(item) || {}; const tolerance = payload.tol ?? payload.tolerance
  return tolerance === undefined || tolerance === null ? '' : `允许误差：${tolerance}`
}
</script>

<template>
  <section v-if="section === 'problems'" class="evidence-section">
    <header v-if="wrongItems.length" class="section-title"><div><i></i><h2>{{ heading }}</h2><span>共 {{ wrongItems.length }} 题</span></div></header>
    <div v-if="wrongItems.length" class="question-list">
      <article v-for="item in wrongItems" :id="`single-question-${item.position}`" :key="item.paper_item_id" :class="['question-card', `evidence-chapter-${String(item.chapter_id || 'unmapped').replace(/[^\w-]/g, '-')}`]">
        <header class="question-head"><div class="question-tags"><b>第 {{ item.position }} 题</b><span>{{ TYPE_LABELS[item.question_type] || item.question_type || '题型未知' }}</span><span class="chapter-tag">{{ item.chapter?.title || '章节待确认' }}</span></div><strong>{{ scoreText(item) }}</strong></header>
        <div class="question-body">
          <div v-if="item.is_scoring_revision" class="revision-label">修订版（评分依据）</div>
          <div class="stem"><MathText v-if="item.is_scoring_revision" :text="item.stem" /><SourceQuestionContent v-else :question="item" /></div>
          <div v-if="optionsFor(item).length" class="options-list">
            <div v-for="option in optionsFor(item)" :key="option.key" class="option-row" :class="optionClass(item, option.key)">
              <b>{{ option.key }}</b><MathText :text="option.text || option.value || ''" />
              <span v-if="isCorrectOption(item, option.key)" class="option-note correct-note">{{ selected(item, option.key) ? '学生答对' : '正确答案' }}</span>
              <span v-if="selected(item, option.key) && !isCorrectOption(item, option.key)" class="option-note wrong-note">学生选择</span>
            </div>
          </div>
          <div v-if="optionsFor(item).length" class="choice-answer-compare">
            <div class="choice-answer student-choice"><span>学生答案</span><b>{{ selectedKeys(item).join('、') || answerText(item.response) }}</b></div>
            <div class="choice-answer correct-choice"><span>正确答案</span><b>{{ answerKeys(correctPayload(item)).join('、') || '暂未提供' }}</b></div>
          </div>
          <div v-else class="answer-compare">
            <div class="answer-box student-answer"><small>学生作答</small><MathText :text="answerText(item.response)" /></div>
            <div class="answer-box correct-answer"><small>正确答案</small><MathText :text="answerText(correctPayload(item))" /><em v-if="toleranceText(item)">{{ toleranceText(item) }}</em></div>
          </div>
          <details v-if="rubricRows(item).length" class="learning-detail">
            <summary><span>评分要点</span><el-icon><ArrowDown /></el-icon></summary>
            <div class="detail-content rubric-content"><ul><li v-for="(point, index) in rubricRows(item)" :key="index">{{ point.description || point.text || point.criterion || answerText(point) }}<em v-if="point.score ?? point.weight">{{ point.score ?? point.weight }} 分</em></li></ul></div>
          </details>
          <details v-if="explanationText(item)" class="learning-detail">
            <summary><span>参考解析</span><el-icon><ArrowDown /></el-icon></summary>
            <div class="detail-content reference-content"><MathText :text="explanationText(item)" /></div>
          </details>
          <details v-if="item.original_question" class="learning-detail">
            <summary><span>查看原始题目</span><el-icon><ArrowDown /></el-icon></summary>
            <div class="detail-content"><MathText :text="item.original_question.stem" /><div v-for="option in optionsFor(item.original_question)" :key="option.key">{{ option.key }}. <MathText :text="option.text || option.value || ''" /></div></div>
          </details>
        </div>
      </article>
    </div>
    <div v-else class="all-correct"><el-icon><CircleCheck /></el-icon><div><b>本次已评分题目全部答对</b><span>可在下方展开查看已答对题目。</span></div></div>
    <section v-if="ungradedItems.length" class="ungraded-block">
      <header><b>未计入成绩的题目</b><span>{{ ungradedItems.length }} 题</span></header>
      <article v-for="item in ungradedItems" :key="item.paper_item_id"><b>第 {{ item.position }} 题 · {{ TYPE_LABELS[item.question_type] || item.question_type }}</b><small v-if="item.is_scoring_revision">修订版（评分依据）</small><MathText :text="item.stem" /><p>学生作答：{{ answerText(item.response) }}</p><details v-if="item.original_question" class="learning-detail"><summary>查看原始题目</summary><div class="detail-content"><MathText :text="item.original_question.stem" /></div></details></article>
    </section>
  </section>

  <section v-else class="correct-section">
    <button type="button" class="correct-toggle" @click="correctOpen = !correctOpen"><span><el-icon><CircleCheck /></el-icon><b>已答对题目</b>（共 {{ correctItems.length }} 题）</span><el-icon :class="{ opened: correctOpen }"><ArrowDown /></el-icon></button>
    <div v-if="correctOpen" class="correct-list">
      <article v-for="item in correctItems" :key="item.paper_item_id"><header><b>第 {{ item.position }} 题</b><span>{{ TYPE_LABELS[item.question_type] || item.question_type }}</span><em>{{ scoreText(item) }}</em></header><small v-if="item.is_scoring_revision">修订版（评分依据）</small><div class="stem"><MathText :text="item.stem" /></div><p><b>学生答案：</b>{{ answerText(item.response) }}　<b>正确答案：</b>{{ answerText(correctPayload(item)) }}</p><details v-if="item.original_question" class="learning-detail"><summary>查看原始题目</summary><div class="detail-content"><MathText :text="item.original_question.stem" /></div></details></article>
      <p v-if="!correctItems.length" class="empty-correct">没有正式判定为正确的题目。</p>
    </div>
  </section>
</template>

<style scoped>
.evidence-section,.correct-section{min-width:0}.section-title{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;margin:5px 0 11px}.section-title>div{display:flex;align-items:center;gap:10px}.section-title i{width:4px;height:27px;border-radius:4px;background:#e74e5a}.section-title h2{margin:0;color:#17243d;font-size:21px;font-weight:800}.section-title span{padding:4px 9px;border-radius:999px;background:#fff0f1;color:#c73d47;font-size:14px;font-weight:700}.question-list{column-count:2;column-gap:14px}.question-card{display:inline-block;width:100%;margin:0 0 14px;overflow:hidden;break-inside:avoid;border:1px solid #efb8bd;border-radius:13px;background:#fff;box-shadow:0 4px 15px rgb(52 69 98 / 4%);vertical-align:top}.question-head{display:flex;align-items:center;justify-content:space-between;gap:9px;padding:10px 12px;background:linear-gradient(90deg,#fff4f5,#fff)}.question-tags{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.question-tags b{padding:5px 9px;border-radius:6px;background:#e95761;color:#fff;font-size:14px}.question-tags span{padding:5px 7px;border-radius:6px;background:#f1f4f8;color:#3f4d63;font-size:14px;font-weight:650}.question-tags .chapter-tag{background:#eaf1ff;color:#315fca}.question-head>strong{flex:none;color:#c9333e;font-size:14px;font-weight:800}.question-body{padding:12px 13px 13px}.stem{color:#17243d;font-size:16px;font-weight:750;line-height:1.65;overflow-wrap:anywhere}.stem :deep(.katex-display){overflow-x:auto}.options-list{display:grid;gap:6px;margin-top:10px}.option-row{display:grid;grid-template-columns:25px minmax(0,1fr) auto;align-items:center;gap:7px;padding:8px 10px;border:1px solid #e5eaf1;border-radius:8px;background:#fff;color:#26364f;font-size:14px;line-height:1.5}.option-row>b{color:#35445a;font-size:14px}.option-row.is-correct{border-color:#addfc3;background:#eaf8f0}.option-row.is-wrong-picked{border-color:#efb8bd;background:#ffedef}.option-note{justify-self:end;padding:3px 6px;border-radius:5px;font-size:14px;font-weight:800}.correct-note{background:#d9f2e4;color:#11784c}.wrong-note{background:#fbdcdf;color:#b92f3b}.choice-answer-compare,.answer-compare{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}.choice-answer,.answer-box{min-width:0;padding:9px 11px;border-radius:8px;overflow-wrap:anywhere}.choice-answer span,.answer-box small{display:block;margin-bottom:4px;font-size:14px;font-weight:800}.choice-answer b{font-size:16px}.student-choice,.student-answer{background:#ffedef;color:#9c2d38}.correct-choice,.correct-answer{background:#eaf8f0;color:#126944}.answer-box{font-size:14px;line-height:1.6}.answer-box em{display:block;margin-top:4px;color:#5f7a6d;font-size:14px}.learning-detail{margin-top:8px;overflow:hidden;border:1px solid #e1e7f0;border-radius:8px;background:#fbfcfe}.learning-detail>summary{display:flex;align-items:center;justify-content:space-between;padding:9px 11px;color:#34465e;font-size:14px;font-weight:750;cursor:pointer;list-style:none}.learning-detail>summary::-webkit-details-marker{display:none}.learning-detail>summary .el-icon{color:#71829a;transition:transform .2s}.learning-detail[open]>summary{border-bottom:1px solid #e7ebf2;background:#f7f9fd}.learning-detail[open]>summary .el-icon{transform:rotate(180deg)}.detail-content{padding:10px 12px;color:#4b5b70;font-size:14px;line-height:1.7}.rubric-content ul{margin:0;padding-left:18px}.rubric-content li+li{margin-top:5px}.rubric-content em{margin-left:5px;color:#315cf0}.reference-content{background:#fff}.all-correct{display:flex;align-items:center;gap:13px;padding:20px;border:1px solid #bfe6d2;border-radius:13px;background:#f1fbf6}.all-correct>.el-icon{color:#1a9b66;font-size:28px}.all-correct b,.all-correct span{display:block}.all-correct b{color:#176c4b;font-size:14px}.all-correct span{margin-top:3px;color:#668174;font-size:14px}.ungraded-block{margin-top:14px;border:1px solid #e1e6ee;border-radius:12px;background:#fff}.ungraded-block>header{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid #e9edf3;color:#59687d}.ungraded-block>header span{margin-left:auto}.ungraded-block article{padding:12px 14px;border-bottom:1px solid #edf0f4}.ungraded-block article:last-child{border:0}.ungraded-block article>b{display:block;color:#314159;font-size:14px}.ungraded-block p,.ungraded-block small{display:block;margin:5px 0 0;color:#6f7c8f;font-size:14px}.correct-section{overflow:hidden;border:1px solid #dbe5f4;border-radius:13px;background:#fff}.correct-toggle{display:flex;width:100%;align-items:center;justify-content:space-between;padding:13px 16px;border:0;background:#f6f9ff;color:#263a57;font-size:14px;cursor:pointer}.correct-toggle span{display:flex;align-items:center;gap:7px}.correct-toggle span>.el-icon{color:#18a168;font-size:19px}.correct-toggle>.el-icon{transition:transform .2s}.correct-toggle>.el-icon.opened{transform:rotate(180deg)}.correct-list{display:grid;gap:9px;padding:12px}.correct-list article{padding:12px;border:1px solid #e5eaf2;border-radius:9px}.correct-list header{display:flex;align-items:center;gap:8px}.correct-list header span{color:#718099;font-size:14px}.correct-list header em{margin-left:auto;color:#16805a;font-size:14px}.correct-list .stem{margin-top:7px;font-size:14px}.correct-list p{margin:7px 0 0;color:#58677d;font-size:14px}.empty-correct{margin:0;padding:12px;color:#7c899b;text-align:center}@media(max-width:1320px){.question-list{column-count:1}}@media(max-width:760px){.section-title{align-items:flex-start;flex-direction:column}.answer-compare,.choice-answer-compare{grid-template-columns:1fr}.option-row{grid-template-columns:22px minmax(0,1fr)}.option-note{grid-column:2;justify-self:start}}
.question-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));align-items:start;gap:14px;column-count:auto}.question-card{display:block;width:auto;margin:0;break-inside:auto;vertical-align:initial}.question-list .stem{font-weight:500;line-height:1.7}
</style>
<style scoped>
.revision-label,
.correct-list article > small,
.ungraded-block article > small {
  display: block;
  margin-bottom: 5px;
  color: #718099;
  font-size: 14px;
  font-weight: 400;
}
</style>
