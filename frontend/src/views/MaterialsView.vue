<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Document, Star, StarFilled } from '@element-plus/icons-vue'
import { api } from '../api/client'
import ImportDialog from '../components/ImportDialog.vue'
import { friendlyError } from '../utils/presentation'

const route = useRoute()
const router = useRouter()

// 嵌在「学习内容库」的页签里时，不再重复渲染自己的标题区。
defineProps<{ embedded?: boolean }>()

const uploads = ref<any[]>([])
const accept = ref<string[]>([])
const types = ref<{ value: string; label: string }[]>([])
const loadingUploads = ref(true)
const importOpen = ref(false)
const activeCategory = ref('all')
// 从「选题依据」等页面跳进来时带上 ?q=文件名，直接按名称检索
const keyword = ref(String(route.query.q || ''))
const quizBusy = ref('')
const codeOpen = ref(false)
const codeText = ref('')
const codeTitle = ref('')
const codeBusy = ref('')
let timer: number | null = null

const IN_PROGRESS = ['queued', 'parsing', 'generating']
const CATEGORIES = [
  { key: 'all', label: '全部' },
  { key: 'slides', label: '课程课件' },
  { key: 'exam_paper', label: '考试试卷' },
  { key: 'notes', label: '学习笔记' },
  { key: 'code', label: '实验与代码' },
  { key: 'ai_answer', label: '答案册' },
]

const rows = computed(() => uploads.value.filter((item) => {
  if (activeCategory.value === 'ai_answer' && !item.booklet_version) return false
  if (!['all', 'ai_answer'].includes(activeCategory.value)
    && item.material_type !== activeCategory.value) return false
  const search = keyword.value.trim().toLowerCase()
  if (!search) return true
  return [item.card_title, item.filename, item.material_type_label]
    .join(' ').toLowerCase().includes(search)
}))

const pending = computed(() =>
  uploads.value.filter((item) => IN_PROGRESS.includes(item.status)).length)

function schedule() {
  if (timer) window.clearTimeout(timer)
  timer = pending.value ? window.setTimeout(() => loadUploads(true), 2500) : null
}

async function loadUploads(silent = false) {
  if (!silent) loadingUploads.value = true
  try {
    const response: any = await api.get('/uploads', { limit: 200 })
    uploads.value = response.items || []
    accept.value = response.accept || []
    types.value = response.material_types || []
    schedule()
  } catch (error) {
    if (!silent) ElMessage.error(friendlyError(error, '资料加载失败，请稍后重试'))
  } finally {
    loadingUploads.value = false
  }
}

function extLabel(item: any) {
  const name = String(item.filename || '')
  const ext = name.slice(name.lastIndexOf('.') + 1).toUpperCase()
  return ext || '文件'
}

/* 按钮按真实能力出现：没有的功能不渲染，避免视觉占位 */
function showPreview(item: any) {
  return item.material_type !== 'code' && Boolean(item.preview?.can_preview)
}
function showCode(item: any) {
  return item.material_type === 'code' && Boolean(item.preview?.can_view_code)
}
function showQuiz(item: any) {
  return item.material_type === 'exam_paper'
    && Boolean(item.preview?.can_quiz)
    && activeCategory.value !== 'ai_answer'
}

function openPreview(item: any) {
  const url = item.preview?.file_url
  if (!url) {
    ElMessage.info(item.preview?.note || '该文件暂不支持在线预览')
    return
  }
  window.open(url, '_blank', 'noopener')
}

function download(item: any) {
  const url = item.download_url || `/api/uploads/${item.upload_id}/original?download=1`
  const link = document.createElement('a')
  link.href = url
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
}

async function startQuiz(item: any) {
  if (quizBusy.value) return
  quizBusy.value = item.upload_id
  try {
    const out: any = await api.post(`/uploads/${item.upload_id}/start-quiz`, {})
    ElMessage.success(out?.original?.item_count
      ? `已按原卷顺序生成 ${out.original.item_count} 题`
      : '已进入在线作答')
    router.push(`/exam/${out.attempt_id}`)
  } catch (error) {
    ElMessage.error(friendlyError(error, '无法开始作答'))
  } finally {
    quizBusy.value = ''
  }
}

async function openCode(item: any) {
  codeBusy.value = item.upload_id
  try {
    const res = await fetch(item.preview.file_url)
    if (!res.ok) throw new Error(String(res.status))
    codeText.value = await res.text()
    codeTitle.value = item.card_title
    codeOpen.value = true
  } catch {
    ElMessage.error('读取代码文件失败，可先下载原件')
  } finally {
    codeBusy.value = ''
  }
}

async function toggleFavorite(item: any) {
  if (!item.document_id) {
    ElMessage.info('这份资料还没有文档记录，暂时不能收藏')
    return
  }
  try {
    if (item.favorite_entry_id) {
      await api.del(`/me/favorites/${item.favorite_entry_id}`)
      item.favorite_entry_id = ''
      item.favorited = false
    } else {
      const out: any = await api.post('/me/favorites', {
        kind: 'document', target_id: item.document_id, title: item.card_title,
      })
      item.favorite_entry_id = out.entry_id || ''
      item.favorited = true
    }
  } catch (error) {
    ElMessage.error(friendlyError(error, '收藏失败'))
  }
}

onMounted(loadUploads)
onBeforeUnmount(() => {
  if (timer) window.clearTimeout(timer)
})
</script>

<template>
  <section class="materials-page" :class="{ embedded }">
    <div class="toolbar">
      <div class="category-filter" aria-label="资料类型筛选">
        <button v-for="category in CATEGORIES" :key="category.key" type="button"
          class="filter-chip" :class="{ active: activeCategory === category.key }"
          :aria-pressed="activeCategory === category.key"
          @click="activeCategory = category.key">
          {{ category.label }}
        </button>
      </div>
      <div class="toolbar-right">
        <el-input v-model="keyword" clearable placeholder="搜索资料名称"
          aria-label="搜索资料名称" class="toolbar-search" />
        <span v-if="pending" class="result-note" aria-live="polite">{{ pending }} 份处理中</span>
        <el-button type="primary" @click="importOpen = true">导入资料</el-button>
      </div>
    </div>

    <div v-loading="loadingUploads" class="material-grid">
      <article v-for="item in rows" :key="item.upload_id" class="material-card">
        <button type="button" class="fav-btn" :class="{ on: item.favorited }"
          :aria-label="item.favorited ? '取消收藏' : '收藏这份资料'"
          :title="item.favorited ? '取消收藏' : '收藏'"
          @click.stop="toggleFavorite(item)">
          <el-icon><StarFilled v-if="item.favorited" /><Star v-else /></el-icon>
        </button>

        <div class="mat-thumb" :class="`kind-${item.preview?.kind || 'none'}`">
          <img v-if="item.preview?.kind === 'pdf' && item.preview?.image_url"
            :src="item.preview.image_url" :alt="`${item.card_title} 第一页预览`"
            loading="lazy" decoding="async" />
          <img v-else-if="item.preview?.kind === 'image'"
            :src="item.preview.file_url" :alt="`${item.card_title} 预览`"
            loading="lazy" decoding="async" />
          <pre v-else-if="item.preview?.text_preview" class="mat-text">{{
            item.preview.text_preview }}</pre>
          <span v-else class="mat-icon">
            <el-icon><Document /></el-icon>
            <small>{{ extLabel(item) }}</small>
          </span>
        </div>

        <div class="mat-info">
          <h3>{{ item.card_title }}</h3>
          <span class="type-chip">{{ item.material_type_label || '课程资料' }}</span>
          <div class="mat-actions">
            <el-button v-if="showPreview(item)" size="small"
              @click="openPreview(item)">在线预览</el-button>
            <el-button v-if="showCode(item)" size="small"
              :loading="codeBusy === item.upload_id" @click="openCode(item)">查看代码</el-button>
            <el-button size="small" @click="download(item)">下载原件</el-button>
            <el-button v-if="showQuiz(item)" size="small" type="primary"
              :loading="quizBusy === item.upload_id" @click="startQuiz(item)">在线作答</el-button>
          </div>
        </div>
      </article>

      <div v-if="!loadingUploads && !rows.length" class="empty-state">
        <strong>{{ keyword ? '没有匹配的资料' : '还没有导入资料' }}</strong>
        <p>{{ keyword ? '换个关键词试试。' : '支持 PDF、图片、Markdown、文本与代码文件。' }}</p>
        <el-button v-if="!keyword" type="primary" @click="importOpen = true">导入资料</el-button>
      </div>
    </div>

    <ImportDialog v-model="importOpen" :accept="accept" :types="types"
      @imported="loadUploads(true)" />

    <el-dialog v-model="codeOpen" :title="codeTitle || '代码'" width="min(860px, 94vw)"
      class="code-dialog">
      <pre class="code-view"><code>{{ codeText }}</code></pre>
    </el-dialog>
  </section>
</template>

<style scoped>
.materials-page { width: 100%; max-width: 1280px; min-width: 0; margin: 0 auto; }
.materials-page.embedded { max-width: none; }

/* 分类与搜索同一行，尽量紧凑 */
.toolbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin: 0 0 14px; padding: 0;
  border: 0; background: transparent; box-shadow: none; flex-wrap: wrap;
}
.category-filter { display: flex; flex-wrap: wrap; gap: 6px; }
.filter-chip {
  height: 34px; padding: 0 14px; border: 1px solid #e5e7eb; border-radius: 999px;
  background: #fff; color: #4b5563; font-size: 14px; cursor: pointer;
}
.filter-chip:hover { border-color: #c7d2fe; color: #4338ca; }
.filter-chip.active { border-color: #c7d2fe; background: #eef2ff; color: #4338ca; font-weight: 600; }
.toolbar-right { display: flex; align-items: center; gap: 10px; margin-left: auto; }
.toolbar-search { width: 240px; }
.result-note { color: #8b93a3; font-size: 14px; white-space: nowrap; }

.material-grid {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px; align-items: stretch;
}
.material-card {
  position: relative; display: grid; grid-template-columns: 115px minmax(0, 1fr);
  gap: 18px; height: 182px; padding: 16px; overflow: hidden;
  border: 1px solid #e5e7eb; border-radius: 12px; background: #fff;
}
.material-card:hover { border-color: #c7d2fe; box-shadow: 0 4px 16px rgb(31 41 55 / 6%); }

.fav-btn {
  position: absolute; top: 10px; right: 10px; display: grid; place-items: center;
  width: 26px; height: 26px; padding: 0; border: 0; border-radius: 6px;
  background: transparent; color: #c2c8d2; cursor: pointer; font-size: 15px;
}
.fav-btn :deep(.el-icon),
.fav-btn :deep(svg) { width: 17px; height: 17px; }
.fav-btn:hover { background: #f4f6fb; color: #6b7280; }
.fav-btn.on { color: #f59e0b; }

/* 固定 115×150 预览框，contain 不拉伸 */
.mat-thumb {
  display: flex; align-items: center; justify-content: center; overflow: hidden;
  width: 115px; height: 150px; border: 1px solid #eceff5; border-radius: 8px;
  background: #f7f8fb;
}
.mat-thumb img { max-width: 100%; max-height: 100%; object-fit: contain; box-shadow: 0 1px 3px rgb(15 23 42 / 12%); }
.mat-text {
  width: 100%; height: 100%; margin: 0; padding: 8px; overflow: hidden;
  color: #4b5563; font-size: 14px; line-height: 1.5; white-space: pre-wrap;
  word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.mat-icon { display: flex; flex-direction: column; align-items: center; gap: 6px; color: #9aa1ae; }
.mat-icon :deep(svg) { width: 28px; height: 28px; }
.mat-icon small { font-size: 14px; letter-spacing: .04em; }

.mat-info {
  display: flex; min-width: 0; flex-direction: column; gap: 8px;
  padding-right: 20px; overflow: hidden;
}
.mat-info h3 {
  margin: 0; font-size: 15px; line-height: 1.45; font-weight: 600;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; overflow-wrap: anywhere;
}
.type-chip {
  align-self: flex-start; padding: 2px 10px; border-radius: 999px;
  background: #eef2ff; color: #4338ca; font-size: 14px; font-weight: 500;
}
/* 按钮固定在信息区底部，标题长短不影响对齐 */
.mat-actions {
  display: flex; flex-wrap: wrap; gap: 8px;
  margin-top: auto; padding-top: 8px;
}
.mat-actions :deep(.el-button) { min-height: 30px; margin-left: 0; font-size: 14px; }
.mat-actions :deep(.el-button--primary) {
  --el-button-bg-color: #4f46e5; --el-button-border-color: #4f46e5;
}

.empty-state {
  grid-column: 1 / -1; padding: 48px 20px; text-align: center;
  border: 1px dashed #e5e7eb; border-radius: 12px; background: #fbfcfe;
}
.empty-state strong { display: block; font-size: 15px; }
.empty-state p { margin: 8px 0 14px; color: #8b93a3; font-size: 14px; }

.code-view {
  max-height: 62vh; margin: 0; overflow: auto; padding: 14px 16px;
  border: 1px solid #eef0f4; border-radius: 8px; background: #0f172a;
  color: #e2e8f0; font-size: 14px; line-height: 1.7;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

@media (max-width: 1080px) {
  .material-grid { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 720px) {
  .toolbar { flex-direction: column; align-items: stretch; }
  .toolbar-right { margin-left: 0; width: 100%; }
  .toolbar-search { width: 100%; }
  .material-card {
    grid-template-columns: 96px minmax(0, 1fr); gap: 12px;
    height: auto; min-height: 150px; padding: 13px;
  }
  .mat-thumb { width: 96px; height: 128px; }
  .mat-info { padding-right: 16px; }
  .mat-actions :deep(.el-button) { flex: 1 1 0; min-width: 0; padding: 0 6px; font-size: 14px; }
}
</style>

<style>
.code-dialog { border-radius: 14px; max-width: 94vw; }
.code-dialog .el-dialog__body { padding-top: 8px; }
</style>
