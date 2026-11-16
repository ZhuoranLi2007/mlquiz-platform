<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { auth } from '../auth'
import { api } from '../api/client'
import { friendlyError } from '../utils/presentation'

type QuickAccount = {
  user_id: string
  label: string
  role: 'teacher' | 'student'
}

const router = useRouter()
const userId = ref('')
const password = ref('')
const busy = ref(false)
const quickLoading = ref(true)
const quickEnabled = ref(true)
const quickAccounts = ref<QuickAccount[]>([])
const selectedQuick = ref('')
const manualMode = ref(false)
const quickError = ref('')
const teacherAccounts = computed(() => quickAccounts.value.filter((row) => row.role === 'teacher'))
const studentAccounts = computed(() => quickAccounts.value.filter((row) => row.role === 'student'))

async function chooseQuickAccount(value: string) {
  const account = quickAccounts.value.find((row) => row.user_id === value)
  if (!account) return
  busy.value = true
  quickError.value = ''
  try {
    const principal = await auth.quickLogin(account.user_id)
    await router.replace(principal.role === 'teacher' ? '/teacher' : '/student')
  } catch (error) {
    quickError.value = friendlyError(error, '登录失败，请重新选择账号')
    selectedQuick.value = ''
  } finally {
    busy.value = false
  }
}

async function loadIdentities() {
  quickLoading.value = true
  quickError.value = ''
  try {
    const result = await api.get<{ enabled: boolean; accounts: QuickAccount[] }>('/auth/quick-login-identities')
    quickEnabled.value = result.enabled === true
    quickAccounts.value = Array.isArray(result.accounts) ? result.accounts : []
    manualMode.value = !quickEnabled.value || quickAccounts.value.length === 0
  } catch {
    quickEnabled.value = false
    quickAccounts.value = []
    manualMode.value = true
    quickError.value = '账号列表加载失败，请重试或手动登录。'
  } finally {
    quickLoading.value = false
  }
}
onMounted(loadIdentities)

async function submit() {
  busy.value = true
  try {
    const principal = await auth.login(userId.value.trim(), password.value)
    await router.replace(principal.role === 'teacher' ? '/teacher' : '/student')
  } catch (error) {
    ElMessage.error(friendlyError(error, '登录失败'))
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-story">
      <span class="product-tag">机器学习教学平台</span>
      <h1>欢迎使用</h1>
      <p>登录后查看课程任务与学习反馈。</p>
    </section>

    <section class="login-card">
      <div>
        <h2>选择身份</h2>
      </div>
      <div v-if="!manualMode" class="quick-login">
        <label>教师或学生账号</label>
        <el-select v-model="selectedQuick" size="large" placeholder="请选择教师或学生账号"
          filterable :loading="quickLoading || busy" :disabled="quickLoading || busy" @change="chooseQuickAccount">
          <el-option-group v-if="teacherAccounts.length" label="教师账号">
            <el-option v-for="account in teacherAccounts" :key="account.user_id"
              :label="`${account.label}（${account.user_id}）`" :value="account.user_id" />
          </el-option-group>
          <el-option-group v-if="studentAccounts.length" label="学生账号">
            <el-option v-for="account in studentAccounts" :key="account.user_id"
              :label="`${account.label}（${account.user_id}）`" :value="account.user_id" />
          </el-option-group>
        </el-select>
        <p v-if="quickError" class="login-error">{{ quickError }}</p>
        <button class="manual-link" type="button" @click="manualMode=true">手动输入其他账号</button>
      </div>
      <div v-else-if="quickError" class="quick-fallback">
        <p class="login-error">{{ quickError }}</p>
        <button type="button" class="manual-link" @click="manualMode=false; loadIdentities()">重试账号列表</button>
      </div>
      <el-form v-if="manualMode" label-position="top" @submit.prevent="submit">
        <el-form-item label="账号">
          <el-input v-model="userId" size="large" autocomplete="username"
            :readonly="quickEnabled && !manualMode" placeholder="请选择账号或手动输入" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" size="large"
            show-password autocomplete="current-password" @keyup.enter="submit" />
        </el-form-item>
        <el-button type="primary" size="large" :loading="busy" class="login-submit"
          @click="submit">进入平台</el-button>
        <button v-if="quickEnabled && quickAccounts.length" class="manual-link" type="button"
          @click="manualMode=false; quickError=''">返回选择身份</button>
      </el-form>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  display: grid;
  grid-template-columns: minmax(0, 1.12fr) minmax(390px, .88fr);
  min-height: 100dvh;
  background: var(--app-bg);
  color: var(--app-text);
}
.login-story {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  padding: clamp(40px, 6vw, 90px);
  background: #eaf1fc;
}
.product-tag { color: var(--app-primary); font-size: 15px; font-weight: var(--font-weight-semibold); }
.login-story h1 {
  margin: 16px 0;
  color: var(--text-primary);
  font-size: clamp(34px, 4vw, 50px);
  font-weight: var(--font-weight-bold);
  line-height: 1.2;
}
.login-story > p { margin: 0; color: var(--text-secondary); font-size: 16px; line-height: 1.65; }
.login-card {
  align-self: center;
  justify-self: center;
  width: min(440px, calc(100% - 48px));
  padding: 40px;
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius);
  background: var(--app-surface);
  box-shadow: var(--app-shadow);
}
.login-card h2 { margin: 0; color: var(--text-primary); font-size: var(--font-size-page); font-weight: var(--font-weight-bold); }
.quick-login { display: grid; gap: 9px; margin: 22px 0 12px; }
.quick-login label { color: var(--text-primary); font-size: 15px; font-weight: var(--font-weight-semibold); }
.quick-login :deep(.el-select) { width: 100%; }
.manual-link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 40px;
  padding: 4px 0;
  border: 0;
  background: none;
  color: var(--app-primary);
  cursor: pointer;
  font: inherit;
  font-size: 14px;
  font-weight: var(--font-weight-semibold);
}
.manual-link:focus-visible { outline: 2px solid var(--app-primary); outline-offset: 2px; border-radius: 3px; }
.login-error { margin: 4px 0; color: #9b414b; font-size: 14px; line-height: 1.5; }
.quick-fallback { margin-top: 18px; }
.login-submit { width: 100%; min-height: 42px; margin-top: 8px; }
@media (max-width: 820px) {
  .login-page { grid-template-columns: 1fr; align-content: start; }
  .login-story { padding: 28px 26px; }
  .login-story h1 { margin: 7px 0; font-size: 30px; }
  .login-story > p { font-size: 14px; }
  .login-card { margin: 18px auto; padding: 28px; }
}
</style>