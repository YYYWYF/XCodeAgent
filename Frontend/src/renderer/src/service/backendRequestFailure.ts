/** 仅识别浏览器/运行时明确报告的网络故障；业务、HTTP、超时、校验和未知异常不能推断后台失联。 */
export function isBackendTransportError(reason: unknown): boolean {
  if (!(reason instanceof Error)) return false
  // 收到 HTTP 错误响应仍说明服务有响应，不能被正文中的网络错误文字误导。
  if ('status' in reason || /^HTTP\s+\d{3}(?::|\s|$)/i.test(reason.message)) return false
  if (reason.name === 'NetworkError') return true
  return reason instanceof TypeError && /^(Failed to fetch|fetch failed|Load failed|NetworkError when attempting to fetch resource\.?|network error|terminated)$/i.test(reason.message.trim())
}

/** 保留具体失败原因；未知异常采用当前操作文案，绝不补写 Backend 不可用。 */
export function backendRequestErrorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : typeof reason === 'string' && reason.trim() ? reason : fallback
}
