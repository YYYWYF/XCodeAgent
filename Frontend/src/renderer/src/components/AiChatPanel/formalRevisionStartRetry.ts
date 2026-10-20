import type { ApplicationLifecycle, WorkflowDesignStageRevisionStart, WorkflowRevisionImpact } from '../../typings'
import type { ApplicationPlanningAuthoritativeSnapshot } from '../../service/applicationPlanningRecovery'
import type { SessionIdentity } from './hooks/sessionRuntime'
import { initialFormalRevisionPhase } from './hooks/revisionSession'

/** 只从已确认创建的目标阶段会话和 Backend 当前 pending impact 还原原启动请求。 */
export function pendingFormalRevisionStart(
  lifecycle: ApplicationLifecycle | undefined,
  session: SessionIdentity | undefined
): WorkflowDesignStageRevisionStart | undefined {
  const context = session?.revisionContext
  const pending = lifecycle?.pendingRevisionImpact
  if (!lifecycle || !session || !context || !pending || lifecycle.activeFormalRevision ||
      session.workflowId !== lifecycle.application.id ||
      session.workbenchPhase !== initialFormalRevisionPhase(context.formalBranch) ||
      context.planningThreadId !== lifecycle.initialization.threadId ||
      pending.status !== 'pending' || pending.interactionId !== context.impactInteractionId ||
      pending.sourceThreadId !== context.sourceConversationThreadId || pending.sourceRunId !== context.sourceRunId ||
      typeof pending.request !== 'string' || !pending.request.trim()) return undefined
  const impact = pending.impact as WorkflowRevisionImpact | undefined
  const target = pending.target as Record<string, unknown> | undefined
  if (!impact || impact.formalBranch !== context.formalBranch || !target ||
      !['application', 'page', 'endpoint'].includes(String(target.type))) return undefined
  return {
    request: pending.request,
    target,
    impact: { ...impact, interactionId: context.impactInteractionId, status: 'pending' },
    sourceSessionId: context.sourceSessionId,
    sourceConversationThreadId: context.sourceConversationThreadId,
    sourceRunId: context.sourceRunId
  }
}

/** 显式重试先证明当前交接状态：未入场重发原确认，已入场只走原执行恢复。 */
export async function retryFormalRevisionStart(input: {
  session: SessionIdentity
  read: () => Promise<{ lifecycle: ApplicationLifecycle; snapshot?: ApplicationPlanningAuthoritativeSnapshot }>
  onLifecycle: (lifecycle: ApplicationLifecycle) => void
  start: (request: WorkflowDesignStageRevisionStart) => Promise<void>
  recover: () => void
}): Promise<void> {
  const { lifecycle, snapshot } = await input.read()
  input.onLifecycle(lifecycle)
  const context = input.session.revisionContext
  if (!context) { input.recover(); return }
  if (input.session.workflowId !== lifecycle.application.id ||
      context.planningThreadId !== lifecycle.initialization.threadId) {
    throw new Error('正式修改的应用或规划会话已变化，请同步最新状态。')
  }
  const active = lifecycle.activeFormalRevision
  if (active && active.impactInteractionId === context.impactInteractionId &&
      active.formalBranch === context.formalBranch && active.sourceRunId === context.sourceRunId &&
      active.sourceThreadId === context.sourceConversationThreadId && active.planningThreadId === context.planningThreadId) {
    input.recover()
    return
  }
  const request = pendingFormalRevisionStart(lifecycle, input.session)
  if (!request) throw new Error('这次正式修改的确认已失效，请返回原开发会话同步最新状态。')
  // 设计意图节点已落盘但尚未批准 impact 的窗口同样属于已入场，不重发确认。
  const phase = snapshot?.workflow.state?.phase || snapshot?.workflow.summary.phase
  if (phase === 'design_intent_analysis' && snapshot?.workflow.threadId === context.planningThreadId &&
      snapshot.recovery.classification !== 'completed') {
    input.recover()
    return
  }
  await input.start(request)
}
