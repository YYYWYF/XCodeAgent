import type { ApplicationLifecycle, ChatMessageSkill } from '../../typings'
import type { SessionIdentity } from './hooks/sessionRuntime'

export type PendingWorkflowInput = {
  identity: SessionIdentity
  draft: string
  skills: ChatMessageSkill[]
  baselineRunIds: string[]
  restored: boolean
  submit: () => Promise<boolean>
}

/** 只恢复同一工作区会话中尚无新执行事实的原输入；候选为空不能证明请求未被接收。 */
export function canRestorePendingWorkflowInput(
  pending: PendingWorkflowInput,
  identity: SessionIdentity,
  lifecycle: ApplicationLifecycle | undefined
): boolean {
  if (!lifecycle || pending.identity.key !== identity.key ||
    pending.identity.workspaceRoot !== identity.workspaceRoot ||
    pending.identity.sessionId !== identity.sessionId || pending.identity.threadId !== identity.threadId) return false
  return !Object.values(lifecycle.activeExecutions || {}).some((execution) =>
    execution.ownerSessionId === identity.sessionId && !pending.baselineRunIds.includes(execution.runId))
}
