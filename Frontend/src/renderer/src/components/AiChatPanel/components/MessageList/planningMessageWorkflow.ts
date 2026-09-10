import type { WorkflowRunPayload } from '../../../../typings'

/** 在明确标记为当前规划卡时使用唯一当前快照，其余消息保持历史快照。 */
export function resolvePlanningMessageWorkflow(
  historicalWorkflow: WorkflowRunPayload | undefined,
  currentPlanningWorkflow: WorkflowRunPayload | undefined,
  isCurrentPlanningMessage: boolean
): WorkflowRunPayload | undefined {
  return isCurrentPlanningMessage ? currentPlanningWorkflow : historicalWorkflow
}
