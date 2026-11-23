import { useEffect, useMemo, useRef, useState } from 'react'
import type { DebugResponse } from '../../../../service/externalApiDebug'
import type { DebugHttpValues, DebugMethod } from '../../../../service/internalApiDebug'
import type { DebugAuth } from '../../../DataSourcesPage/DebugRequestFields'
import { restoreDebug, sameDebugDraft, values, type DebugResult, type DebugSession, type Draft, type Entry } from '../../../DataSourcesPage/debugDraft'
import { debugCredentials, isSensitiveDebugField, publicDebugResponse, publicDebugText, publicDraft } from '../../../DataSourcesPage/debugPrivacy'

type InternalDraft = Draft<DebugMethod>
type Result = { saved: DebugResult<DebugMethod>; live?: DebugResponse; credentials?: string[] }
type Options = {
  storageKey: string
  defaults: InternalDraft
  request: (values: DebugHttpValues, signal: AbortSignal) => Promise<DebugResponse>
}

/** 预览最终地址，敏感路径及 Query 参数显示为掩码。 */
export function debugRequestUrl(baseUrl: string, draft: InternalDraft): string {
  /** 使用已有敏感名称规则遮盖请求地址中的值。 */
  const mask = (rows: Draft['query']): Draft['query'] =>
    rows.map((row) => isSensitiveDebugField(row.name) && row.value ? { ...row, value: '•••' } : row)
  const pathValues = values(mask(draft.path))
  const query = new URLSearchParams(values(mask(draft.query))).toString()
  const path = draft.requestPath.trim().replace(/\{([^}]+)\}/g, (match, name) =>
    pathValues[name] ? encodeURIComponent(pathValues[name]) : match)
  return baseUrl.replace(/\/+$/, '') + path + (query ? '?' + query : '')
}

/** 收集 Cookie 整串及可能独立出现在响应里的值。 */
function cookieValues(cookie: string): string[] {
  return [cookie, ...cookie.split(';').flatMap((item) => {
    const split = item.indexOf('=')
    return split < 0 ? [] : [item.slice(split + 1).trim()]
  })].filter(Boolean)
}

/** 将内部调试的 Cookie 值和通用凭据一并纳入落盘过滤。 */
function internalCredentials(draft: InternalDraft, known: readonly string[], response?: DebugResponse): string[] {
  const credentials = [...known]
  for (const { name, value } of [...draft.path, ...draft.query, ...draft.headers]) {
    if (name.toLowerCase() === 'cookie') credentials.push(...cookieValues(value))
  }
  for (const [name, value] of Object.entries(response?.headers ?? {})) {
    if (name.toLowerCase() === 'cookie') credentials.push(...cookieValues(value))
    if (name.toLowerCase() === 'set-cookie') {
      for (const cookie of value.split(/,(?=\s*[^;,=\s]+\s*=)/)) {
        credentials.push(...cookieValues(cookie.split(';', 1)[0].trim()))
      }
    }
  }
  return debugCredentials(draft, credentials, response)
}

/** 组装本次请求的凭据，仅保留在当前调用中。 */
function applyAuth(headers: Record<string, string>, auth: DebugAuth): string[] {
  if (auth.mode === '无') return []
  if (!auth.credential) throw new Error('请填写鉴权凭据。')
  const basic = auth.mode === 'Basic Auth'
  const name = auth.mode === 'API Key' ? auth.identity.trim() : auth.mode === 'Cookie' ? 'Cookie' : 'Authorization'
  if (!name) throw new Error('请填写 API Key 的 Header 名称。')
  const value = basic
    ? btoa(Array.from(new TextEncoder().encode(auth.identity + ':' + auth.credential), (byte) => String.fromCharCode(byte)).join(''))
    : auth.credential
  for (const current of Object.keys(headers)) {
    if (current.toLowerCase() === name.toLowerCase()) delete headers[current]
  }
  headers[name] = name === 'Authorization' && auth.mode !== 'API Key' ? (basic ? 'Basic ' : 'Bearer ') + value : value
  return auth.mode === 'Cookie' ? cookieValues(value) : [value, auth.credential]
}

/** 管理当前内部接口的请求、临时凭据与最近响应。 */
export function useInternalApiDebugSession({ storageKey, defaults, request }: Options) {
  const lastDefaults = useRef(defaults)
  const [initial] = useState(() => restoreDebug(storageKey, defaults, true))
  const [draft, setDraft] = useState(initial.draft)
  const [history, setHistory] = useState(initial.history)
  const [auth, setAuth] = useState<DebugAuth>({ mode: '无', identity: '', credential: '' })
  const [result, setResult] = useState<Result | undefined>(() => initial.result && { saved: initial.result })
  const [error, setError] = useState(initial.error)
  const [saveFailed, setSaveFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const pending = useRef<AbortController>()
  const alive = useRef(true)

  /** 只投影已检查的请求和最近响应，包含在 Cookie Header 中的凭据也按敏感值处理。 */
  const savedSession = useMemo<DebugSession<DebugMethod>>(() => {
    const current = auth.mode === 'Cookie' ? cookieValues(auth.credential) : [auth.credential]
    const credentials = internalCredentials(draft, [...current, ...(result?.credentials ?? [])])
    return { draft: publicDraft(draft, credentials), history, result: result?.saved, error: publicDebugText(error, credentials) }
  }, [draft, history, result, error, auth.mode, auth.credential])

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; pending.current?.abort() }
  }, [])

  /** 默认配置变化时，只重置用户未改动的草稿。 */
  useEffect(() => {
    const previous = lastDefaults.current
    lastDefaults.current = defaults
    if (!sameDebugDraft(previous, defaults)) {
      setDraft((current) => sameDebugDraft(current, previous) ? defaults : current)
    }
  }, [defaults])

  /** 每个调试目标单独保存请求与结果。 */
  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(savedSession))
      setSaveFailed(false)
    } catch { setSaveFailed(true) }
  }, [storageKey, savedSession])

  /** 最近二十条只记录参数摘要，正文只随最近请求和响应保存。 */
  function record(input: InternalDraft, label: string, elapsed?: number): void {
    const entry: Entry<DebugMethod> = { id: crypto.randomUUID(), time: new Date().toLocaleString(), label, elapsed, draft: { ...input, body: '' } }
    setHistory((old) => [entry, ...old].slice(0, 20))
  }

  /** 显式点击发送才执行，并将响应和发出时的输入绑定。 */
  async function send(): Promise<void> {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setError('')
    const input = { ...draft, requestPath: draft.requestPath.trim() }
    let authValues = [auth.credential]
    try {
      const headers = values(input.headers)
      authValues = applyAuth(headers, auth)
      if (input.body && !Object.keys(headers).some((name) => name.toLowerCase() === 'content-type')) {
        headers['Content-Type'] = 'application/json'
      }
      const response = await request({
        pathValues: values(input.path), query: values(input.query), headers, body: input.body || null, timeoutMs: input.timeout
      }, controller.signal)
      if (!alive.current) return
      const credentials = internalCredentials(input, authValues, response)
      const safeInput = publicDraft(input, credentials)
      const safeResponse = publicDebugResponse(response, credentials)
      const redacted = safeResponse.body !== response.body || JSON.stringify(safeResponse.headers) !== JSON.stringify(response.headers)
      setResult({ live: response, saved: { response: safeResponse, input: safeInput, redacted }, credentials })
      record(safeInput, 'HTTP ' + response.statusCode, response.elapsedMs)
    } catch (cause) {
      if (!alive.current) return
      setError(controller.signal.aborted ? '已取消等待' : cause instanceof Error ? cause.message : '请求失败')
      record(publicDraft(input, internalCredentials(input, authValues)), controller.signal.aborted ? '已取消' : '请求失败')
    } finally {
      pending.current = undefined
      if (alive.current) setBusy(false)
    }
  }

  /** 停止等待当前请求，已收到的上一次响应留在界面。 */
  function cancel(): void {
    pending.current?.abort()
  }

  /** 载入历史参数时重新输入鉴权值。 */
  function load(entry: Entry<DebugMethod>): void {
    setDraft(entry.draft)
    setAuth({ mode: '无', identity: '', credential: '' })
  }

  return {
    draft, setDraft, auth, setAuth, result: result?.saved, busy, send, cancel, saveFailed,
    responseProps: {
      busy,
      response: result?.live ?? result?.saved.response,
      restoredRedacted: Boolean(result && !result.live && result.saved.redacted),
      stale: Boolean(result && (!result.live || busy || error || !sameDebugDraft(savedSession.draft, result.saved.input))),
      error, history, onLoad: load
    }
  }
}
