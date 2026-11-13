<script setup lang="ts">
/**
 * 学习内容库：本地课程资料 + 外部学习资源（B站/知乎/CSDN/抖音等）+ 按考点找内容。
 * 所有条目都带出处；外部链接只登记可访问入口与推荐原因，不编造视频内容。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, pageImageUrl } from '../api/client'
import MaterialsView from './MaterialsView.vue'

type TabName = 'course' | 'external' | 'by-kp' | 'uploads'

const route = useRoute()
const router = useRouter()
const TABS: TabName[] = ['course', 'external', 'by-kp', 'uploads']
const initialTab = String(route.query.tab || '') as TabName
const activeTab = ref<TabName>(TABS.includes(initialTab) ? initialTab : 'course')
watch(activeTab, (value) => {
  if (route.query.tab !== value) {
    void router.replace({ query: { ...route.query, tab: value } })
  }
})
const loading = ref(true)
const error = ref('')

const materials = ref<any[]>([])
const resources = ref<any[]>([])

const knowledgePoints = ref<any[]>([])
const scopeUnits = ref<any[]>([])
const questionStats = ref<Record<string, number>>({})
const categories = ref<Record<string, number>>({})

const keyword = ref('')
const activeCategory = ref('all')
const selectedKp = ref('')

const preview = ref<{ doc: any; page: number; total: number } | null>(null)
const chunks = ref<any[]>([])
const chunkLoading = ref(false)

const CATEGORY_LABELS: Record<string, string> = {
  course_slides: '课程课件',
  class_transcript: '课堂记录',
  classroom_photo: '课堂照片',
  review_note: '复习笔记',
  review_note_by_chapter: '分章笔记',
  exam_prep_note: '备考笔记',
  structure_note: '结构笔记',
  past_exam: '往年试卷',
  page_screenshot: '页面截图',
  study_html: '网页资料',
  archive: '压缩包',
  legacy_doc_uncertain: '旧格式文档',
  other: '其他',
}

const PLATFORM_ORDER = ['bilibili', 'B站', '知乎', 'CSDN', '抖音', '官方文档', '站内检索', '其他']

function platformKey(item: any) {
  const text = `${item.platform || ''} ${item.title || ''}`
  if (/bilibili|B\s*站/i.test(text)) return 'B站视频'
  if (/抖音/.test(text)) return '抖音'
  if (/知乎/.test(text)) return '知乎'
  if (/CSDN/i.test(text)) return 'CSDN / 博客'
  if (/官方|文档|doc/i.test(text)) return '官方文档'
  return item.platform || '其他来源'
}

const courseDocs = computed(() => materials.value
  .filter((item) => item.processing_status !== 'excluded_non_course')
  .filter((item) => activeCategory.value === 'all' || item.source_category === activeCategory.value)
  .filter((item) => !keyword.value.trim()
    || `${item.filename || ''} ${item.relative_path || ''}`.toLowerCase()
      .includes(keyword.value.trim().toLowerCase()))
  .sort((a, b) => (b.chunk_count || 0) - (a.chunk_count || 0)))

const externalGroups = computed(() => {
  const groups = new Map<string, any[]>()
  for (const item of resources.value) {
    const key = platformKey(item)
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(item)
  }
  return [...groups.entries()]
    .sort((a, b) => {
      const ia = PLATFORM_ORDER.findIndex((p) => a[0].includes(p))
      const ib = PLATFORM_ORDER.findIndex((p) => b[0].includes(p))
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
    })
    .map(([name, items]) => ({ name, items }))
})

const kpChapters = computed(() => {
  const map = new Map<string, any[]>()
  for (const kp of knowledgePoints.value) {
    const chapter = kp.chapter || '未分章'
    if (!map.has(chapter)) map.set(chapter, [])
    map.get(chapter)!.push(kp)
  }
  return [...map.entries()].map(([chapter, items]) => ({ chapter, items }))
})

const currentKp = computed(() => knowledgePoints.value.find((k) => k.kp_id === selectedKp.value) || null)
const currentUnit = computed(() => scopeUnits.value.find((u) => u.kp_id === selectedKp.value) || null)
const kpResources = computed(() => resources.value.filter(
  (item) => (item.kp_ids || []).includes(selectedKp.value)))
const kpQuestions = computed(() => questionStats.value[selectedKp.value] || 0)
/** 课件里讲这个考点的那几页：直接把文件+页码变成可点开的推荐。 */
const kpSlides = computed(() => {
  const refs = currentUnit.value?.ppt_refs || []
  const out: { label: string; doc: any; page: number }[] = []
  for (const ref of refs) {
    if (!ref || typeof ref !== 'object') continue
    const doc = materials.value.find((m) => m.filename === ref.file)
    if (!doc) continue
    for (const page of (ref.pages || []).slice(0, 3)) {
      out.push({ label: `${ref.file} · 第 ${page} 页`, doc, page: Number(page) })
    }
  }
  return out
})
const PARSE_LABELS: Record<string, string> = {
  succeeded: '已解析',
  parsed: '已解析',
  partial: '部分可读（公式/图需看原页）',
  failed: '解析失败',
  pending: '排队中',
  excluded_non_course: '非课程资料',
}

function parseLabel(status?: string) {
  if (!status) return ''
  if (PARSE_LABELS[status]) return PARSE_LABELS[status]
  return status
}

function formatTime(value?: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())}`
}

function openDoc(item: any, page = 1) {
  preview.value = { doc: item, page, total: item.unit_count || 1 }
  void loadChunks(item.document_id)
}

async function loadChunks(docId: string) {
  chunkLoading.value = true
  chunks.value = []
  try {
    const result: any = await api.get(`/materials/${docId}/chunks`)
    chunks.value = result.items || []
  } catch {
    chunks.value = []
  } finally {
    chunkLoading.value = false
  }
}

function goPage(delta: number) {
  if (!preview.value) return
  const next = preview.value.page + delta
  if (next < 1 || next > preview.value.total) return
  preview.value = { ...preview.value, page: next }
}

function openUrl(url: string) {
  if (!url) return
  window.open(url, '_blank', 'noopener')
}

function practiceKp() {
  if (!selectedKp.value) return
  router.push({ path: '/compose', query: { kp: selectedKp.value, tab: 'quick' } })
}

function goReports() {
  if (!selectedKp.value) return
  router.push({ path: '/reports', query: { tab: 'three' } })
}

/** ppt_refs 可能是 {file,pages,titles} 对象，也可能是字符串；统一成一行可读文本。 */
function refText(ref: any) {
  if (typeof ref === 'string') return ref
  if (ref && typeof ref === 'object') {
    const file = ref.file || ref.rel_path || ''
    const pages = Array.isArray(ref.pages) ? ref.pages.join('、') : ''
    return pages ? `${file} 第 ${pages} 页` : String(file)
  }
  return String(ref || '')
}

const COVERAGE_LABELS: Record<string, string> = {
  covered: '课件已覆盖',
  partial: '课件部分覆盖',
  missing: '课件未覆盖',
  uncovered: '课件未覆盖',
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [mat, res, kps, units, questions] = await Promise.all([
      api.get('/materials'),
      api.get('/resources'),
      api.get('/knowledge-points'),
      api.get('/scope/units'),
      api.get('/questions'),
    ])
    materials.value = mat.items || []
    categories.value = (mat.items || []).reduce((acc: Record<string, number>, item: any) => {
      if (item.processing_status !== 'excluded_non_course') {
        acc[item.source_category] = (acc[item.source_category] || 0) + 1
      }
      return acc
    }, {})
    resources.value = res.items || []
    knowledgePoints.value = kps.items || []
    scopeUnits.value = (units.items || [])
    const stats: Record<string, number> = {}
    for (const q of (questions.items || [])) {
      if (q.main_kp_id) stats[q.main_kp_id] = (stats[q.main_kp_id] || 0) + 1
    }
    questionStats.value = stats
    if (!selectedKp.value && knowledgePoints.value.length) {
      const top = knowledgePoints.value.find((k) => (stats[k.kp_id] || 0) > 0)
      selectedKp.value = top?.kp_id || knowledgePoints.value[0].kp_id
    }
  } catch (err: any) {
    error.value = err?.message || String(err)
  } finally {
    loading.value = false
  }
}

onMounted(load)

watch(() => route.query.tab, (value) => {
  const tab = String(value || '') as TabName
  if (TABS.includes(tab) && tab !== activeTab.value) activeTab.value = tab
})
</script>

<template>
  <section v-loading="loading" class="library-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">课程内外</p>
        <h1>学习内容库</h1>
        <p class="lede">课件、试卷、笔记与外部视频/文章放在一起，按考点找；
          每条都标注出处与核查日期，没读过的内容不写结论。</p>
      </div>
      <div class="heading-stats">
        <div><strong>{{ materials.filter((m) => m.processing_status !== 'excluded_non_course').length }}</strong><span>份课程资料</span></div>
        <div><strong>{{ resources.length }}</strong><span>个外部入口</span></div>
      </div>
    </header>

    <div v-if="error" class="hint error-hint" role="alert">{{ error }}</div>

    <el-tabs v-model="activeTab" class="library-tabs">
      <el-tab-pane label="课程资料" name="course">
        <div class="toolbar">
          <div class="chips">
            <button type="button" class="chip" :class="{ active: activeCategory === 'all' }"
              @click="activeCategory = 'all'">全部 {{ materials.filter((m) => m.processing_status !== 'excluded_non_course').length }}</button>
            <button v-for="(count, key) in categories" :key="key" type="button" class="chip"
              :class="{ active: activeCategory === key }" @click="activeCategory = String(key)">
              {{ CATEGORY_LABELS[key] || key }} {{ count }}
            </button>
          </div>
          <el-input v-model="keyword" clearable placeholder="搜索文件名" class="toolbar-search" />
        </div>

        <div class="doc-grid">
          <article v-for="item in courseDocs" :key="item.document_id" class="doc-card"
            @click="openDoc(item)">
            <div class="doc-thumb">
              <img v-if="item.chunk_count > 0 && item.unit_label === 'page'"
                :src="pageImageUrl(item.document_id, 1)"
                :alt="`${item.filename} 第一页`" loading="lazy" />
              <span v-else class="doc-icon">{{ (item.extension || '').replace('.', '').toUpperCase() || '文件' }}</span>
            </div>
            <div class="doc-body">
              <h3>{{ item.filename }}</h3>
              <p class="doc-meta">
                <span>{{ CATEGORY_LABELS[item.source_category] || item.source_category }}</span>
                <span v-if="item.unit_count">{{ item.unit_count }} {{ item.unit_label === 'page' ? '页' : item.unit_label }}</span>
                <span v-if="item.chunk_count">{{ item.chunk_count }} 个知识片段</span>
              </p>
              <p class="doc-note" v-if="['partial', 'failed'].includes(item.parse_status)">
                解析状态：{{ parseLabel(item.parse_status) }}
              </p>
            </div>
          </article>
          <div v-if="!courseDocs.length" class="empty-state">
            <strong>没有匹配的资料</strong>
            <p>换个关键词，或切到「我的上传」导入新文件。</p>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="外部学习资源" name="external">
        <p class="tab-note">视频与文章都只登记入口和推荐原因；没有读到字幕或正文的内容不会被描述成「已经讲过」。
          每条都标注核查日期与「可验证到什么程度」。</p>

        <section v-for="group in externalGroups" :key="group.name" class="resource-block">
          <h3 class="block-title">{{ group.name }}（{{ group.items.length }}）</h3>
          <div class="res-grid">
            <article v-for="item in group.items" :key="item.resource_id" class="res-card">
              <div class="res-head">
                <el-tag size="small" effect="plain">{{ item.platform || group.name }}</el-tag>
                <span v-if="item.verified" class="res-verified">已核查 {{ item.checked_date }}</span>
              </div>
              <h4>{{ item.title }}</h4>
              <p class="res-desc">{{ item.reason }}</p>
              <div class="kp-chips" v-if="item.kp_ids?.length">
                <span v-for="kp in item.kp_ids.slice(0, 3)" :key="kp" class="kp-chip">{{ kp.replace('kp.', '') }}</span>
              </div>
              <p v-if="item.verifiable_segment" class="res-segment">可验证范围：{{ item.verifiable_segment }}</p>
              <div class="res-foot">
                <el-button size="small" text type="primary" @click="openUrl(item.url)">打开链接 ↗</el-button>
              </div>
            </article>
          </div>
        </section>
      </el-tab-pane>

      <el-tab-pane label="按考点找" name="by-kp">
        <div class="kp-picker">
          <span class="toolbar-label">选择考点</span>
          <el-select v-model="selectedKp" filterable placeholder="搜索考点" class="kp-select">
            <el-option-group v-for="group in kpChapters" :key="group.chapter" :label="group.chapter">
              <el-option v-for="kp in group.items" :key="kp.kp_id" :value="kp.kp_id"
                :label="`${kp.name}（${questionStats[kp.kp_id] || 0} 题）`" />
            </el-option-group>
          </el-select>
          <el-button type="primary" :disabled="!selectedKp" @click="practiceKp">练这个考点</el-button>
          <el-button :disabled="!selectedKp" @click="goReports">看这个考点的诊断</el-button>
        </div>

        <div v-if="currentKp" class="kp-detail">
          <article class="detail-card">
            <h3>{{ currentKp.name }}</h3>
            <p class="muted small">{{ currentKp.chapter }} · 题库里已有 {{ kpQuestions }} 道题</p>
            <div v-if="currentUnit" class="detail-section">
              <h4>老师讲到哪一步</h4>
              <p v-if="currentUnit.teacher_basis">{{ currentUnit.teacher_basis }}</p>
              <div class="kp-chips" v-if="currentUnit.ppt_refs?.length">
                <span v-for="ref in currentUnit.ppt_refs" :key="refText(ref)" class="kp-chip">{{ refText(ref) }}</span>
              </div>
              <p v-if="currentUnit.coverage" class="muted small">
                课件覆盖：{{ COVERAGE_LABELS[currentUnit.coverage] || currentUnit.coverage }}
              </p>
            </div>
            <div v-if="currentUnit?.concepts?.length" class="detail-section">
              <h4>相关概念</h4>
              <div class="kp-chips"><span v-for="c in currentUnit.concepts" :key="c" class="kp-chip">{{ c }}</span></div>
            </div>
          </article>

          <article class="detail-card">
            <h4>推荐的学习内容</h4>
            <div v-if="kpSlides.length" class="rec-list">
              <button v-for="slide in kpSlides" :key="slide.label" type="button" class="rec-item"
                @click="openDoc(slide.doc, slide.page)">
                <span class="rec-platform">课件</span>
                <span class="rec-title">{{ slide.label }}</span>
                <span class="rec-arrow">→</span>
              </button>
            </div>
            <div v-if="kpResources.length" class="rec-list">
              <button v-for="item in kpResources" :key="item.resource_id" type="button" class="rec-item"
                @click="openUrl(item.url)">
                <span class="rec-platform">{{ item.platform }}</span>
                <span class="rec-title">{{ item.title }}</span>
                <span class="rec-arrow">↗</span>
              </button>
            </div>
            <p v-if="!kpSlides.length && !kpResources.length" class="muted small">
              这个考点还没有登记外部资源；可以先用课件原页和题库里的题。
            </p>
            <p class="muted small">推荐依据：先看这个考点的作答记录与失分，再挑对应的课件页/视频/文章。</p>
          </article>
        </div>
      </el-tab-pane>

      <el-tab-pane label="我的上传" name="uploads">
        <MaterialsView embedded />
      </el-tab-pane>
    </el-tabs>

    <el-dialog :model-value="Boolean(preview)" :title="preview?.doc?.filename || '资料预览'"
      width="min(980px, 94vw)" destroy-on-close
      @update:model-value="(v: boolean) => { if (!v) preview = null }">
      <div v-if="preview" class="preview-body">
        <div class="preview-pages">
          <img :src="pageImageUrl(preview.doc.document_id, preview.page)"
            :alt="`第 ${preview.page} 页`" />
          <div class="pager">
            <el-button size="small" :disabled="preview.page <= 1" @click="goPage(-1)">上一页</el-button>
            <span>第 {{ preview.page }} / {{ preview.total }} 页</span>
            <el-button size="small" :disabled="preview.page >= preview.total" @click="goPage(1)">下一页</el-button>
          </div>
        </div>
        <aside class="preview-side">
          <h4>这一页的知识片段</h4>
          <div v-loading="chunkLoading" class="chunk-list">
            <div v-for="chunk in chunks.filter((c) => (c.unit_refs || []).some((u: any) => u.page === preview?.page)).slice(0, 4)"
              :key="chunk.chunk_id" class="chunk-item">
              <span class="chunk-title">{{ chunk.title }}</span>
              <p>{{ chunk.text || '这一页以公式/图片为主，文本层可读内容很少，建议直接看原页。' }}</p>
            </div>
            <p v-if="!chunkLoading && !chunks.filter((c) => (c.unit_refs || []).some((u: any) => u.page === preview?.page)).length"
              class="muted small">这一页没有单独的知识片段。</p>
          </div>
          <p class="muted small">来源：{{ preview.doc.relative_path }}</p>
        </aside>
      </div>
    </el-dialog>
  </section>
</template>

<style scoped>
.library-page { width: 100%; max-width: 1280px; min-width: 0; margin: 0 auto; }
.page-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; flex-wrap: wrap; }
.eyebrow { margin: 0 0 4px; color: #4338ca; font-size: 13px; font-weight: 600; }
.page-heading h1 { margin: 0; font-size: 26px; }
.lede { max-width: 720px; margin: 6px 0 0; color: var(--muted); font-size: 14.5px; line-height: 1.7; }
.heading-stats { display: flex; gap: 22px; }
.heading-stats div { display: flex; flex-direction: column; }
.heading-stats strong { font-size: 24px; color: var(--accent); }
.heading-stats span { color: var(--muted); font-size: 12.5px; }

.library-tabs { margin-top: 12px; }
.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin: 4px 0 14px; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { height: 32px; padding: 0 13px; border: 1px solid var(--border); border-radius: 999px; background: #fff; color: var(--text-2); font-size: 12.5px; cursor: pointer; }
.chip:hover { border-color: #c7d2fe; color: var(--accent); }
.chip.active { background: var(--accent-soft); border-color: #c7d2fe; color: var(--accent); font-weight: 600; }
.toolbar-search { width: 260px; }

.doc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
.doc-card { display: flex; gap: 12px; padding: 12px; border: 1px solid var(--border); border-radius: 12px; background: #fff; cursor: pointer; transition: border-color .15s, box-shadow .15s; }
.doc-card:hover { border-color: #c7d2fe; box-shadow: 0 8px 22px rgb(31 41 55 / 6%); }
.doc-thumb { width: 62px; height: 84px; flex: none; border-radius: 8px; overflow: hidden; background: var(--panel-soft); display: grid; place-items: center; border: 1px solid var(--border); }
.doc-thumb img { width: 100%; height: 100%; object-fit: cover; }
.doc-icon { color: var(--muted-2); font-size: 11px; font-weight: 700; letter-spacing: .04em; text-align: center; line-height: 1.3; }
.doc-body { min-width: 0; }
.doc-body h3 { margin: 0 0 4px; font-size: 14px; line-height: 1.45; overflow-wrap: anywhere; }
.doc-meta { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 4px; color: var(--muted); font-size: 12px; }
.doc-meta span { padding: 1px 7px; border-radius: 999px; background: var(--panel-soft); }
.doc-note { margin: 0; color: #9a5b12; font-size: 12px; }

.resource-block { margin-bottom: 22px; }
.block-title { margin: 6px 0 10px; font-size: 15px; }
.res-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.res-card { padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: #fff; display: flex; flex-direction: column; }
.res-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.res-platform { color: var(--muted); font-size: 12px; }
.res-verified { color: var(--ok); font-size: 12px; }
.res-card h4 { margin: 0 0 6px; font-size: 14px; line-height: 1.5; }
.res-desc { margin: 0 0 8px; color: var(--muted); font-size: 12.5px; line-height: 1.6; }
.res-segment { margin: 8px 0 0; color: var(--muted-2); font-size: 11.5px; }
.res-foot { margin-top: auto; padding-top: 8px; }
.kp-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.kp-chip { padding: 2px 8px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); font-size: 11.5px; }
.tab-note { margin: 0 0 14px; color: var(--muted); font-size: 13px; }

.kp-picker { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.kp-select { width: 340px; max-width: 100%; }
.toolbar-label { color: var(--muted); font-size: 13px; }
.kp-detail { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
.detail-card { padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: #fff; }
.detail-card h3 { margin: 0 0 2px; font-size: 17px; }
.detail-card h4 { margin: 0 0 8px; font-size: 14px; }
.detail-section { margin-top: 14px; }
.detail-section p { margin: 0 0 6px; font-size: 13.5px; line-height: 1.7; color: var(--text-2); }
.rec-list { display: grid; gap: 8px; margin-bottom: 10px; }
.rec-item { display: flex; align-items: center; gap: 8px; width: 100%; padding: 9px 11px; border: 1px solid var(--border); border-radius: 9px; background: var(--panel-soft); cursor: pointer; text-align: left; }
.rec-item:hover { border-color: #c7d2fe; }
.rec-platform { flex: none; color: var(--accent); font-size: 12px; }
.rec-title { flex: 1; min-width: 0; font-size: 13px; overflow-wrap: anywhere; }
.rec-arrow { color: var(--muted); }

.preview-body { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); gap: 16px; }
.preview-pages img { width: 100%; border: 1px solid var(--border); border-radius: 10px; background: #fff; }
.pager { display: flex; align-items: center; justify-content: center; gap: 12px; margin-top: 10px; font-size: 13px; color: var(--muted); }
.preview-side h4 { margin: 0 0 8px; font-size: 14px; }
.chunk-list { display: grid; gap: 8px; max-height: 460px; overflow: auto; }
.chunk-item { padding: 10px; border: 1px solid var(--border); border-radius: 9px; background: var(--panel-soft); }
.chunk-title { color: var(--muted); font-size: 11.5px; }
.chunk-item p { margin: 4px 0 0; font-size: 13px; line-height: 1.7; }

@media (max-width: 900px) {
  .kp-detail, .preview-body { grid-template-columns: 1fr; }
  .heading-stats { gap: 14px; }
}

@media (max-width: 640px) {
  .kp-select, .toolbar-search { width: 100%; max-width: none; }
  .kp-picker { align-items: stretch; }
  .kp-picker > .el-button { width: 100%; margin-left: 0; }
  .doc-grid, .res-grid { grid-template-columns: 1fr; }
}
</style>
