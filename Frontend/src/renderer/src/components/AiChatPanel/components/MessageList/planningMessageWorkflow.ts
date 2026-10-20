import type { WorkflowRunPayload } from '../../../../typings'

/** 判断确认交互是否由当前审阅门或当前权威消息承载，避免新快照复用旧消息宿主时被误锁定。 */
export function planningInteractionIsCurrent(
  isCurrentPlanningReview: boolean,
  isCurrentPlanningMessage: boolean
): boolean {
  return isCurrentPlanningReview || isCurrentPlanningMessage
}

/** 在明确标记为当前规划卡时使用唯一当前快照，其余消息保持历史快照。 */
export function resolvePlanningMessageWorkflow(
  historicalWorkflow: WorkflowRunPayload | undefined,
  currentPlanningWorkflow: WorkflowRunPayload | undefined,
  isCurrentPlanningMessage: boolean
): WorkflowRunPayload | undefined {
  return isCurrentPlanningMessage ? currentPlanningWorkflow : historicalWorkflow
}
