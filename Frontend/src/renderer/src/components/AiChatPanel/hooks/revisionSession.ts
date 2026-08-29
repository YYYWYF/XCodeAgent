import type {
  ChatSessionRevisionContext,
  ChatSessionSummary
} from '../../../service/chatSessions'
import type {
  ApplicationLifecycle,
  WorkflowDesignStageRevisionStart,
  WorkflowFormalRevisionBranch
} from '../../../typings'
import type { WorkflowRevisionContinuation } from '../../../typings'
import type { SessionIdentity } from './sessionRuntime'

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

/** 用已确认 TechnicalPlan 和来源设计会话建立同一 change 下的开发会话身份。 */
export function createRevisionDevelopmentSessionContext(
  source: SessionIdentity,
  continuation: WorkflowRevisionContinuation
): ChatSessionRevisionContext {
  const context = source.revisionContext
  if (
    !context ||
    context.kind !== 'formal_revision' ||
    context.sessionRole !== 'design' ||
    context.changeId !== continuation.changeId ||
    context.formalBranch !== continuation.formalBranch
  ) {
    throw new Error('当前需求设计会话与 revision continuation 身份不一致。')
  }
  return {
    ...context,
    sessionRole: 'development',
    changeId: continuation.changeId,
    handoffFromSessionId: source.sessionId,
    handoffFromConversationThreadId: source.threadId,
    technicalPlanSha256: continuation.technicalPlanSha256
  }
}

/** 按 changeId 和 TechnicalPlan 哈希寻找已创建的开发会话，供重复点击幂等复用。 */
export function revisionDevelopmentSessionForContinuation(
  sessions: ChatSessionSummary[],
  continuation: WorkflowRevisionContinuation
): ChatSessionSummary | undefined {
  return sessions.find((session) => {
    const context = session.revisionContext
    return (
      session.workbenchPhase === 'development' &&
      context?.kind === 'formal_revision' &&
      context.sessionRole === 'development' &&
      context.changeId === continuation.changeId &&
      context.formalBranch === continuation.formalBranch &&
      context.technicalPlanSha256 === continuation.technicalPlanSha256
    )
  })
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

/** 冷恢复时只按 active revision 的完整身份选择独立会话，禁止按标题或原 Graph thread 猜测。 */
export function activeFormalRevisionConversationThreadId(
  sessions: ChatSessionSummary[],
  lifecycle: ApplicationLifecycle | undefined
): string | undefined {
  const active = lifecycle?.activeFormalRevision
  if (!active) return undefined
  return sessions.find((session) => {
    const context = session.revisionContext
    if (
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
  })?.threadId
}

/** 根据 formal branch 返回独立二次修改会话所属的可见阶段。 */
export function formalRevisionSessionPhase(
  branch: WorkflowFormalRevisionBranch
): 'product' | 'planning' {
  return branch === 'design_stage_revision' ? 'product' : 'planning'
}
