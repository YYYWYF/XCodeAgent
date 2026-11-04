import type { ApplicationLifecycle, WorkflowRevisionContinuation } from '../typings'

type ContinuationAttempt = {
  workspace: string
  applicationId: string
  continuation: WorkflowRevisionContinuation
  readLifecycle: () => Promise<ApplicationLifecycle>
  onLifecycle: (lifecycle: ApplicationLifecycle) => void
  submit: () => Promise<boolean>
}

// 续接可能同时来自 Runtime、冷恢复和重试；锁独立于工作台挂载，完成后释放。
const pending = new Map<string, Promise<boolean>>()

/** 只有后端当前仍待消费的同一修订才允许提交；历史快照不授予执行资格。 */
export function revisionContinuationIsCurrent(
  lifecycle: ApplicationLifecycle,
  continuation: WorkflowRevisionContinuation
): boolean {
  const active = lifecycle.activeFormalRevision
  return active?.changeId === continuation.changeId &&
    active.formalBranch === continuation.formalBranch &&
    active.status === 'continuation_ready' &&
    !active.continuationConsumedAt &&
    active.technicalPlanSha256 === continuation.technicalPlanSha256
}

/** 仅当前修订的来源规划/原开发会话可恢复平台交接，历史只读不隐藏该恢复入口。 */
export function revisionContinuationRecoveryVisible(
  lifecycle: ApplicationLifecycle | undefined,
  continuation: WorkflowRevisionContinuation | undefined,
  threadId: string | undefined
): boolean {
  if (!lifecycle || !continuation || !threadId || !revisionContinuationIsCurrent(lifecycle, continuation)) return false
  const active = lifecycle.activeFormalRevision!
  return threadId === active.planningThreadId || threadId === active.sourceThreadId
}

/** 在写消息和发送 AG-UI 请求前去重并读取当前状态；失败后只允许尚未消费的凭据重试。 */
export function continueRevisionOnce(attempt: ContinuationAttempt): Promise<boolean> {
  const { continuation } = attempt
  const key = JSON.stringify([attempt.workspace, attempt.applicationId, continuation.changeId, continuation.token])
  const existing = pending.get(key)
  if (existing) return existing
  const promise = Promise.resolve().then(async () => {
    const lifecycle = await attempt.readLifecycle()
    attempt.onLifecycle(lifecycle)
    if (!revisionContinuationIsCurrent(lifecycle, continuation)) return true
    // submit 返回 false 也可能是已登记执行后的节点失败或断连，必须再读后端事实。
    let failure: unknown
    try {
      if (await attempt.submit()) return true
    } catch (error) {
      failure = error
    }
    const after = await attempt.readLifecycle()
    attempt.onLifecycle(after)
    if (!revisionContinuationIsCurrent(after, continuation)) return true
    if (failure) throw failure
    return false
  }).finally(() => {
    if (pending.get(key) === promise) pending.delete(key)
  })
  pending.set(key, promise)
  return promise
}
