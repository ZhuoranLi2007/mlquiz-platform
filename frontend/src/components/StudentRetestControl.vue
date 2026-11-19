<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import { friendlyError } from '../utils/presentation'
import { actionLabel, roundLabel, loopTime, sourceLabel } from '../utils/learningLoop'

const props = defineProps<{ attemptId?: string; summary?: any; kpIds?: string[] }>()
const emit = defineEmits<{ updated: [] }>()
const router = useRouter()
const ownSummary = ref<any>(null)
const state = computed(() => props.summary || ownSummary.value || {})
const busy = ref(false)
const error = ref('')
const jobId = ref('')
const picker = ref(false)
const topics = ref<any[]>([])
const chosen = ref<string[]>([])
let timer: ReturnType<typeof setTimeout> | undefined
let sequence = 0
const successful = computed(() => state.value.last_passed)

async function openResult(row:any) {
  if (row.attempt_id || row.retest_attempt_id) {
    await router.push(`/exam/${row.attempt_id || row.retest_attempt_id}`)
  } else if (row.release?.release_id || row.release_id) {
    const tasks:any = await api.get('/student/assignments')
    const task = tasks.items.find((t:any) => t.release_id === (row.release_id || row.release?.release_id))
    if (!task) throw new Error('复测任务暂未找到，请刷新后重试。')
    const attempt:any = task.attempt_id ? task : await api.post(`/student/assignments/${task.recipient_id}/start`)
    await router.push(`/exam/${attempt.attempt_id}?recipient=${task.recipient_id}`)
  }
}
async function poll(id:string, expected:number) {
  if (expected !== sequence) return
  try {
    const job:any = await api.get(`/student/retests/jobs/${id}`)
    if (expected !== sequence) return
    if (['queued','running'].includes(job.status)) {
      timer=setTimeout(() => poll(id,expected),2500)
      return
    }
    busy.value=false
    emit('updated')
    if (job.result?.status === 'published') {
      ElMessage.success(job.result.retest_kind === 'practice' ? '巩固练习已准备好' : '复测已准备好')
      await openResult(job.result)
    } else error.value=job.result?.reason || job.error || '暂未准备好题目，可以使用题库或重试。'
  } catch (e) {
    if (expected === sequence) { busy.value=false;error.value=friendlyError(e,'连接中断，点击重试可恢复任务。') }
  }
}
async function refresh() {
  const expected=++sequence
  clearTimeout(timer);busy.value=false;jobId.value='';error.value=''
  try {
    const result:any=await api.get('/student/retests/state',{attempt_id:props.attemptId || ''})
    if (expected !== sequence) return
    ownSummary.value=result
    if (result.active_job_id) { busy.value=true;jobId.value=result.active_job_id;void poll(jobId.value,expected) }
  } catch { /* Keep the action accessible even when the state request fails. */ }
}
async function start(preferBank=false, retryGeneration=false) {
  if (busy.value) return
  error.value=''
  try {
    if (!retryGeneration && ['published','opened'].includes(state.value.task_state) && state.value.latest_task) {
      await openResult(state.value.latest_task);return
    }
    if (!props.attemptId && !chosen.value.length) {
      const result:any=await api.get('/student/retests/options');topics.value=result.items;picker.value=true;return
    }
    busy.value=true
    const result:any=await api.post('/student/retests/jobs',{
      attempt_id:props.attemptId || '',kp_ids:props.kpIds?.length ? props.kpIds : chosen.value,
      practice:state.value.task_state==='scoring_pending',prefer_bank:preferBank,retry_generation:retryGeneration,
    })
    jobId.value=result.job_id
    void poll(jobId.value,sequence)
  } catch(e) {busy.value=false;error.value=friendlyError(e,'复测发起失败，可以重试。')}
}
watch(() => props.attemptId,refresh,{immediate:true})
onBeforeUnmount(() => {sequence++;clearTimeout(timer)})
</script>

<template>
  <section class="loop-control" aria-label="复测操作区">
    <div class="loop-result">
      <b :class="{passed:state.round_result?.status==='passed'}">{{ roundLabel(state.round_result) }}</b>
      <small v-if="state.round_result">第 {{ state.round_result.round }} 轮<span v-if="state.round_result.accuracy != null"> · {{ Math.round(state.round_result.accuracy*100) }}%</span> · {{ loopTime(state.round_result.completed_at) }}</small>
      <small v-if="state.latest_task?.source_counts">第 {{ state.latest_task.round }} 轮题源：{{ sourceLabel(state.latest_task.source_counts) }}</small>
      <small v-if="successful && successful.cycle_id!==state.round_result?.cycle_id" class="passed">第 {{ successful.round }} 轮曾通过</small>
      <small v-if="state.task_state==='scoring_pending'">成绩确认中，已有达标记录保留</small>
      <small v-if="busy" role="status">正在准备题目，刷新后可恢复进度</small>
      <small v-if="error" role="alert" class="error">{{ error }}</small>
    </div>
    <div class="loop-actions">
      <el-button v-if="state.round_result" @click="openResult(state.round_result).catch(e=>error=friendlyError(e,'答卷打开失败'))">查看结果</el-button>
      <el-button v-if="error" @click="start(true)">使用题库</el-button>
      <el-button v-if="state.latest_task?.retest_kind==='practice' && !busy" @click="start(false,true)">尝试生成新题</el-button>
      <el-button type="primary" :loading="busy" @click="start()">{{ busy ? '准备中' : error ? '重试 / 恢复' : actionLabel(state) }}</el-button>
    </div>
    <el-dialog v-model="picker" title="选择自测知识点" width="min(480px, 94vw)">
      <p>选择 1–2 个知识点，完成 5 道题建立学习基线。</p>
      <el-select v-model="chosen" multiple :multiple-limit="2" filterable placeholder="选择知识点" aria-label="自测知识点" style="width:100%"><el-option v-for="row in topics" :key="row.kp_id" :label="row.name" :value="row.kp_id" /></el-select>
      <template #footer><el-button @click="picker=false">取消</el-button><el-button :disabled="!chosen.length" type="primary" @click="picker=false;start()">开始自测</el-button></template>
    </el-dialog>
  </section>
</template>
<style scoped>
.loop-control{display:flex;align-items:center;justify-content:space-between;gap:12px;border:1px solid #dce5f2;border-radius:10px;background:#f4f8ff;padding:10px 14px;min-width:0}.loop-result{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 10px;min-width:0}.loop-result b{color:#30496c;font-size:15px}.loop-result small{font-size:13px;color:#60738b}.loop-result .passed{color:#148457}.loop-result .error{color:#b04434;max-width:460px;overflow-wrap:anywhere}.loop-actions{display:flex;gap:6px;flex-shrink:0}.loop-actions .el-button{margin:0}@media(max-width:700px){.loop-control{flex-wrap:wrap}.loop-actions{margin-left:auto}.loop-result small{font-size:12px}}
</style>
