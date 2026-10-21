import { createHttpError, reportAuthenticationFailure } from './authentication'
import type { CloudJsonRequest } from '../../../shared/authentication'

/** 云端请求交给 Python 从凭据文件读取 token，渲染器不接触凭据。 */
export async function requestCloudJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const api = window.devAgentStudio?.auth
  if (!api?.requestCloudJson) {
    const error = createHttpError(401, { detail: '当前环境无法访问桌面认证服务。' })
    reportAuthenticationFailure(error)
    throw error
  }
  if (init.body !== undefined && init.body !== null && typeof init.body !== 'string') {
    throw new Error('云端 JSON 请求体必须是字符串。')
  }
  const headers: Record<string, string> = {}
  new Headers(init.headers).forEach((value, key) => {
    if (['content-type', 'accept'].includes(key)) headers[key] = value
  })
  if (init.body && !headers['content-type']) headers['content-type'] = 'application/json'
  const request: CloudJsonRequest = {
    url, method: (init.method || 'GET').toUpperCase() as CloudJsonRequest['method'],
    headers, body: typeof init.body === 'string' ? init.body : undefined
  }
  const result = await api.requestCloudJson(request)
  if (result.httpStatus >= 400 || result.httpStatus < 200 || result.httpStatus >= 300) {
    const error = createHttpError(result.httpStatus, result.body)
    reportAuthenticationFailure(error)
    throw error
  }
  return result.body as T
}
