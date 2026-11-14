import { computed, ref } from 'vue'
import { api, setTabToken, tabToken } from './api/client'
import { clearQuestionBankCache } from './utils/questionBankCache'

export type Principal = {
  user_id: string
  display_name: string
  role: 'teacher' | 'student'
  student_id?: string | null
  student_code?: string | null
  classroom_id?: string | null
  classroom_name?: string | null
  identity_scope?: 'official' | 'test' | 'unassigned' | null
  official_classroom?: boolean
}

const principal = ref<Principal | null>(null)
const ready = ref(false)

async function ensure() {
  if (ready.value) return principal.value
  if (!tabToken()) {
    principal.value = null
    ready.value = true
    return null
  }
  try {
    principal.value = await api.get<Principal>('/auth/me')
  } catch {
    principal.value = null
    setTabToken('')
  } finally {
    ready.value = true
  }
  return principal.value
}

async function login(user_id: string, password: string) {
  const result = await api.post<{ principal: Principal; session_token: string }>('/auth/login',
    { user_id, password, tab_session: true })
  setTabToken(result.session_token)
  principal.value = result.principal
  ready.value = true
  return result.principal
}

async function quickLogin(user_id: string) {
  const result = await api.post<{ principal: Principal; session_token: string }>(
    '/auth/quick-login', { user_id })
  setTabToken(result.session_token)
  principal.value = result.principal
  ready.value = true
  return result.principal
}

async function logout() {
  try { await api.post('/auth/logout') } finally {
    clearQuestionBankCache()
    setTabToken('')
    principal.value = null
    ready.value = true
  }
}

export const auth = {
  principal,
  ready,
  role: computed(() => principal.value?.role || ''),
  isTeacher: computed(() => principal.value?.role === 'teacher'),
  isStudent: computed(() => principal.value?.role === 'student'),
  ensure,
  login,
  quickLogin,
  logout,
}
