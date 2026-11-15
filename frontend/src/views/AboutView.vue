<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import StatusBadge from '../components/StatusBadge.vue'
import TechnicalDetails from '../components/TechnicalDetails.vue'
import { friendlyError } from '../utils/presentation'

const facts = ref<any>(null)
const error = ref('')
const skills = ref<any[]>([])
const graphs = ref<any>(null)
const skillRuns = ref<any[]>([])

const SKILL_LABELS: Record<string, string> = {
  diagnose_question: '题目层诊断',
  diagnose_paper: '试卷层诊断',
  diagnose_history: '历次试卷诊断',
  recommend_resources: '学习内容推荐',
  generate_questions: 'AI 出新题',
  verify_learning: '复测与掌握判定',
  build_study_plan: '学习计划',
  tutor_answer: 'AI 助手答疑',
  solve_answer: 'AI 参考答案',
}

function runCount(name: string) {
  return skillRuns.value.filter((item) => item.skill_name === name).length
}

onMounted(async () => {
  try {
    facts.value = await api.get('/meta/about')
  } catch (e) {
    error.value = friendlyError(e, '运行信息读取失败')
  }
  try {
    const [skillData, graphData, runs] = await Promise.all([
      api.get('/skills'),
      api.get('/graphs'),
      api.get('/skill-runs', { limit: 200 }),
    ])
    skills.value = skillData.items || []
    graphs.value = graphData
    skillRuns.value = runs.items || []
  } catch {
    // 技术信息读取失败不影响页面主体
  }
})
</script>

<template>
  <main class="about-page">
    <header class="about-hero">
      <div>
        <p class="eyebrow">关于平台</p>
        <h1>让每次练习都知道下一步</h1>
        <p>
          这是一个面向机器学习课程复习的本地学习平台。它把课件、试卷和答题记录组织在一起，
          帮助你发现薄弱考点、安排练习并追踪进步。
        </p>
      </div>
      <div class="version-chip">
        <span>当前版本</span>
        <strong>{{ facts?.version || '读取中' }}</strong>
      </div>
    </header>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

    <section class="about-grid">
      <article class="about-card" id="statement">
        <div class="card-icon ai" aria-hidden="true">AI</div>
        <div>
          <h2>AI 使用声明</h2>
          <p>
            参考答案、题目解析、主观题评分和学习建议可能由 AI 辅助生成，仅供学习与自测参考，
            不等同于教师或课程官方答案。
          </p>
          <p>
            AI 可能误解题意、遗漏条件，或无法完整识别图片和公式。遇到疑问时，请优先核对原始试卷、课件和教材，
            并通过“答案有疑问”提交反馈。
          </p>
        </div>
      </article>

      <article class="about-card">
        <div class="card-icon data" aria-hidden="true">本地</div>
        <div>
          <h2>数据与隐私</h2>
          <p>
            平台采用单用户本地模式，课程资料、答卷与学习记录默认保存在这台电脑上。
            请只导入自己有权使用的资料，不要公开分发受版权保护的完整课件或试卷。
          </p>
          <p>
            使用 AI 功能时，完成该次请求所需的题目文本、相关资料片段或作答内容可能会发送到已配置的模型服务；
            页面不会读取或展示模型密钥。
          </p>
          <div class="model-state" v-if="facts">
            <StatusBadge
              :status="facts.model?.configured ? 'ready' : 'pending'"
              :label="facts.model?.configured ? 'AI 服务已配置' : 'AI 服务未配置'"
            />
            <span v-if="!facts.model?.configured">资料管理、答题和确定性评分仍可正常使用。</span>
          </div>
        </div>
      </article>

      <article class="about-card compact-card">
        <div>
          <h2>诊断结果如何理解</h2>
          <p>
            学情诊断来自平台内的答题、提示使用和复测证据。题量、难度和评分方式都会影响结果，
            “待巩固”等状态用于安排复习，不是对学习能力的绝对判断。
          </p>
        </div>
      </article>

      <article class="about-card compact-card">
        <div>
          <h2>项目与版本</h2>
          <dl class="version-list">
            <div><dt>平台名称</dt><dd>机器学习学情诊断平台</dd></div>
            <div><dt>版本</dt><dd>{{ facts?.version || '读取中' }}</dd></div>
            <div><dt>构建日期</dt><dd>{{ facts?.build_date || '—' }}</dd></div>
            <div><dt>项目性质</dt><dd>{{ facts?.project_nature || '课程实践项目' }}</dd></div>
          </dl>
        </div>
      </article>
    </section>

    <section v-if="skills.length" class="capability-section">
      <header class="capability-head">
        <div>
          <p class="eyebrow">课程要求 §四 · 做成 skill</p>
          <h2>诊断、推荐、出新题、复测都是可运行的 skill</h2>
          <p class="subtle">
            每个 skill 都有 SKILL.md、输入输出 schema 和真实执行函数，由静态注册表加载，
            LangGraph 节点直接调用；统一返回结果、证据引用、不确定项与下一步动作。
          </p>
        </div>
        <span class="count-pill">{{ skills.length }} 个 skill · {{ graphs?.checkpointer || 'LangGraph' }}</span>
      </header>

      <div class="skill-grid">
        <article v-for="skill in skills" :key="skill.name" class="skill-card">
          <div class="skill-top">
            <code>{{ skill.name }}</code>
            <el-tag v-if="skill.requires_model" size="small" type="warning" effect="plain">调用模型</el-tag>
            <el-tag v-else size="small" type="success" effect="plain">确定性规则</el-tag>
          </div>
          <h4>{{ SKILL_LABELS[skill.name] || skill.description || skill.name }}</h4>
          <p class="skill-desc">{{ skill.description }}</p>
          <div class="skill-meta">
            <span>{{ skill.version }}</span>
            <span>{{ runCount(skill.name) ? `最近运行 ${runCount(skill.name)} 次` : '本次未运行' }}</span>
          </div>
        </article>
      </div>

      <div v-if="graphs?.graphs" class="graph-grid">
        <article v-for="(graph, name) in graphs.graphs" :key="name" class="graph-card">
          <div class="graph-top">
            <code>{{ name }}</code>
            <span class="muted small">{{ graph.compiled_type }}</span>
          </div>
          <div class="node-chips">
            <span v-for="node in graph.nodes.filter((n: string) => !n.startsWith('__'))"
              :key="node" class="node-chip">{{ node }}</span>
          </div>
        </article>
      </div>
      <p class="subtle">
        检查点：{{ graphs?.checkpointer || '—' }}；thread_id / job_id / user_id / run_id 与数据库记录关联，
        图负责编排，正式成绩与答卷仍以数据库为准。
      </p>
    </section>

    <TechnicalDetails v-if="facts" title="运行配置与工程信息">
      <div class="technical-grid">
        <div><span>资料位置</span><strong>{{ facts.storage?.materials }}</strong></div>
        <div><span>数据库</span><strong>{{ facts.storage?.database }}</strong></div>
        <div><span>数据库路径</span><strong>{{ facts.storage?.database_path }}</strong></div>
        <div><span>备份导出</span><strong>{{ facts.storage?.exports }}</strong></div>
        <div><span>模型提供方</span><strong>{{ facts.model?.configured ? facts.model.provider : '未配置' }}</strong></div>
        <div><span>模型名称</span><strong>{{ facts.model?.configured ? facts.model.model : '—' }}</strong></div>
        <div><span>模型接口</span><strong>{{ facts.model?.configured ? facts.model.base_url : '—' }}</strong></div>
        <div><span>发送内容</span><strong>{{ facts.sent_to_model }}</strong></div>
      </div>
      <h3>会调用外部 AI 的操作</h3>
      <ul><li v-for="item in facts.external_calls || []" :key="item">{{ item }}</li></ul>
      <h3>当前数据规模</h3>
      <div class="count-list">
        <span>资料 {{ facts.counts?.documents || 0 }}</span>
        <span>题目 {{ facts.counts?.questions || 0 }}</span>
        <span>试卷 {{ facts.counts?.papers || 0 }}</span>
        <span>答卷 {{ facts.counts?.attempts || 0 }}</span>
        <span>考点 {{ facts.counts?.knowledge_points || 0 }}</span>
        <span>模型调用 {{ facts.counts?.model_calls || 0 }}</span>
      </div>
      <p>{{ facts.retention?.model_calls }}；{{ facts.retention?.chat }}；{{ facts.retention?.delete }}</p>
      <p class="subtle">
        使用 Vue 3、Element Plus、ECharts、KaTeX、FastAPI、SQLAlchemy、LangGraph、ReportLab 等组件，
        并遵循各自许可证。
      </p>
    </TechnicalDetails>
  </main>
</template>

<style scoped>
.about-page { max-width: 1040px; margin: 0 auto; padding-bottom: 36px; }
.about-hero { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: 40px; margin-bottom: 22px; padding: 34px 36px; border: 1px solid #dfe3f6; border-radius: 20px; background: linear-gradient(135deg, #eef2ff, #fff 58%, #ecfdf5); }
.eyebrow { margin: 0 0 6px; color: #4f46e5; font-size: 14px; font-weight: 800; letter-spacing: .04em; }
.about-hero h1 { margin: 0; color: #1d2540; font-size: clamp(28px, 4vw, 40px); line-height: 1.2; }
.about-hero p:not(.eyebrow) { max-width: 690px; margin: 14px 0 0; color: #596174; font-size: 16px; line-height: 1.8; }
.version-chip { display: flex; flex-direction: column; gap: 4px; min-width: 120px; padding: 15px 18px; border: 1px solid #d9ddf3; border-radius: 14px; background: rgb(255 255 255 / 75%); }
.version-chip span { color: #737b8d; font-size: 14px; }
.version-chip strong { color: #3730a3; font-size: 18px; }
.error-banner { margin-bottom: 16px; padding: 12px 15px; border-radius: 10px; background: #fef2f2; color: #991b1b; }
.about-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.about-card { display: flex; gap: 16px; padding: 24px; border: 1px solid #e3e6ee; border-radius: 16px; background: #fff; }
.about-card h2 { margin: 0 0 10px; color: #242c42; font-size: 19px; }
.about-card p { margin: 0 0 10px; color: #5f687a; line-height: 1.75; }
.about-card p:last-child { margin-bottom: 0; }
.card-icon { display: grid; place-items: center; width: 48px; height: 48px; flex: none; border-radius: 14px; font-size: 13px; font-weight: 800; }
.card-icon.ai { background: #eef2ff; color: #4338ca; }
.card-icon.data { background: #ecfdf5; color: #047857; }
.subtle { color: #7b8290 !important; font-size: 14px; }
.model-state { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 14px; color: #6b7280; font-size: 14px; }
.compact-card { min-height: 170px; }
.version-list { display: grid; gap: 9px; margin: 0; }
.version-list div { display: grid; grid-template-columns: 88px minmax(0, 1fr); gap: 12px; }
.version-list dt { color: #7b8290; }
.version-list dd { margin: 0; color: #31394b; font-weight: 600; }
.technical-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 20px; }
.technical-grid > div { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.technical-grid span { color: #7b8290; font-size: 14px; }
.technical-grid strong { overflow-wrap: anywhere; color: #4b5563; font-size: 14px; }
.count-list { display: flex; flex-wrap: wrap; gap: 8px; }
.count-list span { padding: 6px 10px; border-radius: 8px; background: #f3f4f6; color: #4b5563; font-size: 14px; }

.capability-section { margin: 22px 0; padding: 24px 26px; border: 1px solid #e3e6ee; border-radius: 18px; background: #fff; }
.capability-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; flex-wrap: wrap; margin-bottom: 16px; }
.capability-head h2 { margin: 0 0 8px; font-size: 20px; }
.capability-head p { max-width: 720px; margin: 0; }
.count-pill { flex: none; padding: 7px 12px; border-radius: 999px; background: #eef2ff; color: #4338ca; font-size: 14px; font-weight: 600; }
.skill-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.skill-card { padding: 14px; border: 1px solid #e3e6ee; border-radius: 12px; background: #fbfcfe; }
.skill-top { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.skill-top code { font-size: 14px; color: #4338ca; background: #eef2ff; padding: 2px 7px; border-radius: 6px; }
.skill-card h4 { margin: 0 0 4px; font-size: 14px; }
.skill-desc { margin: 0 0 8px; color: #5f687a; font-size: 14px; line-height: 1.6; }
.skill-meta { display: flex; justify-content: space-between; gap: 8px; color: #7b8290; font-size: 14px; }
.graph-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin: 16px 0 10px; }
.graph-card { padding: 14px; border: 1px solid #e3e6ee; border-radius: 12px; }
.graph-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
.graph-top code { font-size: 14px; font-weight: 600; color: #1d2540; }
.node-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.node-chip { padding: 3px 8px; border-radius: 6px; background: #f3f4f6; color: #4b5563; font-size: 14px; }

@media (max-width: 720px) {
  .about-hero { grid-template-columns: 1fr; align-items: start; gap: 22px; padding: 26px 20px; }
  .version-chip { width: 100%; box-sizing: border-box; }
  .about-grid { grid-template-columns: 1fr; }
  .about-card { padding: 20px 16px; }
  .technical-grid { grid-template-columns: 1fr; }
}
</style>
