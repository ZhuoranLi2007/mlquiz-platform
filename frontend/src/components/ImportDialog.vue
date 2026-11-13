<script setup lang="ts">
/**
 * 「导入资料」——批量选文件、逐文件进度、内容哈希判重、显式选择是否顺带生成答案。
 *
 * V2 §5.4 规则 9：上传本身不触发付费调用。勾选框默认关闭，只有用户主动勾选才会
 * 在上传后立刻生成 AI 答案解析；否则文件停在「已解析（答案待生成）」，
 * 由用户在列表里点「AI 生成答案解析 PDF」。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { friendlyError } from '../utils/presentation'

const props = defineProps<{
  modelValue: boolean
  accept: string[]
  types: { value: string; label: string }[]
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'imported', payload: { uploaded: number; deduped: number }): void
}>()

const TYPE_OPTIONS = [
  { value: 'auto', label: '自动识别（推荐）' },
  { value: 'exam_paper', label: '试卷 / 习题' },
  { value: 'slides', label: '课程 PPT / 课件' },
  { value: 'notes', label: '复习笔记 / 课堂文字稿' },
  { value: 'code', label: '实验代码 / Notebook' },
  { value: 'other', label: '其他' },
]

type Row = {
  file: File
  state: 'wait' | 'uploading' | 'done' | 'deduped' | 'failed'
  msg: string
  uploadId?: string
}

const rows = ref<Row[]>([])
const declaredType = ref('auto')
const autoGenerate = ref(false)
const dragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const busy = ref(false)

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const totalBytes = computed(() =>
  rows.value.reduce((s, r) => s + r.file.size, 0))

const canUpload = computed(() =>
  rows.value.length > 0 && !busy.value &&
  rows.value.some((r) => r.state === 'wait' || r.state === 'failed'))

watch(() => props.modelValue, (v) => {
  if (v) {
    rows.value = []
    declaredType.value = 'auto'
    autoGenerate.value = false
  }
})

function ext(name: string) {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i).toLowerCase() : ''
}

function addFiles(fs: File[]) {
  const allowed = fs.filter((f) => !props.accept.length || props.accept.includes(ext(f.name)))
  const bad = fs.filter((f) => !allowed.includes(f))
  if (bad.length) {
    ElMessage.warning(`已跳过不支持的格式：${bad.map((f) => f.name).join('、')}`)
  }
  const seen = new Set(rows.value.map((r) => `${r.file.name}:${r.file.size}`))
  const fresh = allowed.filter((f) => !seen.has(`${f.name}:${f.size}`))
  rows.value = [...rows.value, ...fresh.map((file) => ({
    file, state: 'wait' as const, msg: '',
  }))]
}

function onPick(e: Event) {
  const t = e.target as HTMLInputElement
  addFiles(Array.from(t.files || []))
  t.value = ''
}

function onDrop(e: DragEvent) {
  dragOver.value = false
  addFiles(Array.from(e.dataTransfer?.files || []))
}

async function uploadAll() {
  busy.value = true
  let uploaded = 0
  let deduped = 0
  let failed = 0
  try {
    for (const row of rows.value) {
      if (row.state !== 'wait' && row.state !== 'failed') continue
      row.state = 'uploading'
      row.msg = '上传中…'
      const fd = new FormData()
      fd.append('file', row.file)
      fd.append('material_type', declaredType.value)
      fd.append('auto_generate', autoGenerate.value ? '1' : '0')
      try {
        const r: any = await api.upload('/materials/upload', fd)
        row.uploadId = r.upload_id
        if (r.deduped || r.reused) {
          row.state = 'deduped'
          row.msg = '内容与已有资料相同，已直接复用'
          deduped += 1
        } else {
          row.state = 'done'
          row.msg = autoGenerate.value
            ? '已保存原卷，后台正在识别题目并生成答案解析'
            : '已保存原卷，后台正在识别题目与原始题号（答案待你点击生成）'
          uploaded += 1
        }
      } catch (e: any) {
        row.state = 'failed'
        row.msg = friendlyError(e, '导入失败，请重试')
        failed += 1
      }
    }
    const parts = [`成功导入 ${uploaded} 个`]
    if (deduped) parts.push(`${deduped} 个内容重复已复用`)
    if (failed) parts.push(`${failed} 个失败`)
    if (failed) ElMessage.warning(parts.join('，'))
    else ElMessage.success(parts.join('，'))
    emit('imported', { uploaded, deduped })
    if (!failed) visible.value = false
  } finally {
    busy.value = false
  }
}

const stateLabel: Record<Row['state'], string> = {
  wait: '等待上传', uploading: '上传中', done: '已导入',
  deduped: '已复用', failed: '失败',
}
</script>

<template>
  <el-dialog v-model="visible" title="导入课程资料" width="min(680px, calc(100vw - 32px))" :close-on-click-modal="!busy">
    <div class="dialog-intro">
      支持一次导入多个文件。原文件会完整保留，之后可随时查看和核对。
    </div>

    <div
      class="drop-zone"
      :class="{ over: dragOver }"
      @dragover.prevent="dragOver = true"
      @dragleave.prevent="dragOver = false"
      @drop.prevent="onDrop"
      @click="fileInput?.click()"
      @keydown.enter.prevent="fileInput?.click()"
      @keydown.space.prevent="fileInput?.click()"
      role="button"
      tabindex="0"
      aria-label="选择要导入的课程资料"
    >
      <div class="dz-title">把文件拖到这里，或点击选择文件</div>
      <div class="small muted" style="margin-top:6px">
        支持 {{ accept.map((e) => e.replace('.', '')).join(' / ') || 'PDF、Word、PPT、文本、图片' }}；
        可一次选择多个文件
      </div>
      <input ref="fileInput" class="sr-only" type="file" multiple :accept="accept.join(',')" @change="onPick" />
    </div>

    <div v-if="rows.length" class="file-list">
      <div class="small muted file-summary">
        待导入 {{ rows.length }} 个文件，共 {{ (totalBytes / 1024).toFixed(0) }} KB
      </div>
      <div v-for="(r, i) in rows" :key="r.file.name + i" class="file-row">
        <div class="fi">
          <div class="fn">{{ r.file.name }}</div>
          <div class="small muted">{{ (r.file.size / 1024).toFixed(0) }} KB · {{ r.msg || '等待上传' }}</div>
        </div>
        <span class="pill" :class="{
          ok: r.state === 'done',
          info: r.state === 'deduped' || r.state === 'uploading',
          danger: r.state === 'failed',
        }">{{ stateLabel[r.state] }}</span>
        <el-button v-if="r.state === 'wait'" link type="danger" size="small"
          @click="rows.splice(i, 1)">移除</el-button>
      </div>
    </div>

    <div class="opt-line">
      <span class="small">资料类型</span>
      <el-select v-model="declaredType" class="type-select" aria-label="资料类型">
        <el-option v-for="t in TYPE_OPTIONS" :key="t.value" :label="t.label" :value="t.value" />
      </el-select>
      <span class="small muted" v-if="types.length">
        当前资料库已有 {{ types.length }} 种类型
      </span>
    </div>

    <div class="opt-line opt-line--top">
      <el-checkbox v-model="autoGenerate" />
      <div>
        <div class="small">上传后立即生成 AI 答案解析</div>
        <div class="small muted option-help">
          默认关闭。建议先检查题目识别结果，需要时再生成答案，避免不必要的模型调用。
        </div>
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <span class="small muted">
          {{ autoGenerate ? '导入后同时生成答案解析' : '本次只识别资料内容' }}
        </span>
        <div class="footer-actions">
          <el-button :disabled="busy" @click="visible = false">取消</el-button>
          <el-button type="primary" :loading="busy" :disabled="!canUpload" @click="uploadAll">
            开始导入{{ rows.length ? `（${rows.length}）` : '' }}
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.drop-zone {
  border: 1.5px dashed var(--border-strong);
  border-radius: var(--radius-lg);
  padding: 26px 16px;
  text-align: center;
  cursor: pointer;
  font-size: 13px;
  color: var(--muted);
  background: var(--panel-soft);
  transition: border-color .15s ease, background .15s ease;
}
.drop-zone.over { border-color: var(--accent); background: var(--accent-soft); }
.drop-zone:focus-visible { outline: 3px solid rgb(79 70 229 / 20%); outline-offset: 2px; }
.dz-title { color: var(--text-2); font-size: 13.5px; }
.dialog-intro { margin: -8px 0 14px; color: var(--muted); font-size: 14px; line-height: 1.7; }
.file-list { margin-top: 12px; }
.file-summary { margin-bottom: 6px; }
.file-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 0; border-bottom: 1px solid var(--border);
}
.file-row .fi { flex: 1; min-width: 0; }
.file-row .fn {
  font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.opt-line { display: flex; gap: 10px; align-items: center; margin-top: 16px; }
.opt-line--top { align-items: flex-start; }
.type-select { width: 220px; }
.option-help { line-height: 1.7; }
.dialog-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; width: 100%; }
.footer-actions { display: flex; gap: 8px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }

@media (max-width: 640px) {
  .drop-zone { min-height: 132px; display: flex; flex-direction: column; justify-content: center; }
  .file-row { align-items: flex-start; flex-wrap: wrap; }
  .file-row .fi { flex-basis: calc(100% - 80px); }
  .opt-line { align-items: stretch; flex-direction: column; }
  .type-select { width: 100%; }
  .opt-line--top { flex-direction: row; }
  .dialog-footer { align-items: stretch; flex-direction: column; text-align: left; }
  .footer-actions { display: grid; grid-template-columns: 1fr 1fr; }
  .footer-actions :deep(.el-button) { min-height: 44px; margin-left: 0; }
}
</style>
