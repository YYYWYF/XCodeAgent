import { randomUUID } from '@ag-ui/client'
import type { SessionIdentity } from './sessionRuntime'

export type PlanControlRequestGuard = {
  requestId: string
  originIdentity: SessionIdentity
  originTargetRunId?: string
}

export type ActivePlanControlContext = {
  identity?: SessionIdentity
  targetRunId?: string
}

/** 创建一次带会话、阶段、目标和 requestId 的 Plan Control 哨兵。 */
export function createPlanControlRequestGuard(
  identity: SessionIdentity,
  targetRunId: string,
  originContext?: ActivePlanControlContext
): PlanControlRequestGuard {
  return {
    requestId: randomUUID(),
    originIdentity: { ...(originContext?.identity || identity) },
    originTargetRunId: originContext ? originContext.targetRunId : targetRunId
  }
}

/** 判断两个 Plan Control 哨兵是否仍指向同一个会话身份。 */
function samePlanControlIdentity(left: SessionIdentity, right: SessionIdentity): boolean {
  return (
    left.key === right.key &&
    left.sessionId === right.sessionId &&
    left.threadId === right.threadId &&
    left.workflowId === right.workflowId &&
    left.workbenchPhase === right.workbenchPhase &&
    left.workspaceRoot === right.workspaceRoot &&
    left.editorMode === right.editorMode
  )
}

/** 判断迟到的 Plan Control 响应是否仍属于当前 request 和 active session。 */
export function isCurrentPlanControlRequest(
  request: PlanControlRequestGuard,
  latestRequest: PlanControlRequestGuard | undefined,
  context: ActivePlanControlContext | undefined
): boolean {
  const currentIdentity = context?.identity
  if (
    !latestRequest ||
    latestRequest.requestId !== request.requestId ||
    !currentIdentity ||
    !samePlanControlIdentity(currentIdentity, request.originIdentity)
  ) {
    return false
  }
  return !context?.targetRunId || context.targetRunId === request.originTargetRunId
}
