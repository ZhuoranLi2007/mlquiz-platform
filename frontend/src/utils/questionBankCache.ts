import { tabToken } from '../api/client'

type BankSnapshot = {
  items: Record<string, any>[]
  total: number
  metadata: Record<string, any>
  drafts: Record<string, any>[]
}

let cached: { token: string; value: BankSnapshot } | null = null

export function readQuestionBankCache(): BankSnapshot | null {
  const token = tabToken()
  return token && cached?.token === token ? cached.value : null
}

export function saveQuestionBankCache(value: BankSnapshot) {
  const token = tabToken()
  if (token) cached = { token, value }
}

export function clearQuestionBankCache() {
  cached = null
}
