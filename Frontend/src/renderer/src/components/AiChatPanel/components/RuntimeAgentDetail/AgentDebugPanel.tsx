import { Alert, Button, Collapse, Input, Select, Space, Typography } from 'antd'
import { InfoCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { randomUUID } from '@ag-ui/client'
import { useEffect, useRef, useState } from 'react'
import type { RuntimeAgent, DebugCredential } from '../../../../service/runtimeWorkbench'
import { requestRuntimeDebug } from '../../../../service/runtimeWorkbench'
import { RuntimeDebugConversation } from '../../../../service/runtimeDebugConversation'
import type { DebugConversation } from '../../../../service/runtimeDebugConversation'
import './AgentDebugPanel.less'

const labels = { idle: '就绪', running: '运行中', completed: '已完成', awaiting_user: '等待交互', unknown: '结果未知' }

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
  const [draft, setDraft] = useState('')
  const [pageId, setPageId] = useState(agent.pageIds[0] || 'debug')
  const [view, setView] = useState<DebugConversation>({ status: 'idle', messages: [], tools: [] })
  const running = view.status === 'running'
  const building = ['running', 'stopping'].includes(agent.development?.execution?.status || '')
  const expired = Boolean(credential && Date.now() >= credential.expiresAt * 1000)
  const statusLabel = busy ? '正在处理…' : credential ? expired ? '已过期' : labels[view.status]
    : external ? '其他窗口调试中' : active ? '等待同步' : '未启动'

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
    conversation.current = new RuntimeDebugConversation(value, agent.agentId, (next) => { if (alive.current) setView(next) })
    setView({ status: 'idle', messages: [], tools: [] }); setDraft('')
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
      if (!conversation.current) throw new Error('调试客户端尚未就绪，请停止后重新启动。')
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
      if (!conversation.current || !credentialRef.current || Date.now() >= credentialRef.current.expiresAt * 1000) {
        throw new Error('调试会话已停止或过期，请重新启动。')
      }
      await conversation.current.send(draft, pageId, answer)
      if (alive.current) setDraft('')
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

  return <div className="agent-debug-panel">
    <header className="agent-debug-heading">
      <div className="agent-debug-identity">
        <h3>{agent.name} · 调试</h3>
        <span className="agent-debug-status" role="status" data-active={Boolean(credential && !expired)}>{statusLabel}</span>
      </div>
      <Space size={8} wrap>
        <Button type="text" icon={<ReloadOutlined />} disabled={busy || running} onClick={() => void sync()}>同步状态</Button>
        {(active || credential || external) ? <Button disabled={busy || external} onClick={() => void stop()}>停止调试</Button>
          : <Button type="primary" loading={busy} disabled={busy || building} onClick={() => void start()}>启动调试</Button>}
      </Space>
    </header>
    <section className="agent-debug-controls" aria-label="调试环境">
      <div className="agent-debug-model"><span>模型</span><strong>{String(agent.agentSettings.model?.model || agent.agentSettings.model?.provider || '跟随应用配置')}</strong></div>
      <div className="agent-debug-context"><span>页面上下文</span>
        {/* 页面选项留在当前面板中，继承明暗主题变量。 */}
        <Select aria-label="调试页面上下文" value={pageId} disabled={busy || running || view.messages.length > 0}
          getPopupContainer={(trigger) => trigger.parentElement || trigger}
          options={(agent.pageIds.length ? agent.pageIds : ['debug']).map((id) => ({ label: id, value: id }))} onChange={setPageId} />
      </div>
    </section>
    <p className="agent-debug-notice"><InfoCircleOutlined /><span>使用真实模型、工具和当前数据源，可能写入业务数据；结果不计入开发或正式验收。</span></p>
    {building && <Alert type="info" message="当前智能体正在生成或停止开发任务，请等待代码写入结束后启动调试。" />}
    {error && <Alert type="error" showIcon message={error} />}
    {view.error && <Alert type="error" showIcon message={view.error} />}
    {external && <Alert type="warning" showIcon message="当前工作区由其他调试窗口占用，请在原窗口停止或等待十分钟凭据过期。" />}
    {restartRequired && <Alert type="warning" showIcon message="代码或配置已变化，请停止并重新启动以加载最新内容。" />}
    <div className="agent-debug-workspace">
    <section className="agent-debug-conversation" aria-label="调试对话">
    <div className="agent-debug-section-heading"><h4>调试对话</h4>
      <Button type="text" size="small" disabled={!credential || busy || expired || running} onClick={() => { if (credential) newConversation(credential) }}>新建会话</Button>
    </div>
    <div className={`agent-debug-messages${!view.messages.length ? ' is-empty' : ''}`} role="log" aria-live="polite" aria-relevant="additions text">
      {!view.messages.length && <div className="agent-debug-empty">
        <h4>{credential ? '发送第一条调试消息' : '开始一次调试'}</h4>
        <p>{credential ? '环境已就绪，输入问题查看智能体回复。' : '启动环境后，即可发送问题并查看回复与工具调用。'}</p>
      </div>}
      {view.messages.map((item) => <article className={`agent-debug-message is-${item.role === 'user' ? 'user' : 'assistant'}`} key={item.id}>
        <strong>{item.role === 'user' ? '我' : agent.name}</strong><p>{item.content}</p></article>)}
    </div>
    <section className="agent-debug-composer" aria-label="调试消息输入">
    {view.pending && <Alert type="warning" showIcon message={view.pending.message} description={view.pending.kind === 'clarification'
      ? '请在下方输入回答并提交。' : <Space><Button disabled={running || busy || expired || restartRequired} onClick={() => void send(true)}>同意</Button>
        <Button disabled={running || busy || expired || restartRequired} onClick={() => void send(false)}>拒绝</Button></Space>} />}
    <Input.TextArea aria-label="调试消息" autoSize={{ minRows: 2, maxRows: 5 }} maxLength={8000} value={draft} onChange={(event) => setDraft(event.target.value)}
      disabled={!credential || busy || expired || running || restartRequired || view.status === 'unknown' || Boolean(view.pending && view.pending.kind !== 'clarification')}
      placeholder={credential ? '输入调试问题或澄清回答…' : '启动调试后，在这里输入问题…'} />
    <div className="agent-debug-composer-footer">
    <Typography.Text type="secondary">仅保留当前面板内的对话</Typography.Text>
    <Space size={8}>
      {running && <Button onClick={() => conversation.current?.stop()}>停止接收</Button>}
      <Button type="primary" disabled={!credential || busy || expired || running || restartRequired || !draft.trim() || view.status === 'unknown'
      || Boolean(view.pending && view.pending.kind !== 'clarification')}
      onClick={() => void send(view.pending ? draft : undefined)}>{view.pending ? '提交回答' : '发送'}</Button></Space>
    </div>
    </section>
    </section>
    <aside className="agent-debug-tools" aria-label="工具调用">
      <div className="agent-debug-section-heading"><h4>工具调用</h4><span>{view.tools.length ? `最近 ${view.tools.length} 条` : '暂无事件'}</span></div>
      {view.tools.length === 0 ? <div className="agent-debug-tools-empty">
        <strong>等待工具调用事件</strong>
        <p>对话中的工具参数与结果会显示在这里。</p>
        <p>尚未收到事件时，无法判断内部是否调用过工具。</p>
      </div> : <>
        <p className="agent-debug-tools-caption">保留最近 40 条，参数和结果各最多展示 4096 字符。</p>
        <Collapse ghost>
          {view.tools.map((tool) => <Collapse.Panel key={tool.id} header={<span className="agent-debug-tool-title"><strong>{tool.name}</strong>
            <span>{tool.elapsed === undefined ? '运行中／未收到结果' : `${tool.elapsed} ms`}</span></span>}>
            <span className="agent-debug-tool-label">参数</span><pre>{tool.args || '暂无参数'}</pre>
            <span className="agent-debug-tool-label">结果</span><pre>{tool.result || '尚未收到结果'}</pre>
          </Collapse.Panel>)}
        </Collapse>
      </>}
    </aside>
    </div>
  </div>
}
