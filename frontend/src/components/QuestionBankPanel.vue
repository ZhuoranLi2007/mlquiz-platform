<script setup lang="ts">
/**
 * 题库浏览：按考点/题型/来源/答案状态筛选。
 * 列表只展示题干、选项与出处（服务端 DTO 不含答案），答案与解析在交卷后才返回。
 */
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'
import MathText from './MathText.vue'

const loading = ref(true)
const error = ref('')
const questions = ref<any[]>([])
const knowledgePoints = ref<any[]>([])
const stats = ref<any>({})
const activeKp = ref('')
const activeType = ref('')
const activeOrigin = ref('')
const activeAnswer = ref('')
const keyword = ref('')
const limit = ref(24)

const TYPE_LABELS: Record<string, string> = {
  single_choice: '单选', multi_choice: '多选', true_false: '判断', numeric: '数值',
  term: '名词解释', short_answer: '简答', derivation: '推导',
  comprehensive: '综合', unknown: '待核对',
}
const ORIGIN_LABELS: Record<string, string> = {
  original: '原卷题', human: '人工整理', ai: 'AI 生成', note: '复习笔记', external: '外部题',
}
const ANSWER_LABELS: Record<string, { text: string; tone: string }> = {
  verified: { text: '答案已核验', tone: 'success' },
  ai_auto_checked: { text: 'AI 参考答案（已自动检查）', tone: 'warning' },
  candidate: { text: '候选答案 · 待核查', tone: 'info' },
  unknown: { text: '答案未知', tone: 'danger' },
  to_check: { text: '待核查', tone: 'info' },
}

const kpName = computed(() => new Map(knowledgePoints.value.map((k) => [k.kp_id, k.name])))
const kpChapter = computed(() => new Map(knowledgePoints.value.map((k) => [k.kp_id, k.chapter])))

const filtered = computed(() => questions.value
  .filter((q) => !activeKp.value || q.main_kp_id === activeKp.value)
  .filter((q) => !activeType.value || q.question_type === activeType.value)
  .filter((q) => !activeOrigin.value || q.origin === activeOrigin.value)
  .filter((q) => {
    if (!activeAnswer.value) return true
    if (activeAnswer.value === 'ready') return ['verified', 'ai_auto_checked'].includes(q.answer_status)
    return q.answer_status === activeAnswer.value
  })
  .filter((q) => {
    const kw = keyword.value.trim().toLowerCase()
    return !kw || String(q.stem || '').toLowerCase().includes(kw)
  }))

const visible = computed(() => filtered.value.slice(0, limit.value))

function typeLabel(value: string) {
  return TYPE_LABELS[value] || value || '题型待核对'
}
function originLabel(value: string) {
  return ORIGIN_LABELS[value] || value || '来源未知'
}
function answerMeta(value: string) {
  return ANSWER_LABELS[value] || { text: value || '未知', tone: 'info' }
}
function sourceText(q: any) {
  const refs = q.source_refs || []
  if (!refs.length) return '来源未登记'
  const first = refs[0]
  const name = String(first.rel_path || first.document_id || '').split('/').pop()
  return `${name || '课程资料'}${first.page ? ` · 第 ${first.page} 页` : ''}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [bank, kps] = await Promise.all([api.get('/questions'), api.get('/knowledge-points')])
    questions.value = bank.items || []
    stats.value = bank.stats || {}
    knowledgePoints.value = kps.items || []
  } catch (err: any) {
    error.value = err?.message || String(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section v-loading="loading" class="bank-panel">
    <div v-if="error" class="hint error-hint" role="alert">{{ error }}</div>

    <div class="bank-stats">
      <div><strong>{{ stats.active || questions.length }}</strong><span>在用题目</span></div>
      <div><strong>{{ stats.usable || 0 }}</strong><span>答案已就绪</span></div>
      <div><strong>{{ stats.ai_reference_answers || 0 }}</strong><span>AI 参考答案</span></div>
      <div><strong>{{ stats.by_origin?.ai || 0 }}</strong><span>AI 生成题</span></div>
    </div>

    <div class="bank-filters">
      <el-select v-model="activeKp" clearable filterable placeholder="全部考点" class="bank-select">
        <el-option v-for="kp in knowledgePoints" :key="kp.kp_id" :value="kp.kp_id" :label="kp.name" />
      </el-select>
      <el-input v-model="keyword" clearable placeholder="搜索题干" class="bank-search" />
      <div class="chip-row">
        <button type="button" class="chip" :class="{ active: !activeType }" @click="activeType = ''">全部题型</button>
        <button v-for="(label, key) in TYPE_LABELS" :key="key" type="button" class="chip"
          :class="{ active: activeType === key }" @click="activeType = String(key)">{{ label }}</button>
      </div>
    </div>

    <div class="bank-filters">
      <div class="chip-row">
        <button type="button" class="chip" :class="{ active: !activeOrigin }" @click="activeOrigin = ''">全部来源</button>
        <button v-for="(label, key) in ORIGIN_LABELS" :key="key" type="button" class="chip"
          :class="{ active: activeOrigin === key }" @click="activeOrigin = String(key)">{{ label }}</button>
      </div>
      <div class="chip-row">
        <button type="button" class="chip" :class="{ active: !activeAnswer }" @click="activeAnswer = ''">全部答案状态</button>
        <button type="button" class="chip" :class="{ active: activeAnswer === 'ready' }" @click="activeAnswer = 'ready'">答案已就绪</button>
        <button type="button" class="chip" :class="{ active: activeAnswer === 'ai_auto_checked' }" @click="activeAnswer = 'ai_auto_checked'">AI 参考答案</button>
        <button type="button" class="chip" :class="{ active: activeAnswer === 'unknown' }" @click="activeAnswer = 'unknown'">答案未知</button>
      </div>
    </div>

    <p class="bank-count">共 {{ filtered.length }} 道题（列表不含答案、解析与评分细则）</p>

    <div class="q-grid">
      <article v-for="q in visible" :key="q.question_id + q.question_version" class="q-item">
        <div class="q-top">
          <el-tag size="small" effect="plain">{{ typeLabel(q.question_type) }}</el-tag>
          <el-tag size="small" effect="plain" :type="q.origin === 'ai' ? 'warning' : 'info'">{{ originLabel(q.origin) }}</el-tag>
          <el-tag size="small" effect="plain" :type="answerMeta(q.answer_status).tone as any">
            {{ answerMeta(q.answer_status).text }}
          </el-tag>
          <span class="q-score">{{ q.score }} 分</span>
        </div>
        <div class="q-stem">
          <span v-if="q.prompt_prefix" class="q-prefix">{{ q.prompt_prefix }}</span>
          <MathText :text="q.stem" />
        </div>
        <div v-if="q.options?.length" class="q-options">
          <span v-for="option in q.options" :key="option.key" class="q-option">
            {{ option.key }}. {{ option.text || '[选项公式缺失]' }}
          </span>
        </div>
        <p class="q-meta">
          <span>{{ kpChapter.get(q.main_kp_id) || '未分章' }} · {{ kpName.get(q.main_kp_id) || '未标注考点' }}</span>
          <span>出处：{{ sourceText(q) }}</span>
        </p>
      </article>
      <div v-if="!filtered.length" class="empty-state">
        <strong>没有匹配的题目</strong>
        <p>换一个考点或题型试试。</p>
      </div>
    </div>

    <div v-if="filtered.length > visible.length" class="load-more">
      <el-button @click="limit += 24">继续加载（还有 {{ filtered.length - visible.length }} 道）</el-button>
    </div>
  </section>
</template>

<style scoped>
.bank-panel { display: grid; gap: 14px; }
.bank-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.bank-stats div { padding: 12px 14px; border: 1px solid var(--border); border-radius: 11px; background: var(--panel); display: flex; flex-direction: column; }
.bank-stats strong { font-size: 22px; color: var(--accent); }
.bank-stats span { color: var(--muted); font-size: 14px; }
.bank-filters { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.bank-select { width: 220px; max-width: 100%; }
.bank-search { width: 200px; max-width: 100%; }
.chip-row { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { height: 30px; padding: 0 11px; border: 1px solid var(--border); border-radius: 999px; background: #fff; color: var(--text-2); font-size: 14px; cursor: pointer; }
.chip:hover { border-color: #c7d2fe; color: var(--accent); }
.chip.active { background: var(--accent-soft); border-color: #c7d2fe; color: var(--accent); font-weight: 600; }
.bank-count { margin: 0; color: var(--muted); font-size: 14px; }
.q-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 12px; }
.q-item { padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: #fff; }
.q-top { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 8px; }
.q-score { margin-left: auto; color: var(--muted); font-size: 14px; }
.q-stem { font-size: 14px; line-height: 1.7; }
.q-prefix { color: var(--muted); }
.q-options { display: grid; gap: 3px; margin-top: 8px; font-size: 14px; color: var(--text-2); }
.q-option { padding: 3px 8px; border-radius: 7px; background: var(--panel-soft); }
.q-meta { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 0; color: var(--muted); font-size: 14px; }
.load-more { display: flex; justify-content: center; padding: 8px 0 4px; }
@media (max-width: 900px) {
  .bank-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .q-grid { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .bank-select, .bank-search { width: 100%; }
}
</style>
