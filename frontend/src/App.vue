<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Files, House, Reading, SwitchButton, Tickets, TrendCharts, UserFilled, VideoPlay } from '@element-plus/icons-vue'
import { auth } from './auth'

const route = useRoute()
const router = useRouter()
const mobileOpen = ref(false)
const title = computed(() => String(route.meta.title || ''))
const publicPage = computed(() => Boolean(route.meta.public))
const immersive = computed(() => Boolean(route.meta.immersive))
const questionBankPage = computed(() => route.name === 'teacher-questions')
const teacherDashboardPage = computed(() => route.name === 'teacher-home')
const studentFeedbackPage = computed(() => route.name === 'student-feedback')
const studentTaskPage = computed(() => route.name === 'student-home')
const teacherAnalysisPage = computed(() => route.name === 'teacher-analysis')
const teacherResourcePage = computed(() => ['teacher-resources', 'teacher-online'].includes(String(route.name || '')))
const teacherNav = [
  { path: '/teacher', label: '教学总览', icon: House, exact: true },
  { path: '/teacher/questions', label: '题库与组卷', icon: Tickets },
  { path: '/teacher/analysis', label: '学情诊断', icon: TrendCharts },
  { path: '/teacher/resources', label: '教学资源', icon: Files },
  { path: '/teacher/online', label: '在线资源', icon: VideoPlay },
]
const studentNav = [
  { path: '/student', label: '我的任务', icon: Reading, exact: true },
  { path: '/student/feedback', label: '学习反馈', icon: TrendCharts },
  { path: '/student/online', label: '在线资源', icon: VideoPlay },
]
const navs = computed(() => auth.isTeacher.value ? teacherNav : studentNav)
const roleLabel = computed(() => auth.isTeacher.value ? '教师端' : '学生端')

function active(item: any) {
  return item.exact ? route.path === item.path : route.path.startsWith(item.path)
}
async function logout() {
  await auth.logout()
  await router.replace('/login')
}
onMounted(() => void auth.ensure())
</script>

<template>
  <router-view v-if="publicPage" />
  <div v-else-if="immersive" class="immersive-shell"><router-view /></div>
  <div v-else class="role-shell" :class="[auth.role.value || 'loading', { 'question-bank-shell': questionBankPage, 'teacher-dashboard-shell': teacherDashboardPage, 'student-feedback-shell': studentFeedbackPage, 'student-task-shell': studentTaskPage, 'teacher-resource-shell': teacherResourcePage }]">
    <button v-if="mobileOpen" class="nav-scrim" aria-label="关闭导航" @click="mobileOpen=false" />
    <aside class="role-side" :class="{open:mobileOpen}">
      <router-link :to="auth.isTeacher.value?'/teacher':'/student'" class="brand">
        <span class="brand-mark" aria-label="ML">ML</span><span><b>机器学习教学平台</b><small>{{ roleLabel }}</small></span>
      </router-link>
      <nav>
        <router-link v-for="item in navs" :key="item.path" :to="item.path"
          :class="{active:active(item)}" @click="mobileOpen=false">
          <el-icon><component :is="item.icon" /></el-icon><span>{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="side-foot">
        <div class="role-chip"><span></span>{{ roleLabel }} · {{ auth.principal.value?.display_name }}</div>
        <button @click="logout"><el-icon><SwitchButton /></el-icon>退出登录</button>
      </div>
    </aside>
    <div class="role-main">
      <header v-if="!questionBankPage && !teacherDashboardPage" class="role-topbar">
        <button class="mobile-menu" @click="mobileOpen=true">☰</button>
        <div><span>{{ studentTaskPage ? '学生端 › 我的任务' : roleLabel }}</span><h1><el-icon v-if="studentTaskPage" class="task-title-icon"><Tickets /></el-icon>{{ title }}<small v-if="studentTaskPage" class="task-title-note">查看与完成学习任务，掌握学习进度</small><small v-if="teacherAnalysisPage" class="analysis-title-note">从答卷表现到教学行动</small></h1></div>
        <div class="identity"><span v-if="studentTaskPage" class="task-avatar"><el-icon><UserFilled /></el-icon></span><b>{{ auth.principal.value?.display_name }}</b></div>
      </header>
      <button v-else-if="questionBankPage" class="mobile-menu bank-mobile-menu" aria-label="打开导航" @click="mobileOpen=true">☰</button>
      <button v-else class="mobile-menu bank-mobile-menu" aria-label="打开导航" @click="mobileOpen=true">☰</button>
      <main class="role-content"><router-view /></main>
    </div>
  </div>
</template>

<style scoped>
.immersive-shell{min-height:100vh}
.role-shell{min-height:100vh;background:var(--app-bg);color:var(--app-text);--el-color-primary:var(--app-primary);--el-color-primary-light-3:#7289bc;--el-color-primary-light-5:#a4b4d3;--el-color-primary-light-8:#e1e9f5;--el-color-primary-light-9:#f0f4fa;--el-border-radius-base:10px}
.role-side{position:fixed;z-index:20;inset:0 auto 0 0;width:220px;padding:24px 14px 18px;display:flex;flex-direction:column;justify-content:flex-start;background:#f8faff;border-right:1px solid var(--app-border)}
.brand{display:flex;flex:none;align-items:center;gap:12px;padding:0 6px;text-decoration:none;color:#17243d;line-height:1.3}
.brand-mark{display:grid;flex:none;place-items:center;width:38px;height:38px;border-radius:10px;background:var(--app-primary);color:#fff;font-size:15px;font-weight:800;letter-spacing:-.04em;box-shadow:0 5px 14px rgb(66 99 173 / 15%)}
.brand b,.brand small{display:block}
.brand b{font-size:16px;font-weight:var(--font-weight-bold);white-space:nowrap}
.brand small{margin-top:3px;color:var(--text-muted);font-size:var(--font-size-min)}
.role-side nav{display:grid;gap:6px;margin-top:34px}
.role-side nav a{display:flex;min-height:46px;align-items:center;gap:12px;padding:0 13px;border-radius:10px;color:var(--text-secondary);text-decoration:none;font-size:15px;font-weight:var(--font-weight-medium);transition:background .15s ease,color .15s ease}
.role-side nav a:hover{background:#eef3fd;color:#244fbd}
.role-side nav a.active{background:#e9f0fa;color:var(--app-primary);font-weight:var(--font-weight-semibold)}
.role-side nav .el-icon{font-size:19px}
.side-foot{margin-top:auto;padding:16px 6px 0;border-top:1px solid #e4eaf4}
.role-chip{display:flex;align-items:center;gap:8px;margin:0 0 10px;padding:9px 10px;border-radius:8px;background:#f0f3f8;color:var(--text-secondary);font-size:14px}
.role-chip>span{width:7px;height:7px;border-radius:50%;background:#34a889;box-shadow:0 0 0 3px #dff4ed}
.side-foot p{margin:0 0 10px;color:var(--text-muted);font-size:var(--font-size-min)}
.side-foot button{display:flex;width:100%;min-height:42px;align-items:center;gap:8px;padding:5px 2px;border:0;background:none;color:var(--text-secondary);cursor:pointer;font:inherit;font-size:14px}
.side-foot button:hover{color:#315cf0}
.role-main{min-height:100vh;margin-left:220px}
.role-topbar{position:sticky;z-index:10;top:0;display:flex;height:77px;align-items:center;justify-content:space-between;padding:0 34px;border-bottom:1px solid var(--app-border);background:var(--app-bg)}
.teacher-resource-shell .role-topbar{background:#f6f8fe;backdrop-filter:none}
.role-topbar>div:first-of-type span{color:var(--app-primary);font-size:14px;font-weight:var(--font-weight-semibold);letter-spacing:.03em}
.role-topbar h1{margin:2px 0;color:var(--text-primary);font-size:var(--font-size-page);font-weight:var(--font-weight-bold);line-height:1.3}
.student-feedback-shell .role-topbar h1{font-size:24px;font-weight:800}
.identity{text-align:right}.identity b,.identity small{display:block}.identity b{font-size:14px}.identity small{margin-top:3px;color:var(--text-muted);font-size:14px}
.role-content{max-width:1440px;margin:0 auto;padding:22px 32px 54px}
.question-bank-shell .role-content{max-width:none;padding:18px 22px 48px}
.mobile-menu{display:none;border:0;background:none;color:#17243d;font-size:23px}
.bank-mobile-menu{position:fixed;z-index:12;top:14px;left:14px;width:40px;height:40px;border:1px solid #dce4f2;border-radius:10px;background:#fff;box-shadow:0 6px 18px rgb(32 55 94 / 12%)}
.nav-scrim{display:none}
@media(max-width:820px){.role-side{width:min(82vw,300px);transform:translateX(-102%);transition:.2s}.role-side.open{transform:translateX(0)}.role-main{margin-left:0}.mobile-menu{display:block}.role-topbar{padding:0 18px}.role-content,.question-bank-shell .role-content{padding:70px 15px 40px}.role-shell:not(.question-bank-shell):not(.teacher-dashboard-shell) .role-content{padding-top:20px}.nav-scrim{display:block;position:fixed;z-index:19;inset:0;border:0;background:rgba(11,28,40,.4)}}
.student-task-shell{height:100dvh;min-height:0;overflow:hidden;background:#f5f8fe}
.student-task-shell .role-side{isolation:isolate;overflow:hidden;width:200px;padding:22px 10px 16px;background:linear-gradient(165deg,#f9fbff 0%,#f1f6ff 66%,#edf4ff 100%);border-color:#e5eaf3}
.student-task-shell .role-side::before,.student-task-shell .role-side::after{position:absolute;z-index:0;pointer-events:none;border-radius:50%;content:''}
.student-task-shell .role-side::before{left:-75px;bottom:62px;width:335px;height:150px;background:rgb(255 255 255 / 35%);transform:rotate(15deg)}
.student-task-shell .role-side::after{left:-100px;bottom:-20px;width:370px;height:125px;background:rgb(221 235 255 / 33%);transform:rotate(-9deg)}
.student-task-shell .role-side>*{position:relative;z-index:1}
.student-task-shell .role-side .brand{gap:9px;padding:0 6px}
.student-task-shell .role-side .brand b{font-size:14px}
.student-task-shell .role-side nav{margin-top:28px}
.student-task-shell .role-side nav a{min-height:42px}
.student-task-shell .role-side nav a.active{background:#dfeaff;color:#2058c2}
.student-task-shell .side-foot{border-color:#dfe8f6}
.student-task-shell .role-main{display:flex;flex-direction:column;height:100%;min-height:0;margin-left:200px}
.student-task-shell .role-topbar{position:relative;flex:none;height:78px;padding:0 20px;background:#f5f8fe;border-color:#e5eaf3}
.student-task-shell .role-content{flex:1;width:100%;max-width:none;min-height:0;overflow:hidden;padding:15px 20px 18px}
.student-task-shell .role-topbar h1{display:flex;align-items:center;gap:10px;color:var(--text-primary);font-size:var(--font-size-page);font-weight:var(--font-weight-bold)}
.student-task-shell .task-title-icon{display:grid;place-items:center;width:31px;height:31px;border-radius:8px;background:#e7efff;color:var(--app-primary);font-size:22px}
.student-task-shell .task-title-note{margin-left:8px;color:var(--text-muted);font-size:var(--font-size-min);font-weight:var(--font-weight-normal)}
.student-task-shell .identity{display:flex;align-items:center;gap:8px}
.student-task-shell .task-avatar{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:#dfeaff;color:#5e87d4;font-size:17px}
.analysis-title-note{margin-left:18px;padding-left:18px;border-left:1px solid var(--app-border);color:var(--text-secondary);font-size:14px;font-weight:var(--font-weight-normal)}
@media(max-width:1120px){.student-task-shell{height:auto;min-height:100vh;overflow:visible}.student-task-shell .role-main{height:auto;min-height:100vh}.student-task-shell .role-content{flex:none;overflow:visible;padding-bottom:30px}}
@media(max-width:820px){.student-task-shell .role-side{width:min(82vw,300px)}.student-task-shell .role-main{margin-left:0}.student-task-shell .role-topbar{padding:0 16px}}
@media(max-width:820px){.student-task-shell .task-title-note{display:none}.student-task-shell .role-topbar h1{font-size:21px}}
@media(max-width:820px){.analysis-title-note{display:none}}
</style>
