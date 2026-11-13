import type { AgentUIHost } from '@za38.03/agent-ui-runtime'

export type RuntimeBusinessStatus = 'idle' | 'running' | 'completed' | 'failed' | 'awaiting_user' | 'unknown'
export interface RuntimeBusinessResult {
  status: RuntimeBusinessStatus
  agentId: string
  threadId: string
  runId: string
  interactionId?: string
  reason?: 'run_identity_unavailable' | 'terminal_result_missing' | 'transport_error' | 'conflicting_results'
}

type ReadonlyHost = Pick<AgentUIHost, 'getSnapshot' | 'subscribe' | 'onAppEvent'>
type ObjectValue = Record<string, unknown>

/** 仅接受对象，数组和空值不能充当公开事件载荷。 */
function object(value: unknown): value is ObjectValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/** 对齐当前 Python 公开合同，拒绝扩展字段，不能隐式兼容其他版本。 */
function keys(value: ObjectValue, expected: readonly string[]): boolean {
  return Object.keys(value).length === expected.length && expected.every((key) => Object.hasOwn(value, key))
}

/** 非空且有界的文本只参与合同校验，不将错误详情或业务载荷存入观察视图。 */
function text(value: unknown, limit = 256): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= limit
}

/** 校验当前 Python 自定义结果 Schema；公开 Host 订阅不会经过插件的 appEventSchemas。 */
export function decodeRuntimeResult(name: string, value: unknown): RuntimeBusinessResult | null {
  if (!object(value) || value.schemaVersion !== 1 || !text(value.agentId)
    || !/^[a-z][a-z0-9_]*$/.test(value.agentId) || !text(value.threadId) || !text(value.runId)) return null
  const identity = { agentId: value.agentId, threadId: value.threadId, runId: value.runId }
  if (name === 'agent_runtime_error.v1') {
    if (!keys(value, ['schemaVersion', 'agentId', 'threadId', 'runId', 'status', 'error'])
      || value.status !== 'failed' || !object(value.error)
      || !keys(value.error, ['code', 'message', 'recoverable']) || !text(value.error.code)
      || !text(value.error.message, 4096) || typeof value.error.recoverable !== 'boolean') return null
    return { ...identity, status: 'failed' }
  }
  // Python 成功和待交互回执始终携带 result（无业务输出时为 null）；这里只观察状态，不保存业务正文。
  if (name !== 'agent_runtime_result.v1'
    || !keys(value, ['schemaVersion', 'agentId', 'threadId', 'runId', 'status', 'pendingInteraction', 'result'])
    || value.result === undefined) return null
  if (value.status === 'completed' && value.pendingInteraction === null) return { ...identity, status: 'completed' }
  const pending = value.pendingInteraction
  if (value.status !== 'awaiting_user' || !object(pending)
    || !keys(pending, ['schemaVersion', 'interactionId', 'kind', 'message', 'payload'])
    || pending.schemaVersion !== 1 || !text(pending.interactionId) || !text(pending.message, 4096)
    || typeof pending.kind !== 'string' || !['clarification', 'confirmation', 'approval_required'].includes(pending.kind)
    || !object(pending.payload)) return null
  return { ...identity, status: 'awaiting_user', interactionId: pending.interactionId }
}

/** 只观察同一公开 Host；不持有消息、凭据，不发送、恢复、查询或自动重放请求。 */
export function observeRuntimeResults(host: ReadonlyHost, agentId: string,
  publish: (result: RuntimeBusinessResult) => void): () => void {
  let current: RuntimeBusinessResult = { status: 'idle', agentId, threadId: '', runId: '' }
  let candidate: RuntimeBusinessResult | null = null
  let conflicted = false
  let active = true

  /** 仅在业务摘要变化时发布；token 和普通消息更新不会引起额外渲染。 */
  function update(next: RuntimeBusinessResult): void {
    if (JSON.stringify(current) === JSON.stringify(next)) return
    current = next
    publish(next)
  }

  /** 以当前公开快照确定运行身份；连接结束且缺少有效业务终态时保留未知。 */
  function synchronize(): void {
    if (!active) return
    const snapshot = host.getSnapshot()
    const runtime = snapshot.status === 'ready' ? snapshot.runtime : null
    const threadId = runtime?.conversation.id ?? ''
    const runId = runtime?.run.id ?? ''
    if (!runtime || current.threadId !== threadId || current.runId !== runId) {
      candidate = null
      conflicted = false
      update({ status: 'idle', agentId, threadId, runId })
    }
    if (!runtime) return
    if (runtime.run.status === 'running') {
      if (current.status !== 'running') {
        candidate = null
        conflicted = false
        update({ status: 'running', agentId, threadId, runId })
      }
      return
    }
    if (current.status !== 'running') return
    const settled = runtime.run.status === 'idle' && !conflicted ? candidate : null
    const reason = !runId ? 'run_identity_unavailable' : runtime.run.status === 'error'
      ? 'transport_error' : conflicted ? 'conflicting_results' : 'terminal_result_missing'
    update(settled ?? { status: 'unknown', agentId, threadId, runId, reason })
  }

  const disposers = [host.subscribe(synchronize)]
  for (const name of ['agent_runtime_result.v1', 'agent_runtime_error.v1']) {
    disposers.push(host.onAppEvent(name, (event) => {
      if (!active || event.producer.type !== 'root') return
      synchronize()
      if (current.status !== 'running' || !current.runId) return
      const result = decodeRuntimeResult(name, event.payload)
      if (!result || result.agentId !== agentId || result.threadId !== current.threadId
        || result.runId !== current.runId) return
      // 同一次运行只能有一个一致终态；重复相同回执幂等，冲突回执不能冒充成功。
      if (candidate && (candidate.status !== result.status || candidate.interactionId !== result.interactionId)) conflicted = true
      candidate = result
    }))
  }
  synchronize()
  publish(current)
  /** 卸载或切身份后先封闭回调，再释放订阅，迟到事件不能写入下一会话。 */
  return (): void => {
    active = false
    for (const dispose of disposers) dispose()
    candidate = null
  }
}
