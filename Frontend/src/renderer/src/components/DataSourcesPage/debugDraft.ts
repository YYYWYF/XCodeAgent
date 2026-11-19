import type { DataSourceFieldType, DataSourceOperation, DataSourceParameter, ExternalApiDataSource } from '../../typings'
import type { DebugResponse } from '../../service/externalApiDebug'
import { JSON_FIELD_TYPES, PATH_FIELD_TYPES } from './jsonFieldTypes'

// HTTP 输入仍以文本传输，字段类型用于与接口定义对齐。
export type Row = { name: string; value: string; enabled: boolean; type: DataSourceFieldType }
export type Draft = {
  method: DataSourceOperation['method']
  requestPath: string
  path: Row[]
  query: Row[]
  headers: Row[]
  body: string
  timeout: number
}
export type Entry = { id: string; time: string; label: string; elapsed?: number; draft: Draft }
export type DebugResult = { input: Draft; response: DebugResponse; redacted: boolean }
export type DebugSession = { draft: Draft; history: Entry[]; result?: DebugResult; error: string }
type StoredRow = Omit<Row, 'type'> & { type?: DataSourceFieldType }
type StoredDraft = Omit<Draft, 'path' | 'query' | 'headers'> & { path: StoredRow[]; query: StoredRow[]; headers: StoredRow[] }

/** 从接口定义读取本轮默认参数，尚未试填的可选参数默认关闭。 */
export function initialDraft(source: ExternalApiDataSource, operation: DataSourceOperation): Draft {
  /** 保留接口定义中的精确名称和声明类型。 */
  const rows = (fields: DataSourceParameter[]): Row[] =>
    fields.map((field) => ({
      name: field.name,
      value: '',
      enabled: field.required,
      type: field.type
    }))
  const headers = rows(operation.headers)
  for (const item of source.headers) {
    const existing = headers.find((row) => row.name.toLowerCase() === item.name.toLowerCase())
    if (existing) Object.assign(existing, { value: item.value, enabled: true })
    else
      headers.push({
        name: item.name,
        value: item.value,
        enabled: true,
        type: 'string'
      })
  }
  return {
    method: operation.method,
    requestPath: operation.path,
    path: rows(operation.pathParameters),
    query: rows(operation.queryParameters),
    headers,
    body: JSON.stringify(operation.requestSample, null, 2) ?? '',
    timeout: source.timeoutMs
  }
}

/** 将启用的参数转换为待发送的键值对。 */
export function values(rows: Row[]): Record<string, string> {
  return Object.fromEntries(
    rows.filter((row) => row.enabled && row.name.trim()).map((row) => [row.name.trim(), row.value])
  )
}

/** 比较请求内容，与对象属性顺序及缓存恢复过程无关。 */
export function sameDebugDraft(left: Draft, right: Draft): boolean {
  return left.method === right.method && left.requestPath.trim() === right.requestPath.trim() &&
    left.body === right.body && left.timeout === right.timeout &&
    (['path', 'query', 'headers'] as const).every((location) =>
      left[location].length === right[location].length &&
      left[location].every((row, index) => {
        const other = right[location][index]
        return row.name === other.name && row.value === other.value && row.enabled === other.enabled && row.type === other.type
      }))
}

/** 按保存的接口地址定位当前调试草稿，配置更新后从新定义初始化。 */
export function debugStorageKey(workspace: string, source: ExternalApiDataSource, operation: DataSourceOperation): string {
  return 'external-api-debug.v2:' + JSON.stringify([workspace, source.id, operation.id, source.baseUrl, operation.method, operation.path])
}

/** 校验本地草稿并读取当前字段，没有指定类型时使用接口定义。 */
function readDraft(value: unknown, defaults: Draft): Draft | undefined {
  const draft = value as Partial<StoredDraft> | null
  if (!draft?.method || !['GET', 'POST', 'PUT', 'DELETE'].includes(draft.method) ||
      typeof draft.requestPath !== 'string' || typeof draft.body !== 'string' ||
      typeof draft.timeout !== 'number' || !Number.isFinite(draft.timeout)) return undefined
  /** Header 不区分大小写，Path 与 Query 使用请求中的实际名称。 */
  const resolve = (location: 'path' | 'query' | 'headers', rows?: StoredRow[]): Row[] | undefined => {
    if (!Array.isArray(rows) || !rows.every((row) =>
      typeof row?.name === 'string' && typeof row.value === 'string' && typeof row.enabled === 'boolean')) return undefined
    const allowed = location === 'path' ? PATH_FIELD_TYPES : JSON_FIELD_TYPES
    const key = (name: string): string => location === 'headers' ? name.toLowerCase() : name
    const known = new Map(defaults[location].map((row) => [key(row.name), row.type]))
    return rows.map(({ name, enabled, value, type }) => ({
      name, enabled, value,
      type: type && allowed.includes(type) ? type : known.get(key(name)) ?? 'string'
    }))
  }
  const path = resolve('path', draft.path)
  const query = resolve('query', draft.query)
  const headers = resolve('headers', draft.headers)
  if (!path || !query || !headers) return undefined
  return { method: draft.method, requestPath: draft.requestPath, path, query, headers, body: draft.body, timeout: draft.timeout }
}

/** 检查可恢复的 HTTP 结果，缺失或损坏时仍能打开其他调试内容。 */
function isDebugResponse(value: unknown): value is DebugResponse {
  const response = value as Partial<DebugResponse> | null
  return Boolean(response && typeof response.statusCode === 'number' &&
    typeof response.elapsedMs === 'number' && typeof response.bytes === 'number' &&
    typeof response.truncated === 'boolean' && typeof response.body === 'string' &&
    response.headers && typeof response.headers === 'object' && !Array.isArray(response.headers) &&
    Object.values(response.headers).every((header) => typeof header === 'string'))
}

/** 恢复当前接口的请求、最近响应及调用摘要。 */
export function restoreDebug(key: string, fallback: Draft): DebugSession {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null')
    const draft = readDraft(value?.draft, fallback) ?? fallback
    const history: Entry[] = Array.isArray(value?.history)
      ? value.history.slice(0, 20).flatMap((entry: Partial<Entry> | null) => {
        const stored = readDraft(entry?.draft, fallback)
        if (!stored || typeof entry?.id !== 'string' || typeof entry.time !== 'string' || typeof entry.label !== 'string') return []
        return [{ id: entry.id, time: entry.time, label: entry.label,
          elapsed: typeof entry.elapsed === 'number' ? entry.elapsed : undefined, draft: stored }]
      }) : []
    const input = readDraft(value?.result?.input, fallback)
    const result = input && isDebugResponse(value.result.response)
      ? { input, response: value.result.response, redacted: value.result.redacted === true } : undefined
    return { draft, history, result, error: typeof value?.error === 'string' ? value.error : '' }
  } catch {
    /* 缓存不可读时仍允许调试。 */
  }
  return { draft: fallback, history: [], error: '' }
}
