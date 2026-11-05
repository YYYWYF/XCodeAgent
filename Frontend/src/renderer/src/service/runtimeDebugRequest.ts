import type { DebugEndpoint } from './runtimeWorkbench'

export type DebugRequestValues = {
  pathParameters: Record<string, string>; query: Record<string, string>; headers: Record<string, string>
  context: Record<string, unknown>
}

/** 请求编辑器只接受 JSON 对象，不把数组或原始文本当成参数映射。 */
export function debugObject(text: string, label: string): Record<string, unknown> {
  let value: unknown
  try { value = JSON.parse(text || '{}') } catch { throw new Error(`${label}必须是有效 JSON 对象。`) }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label}必须是 JSON 对象。`)
  return value as Record<string, unknown>
}

/** 参数/Header 使用字符串值；平台身份和传输 Header 不允许由编辑器覆盖。 */
export function debugStringMap(text: string, label: string): Record<string, string> {
  const value = debugObject(text, label)
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== 'string') throw new Error(`${label}的 ${key} 必须是字符串。`)
    if (label === 'Headers' && (/^(authorization|proxy-authorization|cookie|host|content-length|content-type|accept|connection|transfer-encoding)$/i.test(key)
      || !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(key) || /[\r\n]/.test(item))) throw new Error('不能覆盖平台 Header，或 Header 格式无效。')
  }
  return value as Record<string, string>
}

/** 只将契约路径与显式参数拼到受管 Runtime；不会接受新的域名或任意路由。 */
export function debugRequestPath(endpoint: DebugEndpoint, values: DebugRequestValues): string {
  const names = [...endpoint.path.matchAll(/\{([^}:]+)(?::[^}]+)?\}/g)].map((match) => match[1])
  if (Object.keys(values.pathParameters).some((name) => !names.includes(name))
    || names.some((name) => !values.pathParameters[name])) throw new Error('请按接口契约填写全部路径参数。')
  const queryNames = endpoint.parameters.filter((item) => item.in === 'query').map((item) => item.name)
  if (Object.keys(values.query).some((name) => !queryNames.includes(name))) throw new Error('查询参数不在当前接口契约中。')
  const path = endpoint.path.replace(/\{([^}:]+)(?::[^}]+)?\}/g, (_, name) => encodeURIComponent(values.pathParameters[name]))
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('?') || path.includes('#')) throw new Error('接口路径无效。')
  const query = new URLSearchParams(values.query).toString()
  return path + (query ? `?${query}` : '')
}
