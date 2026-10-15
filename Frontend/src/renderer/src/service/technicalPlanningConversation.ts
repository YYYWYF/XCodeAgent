import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { ApplicationConfig } from '../typings'
import { createAgUiHttpAgent } from './authentication'
import { getApplicationPlanningUrl } from './applicationPagePlanning'

export type TechnicalPlanningMessage = {
  action: 'classify' | 'revise'
  workspaceRoot: string
  applicationId: string
  planningThreadId: string
  request: string
  sourceRunId?: string
  checkpointId?: string
}

export type TechnicalPlanningReply = {
  intent: 'reply' | 'revise'
  message: string
  planningThreadId: string
  sourceRunId: string
  checkpointId: string
}

/** 独立只读 AG-UI 分类不覆盖原 Graph；所有状态校验由后端签发的版本身份决定。 */
export async function readTechnicalPlanningReply(
  application: ApplicationConfig, planningThreadId: string, request: string, signal: AbortSignal
): Promise<TechnicalPlanningReply> {
  const agent = createAgUiHttpAgent({ url: getApplicationPlanningUrl(), threadId: randomUUID() })
  agent.addMessage({ id: randomUUID(), role: 'user', content: request })
  let result: Record<string, unknown> | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'technical-planning-conversation' && event.value && typeof event.value === 'object')
        result = event.value as Record<string, unknown>
    }
  }
  /** 只终止本次只读分类，不取消原失败或待确认的规划执行。 */
  const abort = (): void => agent.abortRun()
  signal.addEventListener('abort', abort, { once: true })
  try {
    if (signal.aborted) throw new Error('技术规划提问已取消。')
    await agent.runAgent({ forwardedProps: { technicalPlanningConversation: {
      action: 'classify', workspaceRoot: application.workspaceRoot,
      applicationId: application.id, planningThreadId, request
    } } }, subscriber)
    if (signal.aborted) throw new Error('技术规划提问已取消。')
    if (result?.status === 'failed') {
      throw new Error((result.error as { message?: string } | undefined)?.message || '技术规划对话失败。')
    }
    if (result?.status !== 'completed' || !['reply', 'revise'].includes(String(result.intent)) ||
        result.planningThreadId !== planningThreadId || typeof result.message !== 'string' ||
        typeof result.sourceRunId !== 'string' || !result.sourceRunId ||
        typeof result.checkpointId !== 'string' || !result.checkpointId) {
      throw new Error('技术规划对话没有返回有效的当前状态。')
    }
    return result as TechnicalPlanningReply
  } finally {
    signal.removeEventListener('abort', abort)
  }
}
