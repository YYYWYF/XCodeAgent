import type { ApplicationLifecycle, WorkflowDebugOptions, WorkflowRunPayload } from '../../typings'

const CONVERSATION_INTENTS = new Set([
  'casual_chat',
  'workspace_question',
  'clarification',
  'implementation_fix',
  'formal_revision'
])

export type ChatInputMode = 'design' | 'conversation'

const ACTIVE_CONVERSATION_STATUSES = new Set([
  'running',
  'in_progress',
  'requires_user_input',
  'paused',
  'stopping'
])

/** 判断当前投影是否来自独立自由对话 Graph。 */
export function isConversationWorkflow(workflow: WorkflowRunPayload | undefined): boolean {
  // 首个扫描节点尚未分类时也属于二次修改，断线不能退回预览重试。
  if (workflow?.state?.workflow_scope === 'conversation') return true
  if (workflow?.summary.phase === 'conversation') return true
  return CONVERSATION_INTENTS.has(String(workflow?.summary?.intent))
}

/** 判断自由对话是否正在等待澄清或升级确认。 */
export function isConversationWaitingForInput(workflow: WorkflowRunPayload | undefined): boolean {
  return isConversationWorkflow(workflow) && workflow?.summary.status === 'requires_user_input'
}

/** 正式修订已由来源 Run/Thread 交接时，原意图确认门不能再次提交。 */
export function conversationRevisionHandoffCommitted(
  workflow: WorkflowRunPayload | undefined,
  lifecycle: ApplicationLifecycle | undefined
): boolean {
  const active = lifecycle?.activeFormalRevision
  return Boolean(active && workflow && isConversationWorkflow(workflow) &&
    active.sourceRunId === workflow.runId && active.sourceThreadId === workflow.threadId)
}

/** 选择普通消息使用自由对话端点，并保证澄清始终沿同一 Graph 接续。 */
export function shouldUseConversation(
  enabled: boolean,
  workflow: WorkflowRunPayload | undefined,
  workflowDebug: WorkflowDebugOptions | undefined,
  inputMode?: ChatInputMode
): boolean {
  if (workflowDebug?.enabled) return false
  const status = String(workflow?.summary?.status || '')
  // 正在等待自由对话本身的澄清或升级确认时，必须沿原 Graph 续跑，不能被模式切换打断。
  if (isConversationWorkflow(workflow) && ACTIVE_CONVERSATION_STATUSES.has(status)) return true
  if (inputMode === 'design') return false
  if (inputMode === 'conversation') return enabled
  if (isConversationWorkflow(workflow)) return true
  if (!enabled) return false
  return !ACTIVE_CONVERSATION_STATUSES.has(status)
}
