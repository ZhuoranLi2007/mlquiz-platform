<script setup lang="ts">
/**
 * 「查看原件」页面内预览。
 *
 * 旧实现用 window.open(url, '_blank', 'noopener')，在嵌入式预览面板里
 * 新标签页常被沙箱策略拦掉，表现为「点了没反应 / 打不开」。后端
 * GET /api/uploads/{id}/original 本身是可用的（PDF 为 inline），所以这里
 * 不再依赖弹窗：
 *   - PDF      -> 同源 iframe，浏览器内置 PDF 阅读器直接渲染；
 *   - 文本类   -> fetch 后放进 <pre>，同时兼容 attachment 响应；
 *   - 图片     -> <img>；
 *   - 二进制   -> 明确给出下载入口，不假装能预览。
 * 弹窗里始终保留「下载原件」和「在新窗口打开」两条退路。
 */
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  modelValue: boolean
  uploadId: string
  filename: string
  mime?: string
  title?: string
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
}>()

const dialogTitle = computed(() =>
  `查看原件 · ${props.title || props.filename}`)

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const textContent = ref('')
const textLoading = ref(false)
const textError = ref('')
const iframeLoaded = ref(false)
let requestSeq = 0

const url = computed(() => `/api/uploads/${encodeURIComponent(props.uploadId)}/original`)
const ext = computed(() => {
  const name = props.filename || ''
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
})

const TEXT_EXTS = ['.txt', '.md', '.markdown', '.py', '.ipynb', '.html', '.htm',
  '.csv', '.json', '.tex', '.log']
const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg']
const BINARY_EXTS = ['.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls', '.zip', '.rar']

type PreviewMode = 'pdf' | 'image' | 'text' | 'binary'
const mode = computed<PreviewMode>(() => {
  const mime = (props.mime || '').toLowerCase()
  if (mime === 'application/pdf' || ext.value === '.pdf') return 'pdf'
  if (mime.startsWith('image/') || IMAGE_EXTS.includes(ext.value)) return 'image'
  if (BINARY_EXTS.includes(ext.value)) return 'binary'
  if (mime.startsWith('text/') || mime === 'application/json'
      || TEXT_EXTS.includes(ext.value)) return 'text'
  return 'binary'
})

const modeLabel = computed(() => ({
  pdf: 'PDF 原件', image: '图片原件', text: '文本原件', binary: '原始文件',
}[mode.value]))

async function loadText() {
  const seq = ++requestSeq
  textContent.value = ''
  textError.value = ''
  textLoading.value = true
  try {
    const response = await fetch(url.value, { credentials: 'same-origin' })
    if (!response.ok) throw new Error(`读取失败（HTTP ${response.status}）`)
    const buffer = await response.arrayBuffer()
    const text = new TextDecoder('utf-8').decode(buffer)
    if (text.includes('\u0000')) throw new Error('这是二进制文件，无法按文本显示')
    if (seq === requestSeq) textContent.value = text
  } catch (err: any) {
    if (seq === requestSeq) textError.value = err?.message || '读取失败'
  } finally {
    if (seq === requestSeq) textLoading.value = false
  }
}

function openNewWindow() {
  window.open(url.value, '_blank', 'noopener')
}

watch([visible, () => props.uploadId], ([open]) => {
  if (!open) {
    requestSeq += 1
    return
  }
  iframeLoaded.value = false
  if (mode.value === 'text') loadText()
}, { immediate: true })
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="dialogTitle"
    width="min(1080px, 94vw)"
    top="3vh"
    class="original-preview-dialog"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <div class="op-toolbar">
      <span class="op-meta">{{ modeLabel }} · 原文件只读，查看原件不计为已见答案</span>
      <div class="op-actions">
        <el-button size="small" @click="openNewWindow">在新窗口打开</el-button>
        <a class="op-download" :href="url" :download="filename">下载原件</a>
      </div>
    </div>

    <div v-if="mode === 'pdf'" v-loading="!iframeLoaded" class="op-body">
      <iframe
        class="op-frame"
        :src="url"
        :title="`${filename} 原件预览`"
        @load="iframeLoaded = true"
      />
    </div>

    <div v-else-if="mode === 'image'" class="op-body op-body--image">
      <img :src="url" :alt="filename" />
    </div>

    <div v-else-if="mode === 'text'" v-loading="textLoading" class="op-body">
      <pre v-if="!textError && textContent" class="op-text">{{ textContent }}</pre>
      <div v-else-if="textError" class="op-placeholder">
        <strong>原件未能按文本显示</strong>
        <p>{{ textError }}。可以点上方「下载原件」保存到本地查看。</p>
      </div>
      <div v-else-if="!textLoading" class="op-placeholder"><p>原件没有可显示的文本内容。</p></div>
    </div>

    <div v-else class="op-body op-body--placeholder">
      <div class="op-placeholder">
        <strong>这类文件无法在网页内直接预览</strong>
        <p>点上方「下载原件」用本机对应的软件打开；原件保持只读，不会改动。</p>
      </div>
    </div>

    <p class="op-footnote">
      如果上方区域长时间空白，说明内置浏览器没有启用对应文件的渲染，点「下载原件」或「在新窗口打开」即可查看。
    </p>
  </el-dialog>
</template>

<style scoped>
.op-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
.op-meta { color: #64748b; font-size: 14px; }
.op-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.op-download {
  display: inline-flex; align-items: center; min-height: 24px; padding: 5px 12px;
  border: 1px solid #c7d2fe; border-radius: 6px; background: #eef2ff;
  color: #4338ca; font-size: 14px; font-weight: 600; text-decoration: none;
}
.op-download:hover { background: #e0e7ff; }
.op-body {
  height: min(74vh, 760px); overflow: auto;
  border: 1px solid #e2e6ed; border-radius: 10px; background: #f8fafc;
}
.op-frame { display: block; width: 100%; height: 100%; border: 0; background: #fff; }
.op-body--image { display: grid; place-items: center; padding: 16px; }
.op-body--image img { max-width: 100%; max-height: 100%; object-fit: contain; }
.op-text {
  margin: 0; padding: 18px; white-space: pre-wrap; word-break: break-word;
  color: #1f2937; font: 13px/1.75 ui-monospace, SFMono-Regular, Consolas, monospace;
}
.op-body--placeholder { display: grid; place-items: center; }
.op-placeholder { padding: 24px; text-align: center; color: #526072; }
.op-placeholder strong { display: block; color: #334155; font-size: 15px; }
.op-placeholder p { margin: 8px 0 0; font-size: 14px; line-height: 1.7; }
.op-footnote { margin: 10px 0 0; color: #8490a2; font-size: 14px; line-height: 1.6; }

@media (max-width: 640px) {
  .op-toolbar { align-items: stretch; flex-direction: column; }
  .op-actions :deep(.el-button), .op-download { min-height: 44px; }
  .op-body { height: 66vh; }
}
</style>

<style>
/* el-dialog 会 teleport 到 body， scoped 样式命中不到根层；
   预览弹窗需要稳定盖在「答案册抽屉」之上。 */
.original-preview-dialog { z-index: 3000 !important; }
</style>
