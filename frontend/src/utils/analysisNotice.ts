/** Describe the actual fallback reason without exposing model diagnostics. */
export function analysisNotice(analysis?: { model_status?: string } | null): string {
  const status = analysis?.model_status
  if (!status) return ''
  if (status === 'invalid_output' || status === 'truncated') {
    return 'AI 评语未通过内容校验，已显示依据作答生成的简要分析。'
  }
  if (status === 'model_not_configured' || status === 'sdk_missing') {
    return '尚未配置 AI 分析，当前显示依据作答生成的简要分析。'
  }
  if (status === 'rate_limited') {
    return 'AI 请求暂时受限，当前显示依据作答生成的简要分析。'
  }
  if (status === 'auth_failed' || status === 'model_not_found' || status === 'bad_request') {
    return 'AI 配置需要检查，当前显示依据作答生成的简要分析。'
  }
  return 'AI 连接未成功，当前显示依据作答生成的简要分析；稍后可重试。'
}
