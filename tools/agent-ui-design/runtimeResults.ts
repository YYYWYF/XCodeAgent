import type { AgentUIHost } from '@za38.03/agent-ui-runtime'

export type RuntimeBusinessStatus = 'idle' | 'running' | 'completed' | 'failed' | 'awaiting_user' | 'unknown'
export interface RuntimeBusinessSummary {
  status: RuntimeBusinessStatus
  agentId: string
  threadId: string
  runId: string
  interactionId?: string
  reason?: 'terminal_result_missing' | 'transport_error' | 'conflicting_results'
}
export interface RuntimeBusinessResult extends RuntimeBusinessSummary {
  pendingInteraction?: { interactionId: string; kind: 'clarification' | 'confirmation' | 'approval_required'; message: string; payload: ObjectValue }
  result?: unknown
  error?: { code: string; message: string; recoverable: boolean }
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

/** 非空且有界的文本只参与合同校验。 */
function text(value: unknown, limit = 256): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= limit
}

/** 校验并解码供用户查看的 Python 业务回执。 */
export function decodeRuntimeReceipt(name: string, value: unknown): RuntimeBusinessResult | null {
  if (!object(value) || value.schemaVersion !== 1 || !text(value.agentId)
    || !/^[a-z][a-z0-9_]*$/.test(value.agentId) || !text(value.threadId) || !text(value.runId)) return null
  const identity = { agentId: value.agentId, threadId: value.threadId, runId: value.runId }
  if (name === 'agent_runtime_error.v1') {
    if (!keys(value, ['schemaVersion', 'agentId', 'threadId', 'runId', 'status', 'error'])
      || value.status !== 'failed' || !object(value.error)
      || !keys(value.error, ['code', 'message', 'recoverable']) || !text(value.error.code)
      || !text(value.error.message, 4096) || typeof value.error.recoverable !== 'boolean') return null
    return { ...identity, status: 'failed', error: value.error as RuntimeBusinessResult['error'] }
  }
  if (name !== 'agent_runtime_result.v1'
    || !keys(value, ['schemaVersion', 'agentId', 'threadId', 'runId', 'status', 'pendingInteraction', 'result'])
    || value.result === undefined) return null
  if (value.status === 'completed' && value.pendingInteraction === null) return { ...identity, status: 'completed', result: value.result }
  const pending = value.pendingInteraction
  if (value.status !== 'awaiting_user' || !object(pending)
    || !keys(pending, ['schemaVersion', 'interactionId', 'kind', 'message', 'payload'])
    || pending.schemaVersion !== 1 || !text(pending.interactionId) || !text(pending.message, 4096)
    || typeof pending.kind !== 'string' || !['clarification', 'confirmation', 'approval_required'].includes(pending.kind)
    || !object(pending.payload)) return null
  return { ...identity, status: 'awaiting_user', interactionId: pending.interactionId,
    pendingInteraction: pending as RuntimeBusinessResult['pendingInteraction'] }
}

/** 只投影有界状态摘要，业务正文和错误详情不进入只读观察视图。 */
export function decodeRuntimeResult(name: string, value: unknown): RuntimeBusinessSummary | null {
  const receipt = decodeRuntimeReceipt(name, value)
  if (!receipt) return null
  const { status, agentId, threadId, runId, interactionId } = receipt
  return { status, agentId, threadId, runId, ...(interactionId ? { interactionId } : {}) }
}

/** 只观察同一公开 Host；不持有消息、凭据，不发送、恢复、查询或自动重放请求。 */
export function observeRuntimeResults(host: ReadonlyHost, agentId: string,
  publish: (result: RuntimeBusinessSummary) => void,
  publishReceipt?: (result: RuntimeBusinessResult | null) => void): () => void {
  let current: RuntimeBusinessSummary = { status: 'idle', agentId, threadId: '', runId: '' }
  let candidate: RuntimeBusinessSummary | null = null
  let candidateReceipt: RuntimeBusinessResult | null = null
  let conflicted = false
  let active = true
  let publicRunId = ''
  const settledRuns = new Set<string>()

  /** 仅在业务摘要变化时发布；token 和普通消息更新不会引起额外渲染。 */
  function update(next: RuntimeBusinessSummary, receipt?: RuntimeBusinessResult | null): void {
    if (JSON.stringify(current) !== JSON.stringify(next)) {
      current = next
      publish(next)
    }
    if (receipt !== undefined) publishReceipt?.(receipt)
  }

  /** 以当前公开快照确定运行身份；连接结束且缺少有效业务终态时保留未知。 */
  function synchronize(): void {
    if (!active) return
    const snapshot = host.getSnapshot()
    const runtime = snapshot.status === 'ready' ? snapshot.runtime : null
    const threadId = runtime?.conversation.id ?? ''
    const runId = runtime?.run.id ?? ''
    if (!runtime || current.threadId !== threadId || publicRunId !== runId) {
      if (current.threadId !== threadId) settledRuns.clear()
      publicRunId = runId
      candidate = null
      candidateReceipt = null
      conflicted = false
      update({ status: 'idle', agentId, threadId, runId }, null)
    }
    if (!runtime) return
    if (runtime.run.status === 'running') {
      if (current.status !== 'running') {
        if (current.runId) settledRuns.add(current.runId)
        if (settledRuns.size > 128) settledRuns.delete(settledRuns.values().next().value!)
        candidate = null
        candidateReceipt = null
        conflicted = false
        update({ status: 'running', agentId, threadId, runId }, null)
      }
      return
    }
    if (current.status !== 'running') return
    // 标准 RUN_ERROR 可以闭合明确业务失败；断流或成功候选遇到传输错误仍为未知。
    const terminal = ['idle', 'awaiting-input'].includes(runtime.run.status)
      || (runtime.run.status === 'error' && candidate?.status === 'failed')
    const settled = terminal && !conflicted ? candidate : null
    const reason = runtime.run.status === 'error'
      ? 'transport_error' : conflicted ? 'conflicting_results' : 'terminal_result_missing'
    update(settled ?? { status: 'unknown', agentId, threadId, runId, reason }, settled ? candidateReceipt : null)
    candidateReceipt = null
  }

  const disposers = [host.subscribe(synchronize)]
  for (const name of ['agent_runtime_result.v1', 'agent_runtime_error.v1']) {
    disposers.push(host.onAppEvent(name, (event) => {
      if (!active || event.producer.type !== 'root') return
      synchronize()
      if (current.status !== 'running') return
      const receipt = decodeRuntimeReceipt(name, event.payload)
      const result = decodeRuntimeResult(name, event.payload)
      if (!result || !receipt || result.agentId !== agentId || result.threadId !== current.threadId
        || (publicRunId ? result.runId !== publicRunId : settledRuns.has(result.runId))) return
      // 同一次运行只能有一个一致终态；重复相同回执幂等，冲突回执不能冒充成功。
      if (candidate && (candidate.status !== result.status || candidate.interactionId !== result.interactionId
        || (publishReceipt && JSON.stringify(candidateReceipt) !== JSON.stringify(receipt)))) conflicted = true
      candidate = result
      if (publishReceipt) candidateReceipt = receipt
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

/** 单独订阅用户可见业务回执；摘要观察仍通过 decodeRuntimeResult 丢弃业务正文。 */
export function observeRuntimeReceipts(host: ReadonlyHost, agentId: string,
  publish: (result: RuntimeBusinessResult | null) => void): () => void {
  return observeRuntimeResults(host, agentId, () => undefined, publish)
}
