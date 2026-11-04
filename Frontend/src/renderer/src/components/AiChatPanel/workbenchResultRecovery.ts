import type { ApplicationLifecycle } from '../../typings'
import type { AgentChatMessage } from './types'
import type { SessionIdentity } from './hooks/sessionRuntime'

/** 将后端当前会话的已完成选择结果合并到原回复；不新增 Run，不根据提示文字推断完成。 */
export function mergeWorkbenchResult(
  messages: AgentChatMessage[], lifecycle: ApplicationLifecycle, identity: SessionIdentity
): AgentChatMessage[] {
  const result = lifecycle.extensions.workbenchResult
  if (!result || lifecycle.application.id !== identity.workflowId ||
    result.ownerSessionId !== identity.sessionId || result.threadId !== identity.threadId ||
    result.workflow.runId !== result.runId || result.workflow.threadId !== identity.threadId ||
    result.workflow.summary.status !== 'completed' ||
    result.workflow.summary.clarification?.mode !== 'development_target_selection') return messages
  const existingIndex = messages.findIndex((item) => item.role === 'assistant' &&
    item.workflow?.runId === result.runId && item.workflow.threadId === result.threadId)
  const existing = messages[existingIndex]
  if (existing?.workflow?.summary.status === 'completed' &&
    existing.workflow.summary.clarification?.mode === 'development_target_selection') return messages
  const startedAt = Date.parse(result.startedAt)
  if (!Number.isFinite(startedAt)) return messages
  // 查询期间已发出新一轮输入时，旧 completed 不能插到新回复后面重新成为当前卡片。
  if (messages.some((item) => item.role === 'user' && item.createdAt > startedAt)) return messages
  const last = messages[messages.length - 1]
  const replaceIndex = existingIndex >= 0 ? existingIndex : last?.role === 'assistant' &&
    !last.workflow && !last.content.trim() ? messages.length - 1 : -1
  const previous = replaceIndex >= 0 ? messages[replaceIndex] : undefined
  const restored: AgentChatMessage = {
    ...previous,
    id: previous?.id ?? startedAt * 1000 + 500,
    role: 'assistant', createdAt: previous?.createdAt ?? startedAt,
    content: result.workflow.summary.message || '',
    error: undefined, planningLoading: false,
    workflow: result.workflow
  }
  const next = [...messages]
  if (replaceIndex >= 0) next[replaceIndex] = restored
  else next.push(restored)
  return next
}
