import { CompressOutlined, ExpandOutlined, SendOutlined } from '@ant-design/icons'
import { Alert, Button, Input, Modal, Select, Tooltip } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { DataSourceOperation, ExternalApiDataSource } from '../../typings'
import { sendExternalApiRequest, type DebugResponse } from '../../service/externalApiDebug'
import DebugRequestFields, { type DebugAuth } from './DebugRequestFields'
import DebugResponsePanel from './DebugResponsePanel'
import { debugStorageKey, initialDraft, restoreDebug, sameDebugDraft, values, type DebugResult, type DebugSession, type Draft, type Entry } from './debugDraft'
import { debugCredentials, isSensitiveDebugField, publicDebugResponse, publicDebugText, publicDraft } from './debugPrivacy'
import { synchronizePathParameters } from './dataSourceOperations'
import { captureDebugImport, validateDebugImport, type DebugImport } from './operationDebugSync'
import OperationTabLayout from './OperationTabLayout'
import './ExternalApiDebug.less'

type Props = {
  workspace: string
  source: ExternalApiDataSource
  operation: DataSourceOperation
  active: boolean
  seed?: Draft
  onDraftChanged: (changed: boolean) => void
  onSyncToEdit: (snapshot: DebugImport) => void
}
type Result = { saved: DebugResult; live?: DebugResponse; credentials?: string[] }
const methods: DataSourceOperation['method'][] = ['GET', 'POST', 'PUT', 'DELETE']

/** 重建路径参数，保留同名值；输入过程允许暂时未闭合的占位符。 */
function withPath(draft: Draft, path: string): Draft {
  try {
    const parameters = synchronizePathParameters(path, [])
    const rows = parameters.map(({ name, type }) => draft.path.find(row => row.name === name) ??
      { name, value: '', enabled: true, type })
    return { ...draft, requestPath: path, path: rows }
  } catch { return { ...draft, requestPath: path } }
}

/** 预览请求的最终地址，在地址中掩盖敏感参数值。 */
function previewUrl(baseUrl: string, draft: Draft): string {
  const mask = (rows: Draft['query']): Draft['query'] =>
    rows.map(row => isSensitiveDebugField(row.name) && row.value ? { ...row, value: '•••' } : row)
  const pathValues = values(mask(draft.path))
  const query = new URLSearchParams(values(mask(draft.query))).toString()
  const path = draft.requestPath.trim().replace(/\{([^}]+)\}/g, (match, name) =>
    pathValues[name] ? encodeURIComponent(pathValues[name]) : match)
  return baseUrl.replace(/\/+$/, '') + path + (query ? '?' + query : '')
}

/** 组装临时鉴权，返回要在导入时过滤的凭据字面值。 */
function addAuth(headers: Record<string, string>, auth: DebugAuth): string[] {
  if (auth.mode === '无') return []
  if (!auth.credential) throw new Error('请填写鉴权凭据。')
  if (auth.mode === 'API Key') {
    if (!auth.identity.trim()) throw new Error('请填写 API Key 的 Header 名称。')
    headers[auth.identity.trim()] = auth.credential
    return [auth.credential]
  }
  const credential = auth.mode === 'Basic Auth'
    ? btoa(Array.from(new TextEncoder().encode(auth.identity + ':' + auth.credential), byte => String.fromCharCode(byte)).join(''))
    : auth.credential
  headers.Authorization = (auth.mode === 'Basic Auth' ? 'Basic ' : 'Bearer ') + credential
  return [credential, auth.credential]
}

/** 管理当前接口的调试草稿、单次调用快照以及展开视图。 */
export default function ExternalApiDebug({ workspace, source, operation, active, seed, onDraftChanged, onSyncToEdit }: Props) {
  const key = debugStorageKey(workspace, source, operation)
  const defaults = useMemo(() => initialDraft(source, operation), [source, operation])
  const lastDefaults = useRef(defaults)
  const [initial] = useState(() => restoreDebug(key, defaults))
  const [draft, setDraft] = useState(seed ?? initial.draft)
  const [history, setHistory] = useState(initial.history)
  const [auth, setAuth] = useState<DebugAuth>({ mode: '无', identity: '', credential: '' })
  const [result, setResult] = useState<Result | undefined>(() => initial.result && { saved: initial.result })
  const [error, setError] = useState(initial.error)
  const [syncError, setSyncError] = useState('')
  const [saveFailed, setSaveFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const pending = useRef<AbortController>()
  const alive = useRef(true)
  const snapshot = useMemo(() => result && captureDebugImport(result.saved), [result])

  /** 仅将当前草稿、调用摘要和脱敏后的最近响应投影到磁盘。 */
  const savedSession = useMemo<DebugSession>(() => {
    const credentials = debugCredentials(draft, [auth.credential, ...(result?.credentials ?? [])])
    return { draft: publicDraft(draft, credentials), history, result: result?.saved, error: publicDebugText(error, credentials) }
  }, [draft, history, result, error, auth.credential])

  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; pending.current?.abort() }
  }, [])

  /** 正式定义变化时刷新未动过的请求，已有试填参数与响应保留。 */
  useEffect(() => {
    const previous = lastDefaults.current
    lastDefaults.current = defaults
    if (!sameDebugDraft(previous, defaults)) {
      setDraft((current) => sameDebugDraft(current, previous) ? defaults : current)
    }
  }, [defaults])

  /** 接收编辑页传来的请求草稿，已有响应继续关联原始请求。 */
  useEffect(() => {
    if (seed) { setDraft(seed); setError(''); setSyncError('') }
  }, [seed])

  /** 离开调试视图时取消在途等待并移除展开层。 */
  useEffect(() => {
    if (!active) { pending.current?.abort(); setExpanded(false) }
  }, [active])

  /** 持久化已脱敏的请求及结果，实际凭据和原始响应继续留在会话中。 */
  useEffect(() => {
    onDraftChanged(!sameDebugDraft(draft, defaults))
    try {
      localStorage.setItem(key, JSON.stringify(savedSession))
      setSaveFailed(false)
    } catch { setSaveFailed(true) }
  }, [draft, savedSession, key, defaults, onDraftChanged])

  /** 历史保留参数摘要，完整正文只随最近一次请求和响应保存。 */
  function record(input: Draft, label: string, elapsed?: number): void {
    const entry: Entry = { id: crypto.randomUUID(), time: new Date().toLocaleString(), label, elapsed, draft: { ...input, body: '' } }
    setHistory(old => [entry, ...old].slice(0, 20))
  }

  /** 仅在点击发送时发起请求，并将响应和本次输入绑定。 */
  async function send(): Promise<void> {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setError('')
    setSyncError('')
    const input = { ...draft, requestPath: draft.requestPath.trim() }
    let authValues = [auth.credential]
    try {
      const headers = values(input.headers)
      authValues = addAuth(headers, auth)
      if (input.body && !Object.keys(headers).some(name => name.toLowerCase() === 'content-type')) {
        headers['Content-Type'] = 'application/json'
      }
      const response = await sendExternalApiRequest({
        workspaceRoot: workspace, sourceId: source.id, operationId: operation.id,
        expectedBase: source.baseUrl, expectedMethod: operation.method, expectedPath: operation.path,
        requestMethod: input.method, requestPath: input.requestPath,
        pathValues: values(input.path), query: values(input.query), headers, body: input.body || null,
        timeoutMs: input.timeout
      }, controller.signal)
      if (!alive.current) return
      const credentials = debugCredentials(input, authValues, response)
      const safeInput = publicDraft(input, credentials)
      const safeResponse = publicDebugResponse(response, credentials)
      const redacted = safeResponse.body !== response.body || JSON.stringify(safeResponse.headers) !== JSON.stringify(response.headers)
      setResult({ live: response, saved: { response: safeResponse, input: safeInput, redacted }, credentials })
      record(safeInput, 'HTTP ' + response.statusCode, response.elapsedMs)
    } catch (cause) {
      if (!alive.current) return
      setError(controller.signal.aborted ? '已取消等待' : cause instanceof Error ? cause.message : '请求失败')
      record(publicDraft(input, debugCredentials(input, authValues)), controller.signal.aborted ? '已取消' : '请求失败')
    } finally {
      pending.current = undefined
      if (alive.current) setBusy(false)
    }
  }

  /** 载入历史请求时重新填写鉴权值，并保留现有响应的归属。 */
  function load(entry: Entry): void {
    setDraft(entry.draft)
    setAuth({ mode: '无', identity: '', credential: '' })
  }

  /** 只同步本次响应关联的请求快照，名称冲突时不打开编辑页。 */
  function syncToEdit(): void {
    if (busy || !snapshot) return
    try {
      validateDebugImport(snapshot, source)
      setSyncError('')
      onSyncToEdit(snapshot)
    } catch (cause) {
      setSyncError(cause instanceof Error ? cause.message : '无法同步接口配置。')
    }
  }

  const panel = <OperationTabLayout scrollLabel="调试接口内容"
    sync={{ label: '同步至编辑接口', disabled: busy || !result, onClick: syncToEdit }}
    feedback={syncError && <Alert type="error" showIcon message={syncError} />}>
    <section className="external-api-debug">
      <div className="debug-request-bar">
        <Select aria-label="调试请求方法" value={draft.method} onChange={method => setDraft({ ...draft, method })}
          options={methods.map(value => ({ value }))} />
        <Input aria-label="调试路径" placeholder="/posts/{id}" value={draft.requestPath}
          onChange={event => setDraft(withPath(draft, event.target.value))}/>
        <Button className="debug-send" type="primary" icon={busy ? undefined : <SendOutlined />}
          onClick={busy ? () => pending.current?.abort() : send}>{busy ? '取消' : '发送'}</Button>
        <Tooltip title={expanded ? '收起调试视图' : '展开调试视图'}>
          <Button type="text" aria-label={expanded ? '收起调试视图' : '展开调试视图'}
            icon={expanded ? <CompressOutlined /> : <ExpandOutlined />} onClick={() => setExpanded(!expanded)}/>
        </Tooltip>
      </div>
      <code className="debug-url" title={previewUrl(source.baseUrl, draft)}>{previewUrl(source.baseUrl, draft)}</code>
      {saveFailed && <p className="debug-save-error" role="status">调试内容保存失败</p>}
      <DebugRequestFields draft={draft} auth={auth} update={setDraft} updateAuth={setAuth}/>
      <DebugResponsePanel busy={busy} response={result?.live ?? result?.saved.response} responseSample={snapshot?.responseSample}
        restoredRedacted={Boolean(result && !result.live && result.saved.redacted)}
        stale={Boolean(result && (!result.live || busy || error || !sameDebugDraft(savedSession.draft, result.saved.input)))}
        error={error} history={history} onLoad={load} />
    </section>
  </OperationTabLayout>
  return expanded && active ? <Modal visible centered getContainer={false} title={operation.name + ' · 调试接口'}
    width="85vw" footer={null} wrapClassName="external-api-debug-modal" onCancel={() => setExpanded(false)}>{panel}</Modal> : panel
}
