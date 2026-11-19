import type { DebugResponse } from '../../service/externalApiDebug'
import type { Draft, Row } from './debugDraft'

const sensitive = /auth|cookie|passw|passwd|pwd|secret|token|api.?key|credential|session.?id|signature|密码|令牌|密钥|凭据/i
const hidden = '（敏感内容已隐藏）'

/** 自动识别需要隐藏的鉴权和敏感参数名称。 */
export function isSensitiveDebugField(name: string): boolean {
  return sensitive.test(name)
}

/** 查找请求或响应中已知凭据的原值和回显。 */
function containsCredential(value: string, credentials: readonly string[]): boolean {
  return credentials.some((credential) =>
    credential && (value === credential || (credential.length >= 4 && value.includes(credential))))
}

/** 收集已知凭据，包括敏感字段中的值，避免回显到其他字段或正文时被保存。 */
export function debugCredentials(draft: Draft, authValues: readonly string[] = [], response?: DebugResponse): string[] {
  const values = new Set(authValues.filter(Boolean))
  /** 也识别标准鉴权头中去掉方案前缀后的值。 */
  const remember = (name: string, value: string): void => {
    if (!isSensitiveDebugField(name) || !value) return
    values.add(value)
    const authorization = /^(?:Basic|Bearer)\s+(.+)$/i.exec(value.trim())
    if (authorization) values.add(authorization[1])
  }
  for (const row of [...draft.path, ...draft.query, ...draft.headers]) {
    remember(row.name, row.value)
  }
  for (const [name, value] of Object.entries(response?.headers ?? {})) {
    remember(name, value)
  }
  /** 用队列遍历 JSON 字段，识别敏感键下的所有标量。 */
  const collect = (text: string): void => {
    try {
      const pending: Array<{ value: unknown; privateField: boolean }> = [{ value: JSON.parse(text), privateField: false }]
      while (pending.length) {
        const { value, privateField } = pending.pop()!
        if (value !== null && typeof value === 'object') {
          for (const [name, child] of Object.entries(value)) {
            pending.push({ value: child, privateField: privateField || isSensitiveDebugField(name) })
          }
        } else if (privateField && value != null && value !== '') {
          values.add(String(value))
        }
      }
    } catch {
      // 非 JSON 内容交给文本规则处理。
    }
  }
  collect(draft.body)
  if (response) collect(response.body)
  return [...values]
}

/** 保存文本时保留原格式； JSON 敏感键被移除，已知凭据回显被隐藏。 */
export function publicDebugText(text: string, credentials: readonly string[]): string {
  if (!text) return text
  let json: unknown
  try {
    json = JSON.parse(text)
  } catch {
    if (containsCredential(text, credentials)) return hidden
    for (const match of text.matchAll(/(?:^|[\s?&,;{"'])([\p{L}\w.-]+)["']?\s*[:=]/gu)) {
      if (isSensitiveDebugField(match[1])) return hidden
    }
    return text
  }

  let changed = false
  /** 递归过滤敏感键和回显值，同时保持其余字段的原始类型。 */
  const clean = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(clean)
    if (value !== null && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).flatMap(([name, child]) => {
        if (!isSensitiveDebugField(name) && !containsCredential(name, credentials)) return [[name, clean(child)]]
        changed = true
        return []
      }))
    }
    if (value !== null && containsCredential(String(value), credentials)) {
      changed = true
      return null
    }
    return value
  }

  try {
    const result = clean(json)
    return changed ? result === null ? hidden : JSON.stringify(result, null, 2) : text
  } catch {
    // 无法完整过滤过深的 JSON 时保留明确提示，不保存未检查的正文。
    return hidden
  }
}

/** 保存调试草稿时保留普通请求内容，鉴权、敏感值和未命名参数值不写入。 */
export function publicDraft(draft: Draft, credentials: readonly string[] = debugCredentials(draft)): Draft {
  /** 过滤各位置的参数值，仅写当前字段定义。 */
  const clean = (rows: Row[]): Row[] => rows.map(({ name, value, enabled, type }) => ({
    name: containsCredential(name, credentials) ? '' : name, enabled, type,
    value: name.trim() && !isSensitiveDebugField(name) && !containsCredential(value, credentials) ? value : ''
  }))
  const body = publicDebugText(draft.body, credentials)
  return {
    method: draft.method,
    requestPath: containsCredential(draft.requestPath, credentials) ? '' : draft.requestPath,
    path: clean(draft.path),
    query: clean(draft.query),
    headers: clean(draft.headers),
    body: body === hidden ? '' : body,
    timeout: draft.timeout
  }
}

/** 保留最近一份响应的状态、响应头和响应体，先过滤凭据再保存。 */
export function publicDebugResponse(response: DebugResponse, credentials: readonly string[]): DebugResponse {
  const headers = Object.fromEntries(Object.entries(response.headers).flatMap(([name, value]) => {
    if (containsCredential(name, credentials)) return []
    return [[name, isSensitiveDebugField(name) || containsCredential(value, credentials) ? hidden : value]]
  }))
  return { ...response, headers, body: publicDebugText(response.body, credentials) }
}
