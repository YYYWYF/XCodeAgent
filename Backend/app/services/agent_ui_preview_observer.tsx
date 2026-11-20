import { useEffect as useObservationEffect, useState as useObservationState } from 'react'
import { createPortal as createObservationPortal } from 'react-dom'
import { useOptionalAgentUI as useObservationHost, type AgentUIHost } from '@za38.03/agent-ui-runtime'
import { useRuntimeResult as useObservationBusinessResult } from './__agent_ui_observation__/useRuntimeResult'
import { useSharedContextObservation as useObservationContext } from './__agent_ui_observation__/runtimeContext'

interface ObservationContext {
  pageId: string
  actionId: string
  fields: readonly { name: string; source: 'page_record' | 'page_input' | 'form_input' }[]
  truncated: boolean
  hasDescription: boolean
  revision: number
  updatedAt: number
}

interface PreviewHostObservation {
  connection: string
  threadId: string | null
  runId: string | null
  runStatus: string
  messages: number
  pending: number
  eventCount: number
  events: Array<{ sequence: number; type: string; runVerified: boolean; observedAt: number; elapsedMs: number }>
  tools: Array<{ id: string; status: string; durationMs: number }>
}

/** 只接受有界标识，不复制公开快照中的消息、状态、错误或 Context 内容。 */
function observationId(value: unknown): string | null {
  return typeof value === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value) ? value : null
}

/** 订阅同一个公开 Host；事件仅表示观察到，不作为业务完成或批准证据。 */
export function observePreviewHost(host: Pick<AgentUIHost, 'getSnapshot' | 'subscribe' | 'on' | 'onAppEvent'>,
  publish: (value: PreviewHostObservation) => void): () => void {
  let active = true
  let current: PreviewHostObservation | null = null
  let events: PreviewHostObservation['events'] = []
  let eventCount = 0
  let started = performance.now()
  const tools = new Map<string, { id: string; status: string; started: number; durationMs: number }>()

  /** 线程变化立即隔离前项摘要；token 增量不会重复发布未变化的计数。 */
  function synchronize(): PreviewHostObservation {
    const snapshot = host.getSnapshot()
    const runtime = snapshot.status === 'ready' ? snapshot.runtime : null
    const threadId = observationId(runtime?.conversation.id)
    const runId = observationId(runtime?.run.id)
    if (!runtime || current?.threadId !== threadId) { events = []; eventCount = 0; tools.clear(); started = performance.now() }
    // 只消费公开执行投影的身份与状态，绝不复制 arguments/result/error 或消息正文。
    for (const execution of (runtime?.executions ?? []).slice(-64)) {
      if (execution.type !== 'tool' || execution.producer.type !== 'root' || !observationId(execution.id)) continue
      let tool = tools.get(execution.id)
      if (!tool && tools.size < 32) {
        tool = { id: `tool-${tools.size + 1}`, status: 'preparing', started: performance.now(), durationMs: 0 }
        tools.set(execution.id, tool)
      }
      if (!tool) continue
      const status = ['preparing', 'awaiting-result', 'completed', 'error', 'interrupted'].includes(execution.status)
        ? execution.status === 'completed' ? 'returned' : execution.status : 'unknown'
      if (tool.status !== status) {
        tool.status = status
        tool.durationMs = Math.max(0, Math.round(performance.now() - tool.started))
      }
    }
    const next: PreviewHostObservation = {
      connection: ['initializing', 'ready', 'blocked', 'error'].includes(snapshot.status) ? snapshot.status : 'blocked',
      threadId, runId,
      runStatus: runtime && ['idle', 'running', 'awaiting-input', 'error'].includes(runtime.run.status)
        ? runtime.run.status : 'unavailable',
      messages: Math.min(runtime?.messages.length ?? 0, 10000),
      pending: Math.min(runtime?.interrupts.length ?? 0, 1000), eventCount, events: [...events],
      tools: [...tools.values()].map(({ id, status, durationMs }) => ({ id, status, durationMs }))
    }
    if (active && JSON.stringify(current) !== JSON.stringify(next)) { current = next; publish(next) }
    return next
  }

  /** 只保留固定事件名和身份核对结果，拒绝其他线程和有可信身份时的旧 run。 */
  function observed(type: string, thread: unknown, run: unknown): void {
    if (!active) return
    const snapshot = synchronize()
    if (typeof thread === 'string' && thread !== snapshot.threadId) return
    if (snapshot.runId && typeof run === 'string' && run !== snapshot.runId) return
    if (eventCount >= 1000000) return
    if (type === 'run-start') { started = performance.now(); tools.clear() }
    eventCount += 1
    events = [...events.slice(-19), { sequence: eventCount, type,
      observedAt: Date.now(), elapsedMs: Math.max(0, Math.round(performance.now() - started)),
      runVerified: Boolean(snapshot.runId && snapshot.runId === run) }]
    synchronize()
  }

  const disposers = [host.subscribe(() => { if (active) synchronize() })]
  for (const name of ['ready', 'status-change', 'thread-change', 'run-start', 'run-end'] as const) {
    disposers.push(host.on(name, payload => {
      const value = payload as { threadId?: string; run?: { id?: string } }
      observed(name, value.threadId, value.run?.id)
    }))
  }
  for (const name of ['agent_runtime_result.v1', 'agent_runtime_error.v1']) {
    disposers.push(host.onAppEvent(name, event => {
      if (!active || event.producer.type !== 'root') return
      const value = event.payload
      if (!value || typeof value !== 'object' || Array.isArray(value)) return
      observed(name, (value as Record<string, unknown>).threadId, (value as Record<string, unknown>).runId)
    }))
  }
  synchronize()
  /** 释放前封闭所有回调，重启、切来源或卸载不能留下旧观察者。 */
  return (): void => { active = false; for (const dispose of disposers) dispose(); events = []; tools.clear() }
}

type StandaloneLayoutIssue = 'conversation' | 'composer' | null
const standaloneSelector = '#shared-agent-conversation.is-standalone:not([hidden])'
const composerSelector = '.agent-ui-platform-shell textarea'

/** 只读取会话及编辑框的可见几何范围，不访问业务数据或输入值。 */
export function standaloneLayoutIssue(doc: Document): StandaloneLayoutIssue {
  const conversation = doc.querySelector<HTMLElement>(standaloneSelector)
  const viewport = doc.defaultView
  if (!conversation || !viewport) return null
  /** 排除零尺寸和完全落在可视区域外的元素。 */
  const overlaps = (a: DOMRect, b: Pick<DOMRect, 'left' | 'top' | 'right' | 'bottom'>): boolean =>
    Math.min(a.right, b.right) > Math.max(a.left, b.left) &&
    Math.min(a.bottom, b.bottom) > Math.max(a.top, b.top)
  const screen = { left: 0, top: 0, right: viewport.innerWidth, bottom: viewport.innerHeight }
  const bounds = conversation.getBoundingClientRect()
  if (!overlaps(bounds, screen)) return 'conversation'
  // 未连接的宿主没有编辑框，此时按原有连接状态提示。
  if (!conversation.querySelector('.agent-ui-platform-shell')) return null
  const composer = conversation.querySelector<HTMLTextAreaElement>(composerSelector)
  if (!composer || composer.hidden || viewport.getComputedStyle(composer).visibility === 'hidden') return 'composer'
  const input = composer.getBoundingClientRect()
  return overlaps(input, {
    left: Math.max(bounds.left, screen.left), top: Math.max(bounds.top, screen.top),
    right: Math.min(bounds.right, screen.right), bottom: Math.min(bounds.bottom, screen.bottom)
  }) ? null : 'composer'
}

/** 观察页切换、视口和控件尺寸，反馈只留在受管预览，不改变会话执行。 */
function useStandaloneLayoutIssue(active: boolean): StandaloneLayoutIssue {
  const [issue, setIssue] = useObservationState<StandaloneLayoutIssue>(null)
  useObservationEffect(() => {
    if (!active) return
    let frame: number | undefined
    let observed: readonly (Element | null)[] = []
    /** 合并同一帧的观察事件，避免流式响应逐个 DOM 变化触发布局读取。 */
    const schedule = (): void => {
      if (frame !== undefined) return
      frame = window.requestAnimationFrame(() => {
        frame = undefined
        const section = document.querySelector(standaloneSelector)
        const nodes = [section, section?.querySelector(composerSelector) ?? null]
        if (nodes.some((node, index) => node !== observed[index])) {
          resize.disconnect()
          for (const node of nodes) if (node) resize.observe(node)
          observed = nodes
        }
        setIssue(standaloneLayoutIssue(document))
      })
    }
    const resize = new ResizeObserver(schedule)
    const changes = new MutationObserver(schedule)
    changes.observe(document.body, { subtree: true, childList: true, attributes: true,
      attributeFilter: ['hidden', 'class', 'style'] })
    window.addEventListener('resize', schedule)
    window.addEventListener('scroll', schedule, true)
    schedule()
    return () => {
      resize.disconnect()
      changes.disconnect()
      window.removeEventListener('resize', schedule)
      window.removeEventListener('scroll', schedule, true)
      if (frame !== undefined) window.cancelAnimationFrame(frame)
    }
  }, [active])
  return active ? issue : null
}

/** 受管开发编译中的只读面板；不进入正式磁盘源码和生产构建。 */
export function AgentUiPreviewObserver({ epoch, source, agentId }: { epoch: string; source: string; agentId: string }): React.ReactElement | null {
  const host = useObservationHost()
  const context: ObservationContext | null = useObservationContext()
  const business = useObservationBusinessResult(agentId || undefined)
  const [observation, setObservation] = useObservationState<{
    host: AgentUIHost; epoch: string; value: PreviewHostObservation
  } | null>(null)
  const [scheme, setScheme] = useObservationState<'light' | 'dark'>('light')
  const layoutIssue = useStandaloneLayoutIssue(Boolean(host))
  useObservationEffect(() => {
    if (!host) return
    return observePreviewHost(host, value => setObservation({ host, epoch, value }))
  }, [host, epoch, source, agentId])
  useObservationEffect(() => {
    /** 仅同步公开主题属性，避免采集任何业务 DOM 或消息正文。 */
    const update = (): void => setScheme(document.querySelector('[data-agent-ui-root]')?.getAttribute('data-color-scheme') === 'dark' ? 'dark' : 'light')
    const observer = new MutationObserver(update)
    observer.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['data-color-scheme'] })
    update()
    return () => observer.disconnect()
  }, [])
  const value = observation?.host === host && observation?.epoch === epoch ? observation.value : null
  if (!host) return null
  return createObservationPortal(<details data-agent-ui-observation="readonly" style={{
    position: 'fixed', top: 8, right: 8, zIndex: 2147483646, width: 'min(360px, calc(100vw - 16px))',
    maxHeight: '65vh', overflow: 'auto', padding: 8, border: '1px solid currentColor',
    colorScheme: scheme, background: 'Canvas', color: 'CanvasText', font: '12px/1.5 system-ui,sans-serif', overflowWrap: 'anywhere'
  }}>
    <summary>会话与事件观察 · 只读{layoutIssue && ' · 布局异常'}</summary>
    {layoutIssue && <p role="status" data-observation-layout-issue={layoutIssue}>
      {layoutIssue === 'conversation' ? '独立页会话没有可见区域，请检查页面布局。' : '独立页的消息输入框不可见，请检查会话布局。'}
    </p>}
    <p>来源：{source === 'replay' ? '脱敏录制回放 · 不执行业务' : source === 'official-mock' ? 'Mock · 模拟回复' : source === 'formal-business'
      ? '真实 Python · 业务接线' : source === 'formal-text' ? '真实 Python · 正式文本接线' : '真实 Python · 开发文本接线'}</p>
    <p>预览会话：{epoch}</p>
    <p>连接：{value?.connection || 'initializing'} · 运行：{value?.runStatus || 'unavailable'}</p>
    <p>线程：{value?.threadId || '未提供'}<br />公开 runId：{value?.runId || '包未提供；按当前 Agent/线程核对业务回执'}</p>
    <p data-observation-business-status={business?.status || 'idle'}>业务状态：{!agentId ? 'Mock 不生成真实业务证据' : !business || business.status === 'idle'
      ? '尚无本轮结果' : ({ running: '运行中', completed: '已收到完成回执', failed: '已收到失败回执',
        awaiting_user: '等待用户处理', unknown: '结果未知，不自动重发' } as const)[business.status]}</p>
    {business?.runId && <p>业务回执 runId：{observationId(business.runId) || '标识已隐藏'}</p>}
    <p>Host 消息数：{value?.messages ?? 0} · Host 待办数：{value?.pending ?? 0} · 观察事件：{value?.eventCount ?? 0}</p>
    <ol>{value?.events.map(event => <li key={event.sequence}>
      <time dateTime={new Date(event.observedAt).toISOString()}>{new Date(event.observedAt).toLocaleTimeString()}</time>
      {' '}+{event.elapsedMs} ms · {event.type} · {event.runVerified ? '公开运行身份匹配' : '未核对公开 runId'}</li>)}</ol>
    {!!value?.tools.length && <section aria-label="工具关联摘要"><p>工具关联（匿名编号，本地观察耗时）</p>
      <ul>{value.tools.map(tool => <li key={tool.id}>{tool.id} · {tool.status} · {tool.durationMs} ms · 参数/结果/错误原文已隐藏</li>)}</ul>
      <p>工具返回或运行结束不等于业务成功；计时表示本地首次观察至状态变化。</p></section>}
    <section data-observation-context data-context-revision={context?.revision} aria-label="上下文观察">
      <p>当前页面：{context?.pageId || (source === 'formal-business' ? '等待页面声明' : '此来源不声明业务 Context')} · 动作：{context?.actionId || '—'}</p>
      {context && <><p>本地摘要更新时间：<time dateTime={new Date(context.updatedAt).toISOString()}>{new Date(context.updatedAt).toLocaleTimeString()}</time> · 修订 {context.revision}</p>
        <ul>{context.fields.map((field, index) => <li key={`${field.source}:${index}`}>{field.name} · {field.source === 'page_record' ? '页面记录' : field.source === 'page_input' ? '页面业务输入' : '表单业务输入'}</li>)}</ul>
        {!context.fields.length && <p>当前页面未声明记录或输入字段。</p>}
        {context.truncated && <p>仅显示前 32 项字段。</p>}
        <p>描述性 Context：{context.hasDescription ? '已声明，原文隐藏' : '未声明'}。此处为本地最新声明，不证明服务端已接收。</p></>}
    </section>
    <p>仅保留最近 20 项事件与 32 项字段元数据，不记录正文、Context 值、错误详情或凭据。观察不发请求，查询与恢复仍由原业务面板显式操作；等待、流结束和事件到达均不代表业务成功。</p>
  </details>, document.body)
}
