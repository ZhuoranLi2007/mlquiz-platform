<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { api } from '../api/client'
import { friendlyError } from '../utils/presentation'
import MathText from './MathText.vue'
import LearningResourceList from './LearningResourceList.vue'

const props = defineProps<{ classroomId: string; assessmentVersionId: string }>()
const router = useRouter()
const data = ref<any>(null)
const loading = ref(false)
const error = ref('')
const query = ref('')
const attentionOnly = ref(false)
const sort = ref('attention')
const selected = ref<string[]>([])
const kpId = ref('')
const drawer = ref(false)
const detail = ref<any>(null)
const detailError = ref('')
const detailLoading = ref(false)
const cell = ref<any>(null)
const publishing = ref(false)
const jobs = ref<any[]>([])
let sequence = 0
let detailSequence = 0
let timer: ReturnType<typeof setTimeout> | undefined
const labels: Record<string, string> = {
  needs_review: '需加强', insufficient: '证据不足', achieved: '已达标', delayed_confirmed: '延后确认',
  absent: '缺考', no_submission: '未交卷', pending_key: '待答案核对', ungraded: '待评分',
}
const rows = computed(() => (data.value?.rows || []).filter((row: any) =>
  (!query.value || row.display_code.toLowerCase().includes(query.value.trim().toLowerCase())) &&
  (!attentionOnly.value || row.attention_count || row.ungraded_count ||
    ['absent', 'no_submission'].includes(row.participation_status) || ['published', 'opened'].includes(row.task_state)))
  .sort((a: any, b: any) => sort.value === 'code' ? a.display_code.localeCompare(b.display_code)
    : b.attention_count - a.attention_count || b.ungraded_count - a.ungraded_count || a.display_code.localeCompare(b.display_code)))
const target = computed(() => data.value?.columns?.find((column: any) => column.kp_id === kpId.value))
const selectedRows = computed(() => (data.value?.rows || []).filter((row: any) => selected.value.includes(row.student_id)))
const detailItems = computed(() => (detail.value?.items || []).filter((item: any) => cell.value?.evidence?.positions.includes(item.position)))
const resources = computed(() => (detail.value?.resources || []).filter((row: any) =>
  (row.kp_ids || [row.kp_id]).includes(cell.value?.kp.kp_id)))
function canSelect(row: any) { return row.eligible && row.cells[kpId.value]?.scored_count > 0 }
function label(row: any, kp: string) {
  const evidence = row.cells[kp]
  if (!evidence) return '未覆盖'
  if (!evidence.scored_count) return labels[row.participation_status] || '待评分'
  return labels[evidence.state] || '证据不足'
}
async function load() {
  const expected = ++sequence
  clearTimeout(timer)
  loading.value = true
  error.value = ''
  selected.value = []
  jobs.value = []
  drawer.value = false
  detailSequence++
  publishing.value = false
  try {
    const result = await api.get('/teacher/class-matrix', {
      classroom_id: props.classroomId, assessment_version_id: props.assessmentVersionId,
    })
    if (expected !== sequence) return
    data.value = result
    if (!result.columns.some((column: any) => column.kp_id === kpId.value)) kpId.value = result.columns[0]?.kp_id || ''
  } catch (e) { if (expected === sequence) error.value = friendlyError(e, '班级矩阵加载失败') }
  finally { if (expected === sequence) loading.value = false }
}
async function inspect(row: any, kp: any) {
  kpId.value = kp.kp_id
  const expected = ++detailSequence
  cell.value = { row, kp, evidence: row.cells[kp.kp_id] }
  drawer.value = true
  detail.value = null
  detailError.value = ''
  detailLoading.value = false
  if (!row.attempt_id) return
  detailLoading.value = true
  try {
    const result = await api.get('/teacher/student-attempt', {
      student_id: row.student_id, assessment_version_id: props.assessmentVersionId, attempt_id: row.attempt_id,
    })
    if (expected === detailSequence) detail.value = result
  } catch (e) { if (expected === detailSequence) detailError.value = friendlyError(e, '作答依据加载失败') }
  finally { if (expected === detailSequence) detailLoading.value = false }
}
async function poll(expected: number) {
  if (expected !== sequence) return
  const active = jobs.value.filter(row => ['queued', 'running'].includes(row.status))
  try {
    const states = await Promise.all([...new Set(active.map(row => row.job_id))].map(id =>
      api.get('/teacher/class-matrix/jobs/' + id)))
    if (expected !== sequence) return
    for (const state of states) {
      for (const row of jobs.value.filter(row => row.job_id === state.job_id)) {
        row.status = state.status
        row.result = state.items.find((item: any) => item.student_id === row.student_id)
      }
    }
    if (jobs.value.some(row => ['queued', 'running'].includes(row.status))) {
      timer = setTimeout(() => poll(expected), 2000)
    } else publishing.value = false
  } catch { if (expected === sequence) { publishing.value = false; error.value = '进度读取中断，重新发布会恢复已有任务。' } }
}
async function publish() {
  if (publishing.value || !selected.value.length) return
  const expected = sequence
  const request = {
    classroom_id: props.classroomId, assessment_version_id: props.assessmentVersionId,
    student_ids: [...selected.value], kp_id: kpId.value,
  }
  try {
    await ElMessageBox.confirm(
      target.value.name + ' · ' + selectedRows.value.map((row: any) => row.display_code).join('、') +
      '。使用已核验题库，分别按学生证据准备训练；已有任务会继续复用。',
      '发布分组训练', { confirmButtonText: '确认发布', cancelButtonText: '取消' })
  } catch { return }
  if (expected !== sequence) return
  publishing.value = true
  error.value = ''
  try {
    const result = await api.post('/teacher/class-matrix/practice', request)
    if (expected !== sequence) return
    jobs.value = result.items
    await poll(expected)
  } catch (e) { if (expected === sequence) { error.value = friendlyError(e, '训练发布失败'); publishing.value = false } }
}
function jobLabel(row: any) {
  if (['queued', 'running'].includes(row.status)) return '正在准备'
  if (row.result?.status === 'published') return row.result.reused ? '继续已有任务'
    : row.result.retest_kind === 'practice' ? '巩固练习已准备' : '新题检测已准备'
  return row.error || row.result?.reason || '暂未完成，可重试'
}
watch(kpId, () => { selected.value = [] })
watch(() => [props.classroomId, props.assessmentVersionId], () => { data.value = null; void load() }, { immediate: true })
onBeforeUnmount(() => { sequence++; detailSequence++; clearTimeout(timer) })
</script>
<template>
  <section class="class-matrix" v-loading="loading" aria-label="班级知识点诊断矩阵">
    <div class="matrix-toolbar">
      <el-input v-model="query" clearable placeholder="搜索学生编号" aria-label="搜索学生编号"/>
      <el-checkbox v-model="attentionOnly">优先关注</el-checkbox>
      <el-select v-model="sort" aria-label="矩阵排序"><el-option value="attention" label="需加强优先"/><el-option value="code" label="学生编号排序"/></el-select>
      <el-button :disabled="publishing" @click="load">刷新矩阵</el-button>
    </div>
    <p v-if="data" class="matrix-basis">{{ data.basis }}<span v-if="data.unmapped_item_count">另有 {{ data.unmapped_item_count }} 道原卷题未映射知识点。</span></p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
    <div v-if="data?.columns.length && rows.length" class="matrix-scroll" tabindex="0" aria-label="学生与知识点矩阵，可横向滚动">
      <table>
        <thead><tr><th scope="col">学生</th><th v-for="column in data.columns" :key="column.kp_id" scope="col"><button :aria-pressed="kpId === column.kp_id" @click="kpId = column.kp_id">{{ column.name }}</button></th><th scope="col">关注事项</th></tr></thead>
        <tbody><tr v-for="row in rows" :key="row.student_id">
          <th scope="row"><label><input v-model="selected" type="checkbox" :value="row.student_id" :disabled="!canSelect(row) || publishing" :aria-label="'选择 ' + row.display_code"/>{{ row.display_code }}</label></th>
          <td v-for="column in data.columns" :key="column.kp_id">
            <button class="matrix-cell" :class="row.cells[column.kp_id]?.scored_count ? row.cells[column.kp_id]?.state : 'insufficient'"
              :aria-label="row.display_code + ' ' + column.name + ' ' + label(row, column.kp_id)" @click="inspect(row, column)">
              <b>{{ label(row, column.kp_id) }}</b><small v-if="row.cells[column.kp_id]?.scored_count">原卷答对 {{ row.cells[column.kp_id].correct_count }}/{{ row.cells[column.kp_id].scored_count }}</small><small v-else>尚无有效评分</small>
            </button>
          </td>
          <td class="attention-note"><span v-if="row.attention_count">{{ row.attention_count }} 个知识点需加强</span><span v-if="row.ungraded_count">{{ row.ungraded_count }} 题待评分</span><span v-if="['absent','no_submission'].includes(row.participation_status)">{{ labels[row.participation_status] }}</span><span v-if="['published','opened'].includes(row.task_state)">复测待完成</span><span v-if="!row.eligible">当前无法发布训练</span></td>
        </tr></tbody>
      </table>
    </div>
    <el-empty v-else-if="!loading && !error" description="当前范围没有可展示的矩阵数据"/>
    <div v-if="data?.columns.length" class="matrix-actions">
      <el-select v-model="kpId" aria-label="分组训练目标"><el-option v-for="column in data.columns" :key="column.kp_id" :value="column.kp_id" :label="column.name"/></el-select>
      <el-button :disabled="publishing" @click="selected = rows.filter(canSelect).map((row: any) => row.student_id)">选择筛选结果</el-button>
      <span>已选 {{ selected.length }} 人</span>
      <el-button type="primary" :disabled="!selected.length" :loading="publishing" @click="publish">发布分组训练</el-button>
    </div>
    <ul v-if="jobs.length" class="batch-results" aria-live="polite"><li v-for="row in jobs" :key="row.student_id">{{ data.rows.find((student: any) => student.student_id === row.student_id)?.display_code }} · {{ jobLabel(row) }}</li></ul>
    <el-drawer v-model="drawer" :title="cell ? cell.row.display_code + ' · ' + cell.kp.name : '作答依据'" size="min(640px, 96vw)">
      <div v-if="cell" v-loading="detailLoading">
        <p>{{ label(cell.row, cell.kp.kp_id) }} · 原卷 {{ cell.evidence?.question_count || 0 }} 道关联题</p>
        <el-alert v-if="detailError" :title="detailError" type="error" :closable="false"/>
        <article v-for="item in detailItems" :key="item.paper_item_id" class="matrix-question"><b>第 {{ item.position }} 题 · {{ item.evidence_status === 'scored' ? item.correct ? '答对' : '答错' : '待评分' }}</b><MathText :text="item.stem"/></article>
        <el-empty v-if="!detailLoading && !detailItems.length && !detailError" description="当前没有可查看的关联作答"/>
        <LearningResourceList v-if="resources.length" :resources="resources"/>
        <el-button v-if="cell.row.attempt_id" @click="router.push('/teacher/attempts/' + cell.row.attempt_id)">查看完整答卷</el-button>
      </div>
    </el-drawer>
  </section>
</template>
<style scoped>
.class-matrix {
  height:100%;
  display:flex;
  flex-direction:column;
  gap:12px;
  min-height:0;
  overflow:auto;
  overscroll-behavior:contain;
  color:#34445f
}
.matrix-toolbar,.matrix-actions {
  display:flex;
  align-items:center;
  flex-wrap:wrap;
  gap:10px
}
.matrix-toolbar .el-input {
  max-width:220px
}
.matrix-toolbar .el-select {
  width:160px
}
.matrix-basis {
  margin:0;
  font-size:13px;
  color:#687c96;
  line-height:1.6
}
.matrix-scroll {
  flex:1;
  min-height:130px;
  overflow:auto;
  border:1px solid #dce5f2;
  border-radius:10px;
  background:#fff
}
.matrix-scroll table {
  width:100%;
  border-collapse:separate;
  border-spacing:0;
  text-align:left;
  font-size:14px
}
.matrix-scroll th,.matrix-scroll td {
  padding:9px;
  border-right:1px solid #e5edf6;
  border-bottom:1px solid #e5edf6
}
.matrix-scroll thead th {
  position:sticky;
  top:0;
  background:#f2f6fc;
  z-index:2
}
.matrix-scroll th:first-child {
  position:sticky;
  left:0;
  background:#f8faff;
  min-width:110px;
  z-index:1
}
.matrix-scroll thead th:first-child {
  z-index:3
}
.matrix-scroll label {
  display:flex;
  align-items:center;
  gap:8px;
  white-space:nowrap
}
.matrix-scroll th button {
  border:0;
  background:none;
  text-align:left;
  font:inherit;
  color:#455f83;
  min-width:150px;
  cursor:pointer
}
.matrix-scroll th button[aria-pressed=true] {
  color:#225dc5;
  text-decoration:underline;
  text-underline-offset:5px
}
.matrix-cell {
  display:grid;
  gap:6px;
  width:100%;
  min-width:150px;
  border:1px solid #dce5f2;
  border-radius:7px;
  padding:10px;
  text-align:left;
  cursor:pointer;
  background:#f5f7fa;
  color:#64748b;
  font:inherit
}
.matrix-cell.needs_review {
  background:#fff6e9;
  border-color:#efd8b7;
  color:#a15b1d
}
.matrix-cell.achieved {
  background:#effaf4;
  border-color:#c9e8d7;
  color:#247a5d
}
.matrix-cell.delayed_confirmed {
  background:#edf3ff;
  color:#3c61a4
}
.matrix-cell small {
  font-size:12px
}
.attention-note {
  min-width:160px;
  color:#697d96
}
.attention-note span {
  display:block;
  font-size:12px;
  line-height:1.7
}
.matrix-actions .el-select {
  width:220px
}
.matrix-actions>span {
  font-size:13px;
  color:#69809c
}
.batch-results {
  padding:8px 12px;
  margin:0;
  background:#f2f6fc;
  list-style:none;
  font-size:13px;
  line-height:1.8;
  max-height:90px;
  overflow:auto;
  border-radius:8px
}
.matrix-question {
  display:grid;
  gap:10px;
  padding:14px 0;
  border-bottom:1px solid #e5edf6;
  font-size:14px;
  line-height:1.7
}
.matrix-question:last-of-type {
  margin-bottom:15px
}
.matrix-cell:focus-visible,.matrix-scroll button:focus-visible {
  outline:2px solid #315fc0;
  outline-offset:2px
}

</style>
