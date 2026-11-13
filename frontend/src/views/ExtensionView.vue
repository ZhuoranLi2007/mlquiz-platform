<script setup lang="ts">
/**
 * 扩展学习 = 高校试卷与专题练习资源库（V2.3）。
 *
 * 设计约束：
 *  - 主体是「整套试卷/题集」，不再逐道展示题目；零散题目与网课视频进兼容区；
 *  - 缺失字段显示「未记录 / 待评估」，不填 0、不猜年份、不伪造匹配度；
 *  - 下载按钮按真实资源分四态，绝不生成假 PDF，也不留空转按钮；
 *  - 在线作答按「整卷 / 部分 / 不可用」三态展示，部分作答会明确说明不是原卷。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowDown, Collection, Search } from '@element-plus/icons-vue'
import {
  ChatDotRound, Document, Files, List, Reading,
} from '@element-plus/icons-vue'
import { api } from '../api/client'
import PageHeader from '../components/PageHeader.vue'
import TechnicalDetails from '../components/TechnicalDetails.vue'
import { friendlyError } from '../utils/presentation'

const route = useRoute()
const router = useRouter()

interface PdfState {
  state: 'local' | 'external' | 'web' | 'none'
  label: string
  url: string
  downloadable: boolean
  note: string
}
interface OnlineState {
  state: 'full' | 'partial' | 'none'
  label: string
  note: string
  count: number
}
interface PaperCard {
  epaper_id: string
  title: string
  institution: string
  region: string
  region_label: string
  origin_group: string
  origin_group_label: string
  year: number | null
  semester: string
  paper_type: string
  paper_type_label: string
  difficulty: string
  difficulty_label: string
  difficulty_known: boolean
  course_match_level: string
  course_match_label: string
  question_count: number
  online_question_count: number
  total_score: number | null
  duration_minutes: number | null
  has_answer: boolean | null
  answer_note: string
  kp_names: string[]
  pdf: PdfState
  intro: string
  zh: {
    status: string
    label: string
    pdf_available: boolean
    pdf_url: string
    viewable: boolean
    generated_at: string
    note: string
    model: string
    version: string
    downloadable: boolean
    download_blocked_reason: string
    thumb_available?: boolean
    thumbnail_url?: string
  }
  thumbnail: {
    available: boolean
    reason: string
    url: string
    note: string
  }
  online: OnlineState
  structure?: {
    question_count: number
    online_question_count: number
    full_paper: boolean
    review_status: string
    thumb_generated_at: string
    note: string
  }
  source: {
    source_id: string; name: string; org: string; url: string
    verify_status: string; verify_label: string
    license: string; allows_copy: boolean
  }
  review_status: string
  favorited: boolean
  origin_note: string
  items?: Array<{
    item_id: string; position: number; title: string; question_type: string
    difficulty: string; scope_class: string; score: number
    imported: boolean; question_id: string; online_ready: boolean
    excerpt: string; source_url: string
  }>
  difficulty_basis?: string
  match_basis?: string
  score_note?: string
  preview_note?: string
}
interface FacetItem { value: string | number; label: string; count: number }

const ORIGINS = [
  { value: '', label: '全部来源' },
  { value: 'overseas', label: '国外高校' },
  { value: 'domestic', label: '国内高校' },
  { value: 'industry', label: '企业面试' },
  { value: 'other', label: '其他来源' },
]
const PAPER_TYPES = [
  { value: '', label: '全部类型' },
  { value: 'final', label: '期末试卷' },
  { value: 'midterm', label: '期中试卷' },
  { value: 'mock', label: '模拟试卷' },
  { value: 'topic_set', label: '专题练习' },
  { value: 'interview', label: '面试题集' },
]
const DIFFICULTIES = [
  { value: '', label: '全部难度' },
  { value: 'easy', label: '简单' },
  { value: 'medium', label: '中等' },
  { value: 'hard', label: '较难' },
  { value: 'very_hard', label: '高难度' },
  { value: 'unknown', label: '待评估' },
]
const MATCHES = [
  { value: '', label: '全部匹配度' },
  { value: 'high', label: '高度匹配' },
  { value: 'partial', label: '部分拓展' },
  { value: 'challenge', label: '拓展挑战' },
  { value: 'unassessed', label: '尚未评估' },
]

const loading = ref(false)
const error = ref('')
const papers = ref<PaperCard[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(12)
const pages = ref(1)
const facets = ref<any>({ origins: [], paper_types: [], difficulties: [], matches: [], years: [] })
const rules = ref('')
const sortOptions = ref<Array<{ value: string; label: string }>>([])

const q = ref('')
const origin = ref('')
const paperType = ref('')
const difficulty = ref('')
const match = ref('')
const year = ref<string>('')
const yearFrom = ref<number | null>(null)
const yearTo = ref<number | null>(null)
const sort = ref('match')
const yearRangeOpen = ref(false)

const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref<PaperCard | null>(null)
const previewOpen = ref(false)

const orphans = ref<any>({ items: [], entries: [], total_items: 0, total_entries: 0 })
const orphansLoading = ref(false)
const legacyVideo = ref(false)

const hasFilters = computed(() =>
  Boolean(q.value || origin.value || paperType.value || difficulty.value
    || match.value || year.value || yearFrom.value || yearTo.value))
const activeFilterCount = computed(() => [
  origin.value, paperType.value, difficulty.value, match.value,
  year.value, yearFrom.value, yearTo.value,
].filter(Boolean).length)

const yearOptions = computed<FacetItem[]>(() => facets.value.years || [])

// 资源类型图标：PDF 是文件格式，不是资源类型；按类型给不同线性图标 + 浅色底。
const TYPE_META: Record<string, { icon: any; tone: string }> = {
  midterm: { icon: Document, tone: 'doc' },
  final: { icon: Document, tone: 'doc' },
  mock: { icon: List, tone: 'list' },
  topic_set: { icon: Reading, tone: 'book' },
  interview: { icon: ChatDotRound, tone: 'chat' },
  other: { icon: Files, tone: 'file' },
}

function typeMeta(paper: PaperCard) {
  return TYPE_META[paper.paper_type] || TYPE_META.other
}

/** 缩略图加载失败（PDF 损坏 / 缓存被清）时退回占位封面，不留破图。 */
function onThumbError(paper: PaperCard) {
  paper.thumbnail.available = false
  paper.thumbnail.reason = 'load_failed'
}

/**
 * 中文版首页预览加载失败时，退回原版缩略图。
 * 布局不变：只是把预览图换成中文版那一张（链接本来指向中文版 PDF）。
 */
function onZhThumbError(paper: any) {
  if (paper?.zh) paper.zh.thumb_available = false
}

/** 卡片上的一行真实信息：只拼有数据的字段，不写「未记录」。 */
function paperLine(paper: PaperCard) {
  const bits: string[] = []
  if (paper.question_count) bits.push(`${paper.question_count} 道已录入小题`)
  if (paper.duration_minutes) bits.push(`${paper.duration_minutes} 分钟`)
  return bits.join(' · ')
}

/** 中文翻译按钮的简短提示，不暴露技术细节。 */
function zhHint(paper: PaperCard) {
  if (!paper.zh.pdf_available) return '中文翻译版尚未生成'
  if (!paper.zh.viewable) return '中文版暂未开放查看（授权待确认）'
  return '打开中文翻译版'
}

function queryParams() {
  const params: Record<string, any> = {
    sort: sort.value, page: page.value, page_size: pageSize.value,
  }
  if (q.value.trim()) params.q = q.value.trim()
  if (origin.value) params.origin = origin.value
  if (paperType.value) params.paper_type = paperType.value
  if (difficulty.value) params.difficulty = difficulty.value
  if (match.value) params.match = match.value
  if (year.value) {
    params.year_from = Number(year.value)
    params.year_to = Number(year.value)
  } else {
    if (yearFrom.value) params.year_from = yearFrom.value
    if (yearTo.value) params.year_to = yearTo.value
  }
  return params
}

function syncQuery() {
  const query: Record<string, string> = {}
  if (q.value.trim()) query.q = q.value.trim()
  if (origin.value) query.origin = origin.value
  if (paperType.value) query.type = paperType.value
  if (difficulty.value) query.difficulty = difficulty.value
  if (match.value) query.match = match.value
  if (year.value) query.year = year.value
  if (yearFrom.value) query.year_from = String(yearFrom.value)
  if (yearTo.value) query.year_to = String(yearTo.value)
  if (sort.value !== 'match') query.sort = sort.value
  if (page.value > 1) query.page = String(page.value)
  router.replace({ path: '/extension', query })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const response: any = await api.get('/external/papers', queryParams())
    papers.value = response.items || []
    total.value = response.total || 0
    page.value = response.page || 1
    pages.value = response.pages || 1
    facets.value = response.facets || facets.value
    rules.value = response.rules || ''
    sortOptions.value = response.sort_options || []
  } catch (reason) {
    error.value = friendlyError(reason, '试卷资源加载失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

async function loadOrphans() {
  orphansLoading.value = true
  try {
    orphans.value = await api.get('/external/orphans')
  } catch {
    orphans.value = { items: [], entries: [], total_items: 0, total_entries: 0 }
  } finally {
    orphansLoading.value = false
  }
}

function applyFilter() {
  page.value = 1
  syncQuery()
  load()
}

function resetFilters() {
  q.value = ''
  origin.value = ''
  paperType.value = ''
  difficulty.value = ''
  match.value = ''
  year.value = ''
  yearFrom.value = null
  yearTo.value = null
  sort.value = 'match'
  applyFilter()
}

function changePage(next: number) {
  if (next < 1 || next > pages.value || next === page.value) return
  page.value = next
  syncQuery()
  load()
}

async function openDetail(paper: PaperCard) {
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  previewOpen.value = false
  try {
    detail.value = await api.get(`/external/papers/${paper.epaper_id}`)
    router.replace({ path: '/extension', query: { ...route.query, paper: paper.epaper_id } })
  } catch (reason) {
    ElMessage.error(friendlyError(reason, '试卷详情加载失败'))
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detailOpen.value = false
  const next = { ...route.query }
  delete next.paper
  router.replace({ path: '/extension', query: next })
}

async function toggleFavorite(paper: PaperCard) {
  try {
    if (paper.favorited) {
      const list: any = await api.get('/me/favorites')
      const hit = (list.items || []).find((f: any) =>
        f.kind === 'external_paper' && f.target_id === paper.epaper_id)
      if (hit) await api.del(`/me/favorites/${hit.entry_id}`)
      paper.favorited = false
      ElMessage.success('已取消收藏')
    } else {
      await api.post('/me/favorites', {
        kind: 'external_paper', target_id: paper.epaper_id,
        title: paper.title, note: paper.institution || paper.origin_group_label,
      })
      paper.favorited = true
      ElMessage.success('已收藏到个人中心')
    }
  } catch (reason) {
    ElMessage.error(friendlyError(reason, '收藏操作失败'))
  }
}

async function startAttempt(paper: PaperCard) {
  if (paper.online.state === 'none') {
    ElMessage.info(paper.online.note)
    return
  }
  try {
    const out: any = await api.post(`/external/papers/${paper.epaper_id}/attempt`)
    if (out.note) ElMessage.info(out.note)
    await router.push(`/exam/${out.attempt_id}`)
  } catch (reason) {
    ElMessage.error(friendlyError(reason, '暂时无法开始作答'))
  }
}

function answerText(value: boolean | null | undefined) {
  if (value === true) return '原卷附参考答案'
  if (value === false) return '原卷未附答案'
  return '未核实'
}

function yearText(paper: PaperCard) {
  if (!paper.year) return '年份未记录'
  return paper.semester ? `${paper.year} · ${paper.semester}` : `${paper.year} 年`
}

function difficultyClass(paper: PaperCard) {
  return `diff-${paper.difficulty || 'unknown'}`
}

function mapLegacyQuery() {
  const legacyGroup = String(route.query.group || route.query.cat || '')
  if (legacyGroup === 'video') legacyVideo.value = true
  if (['overseas', 'domestic'].includes(legacyGroup)) origin.value = legacyGroup
  else if (legacyGroup === 'interview') origin.value = 'industry'
}

function readQuery() {
  q.value = String(route.query.q || '')
  origin.value = String(route.query.origin || '')
  paperType.value = String(route.query.type || '')
  difficulty.value = String(route.query.difficulty || '')
  match.value = String(route.query.match || '')
  year.value = String(route.query.year || '')
  yearFrom.value = route.query.year_from ? Number(route.query.year_from) : null
  yearTo.value = route.query.year_to ? Number(route.query.year_to) : null
  sort.value = String(route.query.sort || 'match')
  page.value = route.query.page ? Number(route.query.page) : 1
}

onMounted(async () => {
  readQuery()
  mapLegacyQuery()
  await Promise.all([load(), loadOrphans()])
  const paperId = String(route.query.paper || '')
  if (paperId) {
    const hit = papers.value.find((p) => p.epaper_id === paperId)
    if (hit) await openDetail(hit)
    else {
      try {
        detail.value = await api.get(`/external/papers/${paperId}`)
        detailOpen.value = true
      } catch {
        /* 收藏指向的试卷已下线时保持列表页 */
      }
    }
  }
})

watch(() => route.query.paper, (value) => {
  if (!value && detailOpen.value) detailOpen.value = false
})
</script>

<template>
  <section class="extension-page">
    <PageHeader
      eyebrow="课程之外"
      title="扩展学习"
      description="浏览高校试卷与专题练习：按地区、类型、难度筛选，下载原卷或直接作答。"
    />

    <div v-if="error" class="feedback-banner feedback-banner--danger" role="alert">
      <span>{{ error }}</span>
      <el-button link @click="load">重新加载</el-button>
    </div>

    <!-- 搜索 + 排序 -->
    <div class="lib-toolbar">
      <el-input v-model="q" class="lib-search" clearable :prefix-icon="Search"
        placeholder="搜索试卷名称、高校或知识点" aria-label="搜索试卷"
        @keyup.enter="applyFilter" @clear="applyFilter" />
      <el-select v-model="sort" class="lib-sort" aria-label="排序方式"
        @change="applyFilter">
        <el-option v-for="option in sortOptions" :key="option.value"
          :label="option.label" :value="option.value" />
      </el-select>
      <el-button @click="applyFilter">搜索</el-button>
    </div>

    <!-- 筛选项：桌面端两列紧凑，只显示分类名称，不带计数 -->
    <div class="lib-filters">
      <div class="filter-grid">
        <div class="filter-cell">
          <span class="filter-name">来源地区</span>
          <div class="chip-group">
            <button v-for="option in ORIGINS" :key="option.value" type="button"
              class="group-chip" :class="{ active: origin === option.value }"
              :aria-pressed="origin === option.value"
              @click="origin = option.value; applyFilter()">{{ option.label }}</button>
          </div>
        </div>
        <div class="filter-cell">
          <span class="filter-name">资源类型</span>
          <div class="chip-group">
            <button v-for="option in PAPER_TYPES" :key="option.value" type="button"
              class="group-chip" :class="{ active: paperType === option.value }"
              :aria-pressed="paperType === option.value"
              @click="paperType = option.value; applyFilter()">{{ option.label }}</button>
          </div>
        </div>
        <div class="filter-cell">
          <span class="filter-name">难度等级</span>
          <div class="chip-group">
            <button v-for="option in DIFFICULTIES" :key="option.value" type="button"
              class="group-chip" :class="{ active: difficulty === option.value }"
              :aria-pressed="difficulty === option.value"
              @click="difficulty = option.value; applyFilter()">{{ option.label }}</button>
          </div>
        </div>
        <div class="filter-cell">
          <span class="filter-name">课程匹配</span>
          <div class="chip-group">
            <button v-for="option in MATCHES" :key="option.value" type="button"
              class="group-chip" :class="{ active: match === option.value }"
              :aria-pressed="match === option.value"
              @click="match = option.value; applyFilter()">{{ option.label }}</button>
          </div>
        </div>

        <div class="filter-cell">
          <span class="filter-name">年份</span>
          <div class="chip-group">
            <el-select v-if="yearOptions.length" v-model="year" class="year-select"
              aria-label="按年份筛选" @change="applyFilter">
              <el-option label="全部年份" value="" />
              <el-option v-for="item in yearOptions" :key="item.value"
                :label="item.label" :value="String(item.value)" />
            </el-select>
            <span v-else class="filter-empty">年份未记录</span>
            <button type="button" class="group-chip ghost"
              :class="{ active: yearRangeOpen }"
              @click="yearRangeOpen = !yearRangeOpen">
              年份范围<el-icon class="el-icon--right"><ArrowDown /></el-icon>
            </button>
          </div>
        </div>
        <div class="filter-cell filter-cell--result">
          <span class="result-count">共 <b>{{ total }}</b> 套<span
            v-if="hasFilters"> · 已筛 {{ activeFilterCount }} 项</span></span>
          <el-button v-if="hasFilters" link @click="resetFilters">清空筛选</el-button>
        </div>
      </div>

      <div v-if="yearRangeOpen" class="filter-range-row">
        <span class="filter-name">起止年份</span>
        <span class="year-range-inputs">
          <el-input-number v-model="yearFrom" :min="2000" :max="2100"
            :controls="false" placeholder="从" />
          <span>—</span>
          <el-input-number v-model="yearTo" :min="2000" :max="2100"
            :controls="false" placeholder="到" />
          <el-button size="small" @click="applyFilter">应用</el-button>
        </span>
      </div>
    </div>

    <!-- 试卷卡片 -->
    <div v-loading="loading" class="paper-grid">
      <article v-for="paper in papers" :key="paper.epaper_id" class="paper-card">
        <button type="button" class="fav-btn" :class="{ on: paper.favorited }"
          :aria-label="paper.favorited ? '取消收藏' : '收藏这套试卷'"
          :title="paper.favorited ? '取消收藏' : '收藏'"
          @click.stop="toggleFavorite(paper)">
          <el-icon><Collection /></el-icon>
        </button>

        <!-- 左：真实 PDF 第一页缩略图；无 PDF / 无预览授权时用类型化占位封面 -->
        <a v-if="paper.pdf.url" class="paper-thumb" :href="paper.pdf.url"
          target="_blank" rel="noopener" :aria-label="`打开 ${paper.title} 原卷`">
          <img v-if="paper.thumbnail.available" :src="paper.thumbnail.url"
            :alt="`${paper.title} 第一页预览`" loading="lazy" decoding="async"
            @error="onThumbError(paper)" />
          <span v-else class="thumb-placeholder" :class="`tone-${typeMeta(paper).tone}`">
            <el-icon><component :is="typeMeta(paper).icon" /></el-icon>
            <small>{{ paper.paper_type_label }}</small>
          </span>
        </a>
        <div v-else class="paper-thumb is-static">
          <img v-if="paper.thumbnail.available" :src="paper.thumbnail.url"
            :alt="`${paper.title} 第一页预览`" loading="lazy" decoding="async"
            @error="onThumbError(paper)" />
          <span v-else class="thumb-placeholder" :class="`tone-${typeMeta(paper).tone}`">
            <el-icon><component :is="typeMeta(paper).icon" /></el-icon>
            <small>{{ paper.paper_type_label }}</small>
          </span>
        </div>

        <div class="paper-info">
          <h3 class="paper-title">
            <button type="button" class="title-link" @click="openDetail(paper)">
              {{ paper.title }}
            </button>
          </h3>
          <p class="paper-org">{{ paper.institution || '来源机构未登记' }}</p>
          <div class="paper-tags">
            <span class="tone-chip">{{ paper.paper_type_label }}</span>
            <span v-if="paper.difficulty_known" class="tone-chip"
              :class="difficultyClass(paper)">{{ paper.difficulty_label }}</span>
          </div>
          <p v-if="paperLine(paper)" class="paper-line">{{ paperLine(paper) }}</p>

          <div class="paper-actions">
            <a v-if="paper.pdf.url" class="card-action" :href="paper.pdf.url"
              target="_blank" rel="noopener">原始 PDF<span aria-hidden="true">↗</span></a>
            <el-tooltip v-else :content="paper.pdf.note" placement="top">
              <span class="disabled-wrap">
                <el-button size="small" disabled>原始 PDF</el-button>
              </span>
            </el-tooltip>

            <a v-if="paper.zh.viewable" class="card-action" :href="paper.zh.pdf_url"
              target="_blank" rel="noopener">中文翻译<span aria-hidden="true">↗</span></a>
            <el-tooltip v-else :content="zhHint(paper)" placement="top">
              <span class="disabled-wrap">
                <el-button size="small" disabled>中文翻译</el-button>
              </span>
            </el-tooltip>

            <el-button v-if="paper.online.state !== 'none'" size="small" type="primary"
              class="primary-action" @click="startAttempt(paper)">在线作答</el-button>
            <el-tooltip v-else :content="paper.online.note" placement="top">
              <span class="disabled-wrap primary-action">
                <el-button size="small" disabled>在线作答</el-button>
              </span>
            </el-tooltip>
          </div>
        </div>
      </article>

      <div v-if="!loading && !papers.length" class="empty-state">
        <strong>{{ hasFilters ? '当前筛选下没有试卷' : '还没有试卷资源' }}</strong>
        <p>{{ hasFilters ? '可以清空筛选条件再试。' : '平台只收录来源明确、可追溯的试卷，不用未核实链接凑数量。' }}</p>
        <el-button v-if="hasFilters" @click="resetFilters">清空筛选</el-button>
      </div>
    </div>

    <div v-if="pages > 1" class="lib-pager">
      <el-button :disabled="page <= 1" @click="changePage(page - 1)">上一页</el-button>
      <span>第 {{ page }} / {{ pages }} 页</span>
      <el-button :disabled="page >= pages" @click="changePage(page + 1)">下一页</el-button>
    </div>

    <!-- 兼容区：零散题目与视频，不伪装成整套试卷 -->
    <section v-if="orphans.total_items || orphans.total_entries" class="orphan-section">
      <TechnicalDetails
        :title="`其他扩展资源（${orphans.total_items} 条零散题目与视频 · ${orphans.total_entries} 个来源入口）`"
        :open="legacyVideo">
        <p class="orphan-note">{{ orphans.note }}</p>

        <div v-if="orphans.items?.length" class="orphan-grid">
          <article v-for="item in orphans.items" :key="item.item_id" class="orphan-card">
            <div class="orphan-head">
              <span class="tone-chip muted-chip">{{ item.kind_label }}</span>
              <h4>{{ item.title }}</h4>
            </div>
            <p v-if="item.excerpt" class="orphan-excerpt">{{ item.excerpt }}</p>
            <div class="orphan-meta">
              <span>{{ item.source_name }}</span>
              <span v-if="item.difficulty">难度 {{ item.difficulty }}</span>
            </div>
            <a v-if="item.url" class="action-link" :href="item.url"
              target="_blank" rel="noopener">打开原始来源<span aria-hidden="true">↗</span></a>
          </article>
        </div>

        <div v-if="orphans.entries?.length" class="source-entries">
          <h4>只有来源主页的资源</h4>
          <ul>
            <li v-for="entry in orphans.entries" :key="entry.source_id">
              <a :href="entry.url" target="_blank" rel="noopener">{{ entry.name }}</a>
              <span>{{ entry.org }} · {{ entry.verify_label }}</span>
            </li>
          </ul>
        </div>
      </TechnicalDetails>
    </section>

    <!-- 试卷详情：居中弹窗，只保留学生需要的信息 -->
    <el-dialog v-model="detailOpen" width="min(720px, 94vw)" align-center destroy-on-close
      class="paper-detail-dialog" @closed="closeDetail">
      <template #header>
        <span class="dlg-title">试卷详情</span>
      </template>
      <div v-loading="detailLoading" class="dlg-body">
        <template v-if="detail">
          <div class="dlg-hero">
            <a v-if="(detail.thumbnail.available || detail.zh.thumb_available)
              && (detail.zh.pdf_url || detail.pdf.url)"
              class="dlg-thumb" :href="detail.zh.pdf_url || detail.pdf.url"
              target="_blank" rel="noopener">
              <img :src="detail.zh.thumb_available ? detail.zh.thumbnail_url
                : detail.thumbnail.url"
                :alt="`${detail.title} ${detail.zh.thumb_available ? '中文版' : ''}第一页预览`"
                loading="lazy"
                @error="detail.zh.thumb_available
                  ? onZhThumbError(detail) : onThumbError(detail)" />
            </a>
            <div v-else class="dlg-thumb is-static">
              <img v-if="detail.thumbnail.available" :src="detail.thumbnail.url"
                :alt="`${detail.title} 第一页预览`" loading="lazy"
                @error="onThumbError(detail)" />
              <span v-else class="thumb-placeholder" :class="`tone-${typeMeta(detail).tone}`">
                <el-icon><component :is="typeMeta(detail).icon" /></el-icon>
                <small>{{ detail.paper_type_label }}</small>
              </span>
            </div>
            <div class="dlg-head">
              <h3>{{ detail.title }}</h3>
              <p class="dlg-org">{{ detail.institution || '来源机构未登记' }}</p>
              <div class="paper-tags">
                <span class="tone-chip">{{ detail.paper_type_label }}</span>
                <span v-if="detail.difficulty_known" class="tone-chip"
                  :class="difficultyClass(detail)">{{ detail.difficulty_label }}</span>
              </div>
            </div>
          </div>

          <p v-if="detail.intro" class="dlg-intro">{{ detail.intro }}</p>

          <dl class="dlg-facts">
            <div v-if="detail.question_count">
              <dt>已录入题目</dt><dd>{{ detail.question_count }} 道</dd>
            </div>
            <div v-if="detail.duration_minutes">
              <dt>考试时长</dt><dd>{{ detail.duration_minutes }} 分钟</dd>
            </div>
            <div v-if="detail.has_answer !== null">
              <dt>参考答案</dt><dd>{{ answerText(detail.has_answer) }}</dd>
            </div>
            <div v-if="detail.year">
              <dt>年份</dt><dd>{{ yearText(detail) }}</dd>
            </div>
          </dl>

          <div v-if="detail.kp_names?.length" class="dlg-block">
            <h4>主要知识点</h4>
            <div class="knowledge-list">
              <span v-for="name in detail.kp_names.slice(0, 6)" :key="name">{{ name }}</span>
            </div>
          </div>

          <div class="dlg-actions">
            <a v-if="detail.pdf.url" class="action-link" :href="detail.pdf.url"
              target="_blank" rel="noopener">原始 PDF<span aria-hidden="true">↗</span></a>
            <el-button v-else disabled>原始 PDF</el-button>

            <a v-if="detail.zh.viewable" class="action-link" :href="detail.zh.pdf_url"
              target="_blank" rel="noopener">中文翻译<span aria-hidden="true">↗</span></a>
            <el-tooltip v-else :content="zhHint(detail)" placement="top">
              <span class="disabled-wrap"><el-button disabled>中文翻译</el-button></span>
            </el-tooltip>

            <el-button v-if="detail.online.state !== 'none'" type="primary"
              @click="startAttempt(detail)">在线作答</el-button>
            <el-button v-else disabled>在线作答</el-button>
          </div>

          <p v-if="detail.online.state === 'partial'" class="dlg-note">
            {{ detail.online.note }}
          </p>
          <p v-if="!detail.zh.viewable" class="dlg-note">{{ zhHint(detail) }}</p>

          <a v-if="detail.source.url" class="dlg-source" :href="detail.source.url"
            target="_blank" rel="noopener">原始来源<span aria-hidden="true">↗</span></a>
        </template>
      </div>
    </el-dialog>
  </section>
</template>

<style scoped>
.extension-page { max-width: 1280px; margin: 0 auto; overflow-x: clip; }
/* 首屏优先：这一页的页头比全局更紧，把试卷列表往上提。 */
.extension-page :deep(.page-header) { margin-bottom: 14px; }
.extension-page :deep(.page-header h2) { font-size: 26px; }
.extension-page :deep(.page-header p) { margin-top: 4px; font-size: 14px; }
.feedback-banner {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
  margin-bottom: 18px; padding: 12px 15px; border: 1px solid #fecaca;
  border-radius: 11px; color: #991b1b; background: #fef2f2;
}

.lib-toolbar { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.lib-search { max-width: 460px; }
.lib-sort { width: 190px; flex: none; }
.lib-toolbar :deep(.el-input__wrapper) { min-height: 42px; }
.lib-toolbar :deep(.el-select__wrapper) { min-height: 42px; }
.lib-toolbar :deep(.el-button) { min-height: 42px; }

.lib-filters {
  padding: 10px 14px; border: 1px solid var(--border, #e5e7eb);
  border-radius: 12px; background: #fff;
}
.filter-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 2px 26px; }
.filter-cell { display: flex; align-items: flex-start; gap: 10px; padding: 4px 0; min-width: 0; }
.filter-cell--result { align-items: center; justify-content: flex-end; gap: 8px; }
.filter-name { flex: 0 0 60px; padding-top: 6px; color: #5f6675; font-size: 12.5px; font-weight: 600; }
.result-count { color: #5f6675; font-size: 12.5px; }
.result-count b { color: #1f2937; font-size: 14px; }
.filter-cell--result :deep(.el-button) { margin-left: 0; }
.filter-range-row { display: flex; align-items: center; gap: 10px; padding: 6px 0 2px; border-top: 1px dashed #eef0f4; min-width: 0; }
.filter-range-row .filter-name { padding-top: 0; line-height: 30px; }
.chip-group { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; min-width: 0; }
.group-chip {
  min-height: 30px; padding: 4px 10px; border: 1px solid var(--border, #e5e7eb);
  border-radius: 999px; background: #fff; color: #4b5563; cursor: pointer; font: inherit;
  font-size: 12.5px; line-height: 1.4;
}
.group-chip:hover { border-color: #a5b4fc; color: #4338ca; }
.group-chip.active { border-color: #c7d2fe; background: #eef2ff; color: #4338ca; font-weight: 650; }
.group-chip.ghost { display: inline-flex; align-items: center; gap: 4px; }
.group-chip:focus-visible, .action-link:focus-visible, .title-link:focus-visible, .fav-btn:focus-visible, .mini-link:focus-visible { outline: 3px solid rgb(79 70 229 / 22%); outline-offset: 2px; }
.year-select { width: 150px; }
.filter-empty { color: #9aa1ae; font-size: 12.5px; line-height: 30px; }
.year-range-inputs { display: inline-flex; align-items: center; gap: 6px; }
.year-range-inputs :deep(.el-input-number) { width: 88px; }

.paper-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }
.paper-card {
  position: relative; display: flex; align-items: stretch; gap: 14px;
  min-width: 0; min-height: 180px; padding: 12px;
  border: 1px solid var(--border, #e5e7eb); border-radius: 12px; background: #fff;
  transition: border-color .15s, box-shadow .15s;
}
.paper-card:hover { border-color: #c7d2fe; box-shadow: 0 4px 16px rgb(31 41 55 / 7%); }

/* 左侧：真实 PDF 第一页缩略图，保持原始比例不裁切 */
.paper-thumb {
  display: block; width: 120px; flex: none; align-self: flex-start;
  border: 1px solid #d8dce6; border-radius: 6px; overflow: hidden;
  background: #fff; box-shadow: 0 1px 3px rgb(31 41 55 / 10%);
  text-decoration: none; line-height: 0;
}
.paper-thumb img { display: block; width: 100%; height: auto; }
.paper-thumb:hover { border-color: #a5b4fc; box-shadow: 0 2px 10px rgb(79 70 229 / 14%); }
.paper-thumb.is-static { cursor: default; }
.paper-thumb.is-static:hover { border-color: #d8dce6; box-shadow: 0 1px 3px rgb(31 41 55 / 10%); }
.thumb-placeholder {
  display: flex; width: 100%; aspect-ratio: 0.77; flex-direction: column;
  align-items: center; justify-content: center; gap: 7px; line-height: 1.4;
}
.thumb-placeholder :deep(.el-icon) { font-size: 26px; }
.thumb-placeholder small { font-size: 11px; opacity: .85; }
.tone-doc { background: #eef2ff; color: #4338ca; }
.tone-chat { background: #ecfdf5; color: #047857; }
.tone-book { background: #f5f3ff; color: #6d28d9; }
.tone-list { background: #fff7ed; color: #b45309; }
.tone-file { background: #f3f4f6; color: #4b5563; }

.fav-btn {
  position: absolute; top: 8px; right: 8px; z-index: 1; display: grid;
  place-items: center; width: 26px; height: 26px; padding: 0; border: 0;
  border-radius: 7px; background: transparent; color: #8b93a3;
  cursor: pointer; font-size: 15px;
}
.fav-btn:hover { background: #f3f4f6; color: #4b5563; }
.fav-btn.on { color: #d97706; }

/* 右侧：标题 / 机构 / 标签 / 底部操作 */
.paper-info { display: flex; min-width: 0; flex: 1; flex-direction: column; padding-right: 20px; }
.paper-title {
  margin: 0; color: var(--text, #1f2937); font-size: 15px; font-weight: 620;
  line-height: 1.42; letter-spacing: -.01em;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.title-link { padding: 0; border: 0; background: transparent; color: inherit; cursor: pointer; font: inherit; text-align: left; overflow-wrap: anywhere; }
.title-link:hover { color: #4338ca; }
.paper-org {
  margin: 4px 0 8px; color: #697181; font-size: 12.5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.paper-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.tone-chip { padding: 1px 8px; border-radius: 999px; background: #eef2ff; color: #4338ca; font-size: 11.5px; font-weight: 600; }
.muted-chip { background: #f3f4f6; color: #5f6675; }
.ok-chip { background: #ecfdf5; color: #047857; }
.diff-easy { background: #ecfdf5; color: #047857; }
.diff-medium { background: #eff6ff; color: #1d4ed8; }
.diff-hard { background: #fdf6ec; color: #9a5b12; }
.diff-very_hard { background: #fef2f2; color: #a63030; }
.diff-unknown { background: #f3f4f6; color: #6b7280; }
.match-high { background: #ecfdf5; color: #047857; }
.match-partial { background: #eff6ff; color: #1d4ed8; }
.match-challenge { background: #fdf6ec; color: #9a5b12; }
.match-unassessed { background: #f3f4f6; color: #6b7280; }
.knowledge-list { display: flex; flex-wrap: wrap; gap: 5px; margin: 7px 0 0; }
.knowledge-list span { padding: 2px 8px; border-radius: 999px; background: #f3f4f6; color: #5f6675; font-size: 11.5px; }
.paper-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: auto; padding-top: 10px; }
.paper-actions :deep(.el-button) { min-height: 30px; height: 30px; padding: 0 10px; margin-left: 0; font-size: 12.5px; }
.mini-link {
  display: inline-flex; height: 30px; align-items: center; gap: 4px;
  padding: 0 10px; border: 1px solid #c7d2fe; border-radius: 6px;
  color: #4338ca; font-size: 12.5px; font-weight: 600; text-decoration: none;
}
.mini-link:hover { background: #eef2ff; }
.disabled-wrap { display: inline-flex; }
.action-link {
  display: inline-flex; min-height: 40px; align-items: center; gap: 4px;
  padding: 0 12px; border: 1px solid #c7d2fe; border-radius: 8px;
  color: #4338ca; font-size: 13px; font-weight: 600; text-decoration: none;
}
.action-link:hover { background: #eef2ff; }
.primary-link { border-color: #4338ca; background: #4338ca; color: #fff; }
.primary-link:hover { background: #3730a3; color: #fff; }
.disabled-wrap { display: inline-flex; }

.empty-state {
  grid-column: 1 / -1; padding: 40px 20px; text-align: center;
  border: 1px dashed var(--border-strong, #d1d5db); border-radius: 12px;
  color: #697181;
}
.empty-state strong { display: block; color: #171923; font-size: 17px; }
.empty-state p { margin: 8px 0 16px; }

.lib-pager { display: flex; align-items: center; justify-content: center; gap: 14px; margin-top: 18px; color: #5f6675; font-size: 13px; }

.orphan-section { margin-top: 22px; }
.orphan-note, .technical-intro { color: #697181; font-size: 13.5px; line-height: 1.75; }
.orphan-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 10px 0 16px; }
.orphan-card { padding: 14px; border: 1px solid #e5e7eb; border-radius: 10px; background: #fbfcfe; }
.orphan-head h4 { margin: 8px 0 6px; font-size: 14.5px; line-height: 1.55; }
.orphan-excerpt { margin: 0 0 8px; color: #697181; font-size: 13px; line-height: 1.7; }
.orphan-meta { display: flex; flex-wrap: wrap; gap: 6px 12px; color: #8b93a3; font-size: 12px; }
.source-entries { padding-top: 12px; border-top: 1px dashed #e5e7eb; }
.source-entries h4 { margin: 0 0 8px; font-size: 14px; }
.source-entries ul { margin: 0; padding-left: 18px; }
.source-entries li { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; font-size: 13px; }
.source-entries li span { color: #8b93a3; }

.paper-line { margin: 8px 0 0; color: #7b8494; font-size: 12.5px; }
.card-action {
  display: inline-flex; align-items: center; gap: 2px;
  height: 30px; padding: 0 10px; border: 1px solid #d8dce6;
  border-radius: 8px; background: #fff; color: #374151;
  font-size: 12.5px; text-decoration: none;
}
.card-action:hover { border-color: #a5b4fc; color: #4338ca; }

/* 居中详情弹窗：只有一个滚动容器，避免嵌套滚动条 */
.dlg-title { font-size: 15px; color: #6b7280; }
.dlg-body { max-height: 68vh; overflow-y: auto; padding-right: 4px; }
.dlg-hero { display: flex; align-items: flex-start; gap: 16px; }
.dlg-thumb {
  display: block; width: 120px; flex: none; overflow: hidden;
  border: 1px solid #d8dce6; border-radius: 8px; background: #fff;
  box-shadow: 0 1px 4px rgb(31 41 55 / 10%); line-height: 0;
}
.dlg-thumb img { display: block; width: 100%; height: auto; }
.dlg-thumb.is-static { cursor: default; }
.dlg-head { min-width: 0; flex: 1; }
.dlg-head h3 { margin: 0; font-size: 19px; line-height: 1.45; overflow-wrap: anywhere; }
.dlg-org { margin: 6px 0 8px; color: #697181; font-size: 13px; }
.dlg-intro { margin: 14px 0 0; color: #3f4756; font-size: 13.5px; line-height: 1.75; }
.dlg-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; margin: 16px 0 0; }
.dlg-facts div { display: flex; justify-content: space-between; gap: 10px; min-width: 0; padding-bottom: 6px; border-bottom: 1px dashed #eef0f4; font-size: 13px; }
.dlg-facts dt { flex: none; color: #7b8494; white-space: nowrap; }
.dlg-facts dd { margin: 0; min-width: 0; color: #1f2937; text-align: right; overflow-wrap: anywhere; }
.dlg-block { margin-top: 16px; }
.dlg-block h4 { margin: 0 0 8px; color: #4338ca; font-size: 13.5px; }
.dlg-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; padding-top: 16px; border-top: 1px solid #eef0f4; }
.dlg-actions :deep(.el-button) { min-height: 38px; margin-left: 0; }
.dlg-actions .action-link {
  display: inline-flex; align-items: center; height: 38px; padding: 0 14px;
  border: 1px solid #d8dce6; border-radius: 8px; color: #374151;
  font-size: 13px; text-decoration: none;
}
.dlg-actions .action-link:hover { border-color: #a5b4fc; color: #4338ca; }
.dlg-note { margin: 10px 0 0; color: #8b93a3; font-size: 12px; line-height: 1.6; }
.dlg-source { display: inline-block; margin-top: 14px; color: #4338ca; font-size: 12.5px; text-decoration: none; }
.dlg-source:hover { text-decoration: underline; }
@media (max-width: 1000px) {
  .paper-grid, .orphan-grid { grid-template-columns: minmax(0, 1fr); }
  .filter-grid { grid-template-columns: minmax(0, 1fr); gap: 0; }
}
@media (max-width: 720px) {
  .lib-toolbar { flex-direction: column; align-items: stretch; }
  .lib-search, .lib-sort { max-width: none; width: 100%; }
  .filter-cell { flex-direction: column; gap: 6px; }
  .filter-name { padding-top: 0; }
  .chip-group { flex-wrap: nowrap; width: 100%; overflow-x: auto; scrollbar-width: none; }
  .group-chip { flex: 0 0 auto; min-height: 38px; }
  .paper-card { gap: 12px; padding: 10px; min-height: 0; }
  .paper-thumb { width: 92px; }
  .paper-info { padding-right: 18px; }
  .paper-title { font-size: 14.5px; }
  .paper-actions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
  .paper-actions .primary-action { grid-column: 1 / -1; }
  .paper-actions :deep(.el-button), .card-action, .disabled-wrap {
    width: 100%; min-width: 0; justify-content: center;
    padding: 0 4px; font-size: 12px;
  }
  .card-action { height: 30px; }
  .dlg-hero { gap: 12px; }
  .dlg-thumb { width: 92px; }
  .dlg-facts { grid-template-columns: 1fr; }
  .dlg-body { max-height: 74vh; }
}
</style>

<style>
/* 居中弹窗：去掉 Element 默认的顶部对齐与多层滚动，移动端限制宽度 */
.paper-detail-dialog {
  border-radius: 14px;
  max-width: 94vw;
}
.paper-detail-dialog .el-dialog__header {
  padding: 18px 22px 4px;
  margin-right: 0;
}
.paper-detail-dialog .el-dialog__body {
  padding: 4px 22px 20px;
}
.paper-detail-dialog .el-dialog__headerbtn { top: 12px; right: 10px; }
</style>
