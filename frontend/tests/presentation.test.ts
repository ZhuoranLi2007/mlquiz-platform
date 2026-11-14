import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  diagnosisView, formatAnswer, noTaskDescription, pickNextAction,
  statusLabel, studentIdentityLabel, friendlyQuizCode,
} from '../src/utils/presentation.ts'
import { compatibilityRedirect } from '../src/utils/navigation.ts'

test('下一步行动遵循继续答卷、到期复测、计划、薄弱点的固定优先级', () => {
  const full = {
    activeAttempt: { attempt_id: 'attempt-1', paper_title: '线性回归练习' },
    dueRetests: [{ task_id: 'r1', kp_id: 'kp-1', kp_name: '梯度下降' }],
    todayPlans: [{ task_id: 'p1', title: '复习 PCA', status: 'pending' }],
    weakStates: [{ kp_id: 'kp-2', kp_name: '支持向量机', mastery: 0.2 }],
    hasMaterials: true,
  }
  assert.equal(pickNextAction(full).kind, 'continue')
  assert.equal(pickNextAction({ ...full, activeAttempt: null }).kind, 'retest')
  assert.equal(pickNextAction({ ...full, activeAttempt: null, dueRetests: [] }).kind, 'plan')
  assert.equal(pickNextAction({
    ...full, activeAttempt: null, dueRetests: [],
    todayPlans: [{ title: '已完成任务', status: 'completed' }],
  }).kind, 'weakness')
})

test('英文状态统一显示为中文', () => {
  assert.equal(statusLabel('in_progress'), '作答中')
  assert.equal(statusLabel('corrected'), '已订正')
  assert.equal(statusLabel('verified'), '已巩固')
})

test('诊断选择器隐藏内部试卷编号，保留课堂小测顺序', () => {
  assert.equal(friendlyQuizCode('paper_7f7aec21dd204609'), '')
  assert.equal(friendlyQuizCode('1.2'), '1.2')
})

test('答案格式化忽略提示和查看解析元数据', () => {
  assert.equal(formatAnswer({ key: 'B', __hints: 2, __solution_viewed: true }), 'B')
  assert.equal(formatAnswer({ keys: ['A', 'C'] }), 'A、C')
  assert.equal(formatAnswer({ value: false }), '错误')
  assert.equal(formatAnswer({}), '未作答')
})

test('诊断摘要提取中文知识点名称和建议', () => {
  const view = diagnosisView({
    status: 'completed',
    result: {
      summary: '需要加强模型评估',
      priority_kps: [{ kp_id: 'kp-1', kp_name: '交叉验证' }],
      recommendations: ['完成 5 道专项题'],
    },
  })
  assert.equal(view.status_label, '已完成')
  assert.deepEqual(view.weaknesses, ['交叉验证'])
  assert.deepEqual(view.suggestions, ['完成 5 道专项题'])
})

test('兼容路由保留原查询参数并强制目标标签', () => {
  assert.deepEqual(
    compatibilityRedirect('/practice', { kp: 'kp-1', count: '10' }, { tab: 'create' }),
    { path: '/practice', query: { kp: 'kp-1', count: '10', tab: 'create' } },
  )
  assert.deepEqual(
    compatibilityRedirect('/reports', { from: 'home' }, { tab: 'plan' }),
    { path: '/reports', query: { from: 'home', tab: 'plan' } },
  )
})

test('学生身份明确区分正式班和旧测试账号', () => {
  const official = {
    user_id: 'stu001', student_code: 'STU001',
    classroom_name: '2025秋机器学习', identity_scope: 'official' as const,
  }
  const demo = {
    user_id: 'student_demo1', student_code: 'DEMO001',
    classroom_name: '机器学习课程演示班', identity_scope: 'test' as const,
  }
  assert.equal(
    studentIdentityLabel(official),
    'STU001 · stu001 · 2025秋机器学习',
  )
  assert.equal(
    studentIdentityLabel(demo),
    'DEMO001 · student_demo1 · 测试数据范围',
  )
  assert.equal(
    noTaskDescription(official),
    'stu001（档案 STU001）当前没有待完成任务',
  )
})

test('教师诊断页只保留三种业务模式并加载当前版本结果', () => {
  const view = readFileSync(new URL('../src/views/TeacherAnalysisView.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(view, /题目层次/)
  assert.doesNotMatch(view, /classroom-chapter-diagnosis-v3/)
  assert.match(view, /grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/)
  for (const label of ['单人单卷', '单人多卷', '多人单卷']) assert.match(view, new RegExp(label))
})

test('三种诊断的分析栏不再用运行后占位文案代替结论', () => {
  for (const file of [
    '../src/components/SingleAttemptWorkbench.vue',
    '../src/components/MultiAttemptWorkbench.vue',
    '../src/components/ClassAssessmentWorkbench.vue',
  ]) {
    const source = readFileSync(new URL(file, import.meta.url), 'utf8')
    assert.doesNotMatch(source, /运行诊断后生成/)
    assert.match(source, /综合结论/)
    assert.match(source, /推荐学习资源/)
  }
})

test('教师首页保持四项统计并把发布表单收进快捷弹窗', () => {
  const source = readFileSync(new URL('../src/views/TeacherDashboardView.vue', import.meta.url), 'utf8')
  const analysis = readFileSync(new URL('../src/views/TeacherAnalysisView.vue', import.meta.url), 'utf8')
  for (const label of ['学生人数', '历史小测', '已发布任务', '待提交答卷']) {
    assert.match(source, new RegExp(label))
  }
  for (const removed of ['从课堂证据出发，安排教学与测验', '正式重评分', '发布一份试卷']) {
    assert.doesNotMatch(source, new RegExp(removed))
  }
  assert.match(source, /publishedReleases\.value\.slice\(0, 5\)/)
  assert.match(source, /发布已有试卷/)
  assert.match(source, /查看班级诊断/)
  assert.match(source, /单卷诊断/)
  assert.match(analysis, /route\.query\.scope/)
  assert.match(analysis, /route\.query\.assessment_version_id/)
})

test('登录页选择身份即登录并保留手动登录降级', () => {
  const source = readFileSync(new URL('../src/views/LoginView.vue', import.meta.url), 'utf8')
  const auth = readFileSync(new URL('../src/auth.ts', import.meta.url), 'utf8')
  const client = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8')
  assert.match(source, /\/auth\/quick-login-identities/)
  assert.match(source, /教师账号/)
  assert.match(source, /学生账号/)
  assert.match(source, /手动输入其他账号/)
  assert.match(source, /await auth\.quickLogin\(account\.user_id\)/)
  assert.match(source, /v-if="manualMode"/)
  assert.match(source, /type="password"/)
  assert.match(source, /show-password/)
  assert.match(auth, /setTabToken\(result\.session_token\)/)
  assert.match(client, /window\.sessionStorage/)
  assert.match(client, /config\.headers\.Authorization/)
})

test('学生学习反馈以错题和复测闭环为核心且不展示技术文案', () => {
  const source = readFileSync(new URL('../src/views/StudentFeedbackView.vue', import.meta.url), 'utf8')
  const router = readFileSync(new URL('../src/router/index.ts', import.meta.url), 'utf8')
  assert.match(router, /student\/feedback[\s\S]*StudentFeedbackView\.vue/)
  for (const label of ['选择试卷', '本次成绩', '班级排名', '错题数量', '针对性复测', '学情分析', '薄弱概念', '学习建议', '推荐学习资源']) {
    assert.match(source, new RegExp(label))
  }
  assert.match(source, /grid-template-columns:minmax\(0,7fr\) minmax\(300px,3fr\)/)
  assert.doesNotMatch(source, /MY LEARNING DESK|correct=NULL|正式指标排除|paper_id|diagnosis_id/)
})

test('学生端侧边栏复用教师在线资源页且保持角色路由隔离', () => {
  const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  const router = readFileSync(new URL('../src/router/index.ts', import.meta.url), 'utf8')
  assert.match(app, /path: '\/student\/online', label: '在线资源', icon: VideoPlay/)
  assert.match(router, /path: '\/student\/online'[\s\S]*name: 'student-online'[\s\S]*OnlineResourcesView\.vue'[\s\S]*roles: \['student'\]/)
  assert.match(router, /path: '\/teacher\/online'[\s\S]*name: 'teacher-online'[\s\S]*OnlineResourcesView\.vue'[\s\S]*roles: \['teacher'\]/)
  assert.match(router, /path: '\/online', redirect: '\/teacher\/online'/)
})

test('资源页面复用共享标题并从筛选工具栏直接开始', () => {
  const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  const materials = readFileSync(new URL('../src/views/MaterialsView.vue', import.meta.url), 'utf8')
  const online = readFileSync(new URL('../src/views/OnlineResourcesView.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(materials, /个人资料|<h1>我的资源<\/h1>/)
  assert.doesNotMatch(online, /外部资源|<h1>在线资源<\/h1>/)
  assert.match(materials, /<div class="toolbar">[\s\S]*导入资料/)
  assert.match(online, /<div class="toolbar">[\s\S]*平台筛选/)
  assert.match(materials, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/)
  assert.match(online, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/)
  assert.match(app, /teacher-resource-shell[\s\S]*role-topbar\{background:#f6f8fe/)
})

test('完整错题卡支持历史原题选项并用选项键展示多选答案', () => {
  const source = readFileSync(new URL('../src/components/AttemptEvidenceTable.vue', import.meta.url), 'utf8')
  assert.match(source, /item\?\.source_options/)
  assert.match(source, /selectedKeys\(item\)\.join/)
  assert.match(source, /is-wrong-picked/)
  assert.match(source, /is-correct/)
  assert.match(source, /<details v-if="rubricRows\(item\)\.length" class="learning-detail">/)
  assert.match(source, /<details v-if="explanationText\(item\)" class="learning-detail">/)
  assert.doesNotMatch(source, /<details[^>]+open/)
  assert.match(source, /student-choice/)
  assert.match(source, /correct-choice/)
})
