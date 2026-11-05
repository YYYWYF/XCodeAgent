import { Alert, Button, Select, Space } from 'antd'
import { InfoCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { randomUUID } from '@ag-ui/client'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { RuntimeAgent, DebugCredential, DebugHttpResponse } from '../../../../service/runtimeWorkbench'
import { requestRuntimeDebug, requestRuntimeDebugHttp } from '../../../../service/runtimeWorkbench'
import { RuntimeDebugConversation } from '../../../../service/runtimeDebugConversation'
import type { DebugConversation } from '../../../../service/runtimeDebugConversation'
import { debugRequestPath } from '../../../../service/runtimeDebugRequest'
import AgentDebugRequestEditor, { debugRequestIssues, initialDebugFields, readDebugFields } from './AgentDebugRequestEditor'
import AgentDebugResponse from './AgentDebugResponse'
import AgentDebugStreamView from './AgentDebugStreamView'
import './AgentDebugPanel.less'

const labels = { idle: '就绪', running: '运行中', completed: '已完成', awaiting_user: '等待交互', unknown: '结果未知' }
const relationLabels = { agent_entry: '智能体运行入口', agent_call: '调用智能体的接口', tool: '智能体使用的工具接口' }

/** 显式启动独立 Debug Profile，通过官方 SDK 调试；凭据及对话不进入开发历史。 */
export default function AgentDebugPanel({ agent, workspaceRoot }: { agent: RuntimeAgent; workspaceRoot: string }): JSX.Element {
  const [owner] = useState(randomUUID)
  const [credential, setCredential] = useState<DebugCredential>()
  const credentialRef = useRef<DebugCredential>()
  const conversation = useRef<RuntimeDebugConversation>()
  const alive = useRef(true)
  const busyRef = useRef(false)
  const sendingRef = useRef(false)
  const stoppingRef = useRef(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [external, setExternal] = useState(false)
  const [active, setActive] = useState(false)
  const ownsProcess = useRef(false)
  const [restartRequired, setRestartRequired] = useState(false)
  const endpoints = agent.debugEndpoints || []
  const [targetKey, setTargetKey] = useState(endpoints[0] ? JSON.stringify([endpoints[0].apiContractId, endpoints[0].endpointId]) : '')
  const endpoint = endpoints.find((item) => JSON.stringify([item.apiContractId, item.endpointId]) === targetKey)
  const [fields, setFields] = useState(() => initialDebugFields(endpoint))
  const [draft, setDraft] = useState(endpoint?.responseMode === 'json' || agent.interactionContract.mode === 'task' || endpoint?.kind === 'business' ? '{}' : '')
  const [jsonResponse, setJsonResponse] = useState<DebugHttpResponse>()
  const [view, setView] = useState<DebugConversation>({ status: 'idle', messages: [], tools: [] })
  const running = view.status === 'running'
  const building = ['running', 'stopping'].includes(agent.development?.execution?.status || '')
  const expired = Boolean(credential && Date.now() >= credential.expiresAt * 1000)
  const taskMode = agent.interactionContract.mode === 'task' || endpoint?.kind === 'business'
  const validationErrors = useMemo(() => endpoint ? debugRequestIssues(endpoint, fields, draft,
    endpoint.responseMode === 'json' ? !['GET', 'HEAD'].includes(endpoint.method) : taskMode && !view.pending) : [],
  [endpoint, fields, draft, taskMode, view.pending])
  const statusLabel = busy ? '正在处理…' : credential ? expired ? '已过期' : jsonResponse ? '已收到响应' : labels[view.status]
    : external ? '其他窗口调试中' : active ? '等待同步' : '未启动'

  // 目录更新移除未完成或已变更接口时，清理旧输入和响应并选择当前可用入口。
  useEffect(() => {
    if (endpoint) return
    const target = endpoints[0]
    const key = target ? JSON.stringify([target.apiContractId, target.endpointId]) : ''
    if (targetKey === key) return
    conversation.current?.dispose(); conversation.current = undefined
    setTargetKey(key); setFields(initialDebugFields(target)); setError(''); setJsonResponse(undefined)
    setView({ status: 'idle', messages: [], tools: [] })
    setDraft(target?.responseMode === 'json' || target?.kind === 'business' || agent.interactionContract.mode === 'task' ? '{}' : '')
  }, [endpoints, endpoint, targetKey, agent.interactionContract.mode])

  /** 所有控制动作共用拥有者，其他窗口不能使用本面板的停止权限。 */
  const control = (action: 'get_debug' | 'start_debug' | 'stop_debug', receive?: (value: DebugCredential) => void) =>
    requestRuntimeDebug({ workspaceRoot, action, agentId: agent.agentId, origin: window.location.origin }, owner, receive)

  /** 刷新只查询环境状态，不恢复模型请求、不自动接管其他窗口凭据。 */
  const sync = async (): Promise<void> => {
    if (busyRef.current) return
    busyRef.current = true; setBusy(true)
    try {
      const result = await control('get_debug')
      if (!alive.current) return
      setActive(Boolean(result.debug?.active))
      ownsProcess.current = Boolean(result.debug?.active && result.debug.ownedByCaller)
      setExternal(Boolean(result.debug?.active && !result.debug.ownedByCaller))
      setRestartRequired(Boolean(result.debug?.restartRequired))
      if (!result.debug?.active && credentialRef.current) {
        conversation.current?.dispose(); conversation.current = undefined
        credentialRef.current = undefined; setCredential(undefined)
        setError('调试进程已退出或过期，请重新启动；不会自动重发消息。')
      }
    } catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : '状态读取失败') }
    finally { busyRef.current = false; if (alive.current) setBusy(false) }
  }

  /** 新会话使用独立 threadId，不清除服务端旧线程，也不重新执行旧消息。 */
  const newConversation = (value: DebugCredential): void => {
    conversation.current?.dispose()
    conversation.current = undefined
    if (!endpoint) throw new Error('尚未读取到当前智能体的调用入口。')
    if (endpoint.responseMode === 'ag-ui') conversation.current = new RuntimeDebugConversation(value, agent.agentId,
      (next) => { if (alive.current) setView(next) }, { endpoint, values: readDebugFields(fields) })
    setView({ status: 'idle', messages: [], tools: [] }); setJsonResponse(undefined)
  }

  /** 启动只准备进程和依赖，收到短期凭据后才开放发送。 */
  const start = async (): Promise<void> => {
    if (busyRef.current) return
    busyRef.current = true; setBusy(true); setError('')
    let issued: DebugCredential | undefined
    try {
      await control('start_debug', (value) => { issued = value; credentialRef.current = value; ownsProcess.current = true })
      if (!alive.current) { await control('stop_debug'); return }
      if (!issued) throw new Error('未收到调试凭据，请同步状态后停止原调试。')
      newConversation(issued); setCredential(issued); setActive(true); setExternal(false); setRestartRequired(false)
    } catch (reason) {
      // 启动响应失败或客户端构造失败时，不把孤立凭据当作可发送的客户端。
      conversation.current?.dispose(); conversation.current = undefined
      credentialRef.current = undefined
      if (issued) {
        try { await control('stop_debug'); ownsProcess.current = false }
        catch { /* 保留拥有者身份，用户可同步或显式停止；不重发启动。 */ }
      }
      if (alive.current) {
        // 控制响应中断不意味着启动失败；保留停止入口，避免再次启动抢占未知进程。
        setCredential(undefined)
        setActive(ownsProcess.current)
        setError(reason instanceof Error ? reason.message : '启动失败，请同步状态')
      }
    } finally { busyRef.current = false; if (alive.current) setBusy(false) }
  }

  /** 停止独立进程并清除凭据，其他预览服务不受影响。 */
  const stop = async (): Promise<void> => {
    if (stoppingRef.current) return
    stoppingRef.current = true
    busyRef.current = true; setBusy(true); setError('')
    conversation.current?.stop()
    try {
      await control('stop_debug')
      conversation.current?.dispose(); conversation.current = undefined
      credentialRef.current = undefined
      ownsProcess.current = false
      if (alive.current) { setCredential(undefined); setActive(false); setExternal(false) }
    } catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : '停止失败') }
    finally {
      stoppingRef.current = false
      busyRef.current = sendingRef.current
      if (alive.current) setBusy(busyRef.current)
    }
  }

  /** 交互和普通发送使用相同运行通道，失败不自动重试。 */
  const send = async (answer?: string | boolean): Promise<void> => {
    if (busyRef.current || sendingRef.current) return
    sendingRef.current = true
    busyRef.current = true; setBusy(true)
    try {
      setError('')
      if (!endpoint) throw new Error('请选择当前智能体的正式调用入口。')
      if (validationErrors.length) throw new Error(validationErrors[0])
      const state = await control('get_debug')
      if (!alive.current) return
      if (stoppingRef.current) return
      if (!state.debug?.active || !state.debug.ownedByCaller || state.debug.launchId !== credentialRef.current?.launchId) {
        throw new Error('调试进程身份已失效，请同步状态后重新启动。')
      }
      if (state.debug.restartRequired) {
        setRestartRequired(true)
        throw new Error('代码或配置已变化，请停止并重新启动；本次未发送模型请求。')
      }
      if (!credentialRef.current || Date.now() >= credentialRef.current.expiresAt * 1000) {
        throw new Error('调试会话已停止或过期，请重新启动。')
      }
      const values = readDebugFields(fields)
      debugRequestPath(endpoint, values)
      if (endpoint.responseMode === 'json') {
        setJsonResponse(undefined)
        const body = ['GET', 'HEAD'].includes(endpoint.method) ? null : JSON.parse(draft || '{}')
        const result = await requestRuntimeDebugHttp({ workspaceRoot, action: 'request_debug', agentId: agent.agentId,
          launchId: credentialRef.current.launchId, apiContractId: endpoint.apiContractId, endpointId: endpoint.endpointId,
          pathParameters: values.pathParameters, query: values.query, headers: values.headers, body }, owner)
        if (alive.current) setJsonResponse(result)
        return
      }
      let input: Record<string, unknown> | undefined
      if (taskMode && !view.pending) {
        const parsed: unknown = JSON.parse(draft)
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('任务输入必须是 JSON 对象。')
        input = parsed as Record<string, unknown>
        if (view.status === 'completed') newConversation(credentialRef.current)
      }
      if (!conversation.current || !view.messages.length) newConversation(credentialRef.current)
      await conversation.current!.send(taskMode && !view.pending ? '执行本次业务任务。' : draft, values.context, answer, input)
      if (alive.current) setDraft(taskMode ? '{}' : '')
    } catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : '发送失败') }
    finally {
      // 发送与停止分别收口，不能由先完成的一方释放另一方仍持有的忙碌状态。
      sendingRef.current = false; busyRef.current = stoppingRef.current
      if (alive.current) setBusy(busyRef.current)
    }
  }

  useEffect(() => {
    alive.current = true
    void sync()
    return () => {
      alive.current = false
      conversation.current?.dispose()
      // 页签切换保留组件；切换工作区或智能体时释放自己创建的进程。
      if (ownsProcess.current) void control('stop_debug').catch(() => undefined)
    }
  }, [])
  useEffect(() => {
    if (!credential) return
    const timer = setTimeout(() => {
      conversation.current?.stop('调试凭据已过期，结果可能未知，请停止后重新启动。')
      setError('调试凭据已过期，请停止后重新启动。')
      void stop()
    }, Math.max(0, credential.expiresAt * 1000 - Date.now()))
    return () => clearTimeout(timer)
  }, [credential])

  return <div className="agent-debug-panel" data-mode={endpoint?.responseMode}>
    <header className="agent-debug-heading">
      <div className="agent-debug-identity">
        <h3>接口调试</h3>
        <span className="agent-debug-status" role="status" data-active={Boolean(credential && !expired)}>{statusLabel}</span>
      </div>
      <Space size={8} wrap>
        <Button type="text" icon={<ReloadOutlined />} disabled={busy || running} onClick={() => void sync()}>同步状态</Button>
        {(active || credential || external) ? <Button disabled={busy || external} onClick={() => void stop()}>停止调试</Button>
          : <Button loading={busy} disabled={busy || building || !endpoint} onClick={() => void start()}>启动调试</Button>}
      </Space>
    </header>
    <section className="agent-debug-controls" aria-label="调试环境">
      <div className="agent-debug-context"><span>调试接口</span>
        <Select aria-label="调试调用接口" value={targetKey || undefined} disabled={busy || running || external || !endpoints.length}
          placeholder="选择与当前智能体相关的接口"
          optionLabelProp="title" virtual={false}
          getPopupContainer={(trigger) => trigger.parentElement || trigger}
          options={endpoints.map((item) => ({ title: item.label,
            label: <div className="agent-debug-endpoint-option">
              <span className="agent-debug-endpoint-option-name">{item.label}</span>
              <span className="agent-debug-endpoint-option-address"><span>{item.method}</span><code>{item.path}</code></span>
            </div>,
            value: JSON.stringify([item.apiContractId, item.endpointId]) }))} onChange={(key) => {
              const target = endpoints.find((item) => JSON.stringify([item.apiContractId, item.endpointId]) === key)
              // 同一受管进程可调试多个相关接口；切换只清理本地会话，不重复启动服务或发送请求。
              conversation.current?.dispose(); conversation.current = undefined
              setTargetKey(key); setFields(initialDebugFields(target)); setError(''); setJsonResponse(undefined)
              setView({ status: 'idle', messages: [], tools: [] })
              setDraft(target?.responseMode === 'json' || target?.kind === 'business' || agent.interactionContract.mode === 'task' ? '{}' : '')
            }} />
      </div>
      <span className="agent-debug-protocol">{endpoint?.responseMode === 'json' ? '非流式 JSON' : '流式 AG-UI'}</span>
    </section>
    {endpoint && <div className="agent-debug-endpoint"><code>{endpoint.method} {endpoint.path}</code>
      <span>{relationLabels[endpoint.relation]}{endpoint.responseMode === 'ag-ui' ? ` · ${taskMode ? '单次任务' : '多轮对话'}` : ' · 一次性响应'}</span></div>}
    <div className="agent-debug-model"><span>模型</span><strong>{String(agent.agentSettings.model?.model || agent.agentSettings.model?.provider || '跟随应用配置')}</strong></div>
    <p className="agent-debug-notice"><InfoCircleOutlined /><span>使用真实模型、工具和当前数据源，可能写入业务数据；结果不计入开发或正式验收。</span></p>
    {building && <Alert type="info" message="当前智能体正在生成或停止开发任务，请等待代码写入结束后启动调试。" />}
    {!endpoints.length && <Alert type="info" message="暂无可调试接口，请先完成接口开发。" />}
    {error && <Alert type="error" showIcon message={error} />}
    {!building && view.error && <Alert type="error" showIcon message={view.error} />}
    {external && <Alert type="warning" showIcon message="当前工作区由其他调试窗口占用，请在原窗口停止或等待十分钟凭据过期。" />}
    {!building && restartRequired && <Alert type="warning" showIcon message="代码或配置已变化，请停止并重新启动以加载最新内容。" />}
    {/* 开发写入期间不挂载输入与结果区域，避免暴露尚不能使用的调试表单和校验信息。 */}
    {!building && (endpoint?.responseMode === 'json' ? <div className="agent-debug-http-workspace">
      <section className="agent-debug-http-request" aria-label="非流式请求">
        <div className="agent-debug-section-heading"><h4>请求</h4>
          <Button type="primary" disabled={!credential || busy || expired || restartRequired || Boolean(validationErrors.length)}
            loading={busy} onClick={() => void send()}>发送请求</Button></div>
        <AgentDebugRequestEditor key={targetKey} endpoint={endpoint} value={fields} onChange={setFields}
          disabled={busy || running} body={draft} onBodyChange={setDraft} />
        <p className="agent-debug-validation-hint" data-invalid={Boolean(validationErrors.length)} role="status">
          {validationErrors.length ? `请求有 ${validationErrors.length} 项问题：${validationErrors[0]}` : '请求输入校验通过'}
        </p>
      </section>
      <AgentDebugResponse response={jsonResponse} loading={busy && sendingRef.current} />
    </div> : endpoint && <>
      <details className="agent-debug-advanced"><summary>请求参数与上下文<span>可选</span></summary>
        <AgentDebugRequestEditor key={targetKey} endpoint={endpoint} value={fields} onChange={setFields}
          disabled={busy || running || view.messages.length > 0} body={draft} onBodyChange={setDraft} />
      </details>
      <AgentDebugStreamView name={agent.name} view={view} taskMode={taskMode} ready={Boolean(credential)} busy={busy}
        expired={expired} restartRequired={restartRequired} draft={draft} onDraftChange={setDraft}
        inputSchema={endpoint.requestSchema} inputInvalid={Boolean(validationErrors.length)}
        onSend={(answer) => void send(answer)} onStop={() => conversation.current?.stop()}
        onNew={() => { if (credential) { newConversation(credential); setDraft(taskMode ? '{}' : '') } }} />
      <AgentDebugResponse stream={view} />
    </>)}
  </div>
}
