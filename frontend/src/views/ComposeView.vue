<script setup lang="ts">
/**
 * V3.3 题库与组卷：取消快速组卷 / 自定义组卷 / 试卷列表 / 题库浏览四个 Tab，
 * 合并为一个统一的真实题库瀑布流页面；组卷改为右上角「开始组卷」弹窗三步流程。
 *
 * 数据全部来自 /api/questions 与 /api/knowledge-points（真实数据库），
 * 建卷走 /api/papers/from-questions（真实写库 + 落库复查），无 Mock、无 AI 补题。
 * 旧路由 ?tab=... 不再切换栏目；?paper=... 仍可打开历史试卷预览，数据一条不删。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import MathText from '../components/MathText.vue'
import MasonryGrid from '../components/MasonryGrid.vue'
import {
  downloadFile, friendlyError, questionTypeLabel,
} from '../utils/presentation'

interface BankQuestion {
  question_id: string
  question_version?: number
  question_type?: string
  /** 后端推断的有效题型：历史数据里 unknown 实际是单选题，筛选与标签都用它 */
  question_type_effective?: string
  question_type_inferred?: boolean
  stem?: string
  stem_zh?: string
  options?: Array<{ key?: string; label?: string; text?: string; content?: string; text_zh?: string }>
  main_kp_id?: string
  aux_kp_ids?: string[]
  difficulty?: string
  origin?: string
  original_exam_id?: string
  original_question_no?: string
  source_refs?: Array<Record<string, any>>
  score?: number
  estimated_minutes?: number
  prompt_prefix?: string
  options_incomplete?: boolean
  repair_hint?: string
  answer_status?: string
  answer_label?: string
}

const route = useRoute()
const router = useRouter()

const loading = ref(true)
const error = ref('')
const questions = ref<BankQuestion[]>([])
const knowledgePoints = ref<Array<{ kp_id: string; name: string; chapter?: string }>>([])
const stats = ref<any>({})

const keyword = ref('')
const typeFilter = ref('all')
const kpFilter = ref('')
const difficultyFilter = ref('')
const originFilter = ref('')

// 用户要求的题型分类；题库里不存在的类型（填空/编程）自动置灰且数量为 0，
// 不伪造题目，也不把 numeric/derivation 硬说成编程题。
const TYPE_FILTERS = [
  { value: 'all', label: '全部题目', types: null as string[] | null },
  { value: 'single_choice', label: '单选题', types: ['single_choice'] },
  { value: 'multi_choice', label: '多选题', types: ['multi_choice'] },
  { value: 'true_false', label: '判断题', types: ['true_false'] },
  { value: 'blank', label: '填空题', types: ['fill_blank', 'blank', 'fill_in'] },
  { value: 'short_answer', label: '简答题', types: ['short_answer', 'term'] },
  { value: 'calculation', label: '计算题', types: ['numeric', 'derivation', 'comprehensive'] },
  { value: 'coding', label: '编程题', types: ['coding', 'programming', 'code'] },
]
const DIFFICULTY_LABELS: Record<string, string> = {
  easy: '简单', 基础: '基础', 中等: '中等', hard: '较难', 困难: '困难',
}
const ORIGIN_LABELS: Record<string, string> = {
  original: '历年原卷', human: '人工整理', ai: 'AI 生成',
  note: '复习笔记', external: '外部试卷', local_class_image: '课堂图片',
}

const kpMap = computed(() => new Map(
  knowledgePoints.value.map((k) => [k.kp_id, k.name])))

function typeCount(value: string) {
  const spec = TYPE_FILTERS.find((t) => t.value === value)
  if (!spec || spec.types === null) return questions.value.length
  return questions.value.filter((q) => spec.types!.includes(effectiveType(q))).length
}
/** 筛选与展示统一用后端推断的有效题型，避免 unknown 题"看不见却会计分"。 */
function effectiveType(q: BankQuestion) {
  return q.question_type_effective || q.question_type || ''
}
function difficultyLabel(value?: string) {
  return DIFFICULTY_LABELS[value || ''] || value || '未标注'
}
function originLabel(value?: string) {
  return ORIGIN_LABELS[value || ''] || value || '来源未登记'
}
function typeChip(q: BankQuestion) {
  return questionTypeLabel(effectiveType(q))
}
/** 答案不可用时明确提示，不能让"看起来正常"的题进入组卷。 */
function repairText(q: BankQuestion) {
  if (q.options_incomplete) return q.repair_hint || '该题选项缺失，待修复'
  const status = q.answer_status || ''
  if (['to_check', 'unknown', 'candidate'].includes(status)) {
    return `参考答案待核验（${q.answer_label || '暂无参考答案'}），暂不参与评分`
  }
  return ''
}
function sourceText(q: BankQuestion) {
  const refs = q.source_refs || []
  if (refs.length) {
    const first = refs[0]
    const name = String(first.rel_path || first.document_id || '').split(/[/\\]/).pop()
    return `${name || '课程资料'}${first.page ? ` · 第 ${first.page} 页` : ''}`
  }
  if (q.original_exam_id) {
    return `原卷 ${q.original_exam_id}${q.original_question_no ? ` · 第 ${q.original_question_no} 题` : ''}`
  }
  return ''
}
function optionText(opt: any) {
  const text = String(opt.text || opt.content || opt.text_zh || '')
  // V3.4：后端现在会保留"只有 key 没有正文"的选项并标 missing_text，
  // 这里显式提示，避免选项行只剩一个字母。
  if (!text) return opt.missing_text ? '（选项正文缺失，待修复）' : ''
  return text
}
function optionKey(opt: any, index: number) {
  return String(opt.key || opt.label || String.fromCharCode(65 + index))
}
function matchesKeyword(q: BankQuestion, kw: string) {
  if (!kw) return true
  const parts = [
    q.stem, q.stem_zh, kpMap.value.get(q.main_kp_id || ''),
    q.original_exam_id, q.original_question_no,
    ...(q.source_refs || []).map((r) => String(r.rel_path || r.document_id || '')),
  ].filter(Boolean).join(' ').toLowerCase()
  return parts.includes(kw)
}
function matchesType(q: BankQuestion, value: string) {
  const spec = TYPE_FILTERS.find((t) => t.value === value)
  if (!spec || spec.types === null) return true
  return spec.types.includes(effectiveType(q))
}
function applyFilters(list: BankQuestion[], kw: string, type: string,
  kp: string, difficulty: string, origin: string) {
  return list.filter((q) =>
    matchesKeyword(q, kw.trim().toLowerCase())
    && matchesType(q, type)
    && (!kp || q.main_kp_id === kp || (q.aux_kp_ids || []).includes(kp))
    && (!difficulty || q.difficulty === difficulty)
    && (!origin || q.origin === origin))
}

const filtered = computed(() => applyFilters(
  questions.value, keyword.value, typeFilter.value, kpFilter.value,
  difficultyFilter.value, originFilter.value))
const difficultyOptions = computed(() => [...new Set(
  questions.value.map((q) => q.difficulty).filter(Boolean) as string[])])
const originOptions = computed(() => [...new Set(
  questions.value.map((q) => q.origin).filter(Boolean) as string[])])

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [bank, kps]: any[] = await Promise.all([
      api.get('/questions', { limit: 2000 }),
      api.get('/knowledge-points'),
    ])
    questions.value = bank.items || []
    stats.value = bank.stats || {}
    knowledgePoints.value = kps.items || []
    const kp = String(route.query.kp || '')
    if (kp) kpFilter.value = kp
    if (String(route.query.compose || '') === '1') openCompose()
    const paperId = String(route.query.paper || '')
    if (paperId && paperId !== handledPaperId) {
      handledPaperId = paperId
      void openPreview({ paper_id: paperId, title: '' })
    }
  } catch (reason) {
    error.value = friendlyError(reason, '题库加载失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

let handledPaperId = ''
let composedForQuery = false
const previewOpen = ref(false)
const previewLoading = ref(false)
const preview = ref<any>(null)

async function openPreview(paper: any) {
  previewOpen.value = true
  previewLoading.value = true
  preview.value = null
  try {
    preview.value = await api.get(`/papers/${paper.paper_id}`)
  } catch (reason) {
    ElMessage.error(friendlyError(reason, '试卷预览加载失败'))
  } finally {
    previewLoading.value = false
  }
}
function exportPdf(paper: any) {
  const title = String(paper.title || '组卷').replace(/[\\/:*?"<>|]/g, '_')
  downloadFile(`/api/papers/${paper.paper_id}/export.pdf`, `${title}.pdf`)
  ElMessage.success('试卷 PDF 已开始下载')
}
const starting = ref('')
async function startPaper(paper: any) {
  if (starting.value) return
  starting.value = paper.paper_id
  try {
    const attempt: any = await api.post(`/papers/${paper.paper_id}/attempts`)
    await router.push(`/exam/${attempt.attempt_id}`)
  } catch (reason) {
    ElMessage.error(friendlyError(reason, '无法开始作答'))
  } finally {
    starting.value = ''
  }
}

// ---------------------------------------------------------------- 组卷弹窗
const composeOpen = ref(false)
const step = ref<1 | 2 | 3>(1)
const dlgKeyword = ref('')
const dlgType = ref('all')
const dlgKp = ref('')
const dlgDifficulty = ref('')
const selectedIds = ref<string[]>([])
const scoreMap = reactive<Record<string, number>>({})
const generating = ref(false)
const result = ref<any>(null)

/**
 * V3.4（RC-3）：出题方式必须由用户显式选择。
 * 任务书要求「题库不足时，由用户明确选择是否允许 AI 补题」，
 * 因此默认是纯题库组卷，AI 只在用户勾选后才调用。
 */
type ComposeMode = 'manual' | 'ai_fill' | 'ai_kp'
const composeMode = ref<ComposeMode>('manual')
const MODE_OPTIONS: Array<{ value: ComposeMode; label: string; desc: string }> = [
  { value: 'manual', label: '仅从题库选题', desc: '按勾选顺序成卷，不调用 AI' },
  { value: 'ai_fill', label: '题库不足时 AI 补题', desc: '勾选的题打底，缺的题由 DeepSeek 补齐' },
  { value: 'ai_kp', label: '按知识点完整 AI 出卷', desc: '只选知识点和题量，整卷由 DeepSeek 生成' },
]
const aiTargetCount = ref(5)
const aiKpIds = ref<string[]>([])
const aiTypes = ref<string[]>(['single_choice'])
const AI_TYPE_OPTIONS = [
  { value: 'single_choice', label: '单选题' },
  { value: 'multi_choice', label: '多选题' },
  { value: 'true_false', label: '判断题' },
  { value: 'short_answer', label: '简答题' },
]
const generatingHint = ref('')

const form = reactive({
  title: '',
  mode: 'practice' as 'practice' | 'quiz',
  minutes: 0,
})

const questionById = computed(() => {
  const map = new Map<string, BankQuestion>()
  for (const q of questions.value) {
    if (!map.has(q.question_id)) map.set(q.question_id, q)
  }
  return map
})
const selectedQuestions = computed(() =>
  selectedIds.value.map((id) => questionById.value.get(id)).filter(Boolean) as BankQuestion[])
const dlgFiltered = computed(() => applyFilters(
  questions.value, dlgKeyword.value, dlgType.value, dlgKp.value,
  dlgDifficulty.value, ''))
const totalScore = computed(() => Math.round(
  selectedQuestions.value.reduce((sum, q) =>
    sum + (Number(scoreMap[q.question_id]) || 0), 0) * 100) / 100)
const defaultMinutes = computed(() => Math.max(5, Math.round(
  selectedQuestions.value.reduce((sum, q) =>
    sum + Number(q.estimated_minutes || 2), 0))))

function defaultTitle() {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `机器学习专项练习 - ${d.getFullYear()}.${pad(d.getMonth() + 1)}.${pad(d.getDate())}`
}
function openCompose() {
  composeOpen.value = true
  step.value = 1
  result.value = null
  generating.value = false
  if (!form.title) form.title = defaultTitle()
  // 若页面已按知识点筛选，弹窗默认沿用，减少重复操作
  if (kpFilter.value && !dlgKp.value) dlgKp.value = kpFilter.value
}
function closeCompose() {
  composeOpen.value = false
}
function isSelected(id: string) {
  return selectedIds.value.includes(id)
}
function toggleSelect(q: BankQuestion) {
  const id = q.question_id
  if (selectedIds.value.includes(id)) {
    selectedIds.value = selectedIds.value.filter((x) => x !== id)
    return
  }
  // 同一道题不能重复加入同一份试卷
  selectedIds.value = [...selectedIds.value, id]
  if (scoreMap[id] === undefined) scoreMap[id] = Number(q.score) || 2
}
function clearSelection() {
  selectedIds.value = []
}
function nextStep() {
  if (step.value === 1) {
    if (composeMode.value === 'ai_kp') {
      // AI 整卷不需要勾题，但必须有明确的知识点与题量
      if (!aiKpIds.value.length) {
        ElMessage.warning('请至少选择一个知识点')
        return
      }
      if (!(aiTargetCount.value >= 1)) {
        ElMessage.warning('题量必须大于 0')
        return
      }
    } else if (!selectedIds.value.length) {
      ElMessage.warning('请至少选择一道有效题目')
      return
    }
    if (!form.minutes) form.minutes = defaultMinutes.value
    step.value = 2
  } else if (step.value === 2) {
    if (!form.title.trim()) {
      ElMessage.warning('请填写试卷名称')
      return
    }
    if (composeMode.value === 'ai_kp') {
      if (!(aiTargetCount.value >= 1)) {
        ElMessage.warning('题量必须大于 0')
        return
      }
      step.value = 3
      return
    }
    if (selectedQuestions.value.some((q) => !(Number(scoreMap[q.question_id]) > 0))) {
      ElMessage.warning('每题分值必须大于 0')
      return
    }
    if (!(totalScore.value > 0)) {
      ElMessage.warning('总分必须大于 0')
      return
    }
    if (composeMode.value === 'ai_fill' && aiTargetCount.value <= selectedIds.value.length) {
      ElMessage.warning('补题目标题量要大于已选题量；不想补题请选「仅从题库选题」')
      return
    }
    step.value = 3
  }
}
function prevStep() {
  if (step.value > 1) step.value = (step.value - 1) as 1 | 2
}

async function generate() {
  if (generating.value) return
  const ids = [...selectedIds.value]
  const scores = selectedQuestions.value.map((q) => Number(scoreMap[q.question_id]))
  if (composeMode.value !== 'ai_kp') {
    if (!ids.length || ids.length !== scores.length || scores.some((s) => !(s > 0))) {
      ElMessage.error('选题或分值无效，请返回检查')
      return
    }
  }
  generating.value = true
  result.value = null
  generatingHint.value = composeMode.value === 'manual'
    ? '正在保存试卷…'
    : '正在调用 DeepSeek 出题，可能需要几十秒…'
  try {
    const out: any = composeMode.value === 'ai_kp'
      ? await api.post('/papers/assemble', {
        blueprint: {
          title: form.title.trim(),
          kp_ids: aiKpIds.value,
          question_types: aiTypes.value,
          count: aiCountSafe(),
          mode: form.mode,
          ai_fill: true,
          scope: 'course_only',
          estimated_minutes: form.minutes || undefined,
        },
      })
      : await api.post('/papers/from-questions', {
        title: form.title.trim(),
        mode: form.mode,
        question_ids: ids,
        scores,
        estimated_minutes: form.minutes,
        ai_fill: composeMode.value === 'ai_fill',
        target_count: composeMode.value === 'ai_fill' ? aiCountSafe() : undefined,
        ai_kp_ids: composeMode.value === 'ai_fill'
          ? selectedQuestions.value.map((q) => q.main_kp_id || '').filter(Boolean)
          : undefined,
      })
    // assemble 失败时不会抛异常，必须显式判定
    if (out?.status === 'failed' || (!out?.paper_id && !out?.paper_items)) {
      ElMessage.error(out?.message || out?.coverage_report?.message || '试卷未生成')
      return
    }
    // 0 题 / 0 分 / 未落库一律不算成功
    if (!out.paper_id || !(out.item_count > 0) || !(out.total_score > 0)) {
      ElMessage.error(out.message || '试卷未成功生成，请检查选题与分值')
      return
    }
    // from-questions 有落库复查；assemble 没有，这里按真实返回值判定
    if (composeMode.value !== 'ai_kp' && !out.verification?.paper_exists) {
      ElMessage.error('试卷未成功生成，请检查选题与分值')
      return
    }
    result.value = out
    const fillNote = out.ai_fill?.enabled
      ? `（含 AI 补题 ${out.ai_fill.filled ?? 0} 道）`
      : ''
    ElMessage.success(`已生成 ${out.item_count} 题 / ${out.total_score} 分${fillNote}`)
  } catch (reason) {
    ElMessage.error(friendlyError(reason, '生成试卷失败'))
  } finally {
    generating.value = false
    generatingHint.value = ''
  }
}

/** 目标题量夹到后端允许的范围，避免把 0 或超大值传过去。 */
function aiCountSafe() {
  return Math.max(1, Math.min(50, Math.round(Number(aiTargetCount.value) || 5)))
}

/** assemble（AI 整卷）与 from-questions 的返回结构不同，统一成展示用的字段。 */
const resultAiFill = computed(() => {
  const out = result.value || {}
  const cov = out.coverage_report || {}
  return out.ai_fill || cov.ai_fill || null
})
const resultBlocked = computed(() => result.value?.completeness?.blocked || [])
const resultAiCount = computed(() => {
  const out = result.value || {}
  if (typeof out.verification?.ai_generated_questions === 'number') {
    return out.verification.ai_generated_questions
  }
  return Number(resultAiFill.value?.filled || 0)
})

onMounted(() => {
  void load()
  // 旧 ?tab=quick|create|papers|bank 链接统一落在同一个题库页，不再切换栏目
  if (String(route.query.tab || '')) {
    router.replace({ query: { ...route.query, tab: undefined } })
  }
})
watch(() => route.query, () => {
  if (String(route.query.compose || '') === '1' && !composedForQuery) {
    composedForQuery = true
    openCompose()
  }
}, { deep: true })
</script>

<template>
  <div class="bank-page">
    <header class="bank-head">
      <div>
        <p class="eyebrow">题库与组卷</p>
        <h2>题库与组卷</h2>
        <p>浏览课程真实题库，按题型、知识点、难度和来源组合筛选；需要练习时从右上角开始组卷。</p>
      </div>
      <el-button type="primary" size="large" class="compose-cta" @click="openCompose">
        开始组卷
      </el-button>
    </header>

    <div v-if="error" class="hint error-hint" role="alert">{{ error }}</div>

    <section class="card bank-filters">
      <el-input v-model="keyword" clearable placeholder="搜索题干、知识点或题目来源" aria-label="搜索题目" />
      <el-select v-model="typeFilter" aria-label="按题型筛选">
        <el-option v-for="t in TYPE_FILTERS" :key="t.value"
          :label="`${t.label}（${typeCount(t.value)}）`" :value="t.value"
          :disabled="t.value !== 'all' && typeCount(t.value) === 0" />
      </el-select>
      <el-select v-model="kpFilter" clearable filterable placeholder="全部知识点" aria-label="按知识点筛选">
        <el-option v-for="kp in knowledgePoints" :key="kp.kp_id"
          :label="kp.name" :value="kp.kp_id" />
      </el-select>
      <el-select v-model="difficultyFilter" clearable placeholder="全部难度" aria-label="按难度筛选">
        <el-option v-for="d in difficultyOptions" :key="d"
          :label="difficultyLabel(d)" :value="d" />
      </el-select>
      <el-select v-model="originFilter" clearable placeholder="全部来源" aria-label="按来源筛选">
        <el-option v-for="o in originOptions" :key="o"
          :label="originLabel(o)" :value="o" />
      </el-select>
      <el-button plain :disabled="!keyword && typeFilter === 'all' && !kpFilter
        && !difficultyFilter && !originFilter"
        @click="keyword = ''; typeFilter = 'all'; kpFilter = ''; difficultyFilter = ''; originFilter = ''">
        重置
      </el-button>
    </section>

    <div class="bank-count">
      <strong>{{ filtered.length }}</strong> 道题目
      <span v-if="filtered.length !== questions.length"> / 题库共 {{ questions.length }} 道</span>
      <span v-if="stats.active"> · 有效题 {{ stats.active }} 道</span>
      <span v-if="keyword || kpFilter || typeFilter !== 'all' || difficultyFilter || originFilter">
        · 已应用筛选
      </span>
    </div>

    <div v-if="loading" class="empty-state">正在加载真实题库…</div>

    <MasonryGrid v-else-if="filtered.length" :items="filtered" :gap="14" class="bank-grid">
      <template #default="{ item: q }">
        <article class="bank-card">
          <div class="bc-tags">
            <span class="bc-type">{{ typeChip(q) }}</span>
            <span v-if="q.main_kp_id" class="bc-kp">{{ kpMap.get(q.main_kp_id) || q.main_kp_id }}</span>
            <span v-if="q.difficulty" class="bc-diff">{{ difficultyLabel(q.difficulty) }}</span>
          </div>

          <div class="bc-stem">
            <span v-if="q.prompt_prefix" class="bc-prompt">{{ q.prompt_prefix }}</span>
            <MathText :text="q.stem || '（题干缺失）'" />
          </div>

          <div v-if="repairText(q)" class="bc-missing">
            {{ repairText(q) }}
          </div>
          <div v-if="(q.options || []).length" class="bc-options">
            <div v-for="(opt, i) in q.options" :key="optionKey(opt, i)" class="bc-option">
              <span class="bc-opt-key">{{ optionKey(opt, i) }}.</span>
              <span class="bc-opt-text"><MathText :text="optionText(opt)" /></span>
            </div>
          </div>

          <div v-if="sourceText(q)" class="bc-source" :title="sourceText(q)">
            来源：{{ sourceText(q) }}
          </div>
          <div v-else class="bc-source is-empty">来源未登记</div>
        </article>
      </template>
    </MasonryGrid>

    <div v-else class="empty-state">
      <h4>没有符合筛选条件的题目</h4>
      <p>可以调整搜索词或筛选条件，题库数据来自真实数据库。</p>
    </div>

    <!-- ------------------------------------------------ 开始组卷弹窗 -->
    <el-dialog v-model="composeOpen" :title="`开始组卷 · 第 ${step} 步 / 共 3 步`"
      width="min(880px, 94%)" top="6vh" class="compose-dialog"
      :close-on-click-modal="false" @closed="closeCompose">
      <div class="compose-steps">
        <span :class="{ active: step === 1, done: step > 1 }">1 {{ composeMode === 'ai_kp' ? '选知识点' : '选择题目' }}</span>
        <span :class="{ active: step === 2, done: step > 2 }">2 试卷设置</span>
        <span :class="{ active: step === 3 }">3 生成试卷</span>
        <strong class="compose-selected">
          {{ composeMode === 'ai_kp' ? `目标 ${aiCountSafe()} 题` : `已选择 ${selectedIds.length} 道题` }}
        </strong>
      </div>

      <div class="compose-body">
        <!-- 出题方式：必须由用户显式选择，默认不调用 AI -->
        <div class="mode-picker">
          <span class="mode-label">出题方式</span>
          <el-radio-group v-model="composeMode" :disabled="step > 1 || generating">
            <el-radio-button v-for="m in MODE_OPTIONS" :key="m.value" :value="m.value">
              {{ m.label }}
            </el-radio-button>
          </el-radio-group>
          <p class="mode-desc">
            {{ MODE_OPTIONS.find((m) => m.value === composeMode)?.desc }}
            <span v-if="composeMode !== 'manual'" class="mode-warn">
              · 这一项会真实调用 DeepSeek，消耗 API 额度
            </span>
          </p>
        </div>

        <!-- 第一步：选题 -->
        <template v-if="step === 1">
          <template v-if="composeMode === 'ai_kp'">
            <div class="ai-form">
              <label>知识点（可多选）
                <el-select v-model="aiKpIds" multiple filterable collapse-tags
                  placeholder="选择要考察的知识点" aria-label="AI 出卷知识点">
                  <el-option v-for="kp in knowledgePoints" :key="kp.kp_id"
                    :label="kp.chapter ? `${kp.name}（${kp.chapter}）` : kp.name"
                    :value="kp.kp_id" />
                </el-select>
              </label>
              <label>题量
                <el-input-number v-model="aiTargetCount" :min="1" :max="20" aria-label="AI 出卷题量" />
              </label>
              <label>题型
                <el-select v-model="aiTypes" multiple collapse-tags aria-label="AI 出卷题型">
                  <el-option v-for="t in AI_TYPE_OPTIONS" :key="t.value"
                    :label="t.label" :value="t.value" />
                </el-select>
              </label>
            </div>
            <p class="dlg-hint">
              整卷由 DeepSeek 依据所选知识点生成；生成后仍会做结构、课程范围与去重校验，
              通过校验的题才会进卷。
            </p>
          </template>
          <template v-else>
            <div class="dlg-filters">
              <el-input v-model="dlgKeyword" clearable placeholder="搜索题目" aria-label="弹窗内搜索题目" />
              <el-select v-model="dlgType" aria-label="弹窗内按题型筛选">
                <el-option v-for="t in TYPE_FILTERS" :key="t.value" :label="t.label"
                  :value="t.value" :disabled="t.value !== 'all' && typeCount(t.value) === 0" />
              </el-select>
              <el-select v-model="dlgKp" clearable filterable placeholder="全部知识点" aria-label="弹窗内按知识点筛选">
                <el-option v-for="kp in knowledgePoints" :key="kp.kp_id"
                  :label="kp.name" :value="kp.kp_id" />
              </el-select>
              <el-select v-model="dlgDifficulty" clearable placeholder="全部难度" aria-label="弹窗内按难度筛选">
                <el-option v-for="d in difficultyOptions" :key="d"
                  :label="difficultyLabel(d)" :value="d" />
              </el-select>
            </div>
            <p class="dlg-hint">
              共 {{ dlgFiltered.length }} 道可选题目；勾选后立即计入，同一道题不会重复加入。
              题干与选项均完整展示。
            </p>
            <div class="dlg-list">
              <div v-for="q in dlgFiltered" :key="q.question_id" class="dlg-row"
                :class="{ picked: isSelected(q.question_id) }"
                @click="toggleSelect(q)">
                <el-checkbox :model-value="isSelected(q.question_id)" @click.stop
                  @change="toggleSelect(q)" />
                <div class="dlg-q">
                  <div class="dlg-q-tags">
                    <span>{{ typeChip(q) }}</span>
                    <span v-if="q.main_kp_id">{{ kpMap.get(q.main_kp_id) || q.main_kp_id }}</span>
                    <span v-if="q.difficulty">{{ difficultyLabel(q.difficulty) }}</span>
                    <span v-if="q.score != null">{{ q.score }} 分</span>
                  </div>
                  <div class="dlg-stem">
                    <span v-if="q.prompt_prefix">{{ q.prompt_prefix }}</span>
                    <MathText :text="q.stem || '（题干缺失）'" />
                  </div>
                  <div v-if="(q.options || []).length" class="dlg-options">
                    <div v-for="(opt, i) in q.options" :key="optionKey(opt, i)">
                      <b>{{ optionKey(opt, i) }}.</b>
                      <MathText :text="optionText(opt)" />
                    </div>
                  </div>
                  <div v-if="repairText(q)" class="dlg-missing">{{ repairText(q) }}</div>
                  <div v-if="sourceText(q)" class="dlg-source">来源：{{ sourceText(q) }}</div>
                </div>
              </div>
              <div v-if="!dlgFiltered.length" class="dlg-empty">没有符合条件的题目</div>
            </div>
          </template>
        </template>

        <!-- 第二步：试卷设置 -->
        <template v-else-if="step === 2">
          <div class="settings-grid">
            <label>试卷名称
              <el-input v-model="form.title" maxlength="80" show-word-limit />
            </label>
            <label>作答模式
              <el-select v-model="form.mode" aria-label="作答模式">
                <el-option label="练习模式" value="practice" />
                <el-option label="模拟考试" value="quiz" />
              </el-select>
            </label>
            <label>考试时长（分钟）
              <el-input-number v-model="form.minutes" :min="1" :max="600" />
            </label>
            <label v-if="composeMode === 'ai_fill'">补题后目标题量
              <el-input-number v-model="aiTargetCount" :min="selectedIds.length + 1"
                :max="50" aria-label="补题后目标题量" />
            </label>
            <label v-if="composeMode === 'ai_kp'">目标题量
              <el-input-number v-model="aiTargetCount" :min="1" :max="20"
                aria-label="AI 出卷题量" />
            </label>
          </div>

          <template v-if="composeMode !== 'ai_kp'">
            <div class="score-head">
              <strong>分值设置</strong>
              <span>默认沿用原题分值，可逐题调整；题量由实际选题自动计算。</span>
              <span class="score-total">总分 {{ totalScore }} 分 · {{ selectedIds.length }} 题</span>
            </div>
            <div class="score-list">
              <div v-for="(q, i) in selectedQuestions" :key="q.question_id" class="score-row">
                <span class="score-index">{{ i + 1 }}</span>
                <div class="score-stem"><MathText :text="q.stem || ''" /></div>
                <span class="score-type">{{ typeChip(q) }}</span>
                <el-input-number v-model="scoreMap[q.question_id]" :min="0.5" :max="100"
                  :precision="1" :step="0.5" size="small" aria-label="题目分值" />
              </div>
            </div>
            <p v-if="composeMode === 'ai_fill'" class="dlg-hint">
              已勾选 {{ selectedIds.length }} 道，AI 会再补
              {{ Math.max(0, aiCountSafe() - selectedIds.length) }} 道同类题目；
              补出来的题按题目自带分值计分。
            </p>
          </template>
          <p v-else class="dlg-hint">
            将针对 {{ aiKpIds.length }} 个知识点生成 {{ aiCountSafe() }} 道
            {{ aiTypes.map((t) => AI_TYPE_OPTIONS.find((x) => x.value === t)?.label || t).join('、') || '题目' }}。
            题量与总分以生成结果为准。
          </p>
        </template>

        <!-- 第三步：生成与结果 -->
        <template v-else>
          <div v-if="!result" class="confirm-box">
            <h4>确认生成</h4>
            <p>出题方式：<b>{{ MODE_OPTIONS.find((m) => m.value === composeMode)?.label }}</b></p>
            <p>试卷名称：<b>{{ form.title }}</b></p>
            <p v-if="composeMode === 'ai_kp'">
              题目数量：<b>目标 {{ aiCountSafe() }} 题</b>（由 DeepSeek 依据所选知识点生成）
            </p>
            <p v-else-if="composeMode === 'ai_fill'">
              题目数量：<b>{{ selectedIds.length }} 道勾选 + 最多
                {{ Math.max(0, aiCountSafe() - selectedIds.length) }} 道 AI 补题</b>
            </p>
            <p v-else>题目数量：<b>{{ selectedIds.length }} 题</b>（实际勾选，不额外抽题、不 AI 补题）</p>
            <p v-if="composeMode !== 'ai_kp'">
              已选题目总分：<b>{{ totalScore }} 分</b> ·
              模式：<b>{{ form.mode === 'practice' ? '练习模式' : '模拟考试' }}</b>
            </p>
            <p v-else>模式：<b>{{ form.mode === 'practice' ? '练习模式' : '模拟考试' }}</b></p>
            <p>考试时长：<b>{{ form.minutes }} 分钟</b></p>
            <p class="confirm-note">
              会保存题目、选项、正确答案与解析快照，并在落库后校验题量与总分确实写入数据库。
              <span v-if="composeMode !== 'manual'">
                AI 生成失败时会显示真实原因，不会用假数据凑数。
              </span>
            </p>
            <p v-if="generatingHint" class="dlg-hint">{{ generatingHint }}</p>
            <el-button type="primary" size="large" :loading="generating" @click="generate">
              生成试卷
            </el-button>
          </div>
          <div v-else class="result-box">
            <div class="result-badge">✓ 试卷已生成并写入数据库</div>
            <h3>{{ result.title }}</h3>
            <div class="result-metrics">
              <div><span>题目数量</span><strong>{{ result.item_count }} 题</strong></div>
              <div><span>总分</span><strong>{{ result.total_score }} 分</strong></div>
              <div><span>考试模式</span><strong>{{ result.mode === 'practice' ? '练习模式' : '模拟考试' }}</strong></div>
              <div><span>建议时长</span><strong>{{ result.estimated_minutes }} 分钟</strong></div>
            </div>
            <p v-if="resultAiFill" class="result-note">
              AI 出题：{{ resultAiFill.enabled ? (resultAiFill.note || `补题 ${resultAiFill.filled} 道`) : '本次未调用 AI' }}
            </p>
            <p class="result-note">
              落库校验：{{ result.item_count }} 条题目关联 · 题量与总分一致 ·
              AI 补题 {{ resultAiCount }} 道
            </p>
            <p v-if="resultBlocked.length" class="result-warn">
              以下题目暂无可靠参考答案，本次不会自动计分：
              {{ resultBlocked.map((b: any) => b.question_id).join('、') }}
            </p>
            <div class="result-actions">
              <el-button @click="openPreview(result)">预览试卷</el-button>
              <el-button type="primary" :loading="starting === result.paper_id"
                @click="startPaper(result)">在线作答</el-button>
              <el-button @click="exportPdf(result)">导出 PDF</el-button>
            </div>
          </div>
        </template>
      </div>

      <template #footer>
        <div class="compose-footer">
          <el-button v-if="step === 1" plain :disabled="!selectedIds.length"
            @click="clearSelection">清空选择</el-button>
          <span class="footer-spacer" />
          <el-button @click="composeOpen = false">{{ result ? '完成' : '取消' }}</el-button>
          <el-button v-if="step > 1 && !result" @click="prevStep">上一步</el-button>
          <el-button v-if="step < 3" type="primary" @click="nextStep">下一步</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 历史试卷预览（旧 ?paper= 深链继续可用） -->
    <el-dialog v-model="previewOpen" title="试卷预览" width="min(760px, 92%)">
      <div v-if="previewLoading">正在加载试卷…</div>
      <template v-else-if="preview">
        <h3>{{ preview.title }}</h3>
        <p>{{ preview.item_count }} 题 · {{ preview.total_score }} 分</p>
        <div v-for="q in preview.questions || []" :key="q.question_id" class="preview-q">
          <MathText :text="q.stem || ''" />
          <div v-for="(opt, i) in q.options || []" :key="i">
            {{ optionKey(opt, i) }}. <MathText :text="optionText(opt)" />
          </div>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.bank-page { display: flex; flex-direction: column; gap: 18px; }
.bank-head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 20px;
}
.bank-head h2 { margin: 4px 0 6px; font-size: 26px; }
.bank-head p { margin: 0; color: #6b7280; font-size: 13px; line-height: 1.7; }
.bank-head .eyebrow { color: #6366f1; font-size: 12px; font-weight: 700; letter-spacing: .08em; }
.compose-cta { flex: 0 0 auto; margin-top: 8px; }

.bank-filters {
  display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  padding: 14px; border: 1px solid #e5e7eb; border-radius: 12px; background: #fff;
}
.bank-filters :deep(.el-input) { flex: 1 1 240px; }
.bank-filters :deep(.el-select) { flex: 0 0 160px; }
.bank-count { color: #6b7280; font-size: 13px; }
.bank-count strong { color: #4338ca; font-size: 18px; }
.bank-grid { width: 100%; }

.bank-card {
  display: flex; flex-direction: column; gap: 10px; height: auto;
  padding: 16px; border: 1px solid #e5e7eb; border-radius: 12px; background: #fff;
}
.bank-card:hover { border-color: #c7d2fe; box-shadow: 0 4px 16px rgb(31 41 55 / 6%); }
.bc-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.bc-tags span { padding: 1px 8px; border-radius: 6px; font-size: 11.5px; }
.bc-type { background: #eef2ff; color: #4338ca; }
.bc-kp { background: #f4f6fb; color: #4b5563; }
.bc-diff { background: #f0fdf4; color: #166044; }
.bc-stem { color: #1f2937; font-size: 13.5px; line-height: 1.72; overflow-wrap: anywhere; }
.bc-prompt { margin-right: 4px; color: #6b7280; font-weight: 600; }
.bc-options { display: flex; flex-direction: column; gap: 6px; }
.bc-option {
  display: flex; gap: 8px; align-items: flex-start;
  padding: 7px 10px; border: 1px solid #eef0f4; border-radius: 8px;
  background: #fafbfd; font-size: 13px; line-height: 1.6;
}
.bc-opt-key { flex: 0 0 auto; font-weight: 700; color: #6b7280; }
.bc-opt-text { min-width: 0; overflow-wrap: anywhere; }
.bc-missing {
  padding: 7px 10px; border: 1px solid #fde68a; border-radius: 8px;
  background: #fffbeb; color: #92400e; font-size: 12px;
}
.dlg-missing {
  margin-top: 6px; padding: 5px 8px; border: 1px solid #fde68a; border-radius: 6px;
  background: #fffbeb; color: #92400e; font-size: 11.5px;
}
.bc-source {
  padding-top: 8px; border-top: 1px dashed #eef0f4;
  color: #8b93a3; font-size: 11.5px; line-height: 1.6; overflow-wrap: anywhere;
}
.bc-source.is-empty { color: #b6bcc8; }
.empty-state {
  padding: 36px 16px; text-align: center; color: #8b93a3; font-size: 13px;
}
.empty-state h4 { margin: 0 0 6px; color: #4b5563; }

/* ---- 组卷弹窗 ---- */
.compose-dialog :deep(.el-dialog) {
  max-height: 85vh; display: flex; flex-direction: column;
}
.compose-dialog :deep(.el-dialog__body) { flex: 1; min-height: 0; overflow: hidden; padding-top: 8px; }
.compose-dialog :deep(.el-dialog__header) { padding-bottom: 10px; }
.compose-steps {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding-bottom: 12px; border-bottom: 1px solid #eef0f4; margin-bottom: 12px;
}
.compose-steps > span {
  padding: 4px 12px; border-radius: 999px; background: #f4f6fb;
  color: #8b93a3; font-size: 12px;
}
.compose-steps > span.active { background: #eef2ff; color: #4338ca; font-weight: 700; }
.compose-steps > span.done { background: #ecfdf5; color: #047857; }
.compose-selected { margin-left: auto; color: #4338ca; font-size: 13px; }
.compose-body { max-height: 66vh; overflow-y: auto; padding-right: 6px; }
.mode-picker {
  display: flex; flex-direction: column; gap: 8px;
  padding: 12px 14px; margin-bottom: 14px;
  border: 1px solid #e5e7eb; border-radius: 10px; background: #fafbff;
}
.mode-label { font-size: 12.5px; font-weight: 700; color: #374151; }
.mode-desc { margin: 0; color: #6b7280; font-size: 12px; line-height: 1.6; }
.mode-warn { color: #b45309; }
.ai-form {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px; margin-bottom: 10px;
}
.ai-form label { display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; color: #4b5563; }
.result-warn {
  margin: 10px 0; padding: 8px 10px; border: 1px solid #fde68a; border-radius: 8px;
  background: #fffbeb; color: #92400e; font-size: 12px; line-height: 1.6;
}
.dlg-filters { display: flex; flex-wrap: wrap; gap: 8px; }
.dlg-filters :deep(.el-input) { flex: 1 1 200px; }
.dlg-filters :deep(.el-select) { flex: 0 0 150px; }
.dlg-hint { margin: 10px 0; color: #6b7280; font-size: 12px; }
.dlg-list { display: flex; flex-direction: column; gap: 8px; }
.dlg-row {
  display: flex; gap: 10px; align-items: flex-start; cursor: pointer;
  padding: 12px; border: 1px solid #e5e7eb; border-radius: 10px; background: #fff;
}
.dlg-row:hover { border-color: #c7d2fe; }
.dlg-row.picked { border-color: #a5b4fc; background: #f8f9ff; }
.dlg-q { flex: 1; min-width: 0; }
.dlg-q-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 5px; }
.dlg-q-tags span {
  padding: 1px 7px; border-radius: 5px; background: #f4f6fb;
  color: #6b7280; font-size: 11px;
}
.dlg-stem { font-size: 13px; line-height: 1.7; color: #1f2937; overflow-wrap: anywhere; }
.dlg-options {
  display: flex; flex-direction: column; gap: 3px; margin-top: 6px;
  color: #4b5563; font-size: 12.5px; line-height: 1.6;
}
.dlg-options > div { display: flex; gap: 6px; }
.dlg-source { margin-top: 6px; color: #9aa1af; font-size: 11px; overflow-wrap: anywhere; }
.dlg-empty { padding: 24px; text-align: center; color: #8b93a3; font-size: 13px; }

.settings-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.settings-grid label { display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; color: #4b5563; }
.score-head {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  margin: 18px 0 10px; font-size: 12.5px; color: #6b7280;
}
.score-head strong { color: #1f2937; }
.score-total { margin-left: auto; color: #4338ca; font-weight: 700; }
.score-list { display: flex; flex-direction: column; gap: 8px; }
.score-row {
  display: flex; gap: 10px; align-items: center;
  padding: 10px 12px; border: 1px solid #eef0f4; border-radius: 9px; background: #fafbfd;
}
.score-index {
  flex: 0 0 26px; height: 26px; display: grid; place-items: center;
  border-radius: 7px; background: #eef2ff; color: #4338ca; font-size: 12px; font-weight: 700;
}
.score-stem {
  flex: 1; min-width: 0; font-size: 12.5px; line-height: 1.6; color: #1f2937;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.score-type { flex: 0 0 auto; color: #8b93a3; font-size: 11.5px; }

.confirm-box { padding: 8px 4px 20px; }
.confirm-box h4 { margin: 0 0 12px; font-size: 17px; }
.confirm-box p { margin: 0 0 8px; color: #4b5563; font-size: 13.5px; }
.confirm-note { color: #6b7280 !important; font-size: 12.5px !important; line-height: 1.7; }
.result-box { padding: 6px 4px 20px; }
.result-badge {
  display: inline-block; padding: 5px 12px; border-radius: 8px;
  background: #ecfdf5; color: #047857; font-size: 12.5px; font-weight: 700;
}
.result-box h3 { margin: 12px 0; font-size: 20px; }
.result-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.result-metrics > div {
  padding: 12px; border: 1px solid #e5e7eb; border-radius: 10px; background: #fafbfd;
  display: flex; flex-direction: column; gap: 4px;
}
.result-metrics span { color: #8b93a3; font-size: 11.5px; }
.result-metrics strong { color: #1f2937; font-size: 16px; }
.result-note { margin: 14px 0; color: #6b7280; font-size: 12.5px; line-height: 1.7; }
.result-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.compose-footer { display: flex; align-items: center; gap: 8px; }
.footer-spacer { flex: 1; }
.preview-q {
  padding: 10px 0; border-bottom: 1px solid #eef0f4; font-size: 13px; line-height: 1.7;
}

@media (max-width: 900px) {
  .settings-grid { grid-template-columns: 1fr; }
  .result-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .bank-filters :deep(.el-select) { flex: 1 1 140px; }
}
</style>
