import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { auth } from '../auth'

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('../views/LoginView.vue'),
    meta: { title: '登录', public: true } },
  { path: '/', redirect: '/teacher' },
  { path: '/teacher', name: 'teacher-home', component: () => import('../views/TeacherDashboardView.vue'),
    meta: { title: '教学总览', roles: ['teacher'] } },
  { path: '/teacher/questions', name: 'teacher-questions', component: () => import('../views/TeacherWorkspaceView.vue'),
    meta: { title: '题库与组卷', roles: ['teacher'] } },
  { path: '/teacher/papers/:paperId', name: 'teacher-paper-builder', component: () => import('../views/PaperBuilderView.vue'),
    meta: { title: '试卷预览与发布', roles: ['teacher'] } },
  { path: '/teacher/analysis', name: 'teacher-analysis', component: () => import('../views/TeacherAnalysisView.vue'),
    meta: { title: '学情诊断', roles: ['teacher'] } },
  { path: '/teacher/resources', name: 'teacher-resources', component: () => import('../views/MaterialsView.vue'),
    meta: { title: '教学资源', roles: ['teacher'] } },
  { path: '/teacher/online', name: 'teacher-online', component: () => import('../views/OnlineResourcesView.vue'),
    meta: { title: '在线资源', roles: ['teacher'] } },
  { path: '/student', name: 'student-home', component: () => import('../views/StudentPortalView.vue'),
    meta: { title: '我的任务', roles: ['student'] } },
  { path: '/student/feedback', name: 'student-feedback', component: () => import('../views/StudentFeedbackView.vue'),
    meta: { title: '学习反馈', roles: ['student'] } },
  { path: '/student/online', name: 'student-online', component: () => import('../views/OnlineResourcesView.vue'),
    meta: { title: '在线资源', roles: ['student'] } },
  { path: '/exam/:attemptId', name: 'exam', component: () => import('../views/ExamView.vue'),
    meta: { title: '在线答题', roles: ['student', 'teacher'], immersive: true } },

  // Old bookmarks land in the teacher-oriented structure.
  { path: '/compose', redirect: '/teacher/questions' },
  { path: '/bank', redirect: '/teacher/questions' },
  { path: '/reports', redirect: '/teacher/analysis' },
  { path: '/practice', redirect: '/teacher/questions' },
  { path: '/materials', redirect: '/teacher/resources' },
  { path: '/online', redirect: '/teacher/online' },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true
  const principal = await auth.ensure()
  if (!principal) return { path: '/login', query: { redirect: to.fullPath } }
  const roles = (to.meta.roles || []) as string[]
  if (roles.length && !roles.includes(principal.role)) {
    return principal.role === 'teacher' ? '/teacher' : '/student'
  }
  if (to.path === '/') return principal.role === 'teacher' ? '/teacher' : '/student'
  return true
})

export default router
