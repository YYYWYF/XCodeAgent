import { createHttpError, reportAuthenticationFailure } from './authentication'

/** 通过 Electron 主进程内存中的 access_token 调用云端 Java JSON 接口。 */
export async function requestCloudJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const requestUrl = normalizeCloudUrl(url)
  const authApi = window.xcodeAgent?.auth
  if (!authApi?.getAccessToken) {
    const error = createHttpError(401, { detail: '当前环境无法读取 access_token。' })
    reportAuthenticationFailure(error)
    throw error
  }

  const { accessToken } = await authApi.getAccessToken()
  if (!accessToken) {
    const error = createHttpError(401, { detail: '缺少 access_token。' })
    reportAuthenticationFailure(error)
    throw error
  }

  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${accessToken}`)
  headers.set('Accept', 'application/json')
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(requestUrl, { ...init, headers })
  if (!response.ok) {
    const payload = await readErrorPayload(response)
    const error = createHttpError(response.status, payload)
    reportAuthenticationFailure(error)
    throw error
  }
  return readSuccessPayload<T>(response)
}

/** 校验云端请求必须使用完整的 HTTP 或 HTTPS 地址。 */
function normalizeCloudUrl(url: string): string {
  const parsedUrl = new URL(url)
  if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
    throw new Error('云端接口只支持 http 或 https URL。')
  }
  return parsedUrl.toString()
}

/** 宽容读取错误响应，确保 401/403 即使返回非 JSON 也能被识别。 */
async function readErrorPayload(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined
  const text = await response.text()
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

/** 严格读取成功的 JSON 响应，无效 JSON 作为普通接口错误抛出。 */
async function readSuccessPayload<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
