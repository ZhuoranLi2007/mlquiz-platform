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
.login-page{min-height:100vh;display:grid;grid-template-columns:minmax(0,1.12fr) minmax(390px,.88fr);background:#f3f6fa;color:#172033}
.login-story{padding:clamp(52px,8vw,120px);display:flex;flex-direction:column;justify-content:center;background:linear-gradient(145deg,#102a43 0%,#17496a 55%,#1f6f78 100%);color:#fff;position:relative;overflow:hidden}
.login-story:after{content:"";position:absolute;width:440px;height:440px;border:1px solid rgba(255,255,255,.14);border-radius:50%;right:-150px;bottom:-180px;box-shadow:0 0 0 60px rgba(255,255,255,.035),0 0 0 120px rgba(255,255,255,.025)}
.product-tag{font-size:14px;letter-spacing:.2em;color:#9fe3dc;font-weight:700}.login-story h1{font-size:clamp(38px,5vw,68px);line-height:1.08;max-width:760px;margin:22px 0}.login-story>p{font-size:19px;color:#d8e9f0}.login-story ol{list-style:none;padding:0;margin:54px 0 34px;display:grid;gap:22px}.login-story li{display:grid;grid-template-columns:72px 1fr;align-items:center}.login-story li b{color:#9fe3dc;font-size:15px;letter-spacing:.15em}.login-story li span{border-left:1px solid rgba(255,255,255,.24);padding-left:20px}.login-story small{color:#bdd3df;max-width:620px;line-height:1.7}
.login-card{align-self:center;justify-self:center;width:min(440px,calc(100% - 48px));padding:44px;background:#fff;border:1px solid #dfe7ee;border-radius:20px;box-shadow:0 24px 70px rgba(30,55,80,.12)}.eyebrow{font-size:14px;font-weight:700;letter-spacing:.16em;color:#16756f;margin:0 0 8px}.login-card h2{font-size:30px;margin:0}.muted{color:#718096}.quick-login{display:grid;gap:7px;margin:24px 0 12px}.quick-login label{color:#34495e;font-size:14px;font-weight:700}.quick-login .el-select{width:100%}.quick-login small{color:#718096;font-size:14px;line-height:1.5}.student-login-note{display:grid;gap:5px;margin-bottom:20px;padding:13px 15px;border:1px solid #d8e8e6;border-radius:10px;background:#f1f8f7;color:#315064}.student-login-note b{color:#176e68}.student-login-note span{font-size:14px}.student-login-note small{font-size:14px;line-height:1.5;color:#718096}.login-submit{width:100%;margin-top:8px}.security-note{font-size:14px;color:#718096;text-align:center;margin:22px 0 0}
.login-page{background:var(--app-bg);color:var(--app-text)}
.login-story{background:#eaf1fc;color:var(--app-text);padding:clamp(40px,6vw,90px)}
.login-story:after{display:none}
.product-tag{color:var(--app-primary);font-size:15px;letter-spacing:0}
.login-story h1{font-size:clamp(34px,4vw,54px);margin:16px 0}
.login-story>p{font-size:18px;color:var(--app-muted)}
.login-card{border-color:var(--app-border);border-radius:var(--app-radius);box-shadow:var(--app-shadow)}
.eyebrow{color:var(--app-primary);font-size:14px;letter-spacing:0}
.quick-login label{font-size:15px}
.quick-login{margin-top:22px}.quick-login label{font-size:16px;color:var(--app-text)}
.manual-link{display:inline-flex;align-items:center;justify-content:center;min-height:36px;padding:4px 0;border:0;background:none;color:var(--app-primary);font:inherit;font-size:14px;font-weight:650;cursor:pointer}
.login-error{margin:4px 0;color:#9b414b;font-size:14px;line-height:1.5}.quick-fallback{margin-top:18px}
@media(max-width:820px){.login-page{grid-template-columns:1fr;align-content:start}.login-story{padding:28px 26px}.login-story h1{font-size:30px;margin:7px 0}.login-story>p{font-size:14px;margin:0}.login-card{margin:18px auto;padding:28px}}
</style>
