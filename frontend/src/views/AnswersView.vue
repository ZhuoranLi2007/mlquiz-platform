<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api/client'

const summary = ref<any>(null)
const gens = ref<any[]>([])
const disputes = ref<any[]>([])
const loading = ref(true)
const busy = ref(false)
const dialogVisible = ref(false)
const active = ref<any>(null)
const form = ref<any>({ resolution: 'corrected', resolution_note: '', answer_payload: '{\n  "key": "A"\n}' })

const statusLabel: Record<string, string> = {
  queued: '排队中',
  running: '生成中',
  succeeded: '已通过自动检查并入库',
  needs_attention: '具体异常待处理',
  pending_model_configuration: '待配置模型',
  failed: '失败',
  retrying: '重试中',
  // 题目在导入阶段就被判定不可发布（如选项公式在文本层丢失），不生成答案
  skipped: '已跳过（不可发布）',
}
const exceptionLabel: Record<string, string> = {
  missing_figure_or_formula: '缺图/缺公式',
  insufficient_conditions: '条件不足',
  contradictory_result: '结果矛盾',
  malformed_output: '输出结构不合规',
  model_not_configured: '模型未配置',
  // 2026-11-05 真实接入后发现的两类：前者是题干被截断，后者是自校验式不可求值
  incomplete_stem: '题干不完整（截断）',
  calculation_unevaluable: '自校验式不可求值',
  output_truncated: '输出被截断',
  unexpected_error: '内部异常',
  not_publishable: '题目本身不可发布',
}

function tagType(status: string) {
  if (status === 'succeeded') return 'success'
  if (status === 'needs_attention' || status === 'failed') return 'danger'
  if (status === 'pending_model_configuration' || status === 'queued') return 'warning'
  return 'info'
}

async function load() {
  loading.value = true
  try {
    const [s, g, d] = await Promise.all([
      api.get('/answers/queue-summary'),
      api.get('/answer-generations', { limit: 200 }),
      api.get('/answer-disputes'),
    ])
    summary.value = s
    gens.value = g.items || []
    disputes.value = d.items || []
  } catch (e: any) {
    ElMessage.error(e?.message || String(e))
  } finally { loading.value = false }
}

async function enqueue() {
  busy.value = true
  try {
    const r: any = await api.post('/answers/generate-batch', { trigger: 'import_batch' })
    ElMessage.success(`已入队 ${r.enqueued} 题（新建 ${r.created}，复用 ${r.reused}）`)
    await load()
  } catch (e: any) { ElMessage.error(e?.message || String(e)) }
  finally { busy.value = false }
}

function openResolve(d: any) {
  active.value = d
  form.value = {
    resolution: 'corrected',
    resolution_note: '',
    answer_payload: '{\n  "key": "A"\n}',
  }
  dialogVisible.value = true
}

async function submitResolve() {
  if (!active.value) return
  const body: any = {
    resolution: form.value.resolution,
    resolution_note: form.value.resolution_note || '人工复核处理',
  }
  if (form.value.resolution === 'corrected') {
    try {
      body.answer_payload = JSON.parse(form.value.answer_payload)
    } catch {
      ElMessage.error('修正答案必须是合法 JSON 对象')
      return
    }
  }
  busy.value = true
  try {
    const r: any = await api.post(`/answer-disputes/${active.value.dispute_id}/resolve`, body)
    ElMessage.success(
      r.new_question_version
        ? `已生成题目版本 v${r.new_question_version}，重评 ${r.rescored_rows || 0} 条作答`
        : '复核已记录',
    )
    dialogVisible.value = false
    await load()
  } catch (e: any) { ElMessage.error(e?.message || String(e)) }
  finally { busy.value = false }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="card">
      <h3>AI 参考答案政策</h3>
      <div class="small" style="line-height: 1.9">
        往年试卷与复习资料大多没有答案。无答案题目会自动进入 DeepSeek 解答队列，
        生成<b>参考答案、解析与评分要点</b>；通过<b>结构检查</b>与<b>可执行自动检查</b>
        （独立计算复算、评分要点分值核对、等价表达/其他解法声明、结果一致性）后，
        <b>直接进入可用题库</b>，用于出卷、评分、错题本与学情诊断，
        <b>不要求逐题人工确认</b>。<br />
        只有题干缺图缺公式、条件不足、结果矛盾等具体异常才进入待处理，其余继续自动处理。
        正式测验在学生开始作答前就固定参考答案与评分标准，同版本缓存复用；
        <b>答案生成请求不含任何学生作答</b>（下表每条都有请求指纹）。
      </div>
      <div class="hint" style="margin-top: 10px">
        <b>AI参考答案</b> 由模型依据题干与课程资料生成并通过自动检查，
        <b>不是教师或原试卷提供的标准答案</b>。界面会标注来源、模型、生成时间与答案版本；
        有疑问请用「答案有疑问」入口复核，复核会生成答案新版本、重评相关作答并更新诊断。
      </div>
    </div>

    <div class="grid four" v-if="summary">
      <div class="card">
        <div class="small muted">待解答题目（最新版本）</div>
        <div style="font-size: 24px; font-weight: 600">{{ summary.answerless_questions }}</div>
      </div>
      <div class="card">
        <div class="small muted">生成记录</div>
        <div style="font-size: 24px; font-weight: 600">{{ summary.total }}</div>
      </div>
      <div class="card">
        <div class="small muted">模型状态</div>
        <div style="font-size: 16px; font-weight: 600; margin-top: 6px">
          <el-tag :type="summary.model_configured ? 'success' : 'warning'" size="small">
            {{ summary.model_configured ? '已配置' : '待配置' }}
          </el-tag>
        </div>
      </div>
      <div class="card">
        <div class="small muted">人工确认</div>
        <div style="font-size: 16px; font-weight: 600; margin-top: 6px">
          <el-tag type="success" size="small">非必需</el-tag>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="tag-line" style="margin-bottom: 10px">
        <el-button type="primary" size="small" :loading="busy" @click="enqueue">
          批量入队（导入后生成）
        </el-button>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        <span class="small muted" style="align-self: center">
          也可在题目首次被选入试卷时按需生成；模型未配置时如实显示「待配置模型」，不生成假答案。
        </span>
      </div>

      <h3>解答队列（{{ gens.length }}）</h3>
      <el-table :data="gens" size="small" max-height="360" v-loading="loading" empty-text="暂无生成记录">
        <el-table-column prop="question_id" label="题目" min-width="170" show-overflow-tooltip />
        <el-table-column label="状态" width="170">
          <template #default="{ row }">
            <el-tag size="small" :type="tagType(row.status)">
              {{ statusLabel[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源标注" width="130" prop="label" />
        <el-table-column label="模型" width="140" prop="model" show-overflow-tooltip />
        <el-table-column label="答案版本" width="100">
          <template #default="{ row }">v{{ row.answer_version }} / 题 v{{ row.question_version }}</template>
        </el-table-column>
        <el-table-column label="生成时间" width="170" prop="generated_at" />
        <el-table-column label="含学生作答" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.student_answer_included ? 'danger' : 'success'">
              {{ row.student_answer_included ? '是（异常）' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="请求指纹" width="120">
          <template #default="{ row }">
            <span class="mono muted">{{ (row.request_fingerprint || '—').slice(0, 12) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="异常码" width="160">
          <template #default="{ row }">
            <span v-if="row.exception_code">
              {{ exceptionLabel[row.exception_code] || row.exception_code }}
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="card">
      <h3>答案有疑问（{{ disputes.length }}）</h3>
      <div v-if="!disputes.length" class="muted small">
        暂无疑问记录。答题页每道题都有「答案有疑问」入口。
      </div>
      <el-table v-else :data="disputes" size="small" max-height="280">
        <el-table-column prop="question_id" label="题目" min-width="170" show-overflow-tooltip />
        <el-table-column prop="reason" label="疑问" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'resolved' ? 'success' : 'warning'">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="新题版本" width="100">
          <template #default="{ row }">{{ row.new_question_version || '—' }}</template>
        </el-table-column>
        <el-table-column label="重评/诊断任务" width="140">
          <template #default="{ row }">
            {{ row.rescored_rows }} / {{ row.diagnosis_jobs_queued }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button size="small" :disabled="row.status === 'resolved' || row.status === 'rejected'"
              @click="openResolve(row)">复核</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" title="复核答案疑问" width="560px">
      <div class="small muted" style="margin-bottom: 10px">
        题目 {{ active?.question_id }} · 复核会生成答案新版本、
        重评相关作答并重新计算三层诊断与知识状态。
      </div>
      <el-form label-width="110px">
        <el-form-item label="处理方式">
          <el-select v-model="form.resolution" style="width: 100%">
            <el-option label="修正答案并重评" value="corrected" />
            <el-option label="重新生成 AI 答案" value="regenerated" />
            <el-option label="复核后确认原答案" value="confirmed" />
            <el-option label="疑问不成立" value="rejected" />
          </el-select>
        </el-form-item>
        <el-form-item label="复核说明">
          <el-input v-model="form.resolution_note" type="textarea" :rows="2"
            placeholder="写明核对依据，例如对照课件第几页或独立计算" />
        </el-form-item>
        <el-form-item v-if="form.resolution === 'corrected'" label="修正答案 JSON">
          <el-input v-model="form.answer_payload" type="textarea" :rows="5" class="mono" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submitResolve">提交复核</el-button>
      </template>
    </el-dialog>
  </div>
</template>
