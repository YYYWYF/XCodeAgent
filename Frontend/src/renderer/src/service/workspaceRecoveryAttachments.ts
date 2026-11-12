import type { ApplicationLifecycle } from '../typings'

type WorkspaceAttachments = {
  completed: Set<string>
  requests: Map<string, Promise<ApplicationLifecycle>>
}
export type WorkspaceAttachmentResult = { lifecycle: ApplicationLifecycle; calibrated: boolean }

/** 只记录本次后台连接下已完成的校准，不保存或复用业务快照。 */
export class WorkspaceRecoveryAttachments {
  private workspaces = new Map<string, WorkspaceAttachments>()
  revision = 0
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
