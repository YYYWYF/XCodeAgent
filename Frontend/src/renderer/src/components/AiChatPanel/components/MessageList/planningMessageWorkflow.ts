import type { WorkflowRunPayload } from '../../../../typings'
import { workbenchPhaseForNode, type WorkbenchPhase } from '../../../../workbenchPhase'

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

/** 从消息自身的 Workflow 节点推导 Agent 身份，避免阶段推进后历史消息被重新标记。 */
export function planningMessageAgentPhase(
  workflow: WorkflowRunPayload | undefined,
  fallback: WorkbenchPhase
): WorkbenchPhase {
  if (!workflow) return fallback
  const phase =
    String(workflow.summary?.phase || '').trim() ||
    String(workflow.state?.phase || '').trim() ||
    [...(workflow.events || [])]
      .reverse()
      .map((event) => String(event.nodeName || event.node?.id || '').trim())
      .find(Boolean) ||
    ''
  // 交接后仍挂起的入口门属于目标规划 Agent；断网恢复不能按来源节点把当前门判为历史。
  if (phase === 'planning_stage_entry' &&
      (workflow.state?.planning_stage_handoff as { targetPhase?: string } | undefined)?.targetPhase === 'planning') {
    return 'planning'
  }
  return workbenchPhaseForNode(phase, fallback)
}
