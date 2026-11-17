import axios from 'axios'
import { friendlyError } from '../utils/presentation'

const TAB_TOKEN_KEY = 'mlq_tab_session'

export function tabToken() {
  return typeof window === 'undefined' ? '' : window.sessionStorage.getItem(TAB_TOKEN_KEY) || ''
}

export function setTabToken(token: string) {
  if (typeof window === 'undefined') return
  if (token) window.sessionStorage.setItem(TAB_TOKEN_KEY, token)
  else window.sessionStorage.removeItem(TAB_TOKEN_KEY)
}

const http = axios.create({ baseURL: '/api', timeout: 60000, withCredentials: false })

http.interceptors.request.use((config) => {
  const token = tabToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (r) => r,
  (e) => {
    const error = new Error(friendlyError(e, '请求失败，请检查服务是否正常运行')) as Error & { detail?: unknown; status?: number }
    error.detail = e?.response?.data?.detail
    error.status = e?.response?.status
    return Promise.reject(error)
  },
)

export const api = {
  get: <T = any>(url: string, params?: any) => http.get<T>(url, { params }).then((r) => r.data),
  post: <T = any>(url: string, data?: any) => http.post<T>(url, data).then((r) => r.data),
  put: <T = any>(url: string, data?: any) => http.put<T>(url, data).then((r) => r.data),
  del: <T = any>(url: string) => http.delete<T>(url).then((r) => r.data),
  patch: <T = any>(url: string, data?: any) => http.patch<T>(url, data).then((r) => r.data),
  // Multipart upload: let the browser set the boundary, do not force JSON.
  upload: <T = any>(url: string, form: FormData) =>
    http.post<T>(url, form, { headers: { 'Content-Type': 'multipart/form-data' }, timeout: 180000 })
      .then((r) => r.data),
}

/** Slow AI workflows are durable jobs; each poll is a short HTTP request. */
export async function runTeacherJob(url: string, payload: any): Promise<any> {
  const queued: any = await api.post(url, payload)
  const deadline = Date.now() + 40 * 60 * 1000
  while (Date.now() < deadline) {
    const job: any = await api.get(`/teacher/jobs/${queued.job_id}`)
    if (job.status === 'succeeded') return job.result
    if (job.status === 'failed' || job.status === 'cancelled') {
      // A partial workflow still contains the truthful diagnosis and failure.
      if (job.result?.outputs) return job.result
      throw new Error(job.error || '后台任务未完成，请检查模型配置和后台服务')
    }
    await new Promise((resolve) => setTimeout(resolve, 2000))
  }
  throw new Error('任务仍在后台处理，请确认 worker 正在运行；稍后再次点击会继续查看已有任务')
}

/** Fetch protected media with this tab's identity; never put tokens in URLs. */
export async function protectedBlobUrl(url: string): Promise<string> {
  const response = await http.get<Blob>(url.replace(/^\/api/, ''), { responseType: 'blob' })
  return URL.createObjectURL(response.data)
}

export const pageImageUrl = (docId: string, page: number) =>
  `/api/materials/${docId}/page/${page}/image`
