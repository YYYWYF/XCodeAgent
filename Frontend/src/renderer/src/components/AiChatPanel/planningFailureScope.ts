import type { ApplicationPlanningCurrentState } from '../../service/activeApplicationPlanning'
import type { WorkflowRunPayload } from '../../typings'
import type { SessionIdentity } from './hooks/sessionRuntime'
import { workbenchPhaseForNode } from '../../workbenchPhase'

/** 仅补接当前业务阶段的权威快照，计划会话不能收到上游遗留的 UI 确认卡。 */
export function planningSnapshotForStage(
  workflow: WorkflowRunPayload | undefined,
  phase: 'product' | 'planning'
): WorkflowRunPayload | undefined {
  const node = String(workflow?.state?.phase || workflow?.summary?.phase || '')
  return workflow && workbenchPhaseForNode(node, 'development') === phase ? workflow : undefined
}

/** 后端当前阶段的恢复入口独立于本机历史；已有历史仍按精确运行归属隔离。 */
export function planningFailureStateForSession(input: {
  state?: ApplicationPlanningCurrentState
  session?: SessionIdentity
  sessionWorkflow?: WorkflowRunPayload
  currentPhase: string
  executionPhase: string
}): ApplicationPlanningCurrentState | undefined {
  const { state, session, sessionWorkflow, currentPhase, executionPhase } = input
  if (!state || state.checkpointMissing) return undefined
  // 阶段交接期间同一 Graph thread 可同时留在设计、计划历史中，不能跨阶段复用失败。
  if (!['product', 'planning'].includes(currentPhase) || currentPhase !== executionPhase) return undefined
  if (!session) return state
  if (session.workbenchPhase !== currentPhase || session.workflowId !== state.application.id) return undefined
  if (!sessionWorkflow) return state
  const sourceRunId = state.recovery?.sourceRunId || state.workflow?.runId
  return sourceRunId && sessionWorkflow.runId === sourceRunId &&
    sessionWorkflow.threadId === state.threadId
    ? state : undefined
}
