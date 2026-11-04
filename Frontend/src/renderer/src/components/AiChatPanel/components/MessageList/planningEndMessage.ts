import type { WorkflowRunPayload } from '../../../../typings'
import { workflowEndedPlanControl } from '../../planExecutionMode'
import type { AgentChatMessage } from '../../types'

/** 结束回执单独追加并按线程和运行去重，不覆盖原规划内容或占位前的审阅卡。 */
export function appendPlanningEndMessage(
  messages: AgentChatMessage[],
  workflow: WorkflowRunPayload,
  messageId: number
): AgentChatMessage[] {
  if (messages.some((message) => message.workflow?.threadId === workflow.threadId &&
    message.workflow.runId === workflow.runId && workflowEndedPlanControl(message.workflow))) {
    return messages
  }
  return [...messages.map((message) => message.planningLoading
    ? { ...message, planningLoading: false } : message), {
    id: messageId,
    role: 'assistant',
    content: workflow.summary.message || '本轮任务已结束，已有文件已保留。',
    workflow,
    createdAt: messageId
  }]
}
