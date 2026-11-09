import type { ApplicationLifecycle } from '../../typings'
import type { AgentChatMessage } from './types'
import type { SessionIdentity } from './hooks/sessionRuntime'
import { readWorkflowPayload } from '../../service/agUiAgent'
import { workflowEndedPlanControl } from './planExecutionMode'

/** 合并后端当前提交的展示结果；同请求更新原回复，禁止覆盖后来明确发送的新消息。 */
export function mergeExecutionOutput(messages: AgentChatMessage[], lifecycle: ApplicationLifecycle, identity: SessionIdentity): AgentChatMessage[] {
  const snapshot = lifecycle.extensions.recoverySnapshot
  if (!snapshot || lifecycle.application.id !== identity.workflowId || snapshot.sessionId !== identity.sessionId || snapshot.threadId !== identity.threadId) return messages
  const workflow = readWorkflowPayload(snapshot.workflow)
  if (!workflow) return messages
  const output = snapshot.output
  if (output && output.runId !== workflow.runId) return messages
  const startedAt = Date.parse(output?.startedAt || snapshot.startedAt || '')
  if (!Number.isFinite(startedAt) || messages.some((message) => message.role === 'user' &&
    message.createdAt > startedAt && (!output || message.executionRequestId !== output.requestId))) return messages
  const existingIndex = messages.findIndex((message) => message.role === 'assistant' &&
    ((output && message.executionRequestId === output.requestId) || (message.workflow?.runId === workflow.runId && message.workflow.threadId === snapshot.threadId)))
  const last = messages[messages.length - 1]
  const index = existingIndex >= 0 ? existingIndex : last?.role === 'assistant' && !last.workflow && !last.content.trim() ? messages.length - 1 : -1
  const previous = messages[index]
  const ended = workflowEndedPlanControl(workflow) || ['cancelled', 'stopped'].includes(String(workflow.summary.status))
  // 只使用统一快照的当前 Workflow；输出仅补充文字和过程，不能恢复旧确认。
  const restored: AgentChatMessage = { ...previous, id: previous?.id ?? startedAt * 1000 + 501,
    role: 'assistant', createdAt: previous?.createdAt ?? startedAt, executionRequestId: output?.requestId || previous?.executionRequestId,
    content: ended ? workflow.summary.message || '本轮任务已结束，已有文件已保留。' : output?.content || workflow.summary.message || '', workflow,
    toolCalls: output?.toolCalls || previous?.toolCalls, processSteps: output?.processSteps || previous?.processSteps, planningLoading: false, error: undefined }
  if (previous && JSON.stringify(previous) === JSON.stringify(restored)) return messages
  const next = [...messages]
  if (index >= 0) next[index] = restored
  else next.push(restored)
  return next
}
