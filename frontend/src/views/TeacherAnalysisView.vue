<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute } from 'vue-router'
import { api, runTeacherJob } from '../api/client'
import SingleAttemptWorkbench from '../components/SingleAttemptWorkbench.vue'
import MultiAttemptWorkbench from '../components/MultiAttemptWorkbench.vue'
import ClassAssessmentWorkbench from '../components/ClassAssessmentWorkbench.vue'
import { friendlyError, friendlyQuizCode } from '../utils/presentation'

type Scope = 'student_attempt' | 'student_history' | 'class_assessment'
const route = useRoute()
const requestedScope = String(route.query.scope || '')
const scope = ref<Scope>(['student_attempt', 'student_history', 'class_assessment'].includes(requestedScope)
  ? requestedScope as Scope : 'student_attempt')
const requestedStudentId = String(route.query.student_id || '')
const requestedVersionId = String(route.query.assessment_version_id || '')
const classroom = ref<any>(null)
const classroomId = ref('')
const students = ref<any[]>([])
const assessments = ref<any[]>([])
const studentId = ref('')
const assessmentVersionId = ref('')
const result = ref<any>(null)
const diagnosisMeta = ref<any>(null)
const diagnosisReady = ref(false)
const selectionError = ref('')
const loading = ref(false)
const selectionLoading = ref(false)
const runningAi = ref(false)
const intervention = ref<any>(null)
const contextLoading = ref(false)
const historyRangeMode = ref<'all' | 'date' | 'paper'>('all')
const historyDates = ref<[string, string] | null>(null)
const historyPaperId = ref('')
let selectionRequest = 0
function assessmentLabel(version: any, includeCount = false) {
  const code = friendlyQuizCode(version.quiz_code)
  return `${code ? `${code} · ` : ''}${version.assessment_title}${includeCount ? ` · ${version.item_count}题` : ''}`
}

const versions = computed(() => assessments.value.flatMap((assessment: any) =>
  (assessment.versions || []).map((version: any) => ({
    ...version,
    assessment_title: assessment.title,
    quiz_code: assessment.quiz_code,
  }))))
const diagnosisVersions = computed(() => {
  const rows = versions.value.filter((version: any) => Number(version.submitted_count || 0) > 0)
  return rows.length ? rows : versions.value
})
const selectedStudentLabel = computed(() => students.value.find((student: any) => student.student_id === studentId.value)?.display_code || '当前学生')
const selectedAssessmentLabel = computed(() => {
  const version = versions.value.find((item: any) => item.assessment_version_id === assessmentVersionId.value)
  return version ? String(version.assessment_title || '').trim() || assessmentLabel(version) : '当前试卷'
})
const historyParams = computed(() => ({
  student_id: studentId.value,
  classroom_id: classroomId.value,
  date_from: historyRangeMode.value === 'date' ? (historyDates.value?.[0] || '') : '',
  date_to: historyRangeMode.value === 'date' ? (historyDates.value?.[1] || '') : '',
  assessment_version_id: historyRangeMode.value === 'paper' ? historyPaperId.value : '',
}))
const historyRangeIncomplete = computed(() => scope.value === 'student_history'
  && historyRangeMode.value === 'date'
  && (!historyDates.value?.[0] || !historyDates.value?.[1]))

async function loadContext() {
  try {
    const official: any = await api.get('/teacher/official-classroom')
    classroom.value = official
    classroomId.value = official.classroom_id
    await loadClassroom()
  } catch (error) {
    selectionError.value = friendlyError(error, '课程与诊断对象加载失败')
  }
}

async function loadClassroom() {
  if (!classroomId.value) return
  contextLoading.value = true
  selectionRequest += 1
  result.value = null
  diagnosisMeta.value = null
  diagnosisReady.value = false
  intervention.value = null
  selectionError.value = ''
  try {
    const [studentRows, assessmentRows] = await Promise.all([
      api.get('/teacher/students', { classroom_id: classroomId.value }),
      api.get('/teacher/assessments', { classroom_id: classroomId.value }),
    ]) as any[]
    students.value = studentRows.items || []
    assessments.value = assessmentRows.items || []
    studentId.value = students.value.some((student: any) => student.student_id === requestedStudentId)
      ? requestedStudentId : (students.value[0]?.student_id || '')
    const preferredVersion = [...diagnosisVersions.value]
      .filter((version: any) => version.submitted_count && version.item_count > 1)
      .sort((a: any, b: any) => Number(b.submitted_count) - Number(a.submitted_count))[0]
    assessmentVersionId.value = diagnosisVersions.value.some(
      (version: any) => version.assessment_version_id === requestedVersionId)
      ? requestedVersionId
      : (preferredVersion?.assessment_version_id ||
        diagnosisVersions.value.find((version: any) => version.submitted_count)?.assessment_version_id ||
        diagnosisVersions.value.find((version: any) => version.scoring_status === 'graded'
          && version.submitted_count)?.assessment_version_id ||
        diagnosisVersions.value.find((version: any) => version.scoring_status === 'graded')
          ?.assessment_version_id ||
        diagnosisVersions.value[0]?.assessment_version_id || '')
  } catch (error) {
    selectionError.value = friendlyError(error, '诊断对象加载失败')
  } finally {
    contextLoading.value = false
  }
  if (scope.value === 'student_attempt') await loadStudentSelection()
  else if (scope.value === 'student_history') await loadHistorySelection()
  else await loadClassAssessmentSelection()
}

function clearSelectionResult() {
  selectionRequest += 1
  result.value = null
  diagnosisMeta.value = null
  diagnosisReady.value = false
  intervention.value = null
  selectionError.value = ''
}

async function loadStudentSelection() {
  if (!studentId.value || !assessmentVersionId.value) {
    clearSelectionResult()
    return
  }
  const request = ++selectionRequest
  result.value = null
  diagnosisMeta.value = null
  diagnosisReady.value = false
  intervention.value = null
  selectionError.value = ''
  selectionLoading.value = true
  const params = {
    student_id: studentId.value,
    assessment_version_id: assessmentVersionId.value,
  }
  const [baseline, latest] = await Promise.allSettled([
    api.get('/teacher/student-attempt', params),
    api.get('/teacher/diagnoses/latest', {
      scope_type: 'student_attempt',
      ...params,
    }),
  ])
  if (request !== selectionRequest) return
  try {
    if (baseline.status === 'rejected') throw baseline.reason
    const baselineResult: any = baseline.value
    if (latest.status === 'fulfilled'
      && (latest.value as any)?.result?.attempt_id === baselineResult?.attempt_id) {
      const saved: any = latest.value
      const baselineHistory = baselineResult?.retest_history || []
      const savedHistory = saved.result?.retest_history || []
      const sameRetestState = savedHistory.length === baselineHistory.length
        && savedHistory.at(-1)?.status === baselineHistory.at(-1)?.status
      result.value = {
        ...saved.result,
        ...baselineResult,
        ai_analysis: sameRetestState
          ? (saved.result?.ai_analysis || baselineResult?.ai_analysis)
          : baselineResult?.ai_analysis,
      }
      diagnosisMeta.value = {
        diagnosis_id: saved.diagnosis_id,
        created_at: saved.created_at,
        engine: saved.engine,
      }
      diagnosisReady.value = true
    } else {
      result.value = baselineResult
    }
  } catch (error) {
    selectionError.value = friendlyError(error, '当前学生或试卷没有可读取的答卷')
  } finally {
    if (request === selectionRequest) selectionLoading.value = false
  }
}

async function loadHistorySelection() {
  if (!studentId.value || !classroomId.value) {
    clearSelectionResult()
    return
  }
  if (historyRangeIncomplete.value) {
    clearSelectionResult()
    return
  }
  if (historyRangeMode.value === 'paper' && !historyPaperId.value) {
    historyPaperId.value = diagnosisVersions.value[0]?.assessment_version_id || ''
  }
  const request = ++selectionRequest
  result.value = null
  diagnosisMeta.value = null
  diagnosisReady.value = false
  intervention.value = null
  selectionError.value = ''
  selectionLoading.value = true
  const params = historyParams.value
  const [baseline, latest] = await Promise.allSettled([
    api.get('/teacher/student-history', params),
    api.get('/teacher/diagnoses/latest', { scope_type: 'student_history', ...params }),
  ])
  if (request !== selectionRequest) return
  try {
    if (baseline.status === 'rejected') throw baseline.reason
    const baselineResult: any = baseline.value
    if (latest.status === 'fulfilled'
      && (latest.value as any)?.skill_version === 'classroom-chapter-diagnosis-v5') {
      const saved: any = latest.value
      result.value = saved.result
      diagnosisMeta.value = {
        diagnosis_id: saved.diagnosis_id,
        created_at: saved.created_at,
        engine: saved.engine,
      }
      diagnosisReady.value = true
    } else {
      result.value = baselineResult
    }
  } catch (error) {
    selectionError.value = friendlyError(error, '当前学生没有可读取的多次考试记录')
  } finally {
    if (request === selectionRequest) selectionLoading.value = false
  }
}

async function loadClassAssessmentSelection() {
  if (!classroomId.value || !assessmentVersionId.value) {
    clearSelectionResult()
    return
  }
  const request = ++selectionRequest
  result.value = null
  diagnosisMeta.value = null
  diagnosisReady.value = false
  intervention.value = null
  selectionError.value = ''
  selectionLoading.value = true
  const params = {
    classroom_id: classroomId.value,
    assessment_version_id: assessmentVersionId.value,
  }
  const [baseline, latest] = await Promise.allSettled([
    api.get('/teacher/class-assessment', params),
    api.get('/teacher/diagnoses/latest', { scope_type: 'class_assessment', ...params }),
  ])
  if (request !== selectionRequest) return
  try {
    if (baseline.status === 'rejected') throw baseline.reason
    const baselineResult: any = baseline.value
    if (latest.status === 'fulfilled'
      && (latest.value as any)?.skill_version === 'classroom-chapter-diagnosis-v5'
      && (latest.value as any)?.result?.class_view_version === 2) {
      const saved: any = latest.value
      result.value = saved.result
      diagnosisMeta.value = {
        diagnosis_id: saved.diagnosis_id,
        created_at: saved.created_at,
        engine: saved.engine,
      }
      diagnosisReady.value = true
    } else {
      result.value = baselineResult
    }
  } catch (error) {
    selectionError.value = friendlyError(error, '当前试卷没有可读取的班级作答数据')
  } finally {
    if (request === selectionRequest) selectionLoading.value = false
  }
}

watch(studentId, () => {
  if (contextLoading.value) return
  if (scope.value === 'student_attempt') void loadStudentSelection()
  else if (scope.value === 'student_history') void loadHistorySelection()
  else clearSelectionResult()
})
watch(assessmentVersionId, async () => {
  // The history view has its own paper filter. Initialising the single-paper
  // selector must not cancel an in-flight history request.
  if (contextLoading.value || scope.value === 'student_history') return
  clearSelectionResult()
  try {
    if (scope.value === 'student_attempt') await loadStudentSelection()
    else if (scope.value === 'class_assessment') await loadClassAssessmentSelection()
  } catch (error) {
    selectionError.value = friendlyError(error, '固定试卷版本加载失败')
  }
})
watch(scope, async (value) => {
  clearSelectionResult()
  if (value === 'student_attempt') await loadStudentSelection()
  else if (value === 'student_history') await loadHistorySelection()
  else if (value === 'class_assessment') await loadClassAssessmentSelection()
})
watch([historyRangeMode, historyDates, historyPaperId], () => {
  if (!contextLoading.value && scope.value === 'student_history') void loadHistorySelection()
}, { deep: true })

async function analyze() {
  if (historyRangeIncomplete.value || loading.value) return
  loading.value = true
  selectionError.value = ''
  const request = ++selectionRequest
  const previousResult = result.value
  const previousReady = diagnosisReady.value
  try {
    if (scope.value === 'student_attempt') {
      const saved: any = await runTeacherJob('/teacher/diagnoses/jobs', {
        scope_type: scope.value,
        classroom_id: classroomId.value,
        student_id: studentId.value,
        assessment_version_id: assessmentVersionId.value,
        include_ai: true,
      })
      if (request !== selectionRequest) return
      result.value = saved.result
      diagnosisMeta.value = {
        diagnosis_id: saved.diagnosis_id,
        created_at: saved.created_at,
        engine: saved.engine,
      }
      diagnosisReady.value = true
      intervention.value = null
      ElMessage.success('诊断已更新')
    } else if (scope.value === 'student_history') {
      const saved: any = await runTeacherJob('/teacher/diagnoses/jobs', {
        scope_type: 'student_history',
        ...historyParams.value,
        include_ai: true,
      })
      if (request !== selectionRequest) return
      result.value = saved.result
      diagnosisMeta.value = {
        diagnosis_id: saved.diagnosis_id,
        created_at: saved.created_at,
        engine: saved.engine,
      }
      diagnosisReady.value = true
      intervention.value = null
      ElMessage.success('多卷分析已更新')
    } else {
      const saved: any = await runTeacherJob('/teacher/diagnoses/jobs', {
        scope_type: 'class_assessment',
        classroom_id: classroomId.value,
        assessment_version_id: assessmentVersionId.value,
        include_ai: true,
      })
      if (request !== selectionRequest) return
      result.value = saved.result
      diagnosisMeta.value = {
        diagnosis_id: saved.diagnosis_id,
        created_at: saved.created_at,
        engine: saved.engine,
      }
      diagnosisReady.value = true
      intervention.value = null
      ElMessage.success('班级分析已更新')
    }
  } catch (error) {
    if (request !== selectionRequest) return
    result.value = previousResult
    diagnosisReady.value = previousReady
    selectionError.value = friendlyError(error, '诊断运行失败')
    ElMessage.error(selectionError.value)
  } finally {
    if (request === selectionRequest) loading.value = false
  }
}

async function runIntervention() {
  if (!['student_attempt', 'student_history', 'class_assessment'].includes(scope.value)
    || !result.value?.retest_plan?.ready) return
  runningAi.value = true
  const request = selectionRequest
  try {
    const value: any = await runTeacherJob('/teacher/workflows/intervention/jobs', {
      student_id: scope.value === 'class_assessment' ? '' : studentId.value,
      assessment_version_id: scope.value === 'student_history' ? historyParams.value.assessment_version_id : assessmentVersionId.value,
      classroom_id: scope.value === 'student_attempt' ? '' : classroomId.value,
      scope_type: scope.value,
      attempt_id: result.value?.attempt_id || result.value?.retest_plan?.anchor_attempt_id || '',
      count: result.value.retest_plan.item_count || 5,
      date_from: scope.value === 'student_history' ? historyParams.value.date_from : '',
      date_to: scope.value === 'student_history' ? historyParams.value.date_to : '',
    })
    if (request !== selectionRequest) {
      ElMessage.info('复测任务已处理完成，请返回原诊断对象查看结果')
      return
    }
    intervention.value = value
    result.value = value?.outputs?.diagnosis?.result || result.value
    diagnosisReady.value = true
    const diagnosisId = value?.outputs?.diagnosis?.extra?.diagnosis_id
    if (diagnosisId) {
      diagnosisMeta.value = {
        diagnosis_id: diagnosisId,
        created_at: new Date().toISOString(),
        engine: value?.outputs?.diagnosis?.engine,
      }
    }
    if (value?.outputs?.retest?.status === 'published') {
      ElMessage.success('复测题已通过自动校验并发布给学生')
      if (value.outputs.retest.remaining_kp_ids?.length) {
        ElMessage.info(`本轮先覆盖 ${value.outputs.retest.target_kp_ids.length} 个薄弱点，其余 ${value.outputs.retest.remaining_kp_ids.length} 个仍保留在诊断中`)
      }
      if (scope.value === 'student_attempt') await loadStudentSelection()
    } else {
      ElMessage.warning(
        value?.errors?.join('；') ||
        '没有生成可通过自动校验的复测题，本次未发布')
    }
  } catch (error) {
    ElMessage.error(friendlyError(error, '复测生成与发布失败'))
  } finally {
    runningAi.value = false
  }
}

function openAttemptDetail(versionId: string) {
  if (!versionId) return
  assessmentVersionId.value = versionId
  scope.value = 'student_attempt'
}

onMounted(loadContext)
</script>

<template>
  <div class="analysis-page">
    <div class="diagnosis-controls">
    <section class="scope-tabs" aria-label="诊断模式">
      <button type="button" :class="{ active: scope === 'student_attempt' }" :aria-pressed="scope === 'student_attempt'"
        @click="scope = 'student_attempt'">
        <b>单人单卷</b>
      </button>
      <button type="button" :class="{ active: scope === 'student_history' }" :aria-pressed="scope === 'student_history'"
        @click="scope = 'student_history'">
        <b>单人多卷</b>
      </button>
      <button type="button" :class="{ active: scope === 'class_assessment' }" :aria-pressed="scope === 'class_assessment'"
        @click="scope = 'class_assessment'">
        <b>多人单卷</b>
      </button>
    </section>

    <section class="filters-panel">
      <label v-if="scope === 'student_attempt' || scope === 'student_history'" class="filter-item">
        <span>学生</span>
        <el-select v-model="studentId" filterable placeholder="选择学生">
          <el-option v-for="student in students" :key="student.student_id"
            :label="student.display_code" :value="student.student_id" />
        </el-select>
      </label>
      <label v-if="scope !== 'student_history'" class="filter-item paper-filter">
        <span>试卷</span>
        <el-select v-model="assessmentVersionId" filterable placeholder="选择固定试卷版本">
          <el-option v-for="version in diagnosisVersions" :key="version.assessment_version_id"
            :label="assessmentLabel(version, true)"
            :value="version.assessment_version_id" />
        </el-select>
      </label>
      <label v-if="scope === 'student_history'" class="filter-item history-range-filter">
        <span>统计范围</span>
        <el-select v-model="historyRangeMode" placeholder="选择统计范围">
          <el-option label="全部历史考试" value="all" />
          <el-option label="按日期范围" value="date" />
          <el-option label="按试卷筛选" value="paper" />
        </el-select>
      </label>
      <label v-if="scope === 'student_history' && historyRangeMode === 'date'" class="filter-item history-value-filter">
        <span>考试日期</span>
        <el-date-picker v-model="historyDates" type="daterange" value-format="YYYY-MM-DD"
          range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" />
      </label>
      <label v-if="scope === 'student_history' && historyRangeMode === 'paper'" class="filter-item history-value-filter">
        <span>指定试卷</span>
        <el-select v-model="historyPaperId" filterable placeholder="选择试卷">
          <el-option v-for="version in diagnosisVersions" :key="version.assessment_version_id"
            :label="assessmentLabel(version)"
            :value="version.assessment_version_id" />
        </el-select>
      </label>
      <el-button type="primary" class="run-button" :loading="loading"
        :disabled="selectionLoading || !classroomId || historyRangeIncomplete" @click="analyze">运行诊断</el-button>
    </section>
    </div>

    <div v-if="selectionLoading" class="loading-panel">
      <el-skeleton :rows="6" animated />
    </div>
    <el-result v-else-if="selectionError && !result" icon="warning"
      title="当前对象暂时不能展示诊断" :sub-title="selectionError">
      <template #extra>
        <el-button @click="scope === 'student_attempt' ? loadStudentSelection() : (scope === 'student_history' ? loadHistorySelection() : loadClassAssessmentSelection())">重新加载</el-button>
      </template>
    </el-result>
    <SingleAttemptWorkbench v-else-if="scope === 'student_attempt' && result"
      :result="result" :diagnosis-ready="diagnosisReady"
      :diagnosis-meta="diagnosisMeta" :intervention="intervention"
      :student-label="selectedStudentLabel" :assessment-label="selectedAssessmentLabel"
      :loading="loading" :running-ai="runningAi"
      @run="analyze" @retest="runIntervention" />
    <div v-else-if="scope === 'student_attempt'" class="empty-selection">
      请选择学生和试卷。
    </div>
    <MultiAttemptWorkbench v-else-if="scope === 'student_history' && result"
      :result="result" :diagnosis-ready="diagnosisReady"
      :diagnosis-meta="diagnosisMeta" :intervention="intervention"
      :loading="loading" :running-ai="runningAi"
      @retest="runIntervention" @open-attempt="openAttemptDetail" />
    <div v-else-if="scope === 'student_history'" class="empty-selection">
      {{ historyRangeIncomplete ? '请选择开始和结束日期，再查看该范围的考试记录。' : '当前学生没有可展示的考试记录。' }}
    </div>
    <ClassAssessmentWorkbench v-else-if="scope === 'class_assessment' && result"
      :result="result" :diagnosis-ready="diagnosisReady"
      :intervention="intervention" :running-ai="runningAi"
      @retest="runIntervention" />
    <div v-else-if="scope === 'class_assessment'" class="empty-selection">
      当前试卷暂无班级作答。
    </div>
  </div>
</template>

<style scoped src="../styles/teacherAnalysis.css"></style>
