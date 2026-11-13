<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'
import StatusBadge from '../components/StatusBadge.vue'
import TechnicalDetails from '../components/TechnicalDetails.vue'
import { formatDateTime, friendlyError, statusLabel } from '../utils/presentation'

const route = useRoute()
const router = useRouter()

function normalizeTab(value: unknown) {
  const tab = String(value || 'records')
  if (['papers', 'attempts', 'materials'].includes(tab)) return 'records'
  if (['favorites', 'settings'].includes(tab)) return tab
  return 'records'
}

const tab = ref(normalizeTab(route.query.tab))
const papers = ref<any[]>([])
const attempts = ref<any[]>([])
const favorites = ref<any[]>([])
const settings = ref<any>(null)
const loading = ref(false)
const error = ref('')
const keyword = ref('')
const favoriteKind = ref('')

const form = ref<any>({
  nickname: '', exam_date: '', daily_minutes: 60,
  default_mode: 'practice', auto_generate_answers: false,
})

const filteredPapers = computed(() => {
  const value = keyword.value.trim().toLowerCase()
  return value ? papers.value.filter((paper) => String(paper.title || '').toLowerCase().includes(value)) : papers.value
})
const filteredAttempts = computed(() => {
  const value = keyword.value.trim().toLowerCase()
  return value ? attempts.value.filter((attempt) => String(attempt.paper_title || '').toLowerCase().includes(value)) : attempts.value
})
const filteredFavorites = computed(() => favoriteKind.value
  ? favorites.value.filter((favorite) => favorite.kind === favoriteKind.value)
  : favorites.value)

watch(() => route.query.tab, (value) => { tab.value = normalizeTab(value) })
watch(tab, async (value) => {
  await router.replace({ path: '/me', query: { tab: value } })
  await loadTab()
})

async function loadSettings() {
  settings.value = await api.get('/me/settings')
  form.value = {
    nickname: settings.value.nickname || '',
    exam_date: settings.value.exam_date || '',
    daily_minutes: settings.value.daily_minutes || 60,
    default_mode: settings.value.default_mode || 'practice',
    auto_generate_answers: Boolean(settings.value.auto_generate_answers),
  }
}

async function loadTab() {
  loading.value = true
  error.value = ''
  try {
    if (tab.value === 'records') {
      const [paperResult, attemptResult]: any[] = await Promise.all([
        api.get('/me/papers', { include_archived: false }),
        api.get('/me/attempts'),
      ])
      papers.value = paperResult.items || []
      attempts.value = attemptResult.items || []
    } else if (tab.value === 'favorites') {
      favorites.value = (await api.get('/me/favorites')).items || []
    } else if (tab.value === 'settings') {
      await loadSettings()
    }
  } catch (e) {
    error.value = friendlyError(e, '内容加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await Promise.allSettled([loadSettings(), loadTab()])
})

async function openPaper(paper: any) {
  const activeAttempt = paper.active_attempt_id || paper.open_attempt_id
  if (activeAttempt) {
    router.push(`/exam/${activeAttempt}`)
    return
  }
  // 「我的试卷继续/重练」属于个人功能，留在个人中心完成，不再跳去组卷页。
  try {
    const attempt: any = await api.post(`/papers/${paper.paper_id}/attempts`)
    router.push(`/exam/${attempt.attempt_id}`)
  } catch (e) {
    ElMessage.error(friendlyError(e, '暂时无法开始答题'))
  }
}

function viewLatestPaper(paper: any) {
  const attemptId = paper.latest_attempt_id || paper.open_attempt_id
  if (attemptId) router.push(`/exam/${attemptId}`)
}

function openFavorite(favorite: any) {
  if (favorite.route) {
    router.push(favorite.route)
    return
  }
  if (favorite.kind === 'kp') router.push(`/compose?tab=create&kp=${encodeURIComponent(favorite.target_id || '')}`)
  else if (favorite.kind === 'paper') router.push(`/compose?tab=papers&paper=${encodeURIComponent(favorite.target_id || '')}`)
  else if (favorite.kind === 'external') router.push('/extension')
  else if (favorite.kind === 'external_paper') router.push('/extension')
  else router.push('/compose')
}

async function removeFavorite(favorite: any) {
  try {
    await api.del(`/me/favorites/${favorite.entry_id}`)
    favorites.value = favorites.value.filter((item) => item.entry_id !== favorite.entry_id)
    ElMessage.success('已取消收藏')
  } catch (e) {
    ElMessage.error(friendlyError(e, '取消收藏失败'))
  }
}

async function saveSettings() {
  try {
    settings.value = await api.patch('/me/settings', form.value)
    ElMessage.success('设置已保存')
  } catch (e) {
    ElMessage.error(friendlyError(e, '设置保存失败'))
  }
}

function diagnosisLabel(attempt: any) {
  if (attempt.status !== 'submitted') return '完成交卷后生成诊断'
  const labels: Record<string, string> = {
    completed: '诊断已完成', failed: '诊断失败', pending: '诊断等待中',
    queued: '诊断等待中', running: '诊断生成中', not_started: '诊断未开始',
  }
  return attempt.diagnosis_status_label || labels[attempt.diagnosis_status] || '诊断状态未知'
}

function favoriteKindLabel(kind: string) {
  return ({ question: '题目', paper: '试卷', kp: '考点', chunk: '资料',
    external: '扩展学习', external_paper: '试卷资源' } as Record<string, string>)[kind] || '学习内容'
}
</script>

<template>
  <main v-loading="loading" class="me-page">
    <header class="page-head">
      <div>
        <p class="page-kicker">个人中心</p>
        <h1>我的学习</h1>
        <p>查看学习记录、收藏内容和个人偏好。</p>
      </div>
    </header>

    <section class="profile-card">
      <div class="avatar" aria-hidden="true">{{ (settings?.nickname || '学').slice(0, 1) }}</div>
      <div class="profile-main">
        <strong>{{ settings?.nickname || '本地学习者' }}</strong>
        <p>
          单用户本地学习空间
          <span v-if="settings?.exam_date"> · 考试日期 {{ settings.exam_date }}</span>
          <span v-if="settings?.daily_minutes"> · 每日 {{ settings.daily_minutes }} 分钟</span>
        </p>
      </div>
      <el-button @click="tab = 'settings'">编辑设置</el-button>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

    <el-tabs v-model="tab" class="me-tabs">
      <el-tab-pane label="学习记录" name="records">
        <div class="records-toolbar">
          <el-input v-model="keyword" clearable placeholder="搜索试卷或答卷" aria-label="搜索学习记录" />
          <div class="shortcut-links">
            <el-button @click="router.push('/materials')">管理资料</el-button>
            <el-button type="primary" @click="router.push('/compose')">去组卷</el-button>
          </div>
        </div>

        <section class="record-section">
          <div class="section-heading">
            <div><h2>我的试卷</h2><p>继续未完成的练习，或再次练习已完成的试卷。</p></div>
            <span>{{ filteredPapers.length }} 份</span>
          </div>
          <div v-if="filteredPapers.length" class="record-grid">
            <article v-for="paper in filteredPapers" :key="paper.paper_id" class="record-card">
              <div class="card-head">
                <StatusBadge :status="paper.status" :label="paper.status_label || statusLabel(paper.status)" />
                <span>{{ paper.item_count }} 题 · {{ paper.total_score }} 分</span>
              </div>
              <h3>{{ paper.title }}</h3>
              <p>{{ paper.kp_names?.length ? paper.kp_names.slice(0, 3).join('、') : '综合练习' }}</p>
              <div class="card-foot">
                <span>{{ formatDateTime(paper.created_at) }}</span>
                <div>
                  <el-button v-if="paper.latest_attempt_id && !paper.active_attempt_id" text @click="viewLatestPaper(paper)">查看上次结果</el-button>
                  <el-button type="primary" @click="openPaper(paper)">
                    {{ paper.active_attempt_id || paper.open_attempt_id ? '继续答题' : paper.latest_attempt_id ? '再次练习' : '开始答题' }}
                  </el-button>
                </div>
              </div>
            </article>
          </div>
          <div v-else class="empty-state">
            <strong>{{ keyword ? '没有找到匹配的试卷' : '还没有自己的试卷' }}</strong>
            <p>{{ keyword ? '试试其他关键词。' : '在组卷中心生成试卷后，会自动保存在这里。' }}</p>
            <el-button v-if="!keyword" type="primary" @click="router.push('/compose')">去组卷</el-button>
          </div>
        </section>

        <section class="record-section">
          <div class="section-heading">
            <div><h2>最近答卷</h2><p>未交卷可继续，已交卷可回看成绩和诊断状态。</p></div>
            <span>{{ filteredAttempts.length }} 次</span>
          </div>
          <div v-if="filteredAttempts.length" class="attempt-list">
            <button v-for="attempt in filteredAttempts" :key="attempt.attempt_id" class="attempt-row" @click="router.push(`/exam/${attempt.attempt_id}`)">
              <div class="attempt-main">
                <strong>{{ attempt.paper_title || '练习答卷' }}</strong>
                <span>{{ formatDateTime(attempt.started_at) }} · {{ diagnosisLabel(attempt) }}</span>
              </div>
              <StatusBadge :status="attempt.status" :label="attempt.status_label || statusLabel(attempt.status)" />
              <strong class="attempt-score">
                {{ attempt.status === 'submitted' ? `${attempt.score ?? 0} / ${attempt.max_score ?? 0}` : '继续答题' }}
              </strong>
              <span aria-hidden="true">›</span>
            </button>
          </div>
          <div v-else class="empty-state compact">
            <strong>还没有答卷记录</strong><p>完成一次练习后，这里会保留你的学习轨迹。</p>
          </div>
        </section>
      </el-tab-pane>

      <el-tab-pane label="收藏" name="favorites">
        <div class="favorite-toolbar">
          <el-radio-group v-model="favoriteKind" size="small">
            <el-radio-button value="">全部</el-radio-button>
            <el-radio-button value="question">题目</el-radio-button>
            <el-radio-button value="paper">试卷</el-radio-button>
            <el-radio-button value="kp">考点</el-radio-button>
            <el-radio-button value="external">资源</el-radio-button>
            <el-radio-button value="external_paper">试卷</el-radio-button>
          </el-radio-group>
          <span>收藏是快捷入口，不代表已经掌握。</span>
        </div>
        <div v-if="filteredFavorites.length" class="favorite-list">
          <article v-for="favorite in filteredFavorites" :key="favorite.entry_id" class="favorite-card">
            <div>
              <span>{{ favoriteKindLabel(favorite.kind) }}</span>
              <h3>{{ favorite.title || '收藏内容' }}</h3>
              <p v-if="favorite.note">{{ favorite.note }}</p>
              <small>{{ formatDateTime(favorite.created_at) }}</small>
            </div>
            <div class="favorite-actions">
              <el-button type="primary" @click="openFavorite(favorite)">打开</el-button>
              <el-button @click="removeFavorite(favorite)">取消收藏</el-button>
            </div>
          </article>
        </div>
        <div v-else class="empty-state">
          <strong>还没有收藏内容</strong>
          <p>在扩展学习或组卷中心收藏后，可以从这里快速返回。</p>
          <el-button type="primary" @click="router.push('/extension')">看看扩展学习</el-button>
        </div>
      </el-tab-pane>

      <el-tab-pane label="设置" name="settings">
        <section class="settings-card">
          <div class="settings-intro">
            <h2>学习偏好</h2>
            <p>这些设置会影响默认练习方式和新生成的学习计划。</p>
          </div>
          <el-form label-position="top" class="settings-form">
            <el-form-item label="昵称">
              <el-input v-model="form.nickname" maxlength="20" />
            </el-form-item>
            <el-form-item label="考试日期">
              <el-date-picker v-model="form.exam_date" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" />
            </el-form-item>
            <el-form-item label="每日学习时长">
              <el-input-number v-model="form.daily_minutes" :min="10" :max="480" :step="10" />
              <span class="form-suffix">分钟</span>
            </el-form-item>
            <el-form-item label="默认练习模式">
              <el-radio-group v-model="form.default_mode">
                <el-radio-button value="practice">学习练习</el-radio-button>
                <el-radio-button value="quiz">模拟测验</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item>
              <el-checkbox v-model="form.auto_generate_answers">导入试卷后自动生成答案解析</el-checkbox>
              <p class="field-help">关闭时可以先检查题目识别结果，再手动生成答案。</p>
            </el-form-item>
            <el-button type="primary" @click="saveSettings">保存设置</el-button>
          </el-form>
          <TechnicalDetails title="模型与本地数据说明">
            <p>{{ settings?.api_key_policy || '模型密钥由本地环境变量管理，页面不会读取或显示密钥。' }}</p>
            <p>学习数据保存在本机数据库中；平台不会在此页面提供高风险的一键删除。</p>
            <el-button @click="router.push('/about')">查看完整说明</el-button>
          </TechnicalDetails>
        </section>
      </el-tab-pane>
    </el-tabs>
  </main>
</template>

<style scoped>
.me-page { max-width: 1180px; margin: 0 auto; }
.page-head { margin-bottom: 18px; }
.page-head h1 { margin: 3px 0 7px; font-size: 28px; }
.page-head p { margin: 0; color: #687083; }
.page-kicker { color: #4f46e5 !important; font-size: 13px; font-weight: 700; }
.profile-card { display: flex; align-items: center; gap: 14px; margin-bottom: 18px; padding: 18px 20px; border: 1px solid #e3e6ee; border-radius: 16px; background: #fff; }
.avatar { display: grid; place-items: center; width: 48px; height: 48px; flex: none; border-radius: 14px; background: #eef2ff; color: #4338ca; font-size: 20px; font-weight: 800; }
.profile-main { flex: 1; min-width: 0; }
.profile-main strong { font-size: 16px; }
.profile-main p { margin: 5px 0 0; color: #687083; font-size: 14px; }
.error-banner { margin-bottom: 12px; padding: 12px 15px; border-radius: 10px; background: #fef2f2; color: #991b1b; }
.records-toolbar, .favorite-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.records-toolbar :deep(.el-input) { max-width: 300px; }
.shortcut-links { display: flex; gap: 8px; }
.record-section { margin-bottom: 28px; }
.section-heading { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 12px; }
.section-heading h2 { margin: 0; font-size: 18px; }
.section-heading p { margin: 5px 0 0; color: #687083; font-size: 14px; }
.section-heading > span { color: #7a8190; font-size: 14px; }
.record-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.record-card { display: flex; flex-direction: column; min-width: 0; padding: 18px; border: 1px solid #e3e6ee; border-radius: 14px; background: #fff; }
.card-head, .card-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.card-head > span, .card-foot > span { color: #737b8d; font-size: 13px; }
.record-card h3 { margin: 17px 0 6px; font-size: 17px; }
.record-card > p { flex: 1; margin: 0 0 18px; color: #687083; line-height: 1.6; }
.card-foot > div { display: flex; align-items: center; gap: 4px; }
.attempt-list { overflow: hidden; border: 1px solid #e3e6ee; border-radius: 14px; background: #fff; }
.attempt-row { display: grid; grid-template-columns: minmax(0, 1fr) auto 110px 18px; align-items: center; gap: 16px; width: 100%; min-height: 76px; padding: 14px 18px; border: 0; border-bottom: 1px solid #edf0f4; background: #fff; color: inherit; text-align: left; cursor: pointer; }
.attempt-row:last-child { border-bottom: 0; }
.attempt-row:hover { background: #fafaff; }
.attempt-row:focus-visible { position: relative; z-index: 1; outline: 3px solid rgb(79 70 229 / 20%); outline-offset: -3px; }
.attempt-main { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.attempt-main strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.attempt-main span { color: #747c8d; font-size: 13px; }
.attempt-score { text-align: right; color: #3730a3; }
.empty-state { padding: 40px 20px; border: 1px dashed #d8dce6; border-radius: 14px; background: #fafbfc; text-align: center; }
.empty-state.compact { padding: 28px 20px; }
.empty-state strong { font-size: 16px; }
.empty-state p { margin: 8px 0 16px; color: #70788a; }
.favorite-toolbar > span { color: #737b8d; font-size: 13px; }
.favorite-list { display: grid; gap: 10px; }
.favorite-card { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 20px; border: 1px solid #e3e6ee; border-radius: 14px; background: #fff; }
.favorite-card > div:first-child { min-width: 0; }
.favorite-card span { color: #4f46e5; font-size: 13px; font-weight: 700; }
.favorite-card h3 { margin: 5px 0; font-size: 16px; }
.favorite-card p, .favorite-card small { margin: 0; color: #70788a; }
.favorite-actions { display: flex; flex: none; gap: 8px; }
.settings-card { max-width: 760px; padding: 26px 28px; border: 1px solid #e3e6ee; border-radius: 16px; background: #fff; }
.settings-intro h2 { margin: 0; font-size: 20px; }
.settings-intro p { margin: 6px 0 22px; color: #687083; }
.settings-form { max-width: 520px; }
.settings-form :deep(.el-date-editor) { width: 100%; }
.form-suffix { margin-left: 8px; color: #687083; }
.field-help { width: 100%; margin: 5px 0 0; color: #757d8f; font-size: 13px; line-height: 1.6; }

@media (max-width: 720px) {
  .profile-card { align-items: flex-start; flex-wrap: wrap; }
  .profile-main { flex-basis: calc(100% - 70px); }
  .profile-card > :deep(.el-button) { width: 100%; min-height: 44px; }
  .records-toolbar, .favorite-toolbar { align-items: stretch; flex-direction: column; }
  .records-toolbar :deep(.el-input) { max-width: none; }
  .shortcut-links { display: grid; grid-template-columns: 1fr 1fr; }
  .shortcut-links :deep(.el-button) { min-height: 44px; margin-left: 0; }
  .record-grid { grid-template-columns: 1fr; }
  .card-foot { align-items: flex-start; flex-direction: column; }
  .card-foot > div { display: grid; grid-template-columns: 1fr 1fr; width: 100%; }
  .card-foot :deep(.el-button) { min-height: 44px; margin-left: 0; }
  .attempt-row { grid-template-columns: minmax(0, 1fr) auto; min-height: 92px; }
  .attempt-row .status-badge { justify-self: end; }
  .attempt-score { text-align: left; }
  .attempt-row > span:last-child { display: none; }
  .favorite-toolbar { overflow-x: auto; }
  .favorite-card { align-items: stretch; flex-direction: column; }
  .favorite-actions { display: grid; grid-template-columns: 1fr 1fr; }
  .favorite-actions :deep(.el-button) { min-height: 44px; margin-left: 0; }
  .settings-card { padding: 20px 16px; }
}
</style>
