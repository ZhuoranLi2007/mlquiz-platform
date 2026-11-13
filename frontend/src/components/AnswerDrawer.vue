<script setup lang="ts">
/**
 * 原卷旁的「AI 答案解析」抽屉。
 *
 * 打开即视为「已见」——由后端写入 AnswerReveal；生成本身不算已见。
 * 内容只读已保存的答案册，不在前端重新生成；「重新生成」是显式动作，
 * 会产生新版本并把旧版本留在变更记录里。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import MathText from './MathText.vue'
import OriginalPreview from './OriginalPreview.vue'
import TechnicalDetails from './TechnicalDetails.vue'
import { downloadFile, formatDateTime, friendlyError, materialDisplayName } from '../utils/presentation'

const props = defineProps<{
  modelValue: boolean
  uploadId: string
  filename: string
  mime?: string
  title?: string
  bookletVersion: number
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'regenerated'): void
}>()

const loading = ref(false)
const booklet = ref<any>(null)
const error = ref('')
const jumped = ref('')
const originalOpen = ref(false)

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const sections = computed(() => booklet.value?.sections || [])

const answeredCount = computed(() => booklet.value?.answered_count || 0)
const totalCount = computed(() => booklet.value?.total_count || 0)

const missing = computed(() => sections.value.flatMap((s: any) =>
  (s.questions || []).filter((q: any) => !q.answered)))

async function load() {
  if (!props.uploadId) return
  loading.value = true
  error.value = ''
  try {
    booklet.value = await api.get(`/uploads/${props.uploadId}/booklet`)
  } catch (e: any) {
    error.value = friendlyError(e, '答案解析还没有准备好')
    booklet.value = null
  } finally {
    loading.value = false
  }
}

watch(() => [props.modelValue, props.uploadId], ([open]) => {
  if (open) { jumped.value = ''; load() }
})

function download() {
  downloadFile(`/api/uploads/${props.uploadId}/booklet.pdf`,
    `${String(props.filename || '资料').replace(/\.[^.]+$/, '')}_AI参考答案.pdf`)
}

function openOriginal() {
  // 与资料页一致：当前页弹窗内嵌预览，不用 window.open。
  originalOpen.value = true
}

function jump(no: string) {
  jumped.value = no
  const el = document.getElementById(`q-${no}`)
  el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

async function regenerate() {
  try {
    await ElMessageBox.confirm(
      `将基于当前题目重新生成一份答案解析（当前为第 ${props.bookletVersion} 版），` +
      '完成后版本号加一；已有答卷仍按交卷时的答案版本计分，不会自动重评。',
      '重新生成答案解析确认',
      { confirmButtonText: '重新生成', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  try {
    const r: any = await api.post(`/uploads/${props.uploadId}/generate-answers`,
      { regenerate: true })
    ElMessage.success(r.note || '已开始重新生成')
    visible.value = false
    emit('regenerated')
  } catch (e: any) {
    ElMessage.error(friendlyError(e, '重新生成失败'))
  }
}
</script>

<template>
  <el-drawer v-model="visible" size="min(920px, 100%)"
    :title="`AI 答案解析 · ${title || materialDisplayName(filename)}`" destroy-on-close>
    <div v-loading="loading">
      <div v-if="error" class="empty">
        <div class="t">还没有可查看的答案解析</div>
        <div class="d">{{ error }}<br />可回到资料库点击「AI 生成答案解析 PDF」后再查看。</div>
      </div>

      <template v-else-if="booklet">
        <div class="ab-head">
          <div>
            <div class="ab-title">
              第 {{ booklet.version || 1 }} 版
              <span class="pill" :class="booklet.status === 'complete' ? 'ok' : 'warn'">
                {{ booklet.status === 'complete' ? '全部完成' : booklet.status === 'partial' ? '部分完成' : '准备中' }}
              </span>
            </div>
            <div class="small muted answer-meta">
              已生成 {{ answeredCount }} / {{ totalCount }} 题
              <span v-if="booklet.generated_at"> · {{ formatDateTime(booklet.generated_at) }}</span>
            </div>
          </div>
          <div class="ab-actions">
            <el-button size="small" @click="openOriginal">对照原卷</el-button>
            <el-button size="small" :disabled="!booklet.pdf_ready" @click="download">下载 PDF</el-button>
          </div>
        </div>

        <div class="answer-notice">
          这是学习参考答案，请结合课程资料核对；打开答案会记录为“已查看”，不会作为独立掌握证据。
        </div>

        <div v-if="missing.length" class="hint">
          有 {{ missing.length }} 题未生成答案，原因已逐题标注（缺图/缺公式与「条件不足」是不同问题，
          需要分别处理）；整卷按「部分完成」保存，可单题重试。
          <div class="missing-links">
            <button v-for="q in missing.slice(0, 12)" :key="q.no" class="miss-chip"
              :aria-label="`跳到第 ${q.no} 题`" @click="jump(q.no)">{{ q.no }}</button>
          </div>
        </div>

        <div v-for="sec in sections" :key="sec.section_index" class="ab-section">
          <h3>{{ sec.section }}</h3>
          <div v-for="q in sec.questions" :key="q.question_id" :id="`q-${q.no}`"
            class="ab-q" :class="{ focused: jumped === q.no }">
            <div class="ab-q-head">
              <b>{{ q.no }}</b>
              <span class="pill">{{ q.type_label }}</span>
              <span class="small muted">{{ q.score }} 分</span>
              <span class="pill" :class="q.answered ? 'ok' : 'warn'">
                {{ q.answered ? q.answer_label || 'AI参考答案' : '未生成' }}
              </span>
            </div>

            <div class="pre" style="margin-top:6px"><MathText :text="q.stem" /></div>
            <div v-for="o in q.options || []" :key="o.key" class="small ab-opt">
              {{ o.key }}. <MathText :text="o.text" />
            </div>

            <div v-if="q.answered" class="ab-answer">
              <div><span class="lbl">【答案】</span><b>{{ q.answer_text }}</b></div>
              <div v-if="q.explanation" style="margin-top:4px">
                <span class="lbl">【解析】</span><MathText :text="q.explanation" />
              </div>
              <div v-if="q.rubric_points?.length" style="margin-top:4px">
                <span class="lbl">【评分要点】</span>
                <span v-for="(p, i) in q.rubric_points" :key="i">
                  {{ p.point || p.text || p }}<span v-if="p.score != null">（{{ p.score }} 分）</span>{{ i < q.rubric_points.length - 1 ? '；' : '' }}
                </span>
              </div>
            </div>
            <div v-else class="ab-pending">
              未生成答案：{{ q.pending_note || '等待重试' }}
            </div>

            <div class="small muted" style="margin-top:6px" v-if="q.source_refs?.page">
              出处：原卷第 {{ q.source_refs.page }} 页<span v-if="q.source_refs.section"> · {{ q.source_refs.section }}</span>
            </div>
          </div>
        </div>

        <div class="empty" v-if="!sections.length">
          <div class="t">答案册尚未生成</div>
          <div class="d">回到资料库点击「AI 生成答案解析 PDF」，生成完成后这里会出现逐题解析。</div>
        </div>

        <TechnicalDetails title="答案版本与生成详情">
          <p>{{ booklet.label_policy }}</p>
          <p>答案版本：第 {{ booklet.version || 1 }} 版</p>
          <p v-if="booklet.content_hash" class="mono">内容校验：{{ booklet.content_hash }}</p>
          <p v-if="booklet.reveal?.recorded">本次打开记录已查看 {{ booklet.reveal.recorded }} 题</p>
          <el-button size="small" @click="regenerate">重新生成答案解析</el-button>
        </TechnicalDetails>
      </template>
    </div>
  </el-drawer>

  <OriginalPreview
    v-if="uploadId"
    v-model="originalOpen"
    :upload-id="uploadId"
    :filename="filename"
    :title="title || materialDisplayName(filename)"
    :mime="mime"
  />
</template>

<style scoped>
.ab-head { display: flex; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
.ab-title { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.ab-actions { margin-left: auto; display: flex; gap: 8px; flex-wrap: wrap; }
.answer-meta { margin-top: 4px; }
.answer-notice { margin-top: 14px; padding: 12px 14px; border-radius: 10px; background: #eef2ff; color: #4338ca; font-size: 14px; line-height: 1.7; }
.ab-section h3 { font-size: 14.5px; margin: 18px 0 8px; }
.ab-q {
  border-bottom: 1px solid var(--border); padding: 12px 10px;
  border-radius: var(--radius-sm); transition: background .2s ease;
}
.ab-q.focused { background: var(--accent-soft); }
.ab-q-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ab-opt { margin: 3px 0 0 14px; color: var(--text-2); }
.ab-answer {
  margin-top: 8px; padding: 8px 10px; background: var(--ok-soft);
  border: 1px solid #cfe6da; border-radius: var(--radius-sm); font-size: 13px; line-height: 1.7;
}
.ab-answer .lbl { color: var(--ok); font-weight: 600; }
.ab-pending {
  margin-top: 8px; padding: 8px 10px; background: var(--warn-soft);
  border: 1px solid #f0dfc4; border-radius: var(--radius-sm);
  font-size: 12.5px; color: #7c4a10;
}
.miss-chip {
  display: inline-flex; align-items: center; justify-content: center; min-width: 36px; min-height: 36px; font-size: 12px; border: 1px solid #f0dfc4;
  border-radius: 7px; padding: 4px 8px; margin: 4px 4px 0 0; cursor: pointer;
  color: #7c4a10; background: #fff;
}
.miss-chip:focus-visible { outline: 3px solid rgb(79 70 229 / 20%); outline-offset: 2px; }
.missing-links { margin-top: 6px; }

@media (max-width: 640px) {
  .ab-head { flex-direction: column; }
  .ab-actions { display: grid; grid-template-columns: 1fr 1fr; width: 100%; margin-left: 0; }
  .ab-actions :deep(.el-button) { min-height: 44px; margin-left: 0; }
  .ab-q { padding: 14px 2px; }
  .ab-answer, .ab-pending { padding: 12px; font-size: 14px; }
}
</style>
