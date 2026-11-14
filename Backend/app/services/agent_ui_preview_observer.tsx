import { useEffect as useObservationEffect, useState as useObservationState } from 'react'
import { createPortal as createObservationPortal } from 'react-dom'
import { useOptionalAgentUI as useObservationHost, type AgentUIHost } from '@za38.03/agent-ui-runtime'

interface PreviewHostObservation {
  connection: string
  threadId: string | null
  runId: string | null
  runStatus: string
  messages: number
  pending: number
  eventCount: number
  events: Array<{ sequence: number; type: string; runVerified: boolean }>
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

  /** 线程变化立即隔离前项摘要；token 增量不会重复发布未变化的计数。 */
  function synchronize(): PreviewHostObservation {
    const snapshot = host.getSnapshot()
    const runtime = snapshot.status === 'ready' ? snapshot.runtime : null
    const threadId = observationId(runtime?.conversation.id)
    const runId = observationId(runtime?.run.id)
    if (!runtime || current?.threadId !== threadId) { events = []; eventCount = 0 }
    const next: PreviewHostObservation = {
      connection: ['initializing', 'ready', 'blocked', 'error'].includes(snapshot.status) ? snapshot.status : 'blocked',
      threadId, runId,
      runStatus: runtime && ['idle', 'running', 'awaiting-input', 'error'].includes(runtime.run.status)
        ? runtime.run.status : 'unavailable',
      messages: Math.min(runtime?.messages.length ?? 0, 10000),
      pending: Math.min(runtime?.interrupts.length ?? 0, 1000), eventCount, events: [...events]
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
    eventCount += 1
    events = [...events.slice(-19), { sequence: eventCount, type,
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
  return (): void => { active = false; for (const dispose of disposers) dispose(); events = [] }
}

/** 受管开发编译中的只读面板；不进入正式磁盘源码和生产构建。 */
export function AgentUiPreviewObserver({ epoch, source }: { epoch: string; source: string }): React.ReactElement | null {
  const host = useObservationHost()
  const [observation, setObservation] = useObservationState<{
    host: AgentUIHost; epoch: string; value: PreviewHostObservation
  } | null>(null)
  const [scheme, setScheme] = useObservationState<'light' | 'dark'>('light')
  useObservationEffect(() => {
    if (!host) return
    return observePreviewHost(host, value => setObservation({ host, epoch, value }))
  }, [host, epoch, source])
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
    <summary>会话与事件观察 · 只读</summary>
    <p>来源：{source === 'official-mock' ? 'Mock' : '真实 Python · 仅文本'}</p>
    <p>预览会话：{epoch}</p>
    <p>连接：{value?.connection || 'initializing'} · 运行：{value?.runStatus || 'unavailable'}</p>
    <p>线程：{value?.threadId || '未提供'}<br />可信 runId：{value?.runId || '未提供，业务归属未验证'}</p>
    <p>消息数：{value?.messages ?? 0} · 待办数：{value?.pending ?? 0} · 观察事件：{value?.eventCount ?? 0}</p>
    <ol>{value?.events.map(event => <li key={event.sequence}>{event.sequence}. {event.type} · {event.runVerified ? '运行身份匹配' : '运行归属未验证'}</li>)}</ol>
    <p>仅保留最近 20 项类型摘要，不记录正文、Context 值或凭据。等待、流结束和事件到达均不代表业务成功；业务 Context 和待办同步尚未接通。</p>
  </details>, document.body)
}
