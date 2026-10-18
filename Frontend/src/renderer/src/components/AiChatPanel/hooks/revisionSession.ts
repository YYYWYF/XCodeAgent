import type {
  ChatSessionRevisionContext,
  ChatSessionSummary
} from '../../../service/chatSessions'
import type {
  ApplicationLifecycle,
  WorkflowDesignStageRevisionStart,
  WorkflowFormalRevisionBranch,
  WorkflowRevisionContinuation
} from '../../../typings'
import type { WorkflowRunPayload } from '../../../typings'
import type { AgentChatMessage } from '../types'
import type { SessionIdentity } from './sessionRuntime'
import type { WorkbenchPhase } from '../../../workbenchPhase'
import { sessionToRestoreForPhase } from './phaseSessionSelection'

/** 正式交接使用原确认卡绑定的目标；无显式目标的应用对话不得传出空对象。 */
export function revisionStartTarget(
  workflow: WorkflowRunPayload,
  lifecycle?: ApplicationLifecycle
): Record<string, unknown> {
  const pending = lifecycle?.pendingRevisionImpact
  const boundTarget = pending?.interactionId === workflow.summary.revisionImpact?.interactionId
    ? pending?.target : undefined
  const candidates = [boundTarget, workflow.summary.target, workflow.state?.change_target]
  return (candidates.find((target) => target && typeof target === 'object' &&
    ['application', 'page', 'endpoint'].includes(String((target as Record<string, unknown>).type))) ||
    { type: 'application' }) as Record<string, unknown>
}

/** 后端已授权续接后选择本机输出会话；来源历史缺失不影响续接资格。 */
export function revisionContinuationOutputSession(
  sessions: ChatSessionSummary[],
  applicationId: string,
  source: SessionIdentity | undefined,
  continuation: WorkflowRevisionContinuation
): ChatSessionSummary | undefined {
  const applicationSessions = sessions.filter((session) => session.workflowId === applicationId)
  return (source && revisionDevelopmentSessionForContinuation(applicationSessions, source, continuation)) ||
    sessionToRestoreForPhase(applicationSessions, 'development')
}

/** 为一次已批准的 formal revision 创建独立前端会话身份。 */
export function createFormalRevisionSessionContext(
  input: WorkflowDesignStageRevisionStart,
  planningThreadId: string
): ChatSessionRevisionContext {
  return {
    kind: 'formal_revision',
    sessionRole: 'design',
    formalBranch: input.impact.formalBranch,
    impactInteractionId: input.impact.interactionId,
    sourceSessionId: input.sourceSessionId,
    sourceConversationThreadId: input.sourceConversationThreadId,
    sourceRunId: input.sourceRunId,
    planningThreadId
  }
}

/** 按 revision 来源身份寻找发起二次修改的原始开发会话。 */
export function revisionDevelopmentSessionForContinuation(
  sessions: ChatSessionSummary[],
  source: SessionIdentity,
  continuation: WorkflowRevisionContinuation
): ChatSessionSummary | undefined {
  const sourceContext = source.revisionContext
  if (
    !sourceContext ||
    sourceContext.kind !== 'formal_revision' ||
    sourceContext.sessionRole !== 'design' ||
    sourceContext.changeId !== continuation.changeId ||
    sourceContext.formalBranch !== continuation.formalBranch
  ) {
    return undefined
  }
  return sessions.find((session) => {
    return (
      session.workflowId === source.workflowId &&
      session.workbenchPhase === 'development' &&
      session.stage === 'DEVELOPMENT' &&
      session.id === sourceContext.sourceSessionId &&
      session.threadId === sourceContext.sourceConversationThreadId
    )
  })
}

/** 移除未完成的空占位消息，并把二次修改开发入口卡合入会话。 */
export function appendRevisionDevelopmentEntryMessage(
  messages: AgentChatMessage[],
  entry: AgentChatMessage
): AgentChatMessage[] {
  const retainedMessages = messages.filter(
    (item) =>
      !(
        item.role === 'assistant' &&
        !item.content.trim() &&
        !item.workflow &&
        !item.error &&
        !item.revisionHandoff
      )
  )
  return [...retainedMessages, entry]
}

/** 用 lifecycle 权威 active revision 为前端会话补上服务端签发的 changeId。 */
export function bindRevisionSessionChangeId(
  context: ChatSessionRevisionContext | undefined,
  lifecycle: ApplicationLifecycle | undefined
): ChatSessionRevisionContext | undefined {
  if (!context) return undefined
  const active = lifecycle?.activeFormalRevision
  if (
    !active ||
    active.formalBranch !== context.formalBranch ||
    active.impactInteractionId !== context.impactInteractionId ||
    active.sourceThreadId !== context.sourceConversationThreadId ||
    active.sourceRunId !== context.sourceRunId ||
    active.planningThreadId !== context.planningThreadId
  ) {
    return context
  }
  return { ...context, changeId: active.changeId }
}

/** 为一次设计到规划的入口生成稳定去重键；新的 revision 或门禁必须得到不同键。 */
export function planningStageTransitionKey(
  checkpointThreadId: string,
  gateIdentity: string,
  context?: ChatSessionRevisionContext
): string {
  const revisionIdentity = String(context?.changeId || context?.impactInteractionId || '').trim()
  return revisionIdentity
    ? `revision-plan:${revisionIdentity}:${gateIdentity.trim()}`
    : `planning-entry:${checkpointThreadId.trim()}:${gateIdentity.trim()}`
}

/** 冷恢复时按当前业务阶段和完整 revision 身份选择 StageSession。 */
export function activeFormalRevisionStageSession(
  sessions: ChatSessionSummary[],
  lifecycle: ApplicationLifecycle | undefined,
  workflowId: string,
  phase: 'product' | 'planning'
): ChatSessionSummary | undefined {
  const active = lifecycle?.activeFormalRevision
  if (!active) return undefined
  return sessions.find((session) => {
    const context = session.revisionContext
    if (
      session.workflowId !== workflowId ||
      session.workbenchPhase !== phase ||
      session.stage !== (phase === 'product' ? 'DESIGN' : 'PLAN') ||
      !context ||
      context.kind !== 'formal_revision' ||
      context.sessionRole !== 'design' ||
      context.formalBranch !== active.formalBranch
    ) return false
    if (
      context.impactInteractionId !== active.impactInteractionId ||
      context.sourceConversationThreadId !== active.sourceThreadId ||
      context.sourceRunId !== active.sourceRunId ||
      context.planningThreadId !== active.planningThreadId
    ) {
      return false
    }
    return !context.changeId || context.changeId === active.changeId
  })
}

/** DESIGN → PLAN 交接时只接受 lifecycle 完整匹配的正式需求设计会话。 */
export function formalRevisionPlanningSourceSession(
  sessions: ChatSessionSummary[],
  lifecycle: ApplicationLifecycle | undefined,
  workflowId: string
): ChatSessionSummary | undefined {
  return activeFormalRevisionStageSession(sessions, lifecycle, workflowId, 'product')
}

/** continuation 到达时优先选择规划会话；规划会话缺失时退回同一 revision 的产品会话。 */
export function formalRevisionContinuationSourceSession(
  sessions: ChatSessionSummary[],
  lifecycle: ApplicationLifecycle | undefined,
  workflowId: string
): ChatSessionSummary | undefined {
  return (
    activeFormalRevisionStageSession(sessions, lifecycle, workflowId, 'planning') ||
    formalRevisionPlanningSourceSession(sessions, lifecycle, workflowId)
  )
}

/** 根据 formal branch 返回正式修改首次进入的可见阶段。 */
export function initialFormalRevisionPhase(
  branch: WorkflowFormalRevisionBranch
): 'product' | 'planning' {
  return branch === 'design_stage_revision' ? 'product' : 'planning'
}

/** 后端已接纳或读取结果未知时保留正式修订展示会话，只有确定未入场才允许撤销。 */
export function retainFormalRevisionSessionAfterStart(
  input: WorkflowDesignStageRevisionStart,
  lifecycle: ApplicationLifecycle | undefined
): boolean {
  if (!lifecycle) return true
  const active = lifecycle.activeFormalRevision
  return Boolean(active && active.formalBranch === input.impact.formalBranch &&
    active.impactInteractionId === input.impact.interactionId &&
    active.sourceThreadId === input.sourceConversationThreadId &&
    active.sourceRunId === input.sourceRunId)
}

/** 判断当前正式修订是否允许恢复指定阶段的展示会话，不启动或重放 Graph。 */
export function formalRevisionHandoffRestorable(
  lifecycle: ApplicationLifecycle | undefined,
  phase: WorkbenchPhase
): boolean {
  const active = lifecycle?.activeFormalRevision
  if (!active || active.planningThreadId !== lifecycle?.initialization.threadId) return false
  if (active.formalBranch === 'design_stage_revision') {
    return active.status === 'design_planning' && ['product', 'planning'].includes(phase)
  }
  return active.formalBranch === 'workbench_plan_revision' && active.status === 'drafting' &&
    phase === 'planning' && ['generating_technical_plan', 'awaiting_technical_plan_confirmation'].includes(lifecycle.initialization.stage)
}
