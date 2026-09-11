import type { WorkflowRunPayload } from '../../../../typings'

/** 判断当前规划消息是否能够承载同步错误，避免再追加一张独立错误卡。 */
export function planningMessageHostsSyncError(
  syncError: string | undefined,
  currentPlanningMessageIndex: number
): boolean {
  return Boolean(syncError?.trim() && currentPlanningMessageIndex >= 0)
}

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
