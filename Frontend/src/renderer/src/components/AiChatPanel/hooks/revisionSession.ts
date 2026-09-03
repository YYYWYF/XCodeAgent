import type {
  ChatSessionRevisionContext,
  ChatSessionSummary
} from '../../../service/chatSessions'
import type {
  ApplicationLifecycle,
  WorkbenchExecution,
  WorkflowDesignStageRevisionStart,
  WorkflowFormalRevisionBranch,
  WorkflowRevisionContinuation
} from '../../../typings'
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

/** 按 changeId、TechnicalPlan 哈希和来源会话寻找本次 revision 的独立开发会话。 */
export function revisionDevelopmentSessionForContinuation(
  sessions: ChatSessionSummary[],
  source: SessionIdentity,
  continuation: WorkflowRevisionContinuation
): ChatSessionSummary | undefined {
  const entryKey = `revision-development:${continuation.changeId}:${continuation.technicalPlanSha256}`
  return sessions.find((session) => {
    const context = session.revisionContext
    return (
      session.workflowId === source.workflowId &&
      session.workbenchPhase === 'development' &&
      session.stage === 'DEVELOPMENT' &&
      session.entryKey === entryKey &&
      context?.kind === 'formal_revision' &&
      context.sessionRole === 'development' &&
      context.changeId === continuation.changeId &&
      context.formalBranch === continuation.formalBranch &&
      context.technicalPlanSha256 === continuation.technicalPlanSha256 &&
      context.handoffFromSessionId === source.sessionId &&
      context.handoffFromConversationThreadId === source.threadId
    )
  })
}

/** 从当前 lifecycle 中选择尚未绑定可见会话的 continuation execution。 */
export function recoverableRevisionDevelopmentExecution(
  sessions: ChatSessionSummary[],
  lifecycle: ApplicationLifecycle | undefined,
  workflowId: string
): WorkbenchExecution | undefined {
  const active = lifecycle?.activeFormalRevision
  const technicalPlanSha256 = String(active?.technicalPlanSha256 || '').trim()
  if (
    !active ||
    !technicalPlanSha256 ||
    !['building', 'failed', 'stopped'].includes(String(active.status || '')) ||
    sessions.some(
      (session) =>
        session.workflowId === workflowId &&
        session.stage === 'DEVELOPMENT' &&
        session.revisionContext?.changeId === active.changeId &&
        session.revisionContext.technicalPlanSha256 === technicalPlanSha256
    )
  ) {
    return undefined
  }
  const target = active.target && typeof active.target === 'object'
    ? (active.target as Record<string, unknown>)
    : {}
  const targetType = String(target.type || '')
  const targetId = String(
    targetType === 'page'
      ? target.pageId || ''
      : targetType === 'endpoint'
        ? target.endpointId || ''
        : targetType === 'entity'
          ? target.entityId || ''
          : 'application'
  ).trim()
  return Object.values(lifecycle.activeExecutions || {})
    .filter(
      (execution) =>
        ['running', 'failed', 'stopped'].includes(execution.status) &&
        !sessions.some((session) => session.threadId === execution.threadId) &&
        ((targetType === 'page' &&
          execution.scope === 'page' &&
          (execution.pageId || execution.targetId) === targetId) ||
          (targetType === 'endpoint' &&
            execution.scope === 'endpoint' &&
            execution.targetId === targetId) ||
          (targetType === 'entity' &&
            execution.scope === 'data_source' &&
            execution.targetId === targetId) ||
          (!['page', 'endpoint', 'entity'].includes(targetType) &&
            execution.scope === 'application'))
    )
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0]
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
