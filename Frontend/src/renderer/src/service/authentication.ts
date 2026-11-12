import { HttpAgent } from '@ag-ui/client'
import type { AgentSubscriber, HttpAgentConfig } from '@ag-ui/client'

export type AuthenticationFailureStatus = 401 | 403

export type AuthenticationFailure = {
  status: AuthenticationFailureStatus
}

type AuthenticationFailureListener = (failure: AuthenticationFailure) => void

type HttpErrorLike = Error & {
  payload?: unknown
  status?: unknown
}

const authenticationFailureListeners = new Set<AuthenticationFailureListener>()
let activeAuthenticationFailure: AuthenticationFailure | undefined

/** 从未知错误中提取 HTTP 401 或 403 状态。 */
export function getAuthenticationFailureStatus(
  error: unknown
): AuthenticationFailureStatus | undefined {
  if (error && typeof error === 'object' && 'status' in error) {
    const status = (error as { status?: unknown }).status
    if (status === 401 || status === 403) return status
  }

  const message = error instanceof Error ? error.message : String(error || '')
  const match = /^HTTP\s+(401|403)(?::|\s|$)/i.exec(message.trim())
  return match ? (Number(match[1]) as AuthenticationFailureStatus) : undefined
}

/** 判断错误是否代表登录认证失败。 */
export function isAuthenticationFailure(error: unknown): boolean {
  return getAuthenticationFailureStatus(error) !== undefined
}

/** 创建带有 HTTP 状态和响应载荷的请求错误。 */
export function createHttpError(status: number, payload?: unknown): Error {
  const detail = readErrorDetail(payload)
  const error = new Error(`HTTP ${status}${detail ? `: ${detail}` : ''}`) as HttpErrorLike
  error.status = status
  error.payload = payload
  return error
}

/** 发布认证失败事件，并返回当前错误是否属于认证失败。 */
export function reportAuthenticationFailure(error: unknown): boolean {
  const status = getAuthenticationFailureStatus(error)
  if (!status) return false
  if (activeAuthenticationFailure) return true
  activeAuthenticationFailure = { status }
  for (const listener of authenticationFailureListeners) listener(activeAuthenticationFailure)
  return true
}

/** 结束本轮认证失败阻断，允许后续失败再次通知界面。 */
export function resetAuthenticationFailure(): void {
  activeAuthenticationFailure = undefined
}

/** 订阅全局认证失败事件，并返回取消订阅函数。 */
export function subscribeAuthenticationFailure(
  listener: AuthenticationFailureListener
): () => void {
  authenticationFailureListeners.add(listener)
  if (activeAuthenticationFailure) listener(activeAuthenticationFailure)
  return () => {
    authenticationFailureListeners.delete(listener)
  }
}

const authenticationSubscriber: AgentSubscriber = {
  onRunFailed: ({ error }) => {
    reportAuthenticationFailure(error)
  }
}

/** 创建自动订阅 HTTP 401/403 认证失败事件的 AG-UI HttpAgent。 */
export function createAgUiHttpAgent(config: HttpAgentConfig): HttpAgent {
  const agent = new HttpAgent(config)
  agent.subscribe(authenticationSubscriber)
  return agent
}

/** 从常见错误响应结构中提取可读详情。 */
function readErrorDetail(payload: unknown): string {
  if (typeof payload === 'string') return payload
  if (!payload || typeof payload !== 'object') return ''
  const detail =
    (payload as { detail?: unknown; message?: unknown }).detail ??
    (payload as { message?: unknown }).message
  return typeof detail === 'string' ? detail : ''
}
