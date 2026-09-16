import type { WorkflowRunPayload } from '../../../../typings'

/** 只锁定 Canonical Planning Current State 对应的当前消息操作。 */
export function planningMessageActionsDisabled(
  isCurrentPlanningMessage: boolean,
  mutationBlocked: boolean
): boolean {
  return isCurrentPlanningMessage && mutationBlocked
}

/** 在明确标记为当前规划卡时使用唯一当前快照，其余消息保持历史快照。 */
export function resolvePlanningMessageWorkflow(
  historicalWorkflow: WorkflowRunPayload | undefined,
  currentPlanningWorkflow: WorkflowRunPayload | undefined,
  isCurrentPlanningMessage: boolean
): WorkflowRunPayload | undefined {
  return isCurrentPlanningMessage ? currentPlanningWorkflow : historicalWorkflow
}
