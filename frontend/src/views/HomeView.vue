<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Calendar, Clock, DataAnalysis, RefreshRight } from '@element-plus/icons-vue'
import { api } from '../api/client'
import EmptyStatePanel from '../components/EmptyStatePanel.vue'
import MathText from '../components/MathText.vue'
import PageHeader from '../components/PageHeader.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { formatDateTime, kindLabel, pickNextAction } from '../utils/presentation'

const router = useRouter()
const data = ref<any>(null)
const attempts = ref<any[]>([])
const daily = ref<any>(null)
const dailyError = ref('')
const dailyLoading = ref(true)
const submitting = ref(false)
const refreshing = ref(false)
const answers = ref<Record<string, any>>({})
const loading = ref(true)
const error = ref('')

function initAnswers() {
  const next: Record<string, any> = {}
  for (const item of daily.value?.items || []) {
    const t = item.question_type
    if (t === 'single_choice') next[item.question_id] = { key: '' }
    else if (t === 'multi_choice') next[item.question_id] = { keys: [] }
    else if (t === 'true_false') next[item.question_id] = { value: null }
    else if (t === 'numeric') next[item.question_id] = { value: '', unit: '' }
    else next[item.question_id] = { text: '' }
  }
  answers.value = next
}

async function loadDaily() {
  dailyLoading.value = true
  dailyError.value = ''
  try {
    daily.value = await api.get('/home/daily-three')
    initAnswers()
  } catch (reason: any) {
    dailyError.value = reason?.message || '暂时无法读取每日 3 题'
  } finally {
    dailyLoading.value = false
  }
}

async function refreshDaily() {
  refreshing.value = true
  try {
    daily.value = await api.post('/home/daily-three/refresh', {})
    initAnswers()
    ElMessage.success('已从题库重新随机抽了一组')
  } catch (reason: any) {
    ElMessage.error(reason?.message || '换题失败，请稍后重试')
    await loadDaily()
  } finally {
    refreshing.value = false
  }
}

async function submitDaily() {
  if (!daily.value?.set_id) return
  submitting.value = true
  try {
    const out: any = await api.post('/home/daily-three/submit', {
      set_id: daily.value.set_id,
      answers: answers.value,
    })
    daily.value = { ...daily.value, submitted: true, result: out,
                    last_score: out.score, max_score: out.max_score,
                    completed_count: 1 }
    ElMessage.success(`交卷完成：${out.score} / ${out.max_score} 分`)
  } catch (reason: any) {
    ElMessage.error(reason?.message || '提交失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

const isSubmitted = computed(() => Boolean(daily.value?.submitted))
const dailyResult = computed(() => (isSubmitted.value ? (daily.value?.result || {}) : {}))
const resultByQid = computed(() => {
  const map: Record<string, any> = {}
  for (const row of dailyResult.value.results || []) map[row.question_id] = row
  return map
})

function verdict(row: any) {
  if (!row) return { label: '待评分', tone: 'wait' }
  if (row.correct === true) return { label: '答对', tone: 'ok' }
  if (row.correct === false) return { label: '答错', tone: 'bad' }
  return { label: '待评分', tone: 'wait' }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [overview, attemptList]: any[] = await Promise.all([
      api.get('/home/overview', { sort: 'weak' }),
      api.get('/me/attempts'),
    ])
    data.value = overview
    attempts.value = attemptList.items || []
  } catch (reason: any) {
    error.value = reason?.message || '暂时无法读取学习数据'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  loadDaily()
})

const today = new Date().toLocaleDateString('sv-SE')
const tasks = computed(() => (data.value?.tasks || []).filter(
  (task: any) => !['done', 'completed', 'complete'].includes(task.status),
))
const dueRetests = computed(() => tasks.value.filter(
  (task: any) => task.kind === 'retest' && (!task.due_date || task.due_date <= today),
))
const todayPlans = computed(() => tasks.value.filter((task: any) => task.kind !== 'retest'))
const activeAttempt = computed(() => attempts.value.find((attempt: any) => attempt.status !== 'submitted'))
const focusItems = computed(() => data.value?.focus?.items || [])
const weakItems = computed(() => {
  const evidenced = focusItems.value.filter((item: any) =>
    item.wrong_count > 0 || item.evidence_count > 0 || !['阶段稳定', '初步通过'].includes(item.state_label),
  )
  return (evidenced.length ? evidenced : focusItems.value).slice(0, 3)
})
const hasQuestions = computed(() => (data.value?.focus?.totals?.tagged_original_questions || 0) > 0)

const nextAction = computed(() => pickNextAction({
  activeAttempt: activeAttempt.value,
  dueRetests: dueRetests.value,
  todayPlans: todayPlans.value,
  weakStates: weakItems.value.map((item: any) => ({
    kp_id: item.kp_id,
    kp_name: item.name,
  })),
  hasMaterials: hasQuestions.value,
}))

const groupedTasks = computed(() => {
  const groups = new Map<string, any>()
  for (const task of tasks.value) {
    const key = `${task.kind || 'task'}:${task.kp_id || task.title}`
    const current = groups.get(key)
    if (current) {
      current.count += 1
      current.minutes = Math.max(current.minutes, Number(task.minutes || 0))
      if (task.due_date && (!current.due_date || task.due_date < current.due_date)) current.due_date = task.due_date
    } else {
      groups.set(key, { ...task, count: 1, minutes: Number(task.minutes || 0) })
    }
  }
  return Array.from(groups.values()).slice(0, 4)
})

const recent = computed(() => (data.value?.recent || []).slice(0, 5))

function go(to: string) {
  router.push(to)
}

</script>

<template>
  <div class="home-page">
    <PageHeader title="今天，从最重要的一步开始" description="按作答与错题整理今日重点。" />

    <div v-if="error" class="feedback-banner feedback-banner--danger" role="alert">
      <span>{{ error }}</span>
      <el-button link @click="load">重新加载</el-button>
    </div>

    <div v-if="loading" class="surface-card loading-surface" aria-live="polite">
      <el-skeleton :rows="6" animated />
      <span class="sr-only">正在整理今日学习重点</span>
    </div>

    <section v-if="data" class="next-action-card" aria-labelledby="next-action-title">
      <div class="next-action-icon" aria-hidden="true"><el-icon><DataAnalysis /></el-icon></div>
      <div class="next-action-copy">
        <span class="next-action-eyebrow">{{ nextAction.eyebrow }}</span>
        <h3 id="next-action-title">{{ nextAction.title }}</h3>
        <p>{{ nextAction.description }}</p>
      </div>
      <el-button type="primary" size="large" @click="go(nextAction.to)">
        {{ nextAction.actionLabel }}<el-icon class="el-icon--right"><ArrowRight /></el-icon>
      </el-button>
    </section>

    <div v-if="data" class="home-grid">
      <section class="surface-card home-daily" aria-labelledby="daily-title">
        <div class="section-heading">
          <div>
            <span class="section-kicker">每日 3 题</span>
            <h3 id="daily-title">今天练这三道</h3>
          </div>
          <div class="daily-head-actions">
            <span v-if="daily" class="daily-date">{{ daily.date }}</span>
            <el-button v-if="!isSubmitted && daily?.items?.length" text :loading="refreshing"
              @click="refreshDaily">换一组</el-button>
          </div>
        </div>

        <div v-if="dailyLoading" class="daily-loading" aria-live="polite">
          <el-skeleton :rows="5" animated />
          <span class="sr-only">正在整理今日 3 题</span>
        </div>

        <EmptyStatePanel v-else-if="dailyError || !daily?.items?.length"
          :title="dailyError ? '每日 3 题暂时不可用' : '题库还没有可用的题目'"
          :description="dailyError || '导入课件或试卷后自动生成。'"
          action-label="重新加载" @action="loadDaily" />

        <template v-else>
          <div v-if="isSubmitted" class="daily-score-bar">
            <div class="daily-score-number">
              <span>本次得分</span>
              <strong>{{ dailyResult.score }} <small>/ {{ dailyResult.max_score }}</small></strong>
            </div>
            <p>已就地交卷，正确答案与解析就在下面；作答记录同时进入错题本与学情诊断。</p>
            <el-button size="small" :loading="refreshing" @click="refreshDaily">再来一组</el-button>
          </div>

          <div class="daily-list">
            <article v-for="(item, index) in daily.items" :key="item.question_id" class="daily-item">
              <div class="daily-item-head">
                <span class="daily-index">{{ index + 1 }}</span>
                <span class="daily-slot">{{ item.slot_label }}</span>
                <span class="daily-type">{{ item.type_label }}</span>
                <span class="daily-kp">{{ item.kp_name || '课内考点' }}</span>
                <span v-if="isSubmitted && resultByQid[item.question_id]" class="daily-verdict"
                  :class="`is-${verdict(resultByQid[item.question_id]).tone}`">
                  {{ verdict(resultByQid[item.question_id]).label }} ·
                  {{ resultByQid[item.question_id].awarded_score }}/{{ resultByQid[item.question_id].max_score }} 分
                </span>
              </div>

              <div class="daily-stem-box">
                <span v-if="item.prompt_prefix" class="daily-prompt">{{ item.prompt_prefix }}</span>
                <MathText :text="item.stem" />
              </div>

              <div class="daily-meta">
                <span class="daily-kp-badge">课内考点 · {{ item.kp_name || '课程考点' }}</span>
                <span v-if="item.chapter">{{ item.chapter }}</span>
                <span v-if="item.ppt_refs?.length" :title="item.ppt_refs.join('；')">
                  出处：{{ item.ppt_refs[0] }}
                </span>
                <span v-if="item.origin_label">{{ item.origin_label }}</span>
              </div>

              <div v-if="!isSubmitted && answers[item.question_id]" class="daily-answer">
                <el-radio-group v-if="item.question_type === 'single_choice'"
                  v-model="answers[item.question_id].key" class="daily-options">
                  <div v-for="option in item.options" :key="option.key" class="daily-option"
                    :class="{ 'is-picked': answers[item.question_id].key === option.key }">
                    <el-radio :value="option.key">
                      <span class="daily-option-key">{{ option.key }}.</span>
                      <MathText :text="option.text" />
                    </el-radio>
                  </div>
                </el-radio-group>

                <el-checkbox-group v-else-if="item.question_type === 'multi_choice'"
                  v-model="answers[item.question_id].keys" class="daily-options">
                  <div v-for="option in item.options" :key="option.key" class="daily-option"
                    :class="{ 'is-picked': answers[item.question_id].keys.includes(option.key) }">
                    <el-checkbox :value="option.key">
                      <span class="daily-option-key">{{ option.key }}.</span>
                      <MathText :text="option.text" />
                    </el-checkbox>
                  </div>
                </el-checkbox-group>

                <el-radio-group v-else-if="item.question_type === 'true_false'"
                  v-model="answers[item.question_id].value" class="daily-boolean">
                  <el-radio :value="true">正确</el-radio>
                  <el-radio :value="false">错误</el-radio>
                </el-radio-group>

                <div v-else-if="item.question_type === 'numeric'" class="daily-numeric">
                  <el-input v-model="answers[item.question_id].value"
                    placeholder="输入计算结果（数值）" aria-label="计算结果" />
                  <el-input v-model="answers[item.question_id].unit"
                    placeholder="单位（可留空）" aria-label="单位" />
                </div>

                <el-input v-else v-model="answers[item.question_id].text" type="textarea"
                  :rows="3" resize="none" placeholder="在这里写下你的答案" aria-label="文字作答" />
              </div>

              <div v-else-if="isSubmitted" class="daily-feedback">
                <div class="feedback-row">
                  <span class="feedback-label">你的答案</span>
                  <div class="feedback-value">
                    <MathText :text="resultByQid[item.question_id]?.your_answer || '未作答'" />
                  </div>
                </div>
                <div class="feedback-row">
                  <span class="feedback-label">正确答案</span>
                  <div class="feedback-value is-correct">
                    <MathText :text="resultByQid[item.question_id]?.correct_answer || '题库未登记'" />
                  </div>
                </div>
                <div class="feedback-row">
                  <span class="feedback-label">解析</span>
                  <div class="feedback-value">
                    <MathText :text="resultByQid[item.question_id]?.explanation || ''" />
                  </div>
                </div>
                <div v-if="resultByQid[item.question_id]?.rubric_points?.length" class="feedback-points">
                  <b>评分要点</b>
                  <ul><li v-for="(point, pi) in resultByQid[item.question_id].rubric_points"
                    :key="pi">{{ point }}</li></ul>
                </div>
              </div>
            </article>
          </div>

          <div v-if="daily.missing?.length" class="feedback-banner feedback-banner--warning">
            题库暂时凑不齐三道：缺少「{{ daily.missing.join('、') }}」。系统不会用其它题型顶替。
          </div>

          <div v-if="!isSubmitted" class="daily-actions">
            <el-button type="primary" size="large" :loading="submitting"
              :disabled="!daily.complete" @click="submitDaily">
              提交答案，查看解析<el-icon class="el-icon--right"><ArrowRight /></el-icon>
            </el-button>
            <span class="daily-hint">不跳转页面 · 提交后就地显示正确答案与解析</span>
          </div>
          <router-link class="daily-more" to="/reports?tab=weak">查看完整学情</router-link>
        </template>
      </section>

      <section class="surface-card" aria-labelledby="tasks-title">
        <div class="section-heading">
          <div>
            <span class="section-kicker">今日安排</span>
            <h3 id="tasks-title">学习任务</h3>
          </div>
          <router-link to="/reports?tab=plan">查看计划</router-link>
        </div>

        <EmptyStatePanel v-if="!groupedTasks.length" title="今天的计划已完成"
          description="来一组快速练习。"
          action-label="快速练习" @action="go('/compose?tab=quick')" />

        <div v-else class="task-list">
          <router-link v-for="task in groupedTasks" :key="`${task.kind}:${task.kp_id || task.title}`"
            :to="task.kp_id ? `/compose?tab=create&kp=${encodeURIComponent(task.kp_id)}` : '/reports?tab=plan'"
            class="task-item">
            <span class="task-icon" aria-hidden="true">
              <el-icon><component :is="task.kind === 'retest' ? RefreshRight : Calendar" /></el-icon>
            </span>
            <span class="task-copy">
              <b>{{ task.kp_name || task.title }}</b>
              <small>{{ kindLabel(task.kind) }}<template v-if="task.count > 1"> · 已合并 {{ task.count }} 项</template></small>
            </span>
            <span class="task-time"><el-icon><Clock /></el-icon>{{ task.minutes || 10 }} 分钟</span>
          </router-link>
        </div>
      </section>

      <section class="surface-card" aria-labelledby="recent-title">
        <div class="section-heading">
          <div>
            <span class="section-kicker">学习足迹</span>
            <h3 id="recent-title">最近记录</h3>
          </div>
          <router-link to="/me?tab=attempts">查看全部</router-link>
        </div>

        <EmptyStatePanel v-if="!recent.length" title="还没有练习记录"
          description="完成练习后显示成绩与诊断。" />

        <div v-else class="recent-list">
          <router-link v-for="item in recent" :key="item.attempt_id" :to="`/exam/${item.attempt_id}`"
            class="recent-item">
            <span class="recent-copy">
              <b>{{ item.paper_title }}</b>
              <small>{{ formatDateTime(item.submitted_at || item.started_at) }}</small>
            </span>
            <span v-if="item.status === 'submitted' && item.max_score" class="recent-score">
              {{ item.score }}/{{ item.max_score }} 分
            </span>
            <StatusBadge v-else :status="item.status" :label="item.status_label" />
          </router-link>
        </div>
      </section>
    </div>

    <details v-if="data || daily" class="technical-details compact-details">
      <summary>查看统计口径</summary>
      <template v-if="daily?.rules">
        <p><b>每日 3 题</b>：{{ daily.rules.selection }}</p>
        <p>{{ daily.rules.eligibility }}</p>
        <p>{{ daily.rules.daily }}</p>
        <p>{{ daily.rules.answer }}</p>
        <p>{{ daily.rules.scoring }}</p>
      </template>
      <template v-if="data?.focus?.rules">
        <p><b>历年高频考点</b>：{{ data.focus.rules.question_count }}</p>
        <p>{{ data.focus.rules.review_count }}</p>
        <p>{{ data.focus.rules.state }}</p>
      </template>
    </details>
  </div>
</template>
