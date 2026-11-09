import type { ApplicationLifecycle, WorkflowRunPayload } from '../typings'
import { beginConnectionRequest, completeConnectionRequest, failConnectionRequest, initialConnectionState, type ConnectionState } from './connectionState'
import { isAuthenticationFailure } from './authentication'
import { subscribeBackendConnection } from './backendConnection'

export type RecoveryOutput = {
  requestId: string; runId: string; threadId: string; ownerSessionId?: string
  startedAt: string; submissionOrder: number; sequence: number; finished: boolean; content: string
  processSteps: import('./agUiAgent').ProcessStepRecord[]; toolCalls: import('./agUiAgent').ToolCallRecord[]
}
export type RecoverySnapshot = {
  applicationId: string; threadId: string; sessionId?: string | null; kind: 'planning' | 'session'
  lifecycleRevision: number; checkpointAvailable: boolean; checkpointId?: string | null
  checkpointAt?: string | null; executionUpdatedAt?: string | null; observedAt: string
  startedAt?: string | null
  workflow: WorkflowRunPayload | null; output: RecoveryOutput | null
  documentReview?: { status: 'available' | 'blocked'; kind?: string; digest?: string; message?: string } | null
}
type RecoveryEntry = {
  lifecycle?: ApplicationLifecycle; connection: ConnectionState; syncError?: string
  failures: number; nextReadAt: number; authenticationBlocked: boolean
}

/** 业务拒绝、读取版本漂移和恢复投影校验失败均已有服务响应，不能冒充网络断连。 */
export function isRecoveryStateReadError(reason: unknown): boolean {
  return reason instanceof Error && ['ApplicationLifecycleActionError', 'RecoveryReadStaleError',
    'ApplicationPlanningRecoveryReadError'].includes(reason.name)
}

/** 同一业务 revision 中按真实 checkpoint、提交和输出序号拒绝回退，查询完成时间不能冒充业务版本。 */
export function recoverySnapshotCanReplace(previous: RecoverySnapshot | null | undefined, incoming: RecoverySnapshot): boolean {
  if (!previous || previous.applicationId !== incoming.applicationId || previous.threadId !== incoming.threadId || previous.sessionId !== incoming.sessionId) return true
  if (incoming.lifecycleRevision !== previous.lifecycleRevision) return incoming.lifecycleRevision > previous.lifecycleRevision
  if (incoming.checkpointAt && previous.checkpointAt && incoming.checkpointAt !== previous.checkpointAt)
    return Date.parse(incoming.checkpointAt) > Date.parse(previous.checkpointAt)
  const before = previous.output, after = incoming.output
  if (before && after && before.submissionOrder !== after.submissionOrder) return after.submissionOrder > before.submissionOrder
  if (before && after && before.requestId === after.requestId && before.sequence !== after.sequence) return after.sequence > before.sequence
  return Date.parse(incoming.observedAt) >= Date.parse(previous.observedAt)
}

/** 保留必要应用事实，只合并同一读取范围内不会后退的恢复投影。 */
export function mergeRecoverySnapshot(base: ApplicationLifecycle, current: ApplicationLifecycle, incoming: ApplicationLifecycle): ApplicationLifecycle {
  const snapshot = incoming.extensions.recoverySnapshot
  const previous = current.extensions.recoverySnapshot
  if (!snapshot) return base
  const selected = incoming.revision >= current.revision && recoverySnapshotCanReplace(previous, snapshot) ? snapshot : previous
  return selected && base.extensions.recoverySnapshot !== selected ? { ...base, extensions: { ...base.extensions, recoverySnapshot: selected } } : base
}

/** 统一规划与会话的读取、连接、退避和失效屏障，不持久化第二套业务状态机。 */
export class RecoveryStore {
  private entries = new Map<string, RecoveryEntry>()
  private requests = new Map<string, Promise<ApplicationLifecycle>>()
  private listeners = new Map<string, Set<() => void>>()
  private generation = 0

  /** 换传输只撤销在途读取并清除退避；保留业务快照、结束事实和认证门禁。 */
  reconnectTransport(): void {
    for (const key of this.entries.keys()) {
      const generation = this.invalidate(key)
      this.update(key, { failures: 0, nextReadAt: 0,
        connection: failConnectionRequest(this.get(key).connection, generation, '后台连接正在恢复，请稍候。') })
    }
  }

  /** 登录环境释放或测试隔离时清空读取缓存；全局代次继续递增，旧请求不能命中新会话。 */
  clear(): void {
    this.requests.clear()
    this.entries.clear()
    this.generation++
    this.listeners.forEach((listeners) => listeners.forEach((listener) => listener()))
  }

  /** 工作区、应用、线程及会话共同隔离恢复状态。 */
  key(workspace: string, applicationId: string, threadId: string, sessionId?: string): string {
    return JSON.stringify([workspace, applicationId, threadId, sessionId || null])
  }
  /** 返回稳定引用，供 React 外部 store 订阅和 Runtime 共用。 */
  get(key: string, initialLifecycle?: ApplicationLifecycle): RecoveryEntry {
    let entry = this.entries.get(key)
    if (!entry) { entry = { lifecycle: initialLifecycle, connection: initialConnectionState(Boolean(initialLifecycle)), failures: 0, nextReadAt: 0, authenticationBlocked: false }; this.entries.set(key, entry) }
    return entry
  }
  /** 发布一份原子恢复状态，避免连接成功先于业务快照可见。 */
  private update(key: string, patch: Partial<RecoveryEntry>): void {
    this.entries.set(key, { ...this.get(key), ...patch })
    this.listeners.get(key)?.forEach((listener) => listener())
  }
  /** 订阅当前读取范围，卸载只移除观察者，不取消后台业务执行。 */
  subscribe(key: string, listener: () => void): () => void {
    const listeners = this.listeners.get(key) || new Set<() => void>()
    listeners.add(listener); this.listeners.set(key, listeners)
    return () => { listeners.delete(listener); if (!listeners.size) this.listeners.delete(key) }
  }
  /** 实时流和只读查询共用连接展示，但通信失败不改写业务确认或结束。 */
  setConnection(key: string, update: (current: ConnectionState) => ConnectionState): void {
    this.update(key, { connection: update(this.get(key).connection) })
  }
  /** 业务写入开始时废弃旧读取；旧请求仍可结束，但不能回写新确认或结束状态。 */
  invalidate(key: string): number {
    const generation = this.generation = Math.max(this.generation, this.get(key).connection.requestGeneration) + 1
    this.requests.delete(key)
    this.update(key, { connection: { ...this.get(key).connection, requestGeneration: generation } })
    return generation
  }
  /** 接受后端已完成的业务操作或最新只读快照，拒绝过期生命周期。 */
  adopt(key: string, lifecycle: ApplicationLifecycle): ApplicationLifecycle {
    const current = this.get(key).lifecycle
    const base = current && current.revision > lifecycle.revision ? current : lifecycle
    const merged = current ? mergeRecoverySnapshot(base, current, lifecycle) : lifecycle
    this.update(key, { lifecycle: merged })
    return merged
  }
  /** 共用失败退避及认证门禁，定时器无需各自再保存重试次数。 */
  canRead(key: string): boolean {
    const entry = this.get(key)
    return !entry.authenticationBlocked && Date.now() >= entry.nextReadAt
  }
  /** 确定性业务错误仍说明连接正常；读取失败统一保留快照并按次数退避。 */
  recordFailure(key: string, reason: unknown): void {
    const entry = this.get(key), message = reason instanceof Error ? reason.message : '暂时无法同步任务状态。'
    const businessFailure = isRecoveryStateReadError(reason)
    this.update(key, { connection: businessFailure ? completeConnectionRequest(entry.connection, entry.connection.requestGeneration)
      : failConnectionRequest(entry.connection, entry.connection.requestGeneration, message), syncError: businessFailure ? message : undefined,
      failures: entry.failures + 1, nextReadAt: Date.now() + Math.min(30_000, 1000 * 2 ** Math.min(entry.failures, 5)),
      authenticationBlocked: isAuthenticationFailure(reason) })
  }
  /** 真实读取成功后统一清除退避；仅到达一帧旧 lifecycle 不会清除读取失败。 */
  recordSuccess(key: string): void {
    this.update(key, { connection: completeConnectionRequest(this.get(key).connection, this.get(key).connection.requestGeneration),
      syncError: undefined, failures: 0, nextReadAt: 0, authenticationBlocked: false })
  }
  /** 一次读取只有一个在途请求；失败保留最后快照，显式 fresh 会建立新失效屏障。 */
  async read(key: string, reader: () => Promise<ApplicationLifecycle>, fresh = false): Promise<ApplicationLifecycle> {
    const pending = this.requests.get(key)
    if (pending && !fresh) return pending
    const generation = this.invalidate(key)
    this.setConnection(key, (current) => beginConnectionRequest(current, generation))
    let request!: Promise<ApplicationLifecycle>
    request = (async (): Promise<ApplicationLifecycle> => {
      try {
        const lifecycle = await Promise.resolve().then(reader)
        if (this.get(key).connection.requestGeneration !== generation) return this.get(key).lifecycle || lifecycle
        const current = this.get(key).lifecycle
        if (current && lifecycle.revision < current.revision) {
          const error = new Error('任务状态仍在同步，请稍后重试。')
          error.name = 'RecoveryReadStaleError'
          throw error
        }
        const merged = this.adopt(key, lifecycle)
        this.update(key, { connection: completeConnectionRequest(this.get(key).connection, generation),
          syncError: undefined, failures: 0, nextReadAt: 0, authenticationBlocked: false })
        return merged
      } catch (reason) {
        if (this.get(key).connection.requestGeneration === generation) {
          this.recordFailure(key, reason)
        }
        throw reason
      } finally { if (this.requests.get(key) === request) this.requests.delete(key) }
    })()
    this.requests.set(key, request)
    return request
  }
}

export const recoveryStore = new RecoveryStore()
subscribeBackendConnection(() => recoveryStore.reconnectTransport())
