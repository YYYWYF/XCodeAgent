import type { ApplicationLifecycle } from '../typings'
import { subscribeBackendConnection } from './backendConnection'

type WorkspaceAttachments = {
  completed: Set<string>
  requests: Map<string, Promise<ApplicationLifecycle>>
}
export type WorkspaceAttachmentResult = { lifecycle: ApplicationLifecycle; calibrated: boolean }

/** 实际请求发现源码后台换实例时，只允许重新读取和校准，不重发业务动作。 */
export class RecoveryBackendChangedError extends Error {}

/** 只记录本次后台连接下已完成的校准，不保存或复用业务快照。 */
export class WorkspaceRecoveryAttachments {
  private workspaces = new Map<string, WorkspaceAttachments>()
  revision = 0
  private backendInstance?: string
  backendRevision = 0

  /** 通过真实业务响应识别同地址后台重启，不增加 health 探测或定时器。 */
  observeBackend(instanceId: string, expectedRevision = this.backendRevision): boolean {
    if (expectedRevision !== this.backendRevision && instanceId !== this.backendInstance) {
      throw new RecoveryBackendChangedError('旧后台响应已失效，请重新同步。')
    }
    const changed = this.backendInstance !== undefined && this.backendInstance !== instanceId
    this.backendInstance = instanceId
    if (changed) { this.backendRevision += 1; this.invalidate() }
    return changed
  }

  /** 仅成功校准的工作区才允许后续请求跳过全局扫描。 */
  has(workspace: string, scope: string): boolean {
    return this.workspaces.get(workspace)?.completed.has(scope) === true
  }

  /** 显式重新进入或后台换代后撤销校准凭据，迟到请求不能重新登记。 */
  invalidate(workspace?: string): void {
    this.revision += 1
    if (workspace === undefined) this.workspaces.clear()
    else this.workspaces.delete(workspace)
  }

  /** 同一作用域只校准一次；失败和被活动任务推迟的会话校准仍可再次执行。 */
  async ensure(
    workspace: string,
    scope: string,
    read: () => Promise<WorkspaceAttachmentResult>
  ): Promise<ApplicationLifecycle | undefined> {
    let entry = this.workspaces.get(workspace)
    if (!entry) {
      entry = { completed: new Set(), requests: new Map() }
      this.workspaces.set(workspace, entry)
    }
    if (entry.completed.has(scope)) return undefined
    const pending = entry.requests.get(scope)
    if (pending) return pending
    const current = entry
    const request = Promise.resolve().then(read).then(({ lifecycle, calibrated }) => {
      if (this.workspaces.get(workspace) !== current) throw new Error('工作区恢复范围已变化，请重新同步。')
      if (calibrated) current.completed.add(scope)
      return lifecycle
    })
    current.requests.set(scope, request)
    try { return await request }
    finally { if (current.requests.get(scope) === request) current.requests.delete(scope) }
  }
}

export const workspaceRecoveryAttachments = new WorkspaceRecoveryAttachments()
subscribeBackendConnection(() => workspaceRecoveryAttachments.invalidate())
