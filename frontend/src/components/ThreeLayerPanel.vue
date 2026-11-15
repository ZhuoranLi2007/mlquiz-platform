<script setup lang="ts">
/**
 * 三层学情诊断：题目层 / 试卷层 / 历次试卷层。
 * 数据来自 GET /api/reports/three-layer，一次取全；不在这里重算任何规则。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, pageImageUrl } from '../api/client'
import MathText from './MathText.vue'

interface AttemptBrief {
  attempt_id: string
  paper_id: string
  title: string
  item_count: number
  score: number | null
  max_score: number | null
  submitted_at: string
  diagnosis_status: string
}

interface QuestionLayerItem {
  question_id: string
  question_type: string
  stem: string
  prompt_prefix: string
  kp_id: string
  kp_name: string
  chapter: string
  score: number | null
  max_score: number | null
  correct: boolean | null
  independent: boolean | null
  hints_used: number | null
  response: Record<string, any> | null
  candidate_causes: string[]
  evidence: Record<string, any>[]
  next_verification: string[]
  explanation: Record<string, any>
  uncertainties: string[]
  next_actions: string[]
  engine: string
  created_at: string
}

interface DiagnosisRow {
  payload: Record<string, any>
  uncertainties: string[]
  next_actions: string[]
  skill_version: string
  created_at: string
}

interface ThreeLayerData {
  attempts: AttemptBrief[]
  selected_attempt_id: string
  question: QuestionLayerItem[]
  paper: DiagnosisRow | null
  history: DiagnosisRow | null
  knowledge_points: Record<string, { kp_name: string; chapter: string }>
  criteria: {
    min_evidence: number
    min_families: number
    stable_days: number
    retest_intervals_days: number[]
    states: string[]
    rule: string
  }
}

const props = defineProps<{ attemptId?: string }>()
const route = useRoute()
const router = useRouter()
const loading = ref(true)
const error = ref('')
const data = ref<ThreeLayerData | null>(null)
const selected = ref('')
const openCauses = ref<Record<string, boolean>>({})
const preview = ref<{ document_id: string; page: number; label: string } | null>(null)

const TYPE_LABELS: Record<string, string> = {
  single_choice: '单选', multi_choice: '多选', true_false: '判断',
  numeric: '数值', term: '名词解释', short_answer: '简答',
  derivation: '推导', comprehensive: '综合', unknown: '题型待核对',
}

const STATE_TONE: Record<string, string> = {
  需加强: 'danger', 证据不足: 'warning', 初步通过: 'success',
  阶段稳定: 'success', 未测: 'info',
}

const questionItems = computed(() => data.value?.question || [])
const attemptOptions = computed(() => (data.value?.attempts || []).map((item) => ({
  value: item.attempt_id,
  label: `${item.title} · ${item.score ?? '—'}/${item.max_score ?? '—'} 分 · ${formatTime(item.submitted_at)}`,
})))

const paperPayload = computed(() => data.value?.paper?.payload || null)
const coveredPoints = computed<any[]>(() => paperPayload.value?.covered_knowledge_points || [])
const reviewPriority = computed<any[]>(() => paperPayload.value?.review_priority || [])
const historyPoints = computed<any[]>(() => data.value?.history?.payload?.per_knowledge_point || [])

function formatTime(value?: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function fmtScore(value: number | null | undefined) {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, '')
}

function kpLabel(kpId: string) {
  return data.value?.knowledge_points?.[kpId]?.kp_name || kpId.replace('kp.', '')
}

function responseText(item: QuestionLayerItem) {
  const resp = item.response || {}
  if (Array.isArray(resp.keys) && resp.keys.length) return `选了 ${resp.keys.join('、')}`
  if (resp.key) return `选了 ${resp.key}`
  if (resp.value === true) return '判断为「正确」'
  if (resp.value === false) return '判断为「错误」'
  if (resp.value !== undefined && resp.value !== null && resp.value !== '') return `填写 ${resp.value}`
  const text = String(resp.text || resp.content || '').trim()
  if (!text) return '未作答'
  return text.length > 80 ? `${text.slice(0, 80)}…` : text
}

function evidenceLabel(ref: Record<string, any>) {
  if (ref.rel_path) return String(ref.rel_path).split('/').pop() || ref.rel_path
  if (ref.document_id) return ref.document_id
  return '作答记录'
}

/** 只展示能定位到资料的证据，并去重；内部 score_id / answer_id 不再当成证据卡片。 */
function evidenceChips(item: QuestionLayerItem) {
  const seen = new Set<string>()
  const out: Record<string, any>[] = []
  for (const ref of item.evidence || []) {
    if (!ref.document_id) continue
    const key = `${ref.document_id}:${ref.page || ''}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(ref)
  }
  return out
}

function hasPageEvidence(item: QuestionLayerItem) {
  return evidenceChips(item).some((ref) => Boolean(ref.page))
}

function openPage(ref: Record<string, any>) {
  if (!ref.document_id || !ref.page) return
  preview.value = {
    document_id: String(ref.document_id),
    page: Number(ref.page),
    label: `${evidenceLabel(ref)} · 第 ${ref.page} 页`,
  }
}

function toggleCauses(id: string) {
  openCauses.value = { ...openCauses.value, [id]: !openCauses.value[id] }
}

function selectAttempt(value: string) {
  selected.value = value
  void router.replace({ query: { ...route.query, tab: 'three', attempt: value } })
  void load(value)
}

async function load(attemptId = '') {
  loading.value = true
  error.value = ''
  try {
    const result = await api.get<ThreeLayerData>('/reports/three-layer', attemptId ? { attempt_id: attemptId } : undefined)
    data.value = result
    selected.value = result.selected_attempt_id || ''
  } catch (err: any) {
    error.value = err?.message || String(err)
  } finally {
    loading.value = false
  }
}

watch(() => props.attemptId, (value) => {
  if (value && value !== selected.value) void load(value)
})

onMounted(() => load(props.attemptId || ''))
</script>

<template>
  <section class="three-layer" v-loading="loading">
    <div v-if="error" class="hint error-hint" role="alert">{{ error }}</div>

    <div class="layer-toolbar">
      <div class="layer-toolbar-main">
        <span class="toolbar-label">看哪一次作答</span>
        <el-select :model-value="selected" placeholder="还没有已交卷的练习" class="attempt-select"
          :disabled="!attemptOptions.length" @change="selectAttempt">
          <el-option v-for="item in attemptOptions" :key="item.value" :value="item.value" :label="item.label" />
        </el-select>
      </div>
      <div class="toolbar-meta">
        <span v-if="data?.paper">报告生成于 {{ formatTime(data.paper.created_at) }}</span>
        <span v-else>本次作答还没有生成报告</span>
      </div>
    </div>

    <div v-if="!attemptOptions.length" class="empty-state">
      <div class="empty-icon" aria-hidden="true">◎</div>
      <h4>还没有可以诊断的作答</h4>
      <p>先在题库里做一次小测并交卷，系统会自动生成题目层、试卷层和历次层三份报告。</p>
      <el-button type="primary" plain @click="router.push('/compose')">去组一份小测</el-button>
    </div>

    <template v-else>
      <!-- ① 题目层 -->
      <article class="layer-card">
        <header class="layer-head">
          <div>
            <span class="layer-index">①</span>
            <div class="layer-title">
              <h3>题目层 · 这道题错在哪</h3>
              <p>单题只给候选误区，不下心理结论；每条证据都能回到原卷页。</p>
            </div>
          </div>
          <el-tag effect="plain" type="info">{{ questionItems.length }} 道题</el-tag>
        </header>

        <div v-if="questionItems.length" class="q-list">
          <article v-for="item in questionItems" :key="item.question_id" class="q-card"
            :class="{ 'is-correct': item.correct }">
            <div class="q-head">
              <div class="q-tags">
                <el-tag size="small" effect="plain">{{ TYPE_LABELS[item.question_type] || item.question_type }}</el-tag>
                <span class="q-kp">{{ item.chapter || '课程考点' }} · {{ item.kp_name || '未标注考点' }}</span>
              </div>
              <div class="q-score">
                <strong :class="item.correct ? 'ok' : 'bad'">{{ fmtScore(item.score) }}</strong>
                <span>/ {{ fmtScore(item.max_score) }} 分</span>
                <el-tag v-if="item.independent === false" size="small" type="info" effect="plain">辅助作答 · 不计入证据</el-tag>
                <el-tag v-else-if="item.independent" size="small" type="success" effect="plain">独立作答</el-tag>
              </div>
            </div>

            <div class="q-stem">
              <span v-if="item.prompt_prefix" class="q-prefix">{{ item.prompt_prefix }}</span>
              <MathText :text="item.stem" />
            </div>

            <div class="q-response">你的作答：{{ responseText(item) }}</div>

            <p v-if="item.correct" class="q-right">
              本题答对了。能否推广到同类题，还要看不同题族的新题（见历次层）。
            </p>

            <div v-if="!item.correct && item.candidate_causes.length" class="q-block">
              <h5>可能原因（候选，需新题验证）</h5>
              <ul>
                <li v-for="(cause, index) in item.candidate_causes" :key="index">{{ cause }}</li>
              </ul>
            </div>

            <div v-if="evidenceChips(item).length" class="q-block">
              <h5>支持证据</h5>
              <div class="evidence-list">
                <button v-for="(ref, index) in evidenceChips(item).slice(0, 4)" :key="index" type="button"
                  class="evidence-chip" :disabled="!ref.page" @click="openPage(ref)">
                  {{ evidenceLabel(ref) }}<span v-if="ref.page"> · 第 {{ ref.page }} 页</span>
                </button>
                <span v-if="!hasPageEvidence(item)" class="muted small">
                  本题只关联到资料文件，未定位到具体页。
                </span>
              </div>
            </div>
            <div v-else class="q-block">
              <h5>支持证据</h5>
              <span class="muted small">本题没有登记页级出处，证据为本次作答记录。</span>
            </div>

            <div v-if="!item.correct && item.next_verification.length" class="q-block next">
              <h5>下一步验证</h5>
              <ol>
                <li v-for="(step, index) in item.next_verification" :key="index">{{ step }}</li>
              </ol>
            </div>

            <div class="q-foot">
              <button type="button" class="text-action" @click="toggleCauses(item.question_id)">
                {{ openCauses[item.question_id] ? '收起模型解释' : '查看模型解释与不确定项' }}
              </button>
              <span class="muted small">{{ item.engine === 'model' ? '模型辅助生成' : '确定性规则生成' }}</span>
            </div>

            <div v-if="openCauses[item.question_id]" class="q-explain">
              <div v-if="item.explanation.observed?.length">
                <h5>观察事实</h5>
                <ul><li v-for="(line, i) in item.explanation.observed" :key="i">{{ line }}</li></ul>
              </div>
              <div v-if="item.explanation.insufficient">
                <h5>不确定项</h5>
                <p>{{ item.explanation.insufficient }}</p>
              </div>
              <div v-if="item.uncertainties.length">
                <h5>边界说明</h5>
                <ul><li v-for="(line, i) in item.uncertainties" :key="i">{{ line }}</li></ul>
              </div>
            </div>
          </article>
        </div>
        <p v-else class="muted small">这次作答没有生成单题报告，可能是题目全部为待评分题型。</p>
      </article>

      <!-- ② 试卷层 -->
      <article class="layer-card">
        <header class="layer-head">
          <div>
            <span class="layer-index">②</span>
            <div class="layer-title">
              <h3>试卷层 · 这张卷子覆盖了什么</h3>
              <p>按考点统计题量、独立题族与失分，给出复习优先级；不把总分直接当成能力。</p>
            </div>
          </div>
          <el-tag v-if="paperPayload" effect="plain">
            {{ fmtScore(paperPayload.score) }} / {{ fmtScore(paperPayload.max_score) }} 分
          </el-tag>
        </header>

        <template v-if="paperPayload">
          <div v-if="coveredPoints.length" class="kp-table">
            <div class="kp-row kp-head">
              <span>考点</span><span>题量</span><span>正确</span><span>证据</span><span>独立题族</span><span>失分</span><span>复习优先级</span>
            </div>
            <div v-for="kp in coveredPoints" :key="kp.kp_id" class="kp-row">
              <span class="kp-name">{{ kp.kp_name }}</span>
              <span>{{ kp.items }}</span>
              <span>{{ kp.correct_items }}</span>
              <span>{{ (kp.evidence_ids || []).length }}</span>
              <span>{{ kp.independent_families }}</span>
              <span :class="kp.lost_points > 0 ? 'bad' : 'ok'">{{ fmtScore(kp.lost_points) }}</span>
              <span>
                <el-tag v-if="reviewPriority.findIndex((p: any) => p.kp_id === kp.kp_id) === 0"
                  size="small" type="danger" effect="plain">优先复习</el-tag>
                <span v-else class="muted">—</span>
              </span>
            </div>
          </div>
          <p v-else class="muted small">本次没有覆盖到带考点的题目。</p>

          <div v-if="reviewPriority.length" class="priority-note">
            <strong>复习顺序：</strong>
            <span v-for="(item, index) in reviewPriority" :key="item.kp_id">
              {{ index + 1 }}. {{ kpLabel(item.kp_id) }}（{{ item.reason }}）
            </span>
          </div>

          <div v-if="paperPayload.model_summary" class="summary-block">
            <h5>报告摘要</h5>
            <p>{{ paperPayload.model_summary.summary }}</p>
            <ul v-if="paperPayload.model_summary.cautions?.length" class="caution-list">
              <li v-for="(line, index) in paperPayload.model_summary.cautions" :key="index">{{ line }}</li>
            </ul>
          </div>

          <div v-if="data?.paper?.next_actions?.length" class="next-actions">
            <h5>接下来做什么</h5>
            <ol><li v-for="(action, index) in data.paper.next_actions" :key="index">{{ action }}</li></ol>
          </div>
        </template>
        <p v-else class="muted small">本次作答还没有试卷层报告，点击右上角「更新学情分析」生成。</p>
      </article>

      <!-- ③ 历次试卷层 -->
      <article class="layer-card">
        <header class="layer-head">
          <div>
            <span class="layer-index">③</span>
            <div class="layer-title">
              <h3>历次试卷层 · 同一个知识点，几次作答放在一起</h3>
              <p>只比较同知识点、相近认知要求的独立作答；不同卷的总分差不当成能力变化。</p>
            </div>
          </div>
          <el-tag v-if="data?.history" effect="plain" type="info">
            累计 {{ data.history.payload.submitted_attempts }} 次交卷
          </el-tag>
        </header>

        <template v-if="historyPoints.length">
          <div class="state-list">
            <div v-for="kp in historyPoints" :key="kp.kp_id" class="state-row">
              <div class="state-main">
                <h4>{{ kp.kp_name || kp.kp_id }}</h4>
                <p class="muted small">独立作答 {{ kp.independent_items }} 条 ·
                  题族 {{ kp.independent_families }} 个 ·
                  时间跨度 {{ kp.time_span_days }} 天
                  <span v-if="kp.assisted_items"> · 含提示/已见答案 {{ kp.assisted_items }} 条（不参与状态提升）</span>
                </p>
              </div>
              <div class="state-series" aria-hidden="true">
                <span v-for="(point, index) in (kp.series || []).slice(-12)" :key="index"
                  class="dot" :class="point.correct ? 'ok' : 'bad'" :title="point.family_id" />
              </div>
              <el-tag :type="STATE_TONE[kp.state] || 'info'" effect="light">{{ kp.state }}</el-tag>
            </div>
          </div>

          <div class="criteria-box">
            <h5>掌握是怎么判定的（项目规则，可调整）</h5>
            <div class="criteria-grid">
              <div><strong>{{ data?.criteria.min_evidence }}</strong><span>条独立证据</span></div>
              <div><strong>{{ data?.criteria.min_families }}</strong><span>个不同题族</span></div>
              <div><strong>{{ data?.criteria.stable_days }}</strong><span>天以上时间跨度</span></div>
              <div><strong>{{ (data?.criteria.retest_intervals_days || []).join(' / ') }}</strong><span>天后延迟复测</span></div>
            </div>
            <p class="muted small">{{ data?.criteria.rule }}</p>
            <el-button text type="primary" @click="router.push('/wrong-book')">去安排新题检验 →</el-button>
          </div>
        </template>
        <p v-else class="muted small">还没有历次诊断；完成两次练习后这里会把同一知识点的作答放在一起比较。</p>
      </article>
    </template>

    <el-dialog :model-value="Boolean(preview)" :title="preview?.label || '原页预览'" width="820px"
      destroy-on-close @update:model-value="(v: boolean) => { if (!v) preview = null }">
      <img v-if="preview" :src="pageImageUrl(preview.document_id, preview.page)"
        :alt="preview.label" style="width: 100%; border-radius: 10px" />
    </el-dialog>
  </section>
</template>

<style scoped>
.three-layer { display: grid; gap: 18px; }
.layer-toolbar {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 12px; padding: 14px 18px; border: 1px solid var(--border);
  border-radius: 12px; background: var(--panel);
}
.layer-toolbar-main { display: flex; align-items: center; gap: 12px; min-width: 0; }
.attempt-select { width: 360px; max-width: 100%; }
.toolbar-label { color: var(--muted); font-size: 14px; }
.toolbar-meta { color: var(--muted-2); font-size: 14px; }

.layer-card {
  padding: 20px 22px; border: 1px solid var(--border); border-radius: 14px;
  background: var(--panel); box-shadow: var(--shadow-sm);
}
.layer-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.layer-head > div { display: flex; gap: 12px; min-width: 0; }
.layer-index {
  flex: none; width: 30px; height: 30px; display: grid; place-items: center;
  border-radius: 9px; background: var(--accent-soft); color: var(--accent);
  font-weight: 700; font-size: 15px;
}
.layer-title h3 { margin: 0; font-size: 17px; }
.layer-title p { margin: 4px 0 0; color: var(--muted); font-size: 14px; line-height: 1.6; }

.q-list { display: grid; gap: 12px; }
.q-card { border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; background: var(--panel-soft); }
.q-card.is-correct { background: #fff; }
.q-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.q-tags { display: flex; align-items: center; gap: 8px; min-width: 0; }
.q-kp { color: var(--muted); font-size: 14px; }
.q-score { display: flex; align-items: center; gap: 8px; font-size: 14px; color: var(--muted); }
.q-score strong { font-size: 18px; }
.q-score .ok { color: var(--ok); }
.q-score .bad { color: var(--danger); }
.q-stem { margin: 10px 0 6px; font-size: 15px; line-height: 1.75; }
.q-prefix { color: var(--muted); margin-right: 4px; }
.q-response { color: var(--text-2); font-size: 14px; padding: 6px 10px; border-radius: 8px; background: #fff; border: 1px dashed var(--border); }
.q-right { margin: 10px 0 0; color: var(--ok); font-size: 14px; }
.q-block { margin-top: 10px; }
.q-block h5 { margin: 0 0 5px; font-size: 14px; color: var(--muted); font-weight: 600; letter-spacing: .02em; }
.q-block ul, .q-block ol { margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.75; color: var(--text-2); }
.q-block.next h5 { color: var(--accent); }
.evidence-list { display: flex; flex-wrap: wrap; gap: 6px; }
.evidence-chip {
  border: 1px solid var(--border); background: #fff; border-radius: 999px;
  padding: 4px 10px; font-size: 14px; color: var(--text-2); cursor: pointer;
}
.evidence-chip:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.evidence-chip:disabled { cursor: default; opacity: .7; }
.q-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 12px; }
.text-action { border: 0; background: transparent; color: var(--accent); cursor: pointer; font-size: 14px; padding: 0; }
.q-explain { margin-top: 10px; padding: 12px; border-radius: 10px; background: #fff; border: 1px solid var(--border); font-size: 14px; }
.q-explain h5 { margin: 10px 0 4px; color: var(--muted); font-size: 14px; }
.q-explain h5:first-child { margin-top: 0; }
.q-explain ul { margin: 0; padding-left: 18px; line-height: 1.7; }

.kp-table { display: grid; gap: 2px; }
.kp-row {
  display: grid; grid-template-columns: minmax(0, 2fr) 52px 52px 52px 76px 62px 104px;
  align-items: center; gap: 8px; padding: 9px 10px; border-radius: 8px; font-size: 14px;
}
.kp-row:nth-child(odd) { background: var(--panel-soft); }
.kp-head { color: var(--muted); font-size: 14px; background: transparent !important; }
.kp-name { font-weight: 500; }
.bad { color: var(--danger); }
.ok { color: var(--ok); }
.priority-note { margin-top: 10px; font-size: 14px; color: var(--text-2); display: flex; flex-wrap: wrap; gap: 10px; }
.summary-block { margin-top: 14px; }
.summary-block h5, .next-actions h5 { margin: 0 0 6px; font-size: 14px; color: var(--muted); }
.summary-block p { margin: 0 0 6px; font-size: 14px; line-height: 1.75; }
.caution-list { margin: 0; padding-left: 18px; color: var(--muted); font-size: 14px; line-height: 1.7; }
.next-actions { margin-top: 14px; }
.next-actions ol { margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.8; }

.state-list { display: grid; gap: 8px; }
.state-row {
  display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center;
  gap: 14px; padding: 11px 12px; border: 1px solid var(--border); border-radius: 10px;
}
.state-main h4 { margin: 0 0 2px; font-size: 14px; }
.state-series { display: flex; gap: 3px; align-items: center; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--border); }
.dot.ok { background: #34d399; }
.dot.bad { background: #f87171; }
.criteria-box {
  margin-top: 16px; padding: 14px 16px; border-radius: 12px;
  background: var(--accent-soft); border: 1px solid #dbe3ff;
}
.criteria-box h5 { margin: 0 0 10px; font-size: 14px; color: var(--accent); }
.criteria-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 8px; }
.criteria-grid div { display: flex; flex-direction: column; }
.criteria-grid strong { font-size: 18px; color: var(--accent); }
.criteria-grid span { font-size: 14px; color: var(--muted); }
.empty-state { padding: 40px 20px; text-align: center; border: 1px dashed var(--border); border-radius: 14px; background: var(--panel); }
.empty-icon { font-size: 26px; color: var(--muted-2); }
.empty-state h4 { margin: 10px 0 6px; }
.empty-state p { margin: 0 0 14px; color: var(--muted); font-size: 14px; }

@media (max-width: 900px) {
  .kp-row { grid-template-columns: minmax(0, 1.6fr) 44px 44px 44px 62px 52px 82px; font-size: 14px; }
  .criteria-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .state-row { grid-template-columns: minmax(0, 1fr) auto; }
  .state-series { display: none; }
}

@media (max-width: 640px) {
  .layer-toolbar { flex-direction: column; align-items: stretch; }
  .layer-toolbar-main { flex-direction: column; align-items: stretch; gap: 6px; }
  .attempt-select { width: 100%; }
  .layer-card { padding: 16px 14px; }
  .layer-head { flex-direction: column; gap: 10px; }
  .q-head { flex-direction: column; align-items: flex-start; }
  .q-score { align-self: flex-start; }
  .kp-row { grid-template-columns: minmax(0, 1.4fr) 38px 38px 38px 52px 46px 70px; gap: 4px; }
}
</style>
