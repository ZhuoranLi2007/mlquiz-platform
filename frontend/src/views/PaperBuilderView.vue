<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api/client'
import MathText from '../components/MathText.vue'
import { friendlyError, questionTypeLabel } from '../utils/presentation'

const route = useRoute()
const router = useRouter()
const paperId = String(route.params.paperId || '')
const loading = ref(true)
const saving = ref(false)
const publishing = ref(false)
const paper = ref<any>(null)
const title = ref('')
const instructions = ref('')
const items = ref<any[]>([])
const bank = ref<any[]>([])
const kpMap = ref(new Map<string, string>())
const replaceOpen = ref(false)
const replaceIndex = ref(-1)
const replaceKeyword = ref('')
const publishOpen = ref(false)
const classroom = ref<any>(null)
const classroomId = ref('')
const students = ref<any[]>([])
const selectedStudents = ref<string[]>([])
const releaseTitle = ref('')
const dueAt = ref('')
const publishRequestKey = ref('')

const editable = computed(() => Boolean(paper.value?.editable))
const totalScore = computed(() => items.value.reduce(
  (sum, item) => sum + Number(item.score || 0), 0))
const onlineStudents = computed(() => students.value.filter(s => s.online_receivable))
const wholeClassSelected = computed(() => onlineStudents.value.length > 0
  && selectedStudents.value.length === onlineStudents.value.length)
const coverage = computed(() => {
  const counts = new Map<string, number>()
  for (const item of items.value) {
    const name = kpMap.value.get(item.main_kp_id) || '未标注'
    counts.set(name, (counts.get(name) || 0) + 1)
  }
  return [...counts.entries()].map(([name, count]) => ({ name, count }))
})
const replacementCandidates = computed(() => {
  const ids = new Set(items.value.map((item) => item.question_id))
  const term = replaceKeyword.value.trim().toLowerCase()
  return bank.value.filter((item) => item.usable_for_quiz && !ids.has(item.question_id)
    && (!term || [stemOf(item), kpMap.value.get(item.main_kp_id) || '']
      .join(' ').toLowerCase().includes(term))).slice(0, 80)
})

function stemOf(item: any) { return item?.stem_zh || item?.stem || '（题干缺失）' }
function optionKey(option: any, index: number) { return option?.key || option?.label || String.fromCharCode(65 + index) }
function optionText(option: any) { return option?.text || option?.content || option?.text_zh || '（选项正文缺失）' }
function answerText(item: any) {
  const answer = item?.answer_payload || {}
  const type = item?.question_type_effective || item?.question_type
  if (type === 'single_choice') return answer.key ? `选项 ${answer.key}` : '未提供可用答案'
  if (type === 'multi_choice') return Array.isArray(answer.keys) ? `选项 ${answer.keys.join('、')}` : '未提供可用答案'
  if (type === 'true_false') return typeof answer.value === 'boolean' ? (answer.value ? '正确' : '错误') : '未提供可用答案'
  if (answer.numeric !== undefined || answer.value !== undefined) return String(answer.numeric ?? answer.value)
  if (Array.isArray(answer.values)) return answer.values.join('；')
  return answer.text || answer.reference || '未提供可用答案'
}

async function load() {
  loading.value = true
  try {
    const [detail, questionBank, official] = await Promise.all([
      api.get(`/teacher/paper-drafts/${paperId}`),
      api.get('/teacher/question-bank', { availability: 'usable', scope: 'course', limit: 2000 }),
      api.get('/teacher/official-classroom'),
    ]) as any[]
    paper.value = detail
    title.value = detail.title
    releaseTitle.value = detail.title
    instructions.value = detail.instructions || ''
    items.value = (detail.questions || []).map((item: any) => ({ ...item }))
    bank.value = questionBank.items || []
    kpMap.value = new Map((questionBank.metadata?.knowledge_points || [])
      .map((item: any) => [item.kp_id, item.name]))
    classroom.value = official
    classroomId.value = official.classroom_id
    await loadStudents()
  } catch (error) {
    ElMessage.error(friendlyError(error, '试卷预览加载失败'))
  } finally { loading.value = false }
}

function move(index: number, offset: number) {
  const target = index + offset
  if (!editable.value || target < 0 || target >= items.value.length) return
  const next = [...items.value]
  ;[next[index], next[target]] = [next[target], next[index]]
  items.value = next
}
function remove(index: number) {
  if (!editable.value) return
  if (items.value.length <= 1) { ElMessage.warning('试卷至少保留一道题'); return }
  items.value.splice(index, 1)
}
function beginReplace(index: number) {
  replaceIndex.value = index
  replaceKeyword.value = ''
  replaceOpen.value = true
}
function replaceWith(question: any) {
  if (replaceIndex.value < 0) return
  const score = items.value[replaceIndex.value].score
  items.value[replaceIndex.value] = { ...question, score }
  replaceOpen.value = false
  ElMessage.success('题目已替换，点击“保存草稿”后写入数据库')
}

async function save(showMessage = true) {
  if (!editable.value) return paper.value
  saving.value = true
  try {
    const result: any = await api.put(`/teacher/paper-drafts/${paperId}`, {
      title: title.value, instructions: instructions.value,
      items: items.value.map((item) => ({
        question_id: item.question_id,
        question_version: item.question_version,
        score: Number(item.score || 0),
      })),
    })
    paper.value = result
    items.value = (result.questions || []).map((item: any) => ({ ...item }))
    if (showMessage) ElMessage.success('草稿已保存，刷新后仍可继续编辑')
    return result
  } catch (error) {
    ElMessage.error(friendlyError(error, '草稿保存失败'))
    throw error
  } finally { saving.value = false }
}

async function loadStudents() {
  if (!classroomId.value) { students.value = []; return }
  try {
    const result: any = await api.get('/teacher/students', { classroom_id: classroomId.value })
    students.value = result.items || []
    selectedStudents.value = []
    publishRequestKey.value = ''
  } catch (error) { ElMessage.error(friendlyError(error, '学生名单加载失败')) }
}
function studentLabel(student: any) {
  if (!student.online_receivable) return `${student.display_code}（仅历史档案，无登录账号）`
  const name = student.linked_display_name ? `${student.linked_display_name} · ` : ''
  return `${name}${student.linked_account} · 档案 ${student.display_code}`
}
function toggleWholeClass() {
  selectedStudents.value = wholeClassSelected.value
    ? [] : onlineStudents.value.map(student => student.student_id)
}
async function openPublish() {
  if (editable.value) {
    try { await save(false) } catch { return }
  }
  publishOpen.value = true
}
async function publish() {
  if (!classroomId.value || !selectedStudents.value.length) {
    ElMessage.warning('请选择课堂和接收学生')
    return
  }
  publishing.value = true
  try {
    publishRequestKey.value ||= `paper-builder:${paperId}:${crypto.randomUUID()}`
    const release: any = await api.post('/teacher/releases', {
      title: releaseTitle.value || title.value, paper_id: paperId,
      classroom_id: classroomId.value, student_ids: selectedStudents.value,
      instructions: instructions.value, due_at: dueAt.value,
      release_type: 'exam', idempotency_key: publishRequestKey.value,
    })
    publishOpen.value = false
    ElMessage.success(`已发布给 ${release.recipient_count} 名学生，试卷版本现已冻结`)
    publishRequestKey.value = ''
    paper.value = await api.get(`/teacher/paper-drafts/${paperId}`)
    items.value = (paper.value.questions || []).map((item: any) => ({ ...item }))
  } catch (error) { ElMessage.error(friendlyError(error, '发布失败')) }
  finally { publishing.value = false }
}

watch([releaseTitle, selectedStudents, dueAt], () => { publishRequestKey.value = '' }, { deep: true })
onMounted(load)
</script>

<template>
  <div v-loading="loading" class="preview-page">
    <header class="preview-head">
      <div>
        <el-button link @click="router.push('/teacher/questions')">← 返回题库</el-button>
        <h2>{{ editable ? '编辑试卷草稿' : '已发布固定版本' }}</h2>
        <p>{{ editable ? '发布后试卷内容不可修改。' : '本版本已发布。' }}</p>
      </div>
      <div class="head-actions"><el-button v-if="editable" :loading="saving" @click="save()">保存草稿</el-button><el-button type="primary" @click="openPublish">{{ editable ? '保存并发布' : '再次发布给学生' }}</el-button></div>
    </header>

    <section class="paper-settings">
      <label>试卷标题<el-input v-model="title" :disabled="!editable" maxlength="256" /></label>
      <label>试卷说明<el-input v-model="instructions" :disabled="!editable" type="textarea" :rows="2" /></label>
      <div class="paper-metrics"><div><b>{{ items.length }}</b><span>题目数量</span></div><div><b>{{ totalScore }}</b><span>试卷总分</span></div><div><b>{{ coverage.length }}</b><span>知识点覆盖</span></div></div>
      <div class="coverage-row"><span v-for="item in coverage" :key="item.name">{{ item.name }} {{ item.count }}题</span></div>
    </section>

    <section class="paper-list">
      <article v-for="(item, index) in items" :key="`${item.question_id}-${index}`" class="paper-item">
        <div class="position">{{ index + 1 }}</div>
        <div class="item-body">
          <div class="item-meta"><span>{{ questionTypeLabel(item.question_type_effective || item.question_type) }}</span><span>{{ kpMap.get(item.main_kp_id) || '知识点未标注' }}</span></div>
          <div class="item-stem"><MathText :text="stemOf(item)" /></div>
          <div v-if="item.options?.length" class="options"><div v-for="(option, optionIndex) in item.options" :key="optionIndex"><b>{{ optionKey(option, optionIndex) }}</b><MathText :text="optionText(option)" /></div></div>
          <details><summary>查看标准答案与解析</summary><div class="solution"><b>标准答案：{{ answerText(item) }}</b><MathText :text="item.explanation || '暂无解析'" /></div></details>
        </div>
        <aside>
          <label>分值<el-input-number v-model="item.score" :disabled="!editable" :min="0.5" :max="1000" :step="0.5" /></label>
          <div v-if="editable" class="order-actions"><el-button size="small" :disabled="index===0" @click="move(index,-1)">上移</el-button><el-button size="small" :disabled="index===items.length-1" @click="move(index,1)">下移</el-button></div>
          <div v-if="editable" class="order-actions"><el-button size="small" @click="beginReplace(index)">替换</el-button><el-button size="small" type="danger" plain @click="remove(index)">删除</el-button></div>
        </aside>
      </article>
    </section>

    <el-dialog v-model="replaceOpen" title="替换题目" width="min(760px, 94vw)" top="5vh">
      <el-input v-model="replaceKeyword" clearable placeholder="搜索题干或知识点" />
      <div class="replacement-list">
        <article v-for="question in replacementCandidates" :key="question.question_id" @click="replaceWith(question)">
          <div><b>{{ questionTypeLabel(question.question_type_effective || question.question_type) }} · {{ kpMap.get(question.main_kp_id) || '未标注' }}</b><p><MathText :text="stemOf(question)" /></p></div><el-button link type="primary">选择</el-button>
        </article>
        <el-empty v-if="!replacementCandidates.length" description="没有可用于替换且未重复的题目" />
      </div>
    </el-dialog>

    <el-dialog v-model="publishOpen" title="选择学生并发布" width="min(680px, 94vw)" :close-on-click-modal="false">
      <div class="publish-form">
        <label>发布标题<el-input v-model="releaseTitle" /></label>
        <label>正式班级<div class="class-readonly">{{ classroom?.name }} · {{ classroom?.term }} · 16人</div></label>
        <label>接收学生<div class="recipient-picker"><el-select v-model="selectedStudents" multiple filterable collapse-tags placeholder="选择 STU001～STU016"><el-option v-for="student in onlineStudents" :key="student.student_id" :label="studentLabel(student)" :value="student.student_id" /></el-select><el-button @click="toggleWholeClass">{{ wholeClassSelected ? '清空' : '选择全班16人' }}</el-button></div></label>
        <p v-if="paper?.assessment_classroom_id && paper.assessment_classroom_id !== classroomId" class="recipient-warning">这份旧试卷属于已归档的演示班固定版本，不能转发到正式班。请从题库复制题目生成新的正式试卷。</p>
        <label>截止时间（可选）<el-date-picker v-model="dueAt" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" placeholder="不设置则留空" /></label>
      </div>
      <template #footer><el-button @click="publishOpen=false">取消</el-button><el-button type="primary" :loading="publishing" :disabled="!onlineStudents.length || Boolean(paper?.assessment_classroom_id && paper.assessment_classroom_id !== classroomId)" @click="publish">确认发布</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.preview-page{display:grid;gap:18px;color:#203447}.preview-head{display:flex;justify-content:space-between;align-items:flex-end;gap:22px}.preview-head h2{margin:4px 0;font-size:28px}.preview-head p:last-child{margin:0;color:#6d7d89}.eyebrow{margin:8px 0 0;color:#277a74;font-size:14px;font-weight:800;letter-spacing:.13em}.head-actions{display:flex;gap:9px}.paper-settings{display:grid;grid-template-columns:1fr 1fr;gap:14px;background:#fff;border:1px solid #dfe7ec;border-radius:14px;padding:18px}.paper-settings>label,.publish-form label{display:grid;gap:7px;font-size:14px;font-weight:650}.paper-metrics{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.paper-metrics>div{background:#f3f7f8;border-radius:9px;padding:12px}.paper-metrics b,.paper-metrics span{display:block}.paper-metrics b{font-size:23px;color:#1f716b}.paper-metrics span{font-size:14px;color:#75848f;margin-top:3px}.coverage-row{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px}.coverage-row span,.item-meta span{font-size:14px;background:#edf3f5;color:#566d79;border-radius:5px;padding:4px 7px}.paper-list{display:grid;gap:12px}.paper-item{display:grid;grid-template-columns:44px 1fr 190px;gap:16px;background:#fff;border:1px solid #dfe7ec;border-radius:13px;padding:18px}.position{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:#e7f3f1;color:#21736d;font-weight:800}.item-meta{display:flex;flex-wrap:wrap;gap:6px}.item-stem{font-weight:650;line-height:1.75;margin:13px 0}.options{display:grid;gap:7px;color:#536774}.options>div{display:grid;grid-template-columns:24px 1fr;gap:7px}.options b{color:#2b7b75}.paper-item aside{display:grid;align-content:start;gap:10px;border-left:1px solid #e8edf0;padding-left:15px}.paper-item aside label{display:grid;gap:5px;font-size:14px}.paper-item aside :deep(.el-input-number){width:100%}.order-actions{display:grid;grid-template-columns:1fr 1fr;gap:6px}.order-actions .el-button+.el-button{margin-left:0}details{margin-top:12px}summary{cursor:pointer;color:#25756f;font-size:14px}.solution{display:grid;gap:9px;margin-top:9px;background:#eef7f6;border-radius:7px;padding:12px}.replacement-list{display:grid;gap:8px;max-height:58vh;overflow:auto;margin-top:12px}.replacement-list article{display:flex;align-items:center;justify-content:space-between;gap:15px;border:1px solid #e3e9ed;border-radius:9px;padding:12px;cursor:pointer}.replacement-list article:hover{border-color:#8dbcb7;background:#f8fbfb}.replacement-list p{margin:5px 0 0;line-height:1.55}.publish-form{display:grid;gap:14px}.publish-form p{margin:0;background:#eef7f6;color:#536f72;border-radius:8px;padding:11px;font-size:14px;line-height:1.65}
.publish-form p.recipient-warning{background:#fff6e6;color:#8a5d20}.publish-form p.recipient-ok{background:#edf8f5;color:#286d66}
.class-readonly{background:#f3f7f8;border:1px solid #dbe5e9;border-radius:8px;padding:10px 12px;color:#2a5558}.recipient-picker{display:grid;grid-template-columns:1fr auto;gap:8px}
@media(max-width:860px){.preview-head{align-items:stretch;flex-direction:column}.paper-settings{grid-template-columns:1fr}.paper-settings>*{grid-column:1!important}.paper-item{grid-template-columns:38px 1fr}.paper-item aside{grid-column:2;border-left:0;border-top:1px solid #e8edf0;padding:12px 0 0}}
@media(max-width:560px){.paper-item{grid-template-columns:1fr}.position,.paper-item aside{grid-column:1}.paper-metrics{grid-template-columns:1fr 1fr}.head-actions{width:100%}}
</style>
