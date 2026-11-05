import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { ApplicationConfig, WorkflowRunPayload } from '../typings'
import { createAgUiHttpAgent } from './authentication'
import { getApplicationPlanningUrl } from './applicationPagePlanning'

export type TechnicalPlanningMessage = {
  action: 'classify' | 'prepare_revision' | 'revise' | 'revise_design'
  workspaceRoot: string
  applicationId: string
  planningThreadId: string
  request: string
  conversationTarget?: import('../typings').PlanningConversationTarget
  sourceRunId?: string
  checkpointId?: string
}

export type TechnicalPlanningReply = {
  intent: 'reply' | 'revise' | 'design_change'
  message: string
  planningThreadId: string
  sourceRunId: string
  checkpointId: string
}

/** 把已完成的只读答复投射为历史消息，绝不替换 Canonical Workflow 或构造确认门。 */
export function technicalPlanningReplyPresentation(reply: TechnicalPlanningReply): WorkflowRunPayload {
  return {
    runId: reply.sourceRunId, threadId: reply.planningThreadId, events: [],
    summary: { status: 'completed', phase: 'technical_planning_reply', message: reply.message,
      productConversationResult: { kind: 'read_only', mutating: false, response: reply.message,
        presentation: { artifactPresentation: 'preserve' } } },
    state: {}, result: {}
  }
}

/** 冷绑定或 transport 收尾重投同一审阅快照时，保留已展示的只读答复和独立确认卡。 */
export function shouldPreserveTechnicalPlanningReply(previous: WorkflowRunPayload | undefined, incoming: WorkflowRunPayload): boolean {
  return previous?.summary.phase === 'technical_planning_reply' &&
    previous.summary.productConversationResult?.mutating === false &&
    ['requires_user_input', 'failed', 'completed'].includes(String(incoming.summary.status)) &&
    incoming.threadId === previous.threadId && incoming.runId === previous.runId
}

/** 独立 AG-UI 读取对话分类或卡片修改版本，均不覆盖原 Graph。 */
async function runTechnicalPlanningPreparation(
  application: ApplicationConfig, planningThreadId: string, request: string, signal: AbortSignal,
  action: 'classify' | 'prepare_revision',
  conversationTarget?: TechnicalPlanningMessage['conversationTarget']
): Promise<TechnicalPlanningReply> {
  const agent = createAgUiHttpAgent({ url: getApplicationPlanningUrl(), threadId: planningThreadId })
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
      action, workspaceRoot: application.workspaceRoot,
      applicationId: application.id, planningThreadId, request,
      ...(conversationTarget ? { conversationTarget } : {})
    } } }, subscriber)
    if (signal.aborted) throw new Error('技术规划提问已取消。')
    if (result?.status === 'failed') {
      throw new Error((result.error as { message?: string } | undefined)?.message || '技术规划对话失败。')
    }
    if (result?.status !== 'completed' || !['reply', 'revise', 'design_change'].includes(String(result.intent)) ||
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

/** 指定目标时确定性准备执行；无目标时由后端识别问答、技术修改和上游变更。 */
export function readTechnicalPlanningReply(
  application: ApplicationConfig, planningThreadId: string, request: string, signal: AbortSignal,
  conversationTarget?: TechnicalPlanningMessage['conversationTarget']
): Promise<TechnicalPlanningReply> {
  return runTechnicalPlanningPreparation(application, planningThreadId, request, signal, 'classify', conversationTarget)
}

/** 卡片修改已明确目标，仅确定性读取当前版本，不调用聊天意图模型。 */
export function prepareTechnicalPlanningRevision(
  application: ApplicationConfig, planningThreadId: string, request: string, signal: AbortSignal
): Promise<TechnicalPlanningReply> {
  return runTechnicalPlanningPreparation(application, planningThreadId, request, signal, 'prepare_revision')
}
