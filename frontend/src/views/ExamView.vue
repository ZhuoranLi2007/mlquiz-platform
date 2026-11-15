<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import { auth } from '../auth'
import MathText from '../components/MathText.vue'
import SourceQuestionContent from '../components/SourceQuestionContent.vue'
import AuthenticatedImage from '../components/AuthenticatedImage.vue'
import StatusBadge from '../components/StatusBadge.vue'
import {
  answerPayload, formatAnswer, friendlyError, hasMeaningfulAnswer, questionTypeLabel,
} from '../utils/presentation'

const route = useRoute()
const router = useRouter()
const attemptId = String(route.params.attemptId || '')
const recipientId = String(route.query.recipient || '')
const assignedStudentMode = Boolean(recipientId)
const feedbackDestination = computed(() => auth.principal.value?.role === 'student'
  ? { path: '/student/feedback', query: { attempt: attemptId } }
  : { path: '/teacher/analysis' })

const state = ref<any>(null)
const targetedRetest = ref<any>(null)
const loading = ref(true)
const error = ref('')
const current = ref(0)
const responses = ref<Record<string, Record<string, unknown>>>({})
const answerMeta = ref<Record<string, { hints_used: number; solution_viewed: boolean }>>({})
const saveState = ref<Record<string, '待保存' | '保存中' | '已保存' | '保存失败'>>({})
const dirty = ref<Set<string>>(new Set())
const savesInFlight = new Map<string, Promise<void>>()
const submitting = ref(false)
const hintBusy = ref(false)
const elapsed = ref('00:00')
const solutionText = ref<Record<string, string>>({})
const hintText = ref<Record<string, string>>({})
const navDrawer = ref(false)

// V2.3：默认中文作答；切换只影响展示，不重新请求、不动已填答案。
const lang = ref<'zh' | 'en'>(
  String(route.query.lang) === 'en' ? 'en'
    : window.localStorage.getItem('mlq-exam-lang') === 'en' ? 'en' : 'zh')
const attachments = ref<Record<string, any[]>>({})
const uploadBusy = ref('')
const ocrTarget = ref('')
const photoInput = ref<HTMLInputElement | null>(null)
const ocrDialog = ref(false)
const ocrCurrent = ref<any>(null)
const ocrText = ref('')
const ocrBusyId = ref('')

let timer: ReturnType<typeof setInterval> | undefined
let elapsedBaseMs: number | null = null
let elapsedAnchor = 0
let saveTimer: ReturnType<typeof setTimeout> | undefined
let diagnosisTimer: ReturnType<typeof setInterval> | undefined
let diagnosisChecks = 0

const submitted = computed(() => state.value?.status === 'submitted')
const isQuiz = computed(() => ['quiz', 'retest'].includes(state.value?.mode))
const isRetest = computed(() => state.value?.mode === 'retest')
const questions = computed<any[]>(() => state.value?.questions || [])
const q = computed(() => questions.value[current.value] || null)
const answeredCount = computed(() => questions.value.filter((item) => hasMeaningfulAnswer(answerFor(item))).length)
const unansweredCount = computed(() => Math.max(0, questions.value.length - answeredCount.value))
const wrongCount = computed(() => Object.values<any>(state.value?.scores_by_item || {}).filter((score) => score.correct === false).length)
const scorePercent = computed(() => {
  const max = Number(state.value?.max_score || 0)
  return max > 0 ? Math.round((Number(state.value?.score || 0) / max) * 100) : null
})
const diagnosisStatus = computed(() => state.value?.diagnosis_status || (submitted.value ? 'pending' : 'not_started'))
const diagnosisCompleted = computed(() => ['completed', 'succeeded'].includes(diagnosisStatus.value))
const diagnosisPending = computed(() => ['not_requested', 'pending', 'queued', 'running'].includes(diagnosisStatus.value))
const diagnosisLabel = computed(() => {
  if (diagnosisCompleted.value) return '诊断已完成'
  if (diagnosisStatus.value === 'failed') return '诊断生成失败'
  if (diagnosisPending.value) return '诊断生成中'
  return '成绩已生成'
})
const diagnosisTone = computed<'success' | 'danger' | 'info'>(() => {
  if (diagnosisCompleted.value) return 'success'
  if (diagnosisStatus.value === 'failed') return 'danger'
  return 'info'
})

function resp(qid: string) { return responses.value[qid] || {} }
function answerFor(item: any) {
  const byItem = submitted.value && state.value?.answers_by_item?.[item?.item_id]
  return byItem ? answerPayload(byItem.response || {}) : resp(item.question_id)
}
function meta(qid: string) { return answerMeta.value[qid] || { hints_used: 0, solution_viewed: false } }

function setLang(value: string | number | boolean) {
  lang.value = value === 'en' ? 'en' : 'zh'
  window.localStorage.setItem('mlq-exam-lang', lang.value)
}

const hasBilingual = computed(() =>
  questions.value.some((item) => String(item.stem_zh || '').trim()))

/** 中文优先；没有译文时回退原文，但会显式提示，不冒充中文版。 */
function displayStem(item: any) {
  if (!item) return ''
  if (lang.value === 'zh' && String(item.stem_zh || '').trim()) return item.stem_zh
  return item.stem || ''
}

function translationMissing(item: any) {
  // 只有原文本身是英文、而中文译文还没生成时才提示；
  // 中文题的 stem_zh 为空是正常的，不能误报「显示英文原文」。
  return lang.value === 'zh'
    && item?.original_language === 'en'
    && !String(item?.stem_zh || '').trim()
    && String(item?.stem || '').trim().length > 0
}

function selectedKeys(qid: string, itemId = ''): string[] {
  const value = (itemId ? answerFor({ question_id: qid, item_id: itemId }) : resp(qid)).keys
  return Array.isArray(value) ? value : []
}

function responseParts(qid: string, itemId = ''): unknown[] {
  const value = (itemId ? answerFor({ question_id: qid, item_id: itemId }) : resp(qid)).parts
  return Array.isArray(value) ? value : []
}

function setResponsePart(qid: string, index: number, value: unknown) {
  const next = [...responseParts(qid)]
  next[index] = value
  setResp(qid, { parts: next })
}

function toggleMulti(qid: string, key: string, checked: boolean) {
  if (submitted.value) return
  const current = selectedKeys(qid)
  const next = checked
    ? [...current, key]
    : current.filter((item) => item !== key)
  setResp(qid, { keys: next })
}

function displayOption(option: any) {
  if (!option) return ''
  if (lang.value === 'zh' && String(option.text_zh || '').trim()) return option.text_zh
  return option.text || ''
}

function pickPhoto(qid: string) {
  if (submitted.value) return
  ocrTarget.value = qid
  photoInput.value?.click()
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

async function loadAttachments() {
  try {
    const res: any = await api.get(`/attempts/${attemptId}/attachments`)
    attachments.value = res.by_question || {}
    // 刷新/换设备后，附件也要算进「已作答」，否则进度会显示成 0
    for (const [qid, list] of Object.entries<any[]>(attachments.value)) {
      if (list.length && !(resp(qid).attachments as any[] | undefined)?.length) {
        responses.value[qid] = {
          ...resp(qid),
          attachments: list.map((a: any) => a.attachment_id),
        }
      }
    }
  } catch {
    attachments.value = {}
  }
}

function syncAttachmentIds(qid: string) {
  const ids = (attachments.value[qid] || []).map((a: any) => a.attachment_id)
  setResp(qid, { attachments: ids })
}

async function uploadOne(file: File, qid: string) {
  if (!file.type.startsWith('image/')) {
    ElMessage.warning(`${file.name} 不是图片`)
    return
  }
  if (file.size > 6 * 1024 * 1024) {
    ElMessage.warning(`${file.name} 超过 6MB，请压缩后再试`)
    return
  }
  const dataUrl = await fileToDataUrl(file)
  const out: any = await api.post(`/attempts/${attemptId}/attachments`, {
    question_id: qid, image: dataUrl,
  })
  attachments.value[qid] = [...(attachments.value[qid] || []), out]
  syncAttachmentIds(qid)
  await saveOne(qid)
}

async function onPhotosPicked(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  const qid = ocrTarget.value
  input.value = ''
  if (!files.length || !qid) return
  uploadBusy.value = qid
  try {
    for (const file of files) {
      await uploadOne(file, qid)
    }
    ElMessage.success(`已上传 ${files.length} 张，原图会保留`)
  } catch (reason) {
    ElMessage.error(friendlyError(reason, '上传失败'))
  } finally {
    uploadBusy.value = ''
  }
}

async function removeAttachment(item: any) {
  try {
    await api.del(`/attachments/${item.attachment_id}`)
    const qid = item.question_id
    attachments.value[qid] = (attachments.value[qid] || [])
      .filter((a: any) => a.attachment_id !== item.attachment_id)
    syncAttachmentIds(qid)
    await saveOne(qid)
    ElMessage.success('已删除该图片')
  } catch (reason) {
    ElMessage.error(friendlyError(reason, '删除失败'))
  }
}

function openOcr(item: any) {
  ocrCurrent.value = item
  ocrText.value = item?.ocr?.text || ''
  ocrDialog.value = true
}

async function runOcr(item: any) {
  if (!item) return
  ocrBusyId.value = item.attachment_id
  try {
    const out: any = await api.post(`/attachments/${item.attachment_id}/ocr`)
    item.ocr = {
      ocr_id: out.ocr_id, text: out.text, status: out.status,
      error: out.error, model: out.model, confirmed: false,
    }
    ocrCurrent.value = item
    if (out.status === 'ok') {
      ocrText.value = out.text
      ElMessage.success('识别完成，可修改后保存')
    } else {
      ElMessage.warning(out.error || '识别失败，原图仍可提交')
    }
  } catch (reason) {
    ElMessage.error(friendlyError(reason, '识别失败'))
  } finally {
    ocrBusyId.value = ''
  }
}

async function confirmOcr() {
  const item = ocrCurrent.value
  if (!item?.ocr?.ocr_id) {
    ElMessage.info('请先识别一次')
    return
  }
  try {
    await api.post(`/ocr/${item.ocr.ocr_id}/confirm`, { text: ocrText.value })
    item.ocr.text = ocrText.value
    item.ocr.confirmed = true
    const qid = item.question_id
    const prev = String(resp(qid).text || '').trim()
    const next = prev ? `${prev}\n\n${ocrText.value}` : ocrText.value
    setResp(qid, { text: next })
    await saveOne(qid)
    ocrDialog.value = false
    ElMessage.success('已保存识别结果并填入作答框，原图保留')
  } catch (reason) {
    ElMessage.error(friendlyError(reason, '保存失败'))
  }
}

function setResp(qid: string, patch: Record<string, unknown>) {
  responses.value[qid] = { ...resp(qid), ...patch }
  markDirty(qid)
}

function markDirty(qid: string) {
  dirty.value.add(qid)
  saveState.value[qid] = '待保存'
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => void flushDirty(), 650)
}

async function flushDirty() {
  const ids = [...dirty.value]
  for (const qid of ids) await saveOne(qid, false)
}

async function saveOne(qid: string, notify = true) {
  if (submitted.value) return
  const pending = savesInFlight.get(qid)
  if (pending) {
    await pending
    if (dirty.value.has(qid)) await saveOne(qid, notify)
    return
  }
  const payload = answerPayload(resp(qid))
  const serialized = JSON.stringify(payload)
  const task = (async () => {
    saveState.value[qid] = '保存中'
    try {
      const endpoint = assignedStudentMode
        ? `/student/attempts/${attemptId}/answers/${qid}`
        : `/attempts/${attemptId}/answers/${qid}`
      await api.put(endpoint, {
        response: payload,
        hints_used: meta(qid).hints_used,
        solution_viewed: meta(qid).solution_viewed,
      })
      // Inputs may change while the request is running.  Only clear the dirty
      // flag when the response just saved is still the current response.
      if (JSON.stringify(answerPayload(resp(qid))) === serialized) dirty.value.delete(qid)
      saveState.value[qid] = '已保存'
      if (notify) ElMessage.success('答案已保存')
    } catch (e) {
      saveState.value[qid] = '保存失败'
      error.value = friendlyError(e, '答案保存失败，请重试')
    }
  })()
  savesInFlight.set(qid, task)
  try { await task } finally { savesInFlight.delete(qid) }
}

async function load(showSpinner = true) {
  if (showSpinner) loading.value = true
  try {
    const endpoint = assignedStudentMode
      ? `/student/attempts/${attemptId}` : `/attempts/${attemptId}`
    const result: any = await api.get(endpoint)
    state.value = result
    syncElapsed(result)
    if (auth.isTeacher.value && result.status === 'submitted') {
      await router.replace(`/teacher/attempts/${attemptId}`)
      return
    }
    if (assignedStudentMode && result.status === 'submitted') {
      const tasks: any = await api.get('/student/assignments')
      targetedRetest.value = (tasks.items || []).find(
        (item: any) => item.recipient_id === recipientId)?.targeted_retest || targetedRetest.value
    }
    const nextResponses: Record<string, Record<string, unknown>> = {}
    const nextMeta: Record<string, { hints_used: number; solution_viewed: boolean }> = {}
    for (const [qid, answer] of Object.entries<any>(result.answers || {})) {
      nextResponses[qid] = answerPayload(answer.response || {})
      nextMeta[qid] = {
        hints_used: Number(answer.hints_used || 0),
        solution_viewed: Boolean(answer.solution_viewed),
      }
      saveState.value[qid] = '已保存'
    }
    responses.value = nextResponses
    answerMeta.value = nextMeta
    if (result.status === 'submitted') startDiagnosisPolling()
  } catch (e) {
    error.value = friendlyError(e, '答卷加载失败')
  } finally {
    if (showSpinner) loading.value = false
  }
}

function startDiagnosisPolling() {
  if (!diagnosisPending.value || diagnosisTimer) return
  diagnosisChecks = 0
  diagnosisTimer = setInterval(async () => {
    diagnosisChecks += 1
    await load(false)
    if (!diagnosisPending.value || diagnosisChecks >= 20) {
      if (diagnosisTimer) clearInterval(diagnosisTimer)
      diagnosisTimer = undefined
    }
  }, 3000)
}

onMounted(async () => {
  await load()
  if (auth.isTeacher.value && submitted.value) return
  await loadAttachments()
  // 深链：?index=3 直接定位到第 3 题（用于演示公式渲染）
  const wantIndex = Number(route.query.index || 0)
  if (wantIndex > 0 && wantIndex <= questions.value.length) {
    current.value = wantIndex - 1
  }
  const wantOcr = String(route.query.ocr || '')
  if (wantOcr) {
    const hit = Object.values(attachments.value).flat()
      .find((a: any) => a.attachment_id === wantOcr)
    if (hit) openOcr(hit)
  }
  if (!submitted.value) timer = setInterval(updateElapsed, 250)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  if (saveTimer) clearTimeout(saveTimer)
  if (diagnosisTimer) clearInterval(diagnosisTimer)
  if (!submitted.value && dirty.value.size) void flushDirty()
})

function syncElapsed(result: any) {
  const value = result?.server_elapsed_ms
  elapsedBaseMs = value === null || value === undefined || !Number.isFinite(Number(value))
    ? null : Math.max(0, Number(value))
  elapsedAnchor = performance.now()
  updateElapsed()
}

function updateElapsed() {
  if (elapsedBaseMs === null) { elapsed.value = '用时未记录'; return }
  const ms = elapsedBaseMs + (submitted.value ? 0 : performance.now() - elapsedAnchor)
  const seconds = Math.floor(ms / 1000)
  const minutes = Math.floor(seconds / 60)
  elapsed.value = minutes >= 60
    ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

function goto(index: number) {
  if (index < 0 || index >= questions.value.length) return
  if (!submitted.value && dirty.value.size) void flushDirty()
  current.value = index
  navDrawer.value = false
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function askHint() {
  if (!q.value || hintBusy.value) return
  if (isQuiz.value) {
    ElMessage.warning('模拟测验交卷前不能使用提示')
    return
  }
  hintBusy.value = true
  try {
    const result: any = await api.post('/assistant/chat', {
      message: '请只给我一个循序渐进的提示，不要直接给出最终答案。',
      session_id: `attempt:${attemptId}`,
      mode: 'practice',
      context: {
        attempt_id: attemptId,
        question_id: q.value.question_id,
        question_type: q.value.question_type,
        stem: q.value.stem,
        options: q.value.options || [],
      },
    })
    const text = result.answer || result.note
    if (!text) throw new Error('助手没有返回可用提示')
    hintText.value[q.value.question_id] = text
    answerMeta.value[q.value.question_id] = {
      ...meta(q.value.question_id),
      hints_used: meta(q.value.question_id).hints_used + 1,
    }
    markDirty(q.value.question_id)
    await saveOne(q.value.question_id, false)
  } catch (e) {
    ElMessage.error(friendlyError(e, '暂时无法获取提示'))
  } finally {
    hintBusy.value = false
  }
}

async function viewSolution() {
  if (!q.value) return
  if (isQuiz.value) {
    ElMessage.warning('模拟测验交卷前不提供解析')
    return
  }
  const qid = q.value.question_id
  try {
    const result: any = await api.get(`/attempts/${attemptId}/solution/${qid}`)
    solutionText.value[qid] = result.explanation || result.note || '这道题暂时没有解析。'
    answerMeta.value[qid] = { ...meta(qid), solution_viewed: true }
    markDirty(qid)
    await saveOne(qid, false)
  } catch (e) {
    ElMessage.error(friendlyError(e, '解析读取失败'))
  }
}

async function submit() {
  if (submitting.value || submitted.value) return
  const unanswered = unansweredCount.value
  try {
    await ElMessageBox.confirm(
      unanswered > 0
        ? `还有 ${unanswered} 道题未作答。交卷后不能修改，仍要交卷吗？`
        : '所有题目都已作答。交卷后不能修改，确认交卷吗？',
      '确认交卷',
      { type: unanswered > 0 ? 'warning' : 'info', confirmButtonText: '确认交卷', cancelButtonText: '继续检查' },
    )
  } catch { return }

  submitting.value = true
  try {
    await flushDirty()
    const endpoint = assignedStudentMode
      ? `/student/assignments/${recipientId}/submit`
      : `/attempts/${attemptId}/submit`
    state.value = await api.post(endpoint, { request_key: `submit:${attemptId}` })
    syncElapsed(state.value)
    if (timer) { clearInterval(timer); timer = undefined }
    if (auth.isTeacher.value) {
      await router.replace(`/teacher/attempts/${attemptId}`)
      return
    }
    targetedRetest.value = state.value?.targeted_retest || targetedRetest.value
    ElMessage.success('交卷成功，成绩已生成')
    startDiagnosisPolling()
    window.scrollTo({ top: 0, behavior: 'smooth' })
  } catch (e) {
    error.value = friendlyError(e, '交卷失败，请重试')
    await load(false)
  } finally {
    submitting.value = false
  }
}

async function openDispute() {
  if (!q.value) return
  try {
    const { value } = await ElMessageBox.prompt(
      '请说明参考答案与课程资料不一致、条件理解不同，或你认为存在其他正确解法的具体原因。',
      '反馈答案问题',
      {
        confirmButtonText: '提交反馈', cancelButtonText: '取消', inputType: 'textarea',
        inputPlaceholder: '例如：课件第 12 页对这个条件的定义不同……',
        inputValidator: (text: string) => (text && text.trim().length >= 4) || '请至少写 4 个字说明问题',
      },
    )
    await api.post(`/questions/${q.value.question_id}/answer-dispute`, {
      reason: String(value).trim(),
      evidence: `来自当前答卷第 ${current.value + 1} 题`,
    })
    ElMessage.success('反馈已提交，之后可在高级入口查看处理进度')
  } catch (e: any) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(friendlyError(e))
  }
}

function scoreOf(item: any) { return state.value?.scores_by_item?.[item?.item_id] || null }
</script>

<template>
  <main v-loading="loading" class="exam-page">
    <header class="exam-toolbar">
      <div class="exam-title">
          <button class="back-button" aria-label="返回" @click="router.push(assignedStudentMode ? '/student' : '/teacher/questions')">←</button>
        <div>
          <p class="eyebrow">{{ isQuiz ? '模拟测验' : '学习练习' }}</p>
          <h1>{{ submitted ? '答卷结果' : '专注答题' }}</h1>
        </div>
      </div>
      <div class="exam-progress" aria-live="polite">
        <span>{{ answeredCount }}/{{ questions.length }} 已作答</span>
        <div class="progress-track" aria-hidden="true">
          <span :style="{ width: `${questions.length ? answeredCount / questions.length * 100 : 0}%` }" />
        </div>
      </div>
      <div class="exam-meta">
        <span class="timer" aria-label="答题用时">{{ elapsed }}</span>
        <small v-if="submitted && state?.elapsed_source === 'historical_display'" class="elapsed-note">历史展示用时</small>
        <StatusBadge :status="submitted ? 'submitted' : 'in_progress'" />
      </div>
    </header>

    <div v-if="error" class="error-banner" role="alert">
      <span>{{ error }}</span>
      <button @click="error = ''">关闭</button>
    </div>

    <section v-if="submitted" class="result-hero">
      <div>
        <p class="eyebrow">本次成绩</p>
        <div class="score-line"><strong>{{ state?.score ?? '暂无成绩' }}</strong><span v-if="state?.score !== null && state?.score !== undefined">/ {{ state?.max_score }} 分</span></div>
        <p v-if="state?.ungraded_item_count && state?.scored_item_count">已记录 {{ state.scored_item_count }} / {{ questions.length }} 题的逐题成绩。</p>
        <p v-if="scorePercent !== null">{{ scorePercent }}% · {{ wrongCount }} 道错题</p>
        <div v-if="isRetest && targetedRetest" :class="['retest-verdict', targetedRetest.status]">
          <b v-if="targetedRetest.status === 'passed'">本轮复测已达标，正确率{{ Math.round(Number(targetedRetest.accuracy) * 100) }}%。</b>
          <b v-else-if="targetedRetest.status === 'not_passed'">本轮复测未达标，正确率{{ Math.round(Number(targetedRetest.accuracy) * 100) }}%，建议继续练习。</b>
          <b v-else>本轮仍有题目未完成可靠评分，暂不能判断是否达标。</b>
        </div>
      </div>
      <div class="result-next">
        <StatusBadge :status="diagnosisStatus" :label="diagnosisLabel" :tone="diagnosisTone" />
        <p v-if="diagnosisStatus === 'failed'">成绩和答卷已经安全保存，诊断失败不会影响本次结果。</p>
        <p v-else-if="!diagnosisCompleted">成绩可以立即查看，学情诊断会在后台继续生成。</p>
        <p v-else>查看本次错题与学习建议。</p>
        <div class="result-actions">
          <el-button type="primary" @click="router.push(feedbackDestination)">查看学情</el-button>
          <el-button @click="router.push(feedbackDestination)">复习错题</el-button>
        </div>
      </div>
    </section>

    <div class="exam-layout">
      <aside class="question-sidebar" aria-label="题号导航">
        <div class="sidebar-head">
          <strong>题目</strong>
          <span>{{ current + 1 }} / {{ questions.length }}</span>
        </div>
        <div class="question-grid">
          <button
            v-for="(item, index) in questions"
            :key="item.item_id"
            class="question-dot"
            :class="{ current: index === current, answered: hasMeaningfulAnswer(answerFor(item)) }"
            :aria-label="`第 ${index + 1} 题${hasMeaningfulAnswer(answerFor(item)) ? '，已作答' : '，未作答'}`"
            :aria-current="index === current ? 'step' : undefined"
            @click="goto(index)"
          >{{ index + 1 }}</button>
        </div>
        <el-button v-if="!submitted" type="primary" class="submit-button" :loading="submitting" @click="submit">
          交卷<span v-if="unansweredCount">（{{ unansweredCount }} 题未答）</span>
        </el-button>
      </aside>

      <section v-if="q" class="question-column">
        <article class="question-card">
          <div class="question-topline">
            <div class="question-tags">
              <span>第 {{ current + 1 }} 题</span>
              <span>{{ questionTypeLabel(q.question_type) }}</span>
              <span v-if="!submitted || scoreOf(q)">{{ q.score }} 分</span>
              <span v-if="submitted && q.is_scoring_revision">修订版（评分依据）</span>
            </div>
            <div class="question-tools">
              <el-segmented v-if="hasBilingual" :model-value="lang" size="small"
                :options="[{ label: '中文题目', value: 'zh' }, { label: '英文原文', value: 'en' }]"
                aria-label="题目语言" @change="setLang" />
              <span class="save-state" :class="{ failed: saveState[q.question_id] === '保存失败' }" aria-live="polite">
                {{ submitted ? '已交卷' : (saveState[q.question_id] || '自动保存') }}
              </span>
            </div>
          </div>

          <div class="question-stem">
            <span v-if="q.prompt_prefix" class="stem-prefix">{{ q.prompt_prefix }}</span>
            <SourceQuestionContent :question="q" :fallback="displayStem(q)" />
          </div>
          <p v-if="translationMissing(q)" class="translation-missing">
            中文译文尚未生成，当前显示英文原文
          </p>
          <div v-if="q.stem_images?.length" class="stem-images">
            <img v-for="(img, index) in q.stem_images" :key="img" :src="'/' + img" :alt="`第 ${current + 1} 题配图 ${index + 1}`" />
          </div>

          <div v-if="submitted && q.is_scoring_revision && q.options?.length" class="revised-options">
            <div v-for="option in q.options || []" :key="option.key" class="revised-option">
              <b>{{ option.key }}</b><MathText :text="displayOption(option)" />
            </div>
          </div>
          <div v-else-if="q.options_incomplete" class="option-missing">
            <strong>{{ q.repair_hint || '该题选项缺失，暂不可作答' }}</strong>
            <span>这道题的选项没能完整保存，不参与本次作答与计分；其他题目不受影响。</span>
          </div>

          <div v-else-if="q.question_type === 'single_choice'" class="answer-area option-list">
            <label v-for="option in q.options" :key="option.key" class="option-item">
              <el-radio
                :model-value="answerFor(q).key || ''"
                :value="option.key"
                :disabled="submitted"
                @update:model-value="(value: any) => setResp(q.question_id, { key: value })"
              />
              <span class="option-key">{{ option.key }}.</span>
              <SourceQuestionContent :question="q" part="option" :option-key="option.key"
                :fallback="displayOption(option) || '选项内容缺失'" />
            </label>
          </div>

          <div
            v-else-if="q.question_type === 'multi_choice' && !q.options_incomplete"
            class="answer-area option-list"
          >
            <label v-for="option in q.options" :key="option.key" class="option-item">
              <el-checkbox
                :model-value="selectedKeys(q.question_id, q.item_id).includes(option.key)"
                :disabled="submitted"
                @update:model-value="(checked: any) => toggleMulti(q.question_id, option.key, checked)"
              />
              <span class="option-key">{{ option.key }}.</span>
              <SourceQuestionContent :question="q" part="option" :option-key="option.key"
                :fallback="displayOption(option) || '选项内容缺失'" />
            </label>
          </div>

          <el-radio-group
            v-else-if="q.question_type === 'true_false'"
            :model-value="answerFor(q).value ?? null"
            :disabled="submitted"
            class="answer-area boolean-options"
            @update:model-value="(value: any) => setResp(q.question_id, { value })"
          >
            <el-radio :value="true">正确</el-radio>
            <el-radio :value="false">错误</el-radio>
          </el-radio-group>

          <div
            v-else-if="q.question_type === 'numeric' && q.response_schema?.kind === 'numeric_multi'"
            class="answer-area multi-part-answer"
          >
            <label v-for="(part, index) in q.response_schema.parts" :key="`${part.label}-${index}`">
              <span>{{ part.label }}<template v-if="part.unit">（{{ part.unit }}）</template></span>
              <el-input
                :model-value="responseParts(q.question_id, q.item_id)[index] ?? ''"
                :disabled="submitted"
                placeholder="输入数值"
                :aria-label="`${part.label}数值答案`"
                @update:model-value="(value: any) => setResponsePart(q.question_id, index, value)"
              />
            </label>
          </div>

          <div v-else-if="q.question_type === 'numeric'" class="answer-area numeric-answer">
            <el-input
              :model-value="answerFor(q).value ?? ''"
              :disabled="submitted"
              placeholder="输入数值"
              aria-label="数值答案"
              @update:model-value="(value: any) => setResp(q.question_id, { value })"
            />
            <el-input
              :model-value="answerFor(q).unit ?? ''"
              :disabled="submitted"
              placeholder="单位（选填）"
              aria-label="答案单位"
              @update:model-value="(unit: any) => setResp(q.question_id, { unit })"
            />
          </div>

          <div
            v-else-if="q.question_type === 'fill_blank' && Number(q.response_schema?.count || 0) > 1"
            class="answer-area multi-part-answer"
          >
            <label v-for="index in q.response_schema.count" :key="index">
              <span>第 {{ index }} 空</span>
              <el-input
                :model-value="responseParts(q.question_id, q.item_id)[index - 1] ?? ''"
                :disabled="submitted"
                placeholder="填写答案"
                :aria-label="`第${index}空答案`"
                @update:model-value="(value: any) => setResponsePart(q.question_id, index - 1, value)"
              />
            </label>
          </div>

          <div v-else class="answer-area">
            <el-input
              type="textarea"
              :rows="q.question_type === 'comprehensive' ? 8 : 5"
              :model-value="answerFor(q).text || ''"
              :disabled="submitted"
              placeholder="在这里写下你的思路和答案"
              aria-label="文字答案"
              @update:model-value="(text: any) => setResp(q.question_id, { text })"
            />
            <div v-if="!submitted" class="ocr-row">
              <el-button size="small" :loading="uploadBusy === q.question_id"
                @click="pickPhoto(q.question_id)">上传手写答案</el-button>
              <span class="ocr-hint">支持 JPG / PNG / WebP，可多张；原图会保留，可单独识别</span>
              <input ref="photoInput" type="file" accept="image/*" multiple
                class="ocr-input" @change="onPhotosPicked" />
            </div>

            <div v-if="(attachments[q.question_id] || []).length" class="attach-grid">
              <div v-for="item in attachments[q.question_id]" :key="item.attachment_id"
                class="attach-item">
                <AuthenticatedImage :src="item.url" :alt="`第 ${current + 1} 题手写答案 ${item.position}`"
                  loading="lazy" @click="openOcr(item)" />
                <div class="attach-meta">
                  <span class="attach-state" :class="item.ocr?.status || 'pending'">
                    {{ item.ocr ? (item.ocr.status === 'ok' ? '已识别' : '识别失败') : '待识别' }}
                  </span>
                  <button v-if="!submitted" type="button" class="attach-del"
                    @click="removeAttachment(item)">删除</button>
                </div>
              </div>
            </div>
          </div>

          <div v-if="!submitted" class="question-actions">
            <el-button @click="saveOne(q.question_id)">立即保存</el-button>
            <el-button v-if="!isQuiz" :loading="hintBusy" @click="askHint">给我提示</el-button>
            <el-button v-if="!isQuiz" @click="viewSolution">查看解析</el-button>
            <span v-if="meta(q.question_id).hints_used" class="support-note">已使用 {{ meta(q.question_id).hints_used }} 次提示</span>
          </div>

          <div v-if="hintText[q.question_id]" class="learning-note" aria-live="polite">
            <strong>提示</strong>
            <MathText :text="hintText[q.question_id]" />
          </div>
          <div v-if="solutionText[q.question_id]" class="learning-note learning-note--solution" aria-live="polite">
            <strong>解析</strong>
            <MathText :text="solutionText[q.question_id]" />
          </div>
        </article>

        <article v-if="submitted && scoreOf(q)" class="feedback-card">
          <div class="feedback-head">
            <div>
              <p class="eyebrow">本题结果</p>
              <h2>{{ scoreOf(q).correct === true ? '回答正确' : '需要再看看' }}</h2>
            </div>
            <strong>{{ scoreOf(q).awarded_score }} / {{ scoreOf(q).max_score }} 分</strong>
          </div>
          <div class="answer-comparison">
            <div><span>你的答案</span><p>{{ formatAnswer(answerFor(q)) }}</p></div>
            <div v-if="q.answer_payload && Object.keys(q.answer_payload).length"><span>参考答案</span><p>{{ formatAnswer(q.answer_payload) }}</p></div>
          </div>
          <div v-if="q.explanation" class="explanation">
            <strong>解题思路</strong>
            <MathText :text="q.explanation" />
          </div>
          <details v-if="q.original_question" class="original-question">
            <summary>查看原始题目</summary>
            <SourceQuestionContent :question="q.original_question" :fallback="q.original_question.stem" />
            <p v-for="option in q.original_question.options || []" :key="option.key">{{ option.key }}. {{ option.text }}</p>
          </details>
          <el-button text @click="openDispute">对参考答案有疑问？</el-button>
        </article>

        <nav class="question-footer" aria-label="题目翻页">
          <el-button :disabled="current === 0" @click="goto(current - 1)">上一题</el-button>
          <button class="mobile-nav-button" @click="navDrawer = true">题号 {{ current + 1 }}/{{ questions.length }}</button>
          <el-button v-if="current < questions.length - 1" type="primary" @click="goto(current + 1)">下一题</el-button>
          <el-button v-else-if="!submitted" type="primary" :loading="submitting" @click="submit">交卷</el-button>
          <el-button v-else type="primary" @click="router.push(feedbackDestination)">查看学情</el-button>
        </nav>
      </section>
    </div>

    <el-drawer v-model="navDrawer" title="选择题目" direction="btt" size="55%" class="question-drawer">
      <div class="question-grid question-grid--mobile">
        <button
          v-for="(item, index) in questions"
          :key="item.item_id"
          class="question-dot"
          :class="{ current: index === current, answered: hasMeaningfulAnswer(answerFor(item)) }"
          @click="goto(index)"
        >{{ index + 1 }}</button>
      </div>
      <el-button v-if="!submitted" type="primary" class="drawer-submit" :loading="submitting" @click="submit">交卷</el-button>
    </el-drawer>

    <el-dialog v-model="ocrDialog" title="手写答案识别" width="min(780px, 94vw)"
      class="ocr-dialog">
      <div v-if="ocrCurrent" class="ocr-panel">
        <div class="ocr-image">
          <AuthenticatedImage :src="ocrCurrent.url" alt="手写答案原图" />
          <small>原图保留，不会被识别结果替换</small>
        </div>
        <div class="ocr-editor">
          <el-alert v-if="ocrCurrent.ocr?.status === 'failed'" type="warning"
            :closable="false" show-icon title="识别失败，原图仍然有效"
            :description="ocrCurrent.ocr?.error || '可以直接用图片交卷，或换一张更清晰的照片'" />
          <el-input v-model="ocrText" type="textarea" :rows="9"
            placeholder="识别文本，可自行修改后保存" />
          <div class="ocr-latex">
            <strong>公式预览</strong>
            <MathText :text="ocrText || '（暂无内容）'" />
          </div>
          <div class="ocr-actions">
            <el-button :loading="ocrBusyId === ocrCurrent.attachment_id"
              @click="runOcr(ocrCurrent)">重新识别</el-button>
            <el-button type="primary" :disabled="!ocrText.trim()"
              @click="confirmOcr">确认保存</el-button>
          </div>
        </div>
      </div>
    </el-dialog>
  </main>
</template>

<style scoped>
.exam-page { max-width: 1240px; margin: 0 auto; padding: 20px 24px 48px; color: #182033; }
.exam-toolbar { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(220px, 420px) auto; align-items: center; gap: 28px; min-height: 76px; margin-bottom: 18px; }
.exam-title { display: flex; align-items: center; gap: 12px; }
.exam-title h1 { margin: 2px 0 0; font-size: 24px; line-height: 1.2; }
.eyebrow { margin: 0; color: #4f46e5; font-size: 14px; font-weight: 700; letter-spacing: .04em; }
.back-button { width: 44px; height: 44px; border: 1px solid #e1e4ec; border-radius: 12px; background: #fff; color: #30384a; font-size: 22px; cursor: pointer; }
.back-button:hover { border-color: #4f46e5; color: #4f46e5; }
.back-button:focus-visible, .question-dot:focus-visible, .mobile-nav-button:focus-visible { outline: 3px solid rgb(79 70 229 / 25%); outline-offset: 2px; }
.exam-progress { display: grid; gap: 7px; color: #4e5668; font-size: 14px; }
.progress-track { height: 7px; overflow: hidden; border-radius: 99px; background: #e8eaf0; }
.progress-track span { display: block; height: 100%; border-radius: inherit; background: #4f46e5; transition: width .2s ease; }
.exam-meta { display: flex; align-items: center; justify-content: flex-end; gap: 10px; }
.timer { min-width: 70px; font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }
.elapsed-note { color: #69778d; font-size: 14px; white-space: nowrap; }
.error-banner { display: flex; justify-content: space-between; gap: 16px; margin-bottom: 16px; padding: 12px 16px; border: 1px solid #fecaca; border-radius: 12px; background: #fef2f2; color: #991b1b; }
.error-banner button { border: 0; background: transparent; color: inherit; cursor: pointer; }
.result-hero { display: grid; grid-template-columns: minmax(240px, .8fr) minmax(320px, 1.2fr); gap: 36px; margin-bottom: 22px; padding: 28px 32px; border-radius: 18px; background: linear-gradient(135deg, #eef2ff, #f0fdfa); border: 1px solid #dfe3fb; }
.score-line { display: flex; align-items: baseline; gap: 8px; margin-top: 6px; }
.score-line strong { font-size: 48px; line-height: 1; color: #3730a3; }
.score-line span, .result-hero p { color: #596174; }
.result-next { display: flex; flex-direction: column; align-items: flex-start; justify-content: center; }
.result-next p { margin: 10px 0 14px; }
.retest-verdict{margin-top:12px;padding:9px 11px;border-radius:8px;background:#fff4df;color:#8b6321;font-size:14px}.retest-verdict.passed{background:#e7f7ee;color:#14794f}.retest-verdict.not_passed{background:#ffedef;color:#c33b46}
.result-actions { display: flex; gap: 10px; }
.exam-layout { display: grid; grid-template-columns: 210px minmax(0, 1fr); align-items: start; gap: 22px; }
.question-sidebar { position: sticky; top: 16px; padding: 18px; border: 1px solid #e3e6ee; border-radius: 16px; background: #fff; box-shadow: 0 8px 30px rgb(22 31 52 / 5%); }
.sidebar-head { display: flex; justify-content: space-between; margin-bottom: 14px; color: #596174; }
.question-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
.question-dot { min-width: 0; aspect-ratio: 1; border: 1px solid #dfe2e9; border-radius: 9px; background: #fff; color: #555e70; font-weight: 600; cursor: pointer; }
.question-dot.answered { border-color: #a7f3d0; background: #ecfdf5; color: #047857; }
.question-dot.current { border-color: #4f46e5; background: #4f46e5; color: #fff; box-shadow: 0 0 0 3px rgb(79 70 229 / 13%); }
.submit-button { width: 100%; min-height: 44px; margin-top: 18px; }
.question-column { min-width: 0; }
.question-card, .feedback-card, .source-card { margin-bottom: 16px; padding: 28px 32px; border: 1px solid #e3e6ee; border-radius: 18px; background: #fff; box-shadow: 0 8px 30px rgb(22 31 52 / 4%); }
.question-topline, .feedback-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.question-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.question-tools :deep(.el-segmented) { --el-segmented-item-selected-bg-color: #eef2ff; --el-segmented-item-selected-color: #4338ca; }
.translation-missing { margin: 8px 0 0; padding: 6px 10px; border-left: 3px solid #d97706; border-radius: 4px; background: #fffbeb; color: #9a5b12; font-size: 14px; }
.option-missing {
  display: flex; flex-direction: column; gap: 4px; padding: 12px 14px;
  margin: 10px 0; border: 1px solid #fde68a; border-radius: 8px;
  background: #fffbeb; color: #92400e; font-size: 14px; line-height: 1.6;
}
.option-missing strong { font-size: 14px; }
.ocr-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 8px; }
.ocr-hint { color: #8b93a3; font-size: 14px; }
.ocr-input { display: none; }
.attach-grid { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.attach-item { width: 96px; overflow: hidden; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; }
.attach-item img { display: block; width: 100%; height: 72px; object-fit: cover; cursor: pointer; }
.attach-meta { display: flex; align-items: center; justify-content: space-between; padding: 3px 5px; font-size: 14px; }
.attach-state.ok { color: #047857; }
.attach-state.failed { color: #b91c1c; }
.attach-state.pending { color: #8b93a3; }
.attach-del { padding: 0; border: 0; background: none; color: #b91c1c; cursor: pointer; font-size: 14px; }
.ocr-panel { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 16px; }
.ocr-image img { width: 100%; border: 1px solid #e5e7eb; border-radius: 8px; }
.ocr-image small { display: block; margin-top: 6px; color: #8b93a3; font-size: 14px; }
.ocr-editor { display: flex; min-width: 0; flex-direction: column; gap: 10px; }
.ocr-latex { max-height: 180px; overflow: auto; padding: 10px 12px; border: 1px solid #eef0f4; border-radius: 8px; background: #fbfcfe; font-size: 14px; }
.ocr-latex strong { display: block; margin-bottom: 6px; color: #697181; font-size: 14px; }
.ocr-actions { display: flex; justify-content: flex-end; gap: 8px; }
.question-tags { display: flex; flex-wrap: wrap; gap: 8px; }
.question-tags span { padding: 5px 9px; border-radius: 7px; background: #f3f4f8; color: #555e70; font-size: 14px; }
.save-state { color: #6b7280; font-size: 14px; white-space: nowrap; }
.save-state.failed { color: #b91c1c; }
.question-stem { margin: 28px 0 22px; font-size: 18px; line-height: 1.8; font-weight: 600; }
.stem-prefix { display: block; margin-bottom: 6px; color: var(--muted); font-size: 14px; font-weight: 500; }
.stem-images { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.stem-images img { max-width: 100%; max-height: 320px; border-radius: 10px; }
.answer-area { margin-top: 16px; }
.option-list { display: grid; gap: 10px; }
.option-item { display: flex; align-items: flex-start; gap: 5px; min-height: 52px; padding: 13px 15px; border: 1px solid #e1e4eb; border-radius: 12px; cursor: pointer; transition: border-color .15s, background .15s; }
.option-item:hover { border-color: #a5b4fc; background: #fafaff; }
.option-key { margin: 2px 5px 0 0; color: #4f46e5; font-weight: 700; }
.boolean-options { display: flex; gap: 14px; }
.boolean-options :deep(.el-radio) { min-height: 52px; min-width: 150px; padding: 0 18px; border: 1px solid #e1e4eb; border-radius: 12px; }
.numeric-answer { display: grid; grid-template-columns: minmax(180px, 260px) minmax(140px, 200px); gap: 12px; }
.multi-part-answer { display: grid; gap: 12px; }
.multi-part-answer label { display: grid; gap: 6px; color: var(--text-2); }
.question-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-top: 20px; padding-top: 18px; border-top: 1px solid #edf0f4; }
.support-note { color: #6b7280; font-size: 14px; }
.learning-note { margin-top: 16px; padding: 16px 18px; border-left: 4px solid #4f46e5; border-radius: 10px; background: #eef2ff; line-height: 1.7; }
.learning-note strong { display: block; margin-bottom: 5px; color: #3730a3; }
.learning-note--solution { border-left-color: #0d9488; background: #f0fdfa; }
.learning-note--solution strong { color: #0f766e; }
.feedback-head h2, .source-card h2 { margin: 4px 0 0; font-size: 20px; }
.feedback-head > strong { font-size: 20px; color: #3730a3; }
.answer-comparison { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 20px 0; }
.answer-comparison > div { padding: 15px; border-radius: 11px; background: #f7f8fb; }
.answer-comparison span { color: #6b7280; font-size: 14px; }
.answer-comparison p { margin: 7px 0 0; line-height: 1.7; white-space: pre-wrap; }
.explanation { margin: 12px 0; line-height: 1.8; }
.explanation > strong { display: block; margin-bottom: 6px; }
.source-card ul { padding-left: 20px; line-height: 1.9; }
.muted { color: #6b7280; }
.page-reference { margin-top: 12px; }
pre { max-width: 100%; overflow: auto; padding: 12px; border-radius: 8px; background: #f7f8fa; color: #4b5563; font-size: 14px; white-space: pre-wrap; }
.question-footer { display: flex; align-items: center; justify-content: space-between; min-height: 52px; margin-top: 6px; }
.mobile-nav-button { display: none; min-height: 44px; border: 1px solid #e1e4eb; border-radius: 10px; background: #fff; color: #4b5563; }
.drawer-submit { width: 100%; min-height: 44px; margin-top: 18px; }

@media (max-width: 900px) {
  .exam-page { padding: 14px 18px 40px; }
  .exam-toolbar { grid-template-columns: 1fr auto; gap: 12px; }
  .exam-progress { grid-column: 1 / -1; grid-row: 2; }
  .exam-layout { grid-template-columns: 1fr; }
  .question-sidebar { display: none; }
  .mobile-nav-button { display: inline-flex; align-items: center; padding: 0 14px; }
}

@media (max-width: 640px) {
  .exam-page { padding: 10px 12px 32px; }
  .exam-title h1 { font-size: 20px; }
  .exam-meta .status-badge { display: none; }
  .timer { min-width: 56px; font-size: 16px; }
  .result-hero { grid-template-columns: 1fr; gap: 18px; padding: 22px 20px; }
  .result-actions { width: 100%; flex-direction: column; }
  .result-actions :deep(.el-button) { width: 100%; margin-left: 0; min-height: 44px; }
  .question-card, .feedback-card, .source-card { padding: 20px 16px; border-radius: 14px; }
  .question-topline { align-items: center; flex-direction: column; gap: 8px; }
  .ocr-panel { grid-template-columns: 1fr; }
  .attach-item { width: 78px; }
  .question-stem { margin: 22px 0 18px; font-size: 17px; }
  .option-item { min-height: 56px; padding: 14px 12px; }
  .numeric-answer, .answer-comparison { grid-template-columns: 1fr; }
  .boolean-options { flex-direction: column; gap: 8px; }
  .boolean-options :deep(.el-radio) { width: 100%; min-height: 52px; margin-right: 0; }
  .question-actions { display: grid; grid-template-columns: repeat(2, 1fr); }
  .question-actions :deep(.el-button) { min-height: 44px; margin-left: 0; }
  .support-note { grid-column: 1 / -1; }
  .question-footer :deep(.el-button), .mobile-nav-button { min-height: 44px; }
  .question-grid--mobile { grid-template-columns: repeat(6, 1fr); }
  .question-grid--mobile .question-dot { min-height: 44px; aspect-ratio: auto; }
}
.revised-options{display:grid;gap:8px;margin-top:16px}
.revised-option{display:grid;grid-template-columns:24px minmax(0,1fr);gap:10px;padding:10px 12px;border:1px solid #e3e6ee;border-radius:9px;background:#fff;line-height:1.6}
.original-question{margin-top:16px;padding-top:12px;border-top:1px solid #e3e6ee;color:#596579;line-height:1.6}
.original-question summary{color:#3159c9;cursor:pointer}
.original-question p{margin:5px 0 0 12px}
</style>
