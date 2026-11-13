<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Calendar, Clock, Document, Plus, Promotion, Upload, User } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { friendlyError } from '../utils/presentation'

const loading = ref(true)
const classroom = ref<any>(null)
const classroomId = ref('')
const overview = ref<any>(null)
const students = ref<any[]>([])
const papers = ref<any[]>([])
const releases = ref<any[]>([])
const selectedStudents = ref<string[]>([])
const paperId = ref('')
const publishing = ref(false)
const publishDialogOpen = ref(false)
const importing = ref(false)
const importInput = ref<HTMLInputElement | null>(null)
const selectedRelease = ref<any | null>(null)
const showAllReleases = ref(false)
const router = useRouter()

const counts = computed(() => overview.value?.counts || {})
const onlineStudents = computed(() => students.value.filter(student => student.online_receivable))
const wholeClassSelected = computed(() => onlineStudents.value.length > 0
  && selectedStudents.value.length === onlineStudents.value.length)
const publishedReleases = computed(() => releases.value.filter(release => release.status === 'published'))
const visibleReleases = computed(() => showAllReleases.value
  ? publishedReleases.value : publishedReleases.value.slice(0, 5))
const currentClassLabel = computed(() => {
  const term = String(classroom.value?.term || '').trim()
  let name = String(classroom.value?.name || '机器学习').trim()
  if (term && name.startsWith(term)) name = name.slice(term.length).trim()
  return `${term ? `${term} ` : ''}${name || '机器学习'} · ${counts.value.students ?? 0}名学生`
})
const selectedPaper = computed(() => papers.value.find(paper => paper.paper_id === paperId.value))
const metricCards = computed(() => [
  { label: '学生人数', value: counts.value.students ?? 0, icon: User, tone: 'blue' },
  { label: '历史小测', value: counts.value.historical_assessments ?? 0, icon: Document, tone: 'violet' },
  { label: '已发布任务', value: counts.value.online_releases ?? 0, icon: Promotion, tone: 'green' },
  { label: '待提交答卷', value: counts.value.pending_online_tasks ?? 0, icon: Clock, tone: 'amber' },
])

async function loadClassroom() {
  if (!classroomId.value) return
  const [summary, studentRows] = await Promise.all([
    api.get('/teacher/overview', { classroom_id: classroomId.value }),
    api.get('/teacher/students', { classroom_id: classroomId.value }),
  ]) as any[]
  overview.value = summary
  students.value = studentRows.items || []
}

async function load() {
  loading.value = true
  try {
    const [official, paperRows] = await Promise.all([
      api.get('/teacher/official-classroom'),
      api.get('/papers', { limit: 300 }),
    ]) as any[]
    classroom.value = official
    classroomId.value = official.classroom_id
    papers.value = (paperRows.items || []).filter((paper: any) => paper.answerable
      && (!paper.assessment_classroom_id || paper.assessment_classroom_id === classroomId.value))
    await Promise.all([loadClassroom(), loadReleases()])
  } catch (error) {
    ElMessage.error(friendlyError(error, '教学总览加载失败'))
  } finally {
    loading.value = false
  }
}

async function loadReleases() {
  if (!classroomId.value) return
  releases.value = ((await api.get('/teacher/releases', {
    classroom_id: classroomId.value,
  })) as any).items || []
}

function openQuickPublish() {
  paperId.value = ''
  selectedStudents.value = []
  publishDialogOpen.value = true
}

function toggleWholeClass() {
  selectedStudents.value = wholeClassSelected.value
    ? [] : onlineStudents.value.map(student => student.student_id)
}

async function publish() {
  if (!paperId.value || !selectedStudents.value.length) {
    ElMessage.warning('请选择试卷和接收学生')
    return
  }
  publishing.value = true
  try {
    const result: any = await api.post('/teacher/releases', {
      title: selectedPaper.value?.title || '课堂测验',
      paper_id: paperId.value,
      student_ids: selectedStudents.value,
      classroom_id: classroomId.value,
      release_type: 'exam',
      idempotency_key: `teacher-dashboard:${crypto.randomUUID()}`,
    })
    ElMessage.success(`已发布给${result.recipient_count}名学生`)
    publishDialogOpen.value = false
    await Promise.all([loadClassroom(), loadReleases()])
  } catch (error) {
    ElMessage.error(friendlyError(error, '发布失败'))
  } finally {
    publishing.value = false
  }
}

function chooseFile() { importInput.value?.click() }

async function importWorkbook(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  importing.value = true
  try {
    const form = new FormData()
    form.append('file', file)
    form.append('course_key', 'ml-2025-fall')
    form.append('classroom_name', '2025秋机器学习')
    form.append('term', '2025秋')
    const result: any = await api.upload('/teacher/classroom-import', form)
    ElMessage.success(`导入完成：${result.students}名学生，${result.attempts}份有效答卷`)
    await load()
  } catch (error) {
    ElMessage.error(friendlyError(error, '课堂数据导入失败'))
  } finally {
    importing.value = false
  }
}

function recipientSummary(release: any) {
  const total = Number(release.recipient_count || 0)
  if (total === onlineStudents.value.length && total > 1) return `全班 ${total} 人`
  return `指定学生 ${total} 人`
}

function formatTime(value: string) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.replace('T', ' ').slice(0, 16)
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(date).replaceAll('/', '-')
}

function recipientLabel(item: any) {
  return item.student_code || item.linked_display_name || '学生'
}

function recipientStatus(item: any) {
  if (item.status === 'submitted') return item.grading_status === 'graded' ? '已提交' : '已提交·待评分'
  if (item.status === 'opened') return '作答中'
  if (item.status === 'expired') return '已失效'
  return '待完成'
}

function recipientScore(item: any) {
  if (item.status !== 'submitted') return '—'
  if (item.grading_status !== 'graded') return '待评分'
  return `${item.score ?? 0} / ${item.max_score ?? '—'}`
}

async function inspectAttempt(attemptId: string) {
  if (attemptId) await router.push(`/exam/${attemptId}`)
}

async function openSingleDiagnosis(row: any) {
  const release = selectedRelease.value
  selectedRelease.value = null
  await router.push({
    path: '/teacher/analysis',
    query: {
      scope: 'student_attempt',
      student_id: row.student_id,
      assessment_version_id: release?.assessment_version_id || '',
    },
  })
}

async function openClassDiagnosis(release: any) {
  selectedRelease.value = null
  await router.push({
    path: '/teacher/analysis',
    query: {
      scope: 'class_assessment',
      assessment_version_id: release.assessment_version_id,
    },
  })
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="teacher-dashboard">
    <section class="dashboard-head">
      <div>
        <span class="page-kicker">教师端</span>
        <h2>教学总览</h2>
        <p>{{ currentClassLabel }}</p>
      </div>
      <div class="head-actions">
        <el-button type="primary" size="large" @click="router.push('/teacher/questions?compose=1')">
          <el-icon><Plus /></el-icon>新建试卷
        </el-button>
        <el-button size="large" :loading="importing" @click="chooseFile">
          <el-icon><Upload /></el-icon>导入课堂数据
        </el-button>
        <input ref="importInput" hidden type="file" accept=".xlsx" @change="importWorkbook" />
      </div>
    </section>

    <section class="metric-grid" aria-label="班级统计">
      <article v-for="card in metricCards" :key="card.label" :class="`tone-${card.tone}`">
        <span class="metric-icon"><el-icon><component :is="card.icon" /></el-icon></span>
        <div><span>{{ card.label }}</span><b>{{ card.value }}</b></div>
      </article>
    </section>

    <section class="release-panel">
      <header class="panel-head">
        <div><el-icon><Calendar /></el-icon><h3>最近发布</h3></div>
        <div class="panel-actions">
          <el-button plain @click="openQuickPublish">发布已有试卷</el-button>
          <el-button v-if="publishedReleases.length > 5" link type="primary"
            @click="showAllReleases = !showAllReleases">
            {{ showAllReleases ? '收起' : '查看全部' }}
          </el-button>
        </div>
      </header>

      <div v-if="visibleReleases.length" class="release-table">
        <div class="release-table-head">
          <span>试卷标题</span><span>接收对象</span><span>提交进度</span><span>发布时间</span><span>操作</span>
        </div>
        <article v-for="release in visibleReleases" :key="release.release_id" class="release-row">
          <div class="release-title"><b>{{ release.title }}</b><small>{{ release.paper_title }}</small></div>
          <span class="release-audience">{{ recipientSummary(release) }}</span>
          <div class="release-progress">
            <el-progress :percentage="release.recipient_count
              ? Math.round(release.submitted_count / release.recipient_count * 100) : 0"
              :show-text="false" :stroke-width="7" />
            <b>{{ release.submitted_count }}/{{ release.recipient_count }} 已提交</b>
          </div>
          <time class="release-time">{{ formatTime(release.published_at) }}</time>
          <el-button class="feedback-button" plain type="primary" @click="selectedRelease = release">查看反馈</el-button>
        </article>
      </div>
      <el-empty v-else :image-size="72" description="尚未发布试卷" />
    </section>

    <el-dialog v-model="publishDialogOpen" title="发布已有试卷" width="min(520px, 92vw)" destroy-on-close>
      <el-form label-position="top" class="quick-publish-form">
        <el-form-item label="选择试卷">
          <el-select v-model="paperId" filterable placeholder="选择已保存试卷" style="width:100%">
            <el-option v-for="paper in papers" :key="paper.paper_id"
              :label="`${paper.title} · ${paper.real_item_count}题`" :value="paper.paper_id" />
          </el-select>
        </el-form-item>
        <el-form-item label="接收学生">
          <div class="recipient-picker">
            <el-select v-model="selectedStudents" multiple filterable collapse-tags
              :max-collapse-tags="2" placeholder="选择学生" style="width:100%">
              <el-option v-for="student in onlineStudents" :key="student.student_id"
                :label="student.display_code" :value="student.student_id" />
            </el-select>
            <el-button @click="toggleWholeClass">{{ wholeClassSelected ? '清空' : '选择全班' }}</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="publishDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="publishing" @click="publish">确认发布</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="selectedRelease" title="发布反馈" size="min(760px, 94vw)">
      <template v-if="selectedRelease">
        <div class="drawer-head">
          <div><h3>{{ selectedRelease.title }}</h3><p>{{ formatTime(selectedRelease.published_at) }} · {{ recipientSummary(selectedRelease) }}</p></div>
          <el-button v-if="selectedRelease.submitted_count" type="primary" plain
            @click="openClassDiagnosis(selectedRelease)">查看班级诊断</el-button>
        </div>
        <div class="feedback-summary">
          <article><span>接收人数</span><b>{{ selectedRelease.recipient_count }}</b></article>
          <article><span>已提交</span><b>{{ selectedRelease.submitted_count }}</b></article>
          <article><span>未提交</span><b>{{ selectedRelease.recipient_count - selectedRelease.submitted_count }}</b></article>
        </div>
        <el-table :data="selectedRelease.recipients" stripe class="feedback-table">
          <el-table-column label="学生" min-width="100">
            <template #default="{ row }"><b>{{ recipientLabel(row) }}</b></template>
          </el-table-column>
          <el-table-column label="提交状态" min-width="110">
            <template #default="{ row }"><span :class="['status-text', row.status]">{{ recipientStatus(row) }}</span></template>
          </el-table-column>
          <el-table-column label="成绩" min-width="100">
            <template #default="{ row }">{{ recipientScore(row) }}</template>
          </el-table-column>
          <el-table-column label="操作" min-width="170" align="right">
            <template #default="{ row }">
              <template v-if="row.attempt_id">
                <el-button link type="primary" @click="inspectAttempt(row.attempt_id)">查看答卷</el-button>
                <el-button v-if="row.status === 'submitted'" link type="primary" @click="openSingleDiagnosis(row)">单卷诊断</el-button>
              </template>
              <span v-else>—</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.teacher-dashboard{display:grid;gap:24px;color:#17243d}.dashboard-head{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:2px 2px 4px}.page-kicker{color:#315cf0;font-size:12px;font-weight:800}.dashboard-head h2{margin:5px 0 4px;font-size:30px;line-height:1.15}.dashboard-head p{margin:0;color:#65758d;font-size:15px;font-weight:600}.head-actions{display:flex;gap:10px}.head-actions .el-button{min-width:132px;font-weight:700}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.metric-grid article{display:flex;min-height:104px;align-items:center;gap:18px;padding:18px 22px;border:1px solid #e4eaf4;border-radius:15px;background:#fff}.metric-icon{display:grid;flex:none;width:52px;height:52px;place-items:center;border-radius:14px;font-size:27px}.metric-grid article>div>span{display:block;color:#44536b;font-size:15px;font-weight:700}.metric-grid b{display:block;margin-top:5px;color:#17243d;font-size:30px;line-height:1}.tone-blue{background:linear-gradient(135deg,#fff,#f3f8ff)!important}.tone-blue .metric-icon{background:#e7f1ff;color:#2874ed}.tone-violet{background:linear-gradient(135deg,#fff,#f6f3ff)!important}.tone-violet .metric-icon{background:#eee9ff;color:#6257e8}.tone-green{background:linear-gradient(135deg,#fff,#eefaf6)!important}.tone-green .metric-icon{background:#ddf7ed;color:#20a67c}.tone-amber{background:linear-gradient(135deg,#fff,#fff8eb)!important}.tone-amber .metric-icon{background:#fff0cc;color:#e69718}.release-panel{overflow:hidden;border:1px solid #e1e8f2;border-radius:16px;background:#fff}.panel-head{display:flex;min-height:68px;align-items:center;justify-content:space-between;gap:16px;padding:0 22px;border-bottom:1px solid #e9eef6}.panel-head>div:first-child{display:flex;align-items:center;gap:10px}.panel-head .el-icon{color:#315cf0;font-size:21px}.panel-head h3{margin:0;font-size:18px}.panel-actions{display:flex;align-items:center;gap:10px}.release-table{padding:0 20px 10px}.release-table-head,.release-row{display:grid;grid-template-columns:minmax(250px,1.5fr) 145px minmax(190px,1fr) 155px 105px;align-items:center;column-gap:18px}.release-table-head{min-height:48px;padding:0 12px;border-radius:9px;background:#f5f7fc;color:#596981;font-size:13px;font-weight:700}.release-row{min-height:72px;padding:8px 12px;border-bottom:1px solid #edf1f6}.release-row:last-child{border-bottom:0}.release-title{min-width:0}.release-title b,.release-title small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.release-title b{font-size:14.5px}.release-title small{margin-top:5px;color:#8996a9;font-size:12px}.release-audience,.release-time{color:#51617a;font-size:13px}.release-progress{display:grid;grid-template-columns:minmax(90px,1fr) auto;align-items:center;gap:12px}.release-progress b{white-space:nowrap;font-size:12px}.feedback-button{justify-self:end}.recipient-picker{display:grid;grid-template-columns:1fr auto;gap:8px;width:100%}.drawer-head{display:flex;align-items:center;justify-content:space-between;gap:16px}.drawer-head h3{margin:0;font-size:21px}.drawer-head p{margin:6px 0 0;color:#75849a;font-size:13px}.feedback-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:22px 0}.feedback-summary article{padding:15px 17px;border:1px solid #e4eaf4;border-radius:11px;background:#f8faff}.feedback-summary span,.feedback-summary b{display:block}.feedback-summary span{color:#75849a;font-size:12px}.feedback-summary b{margin-top:5px;font-size:23px}.status-text{font-weight:650}.status-text.submitted{color:#17855f}.status-text.opened{color:#315cf0}.status-text.expired{color:#9b6a1d}
@media(max-width:1180px){.metric-grid article{padding:16px;gap:12px}.metric-icon{width:45px;height:45px}.release-table-head,.release-row{grid-template-columns:minmax(210px,1.5fr) 120px minmax(165px,1fr) 100px}.release-table-head>span:nth-child(4),.release-time{display:none}}
@media(max-width:900px){.dashboard-head{align-items:flex-start;flex-direction:column}.metric-grid{grid-template-columns:repeat(2,1fr)}.release-table-head{display:none}.release-row{grid-template-columns:1fr auto;gap:10px;padding:15px 8px}.release-audience{grid-column:1}.release-progress{grid-column:1}.feedback-button{grid-column:2;grid-row:1/4}.head-actions{width:100%}.head-actions .el-button{flex:1}}
@media(max-width:560px){.dashboard-head h2{font-size:26px}.head-actions{display:grid;grid-template-columns:1fr}.metric-grid{gap:10px}.metric-grid article{min-height:92px}.metric-icon{display:none}.metric-grid b{font-size:27px}.panel-head{align-items:flex-start;flex-direction:column;padding:15px}.panel-actions{width:100%;justify-content:space-between}.release-table{padding:0 12px 8px}.feedback-summary{gap:8px}.feedback-summary article{padding:12px}.feedback-table{font-size:12px}}
</style>
