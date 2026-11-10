import type { WorkflowClarification, WorkflowRunPayload } from '../../../../typings'
import { workbenchPhaseForNode, type WorkbenchPhase } from '../../../../workbenchPhase'
import { useRef } from 'react'

/** 单页动作仍在同一 UI 审阅阶段时保留页面展示，原始运行状态及中断权限继续由当前快照决定。 */
export function useUiDesignReviewWorkflow(
  workflow: WorkflowRunPayload | undefined,
  workspaceRoot: string | undefined
): WorkflowRunPayload | undefined {
  const previous = useRef<{ workspaceRoot?: string; workflow: WorkflowRunPayload }>()
  const status = workflow?.summary.status
  const phase = workflow?.summary.phase || workflow?.state?.phase
  const cached = previous.current
  const sameSource = cached && cached.workspaceRoot === workspaceRoot && cached.workflow.threadId === workflow?.threadId
  // 原生恢复先经过 review 节点，RunStarted 也可能暂缺 phase；这些帧仍属于同一次 UI 操作。
  const uiPhase = phase === 'ui_confirmation' || phase === 'ui_confirmation_review' ||
    (!phase && status === 'running' && sameSource)
  if (!workflow || !uiPhase || !['requires_user_input', 'running'].includes(String(status))) {
    previous.current = undefined
    return undefined
  }
  const rawClarification = workflow.summary.clarification || workflow.state?.clarification
  const clarification = rawClarification && typeof rawClarification === 'object' && !Array.isArray(rawClarification)
    ? rawClarification as WorkflowClarification : undefined
  const hasPages = clarification?.mode === 'ui_design_confirmation' &&
    Array.isArray(clarification.pages) && clarification.pages.length > 0
  const retained = !hasPages && status === 'running' && sameSource ? cached.workflow.summary.clarification : undefined
  const display = hasPages ? clarification : retained
  if (!display) {
    previous.current = undefined
    return undefined
  }
  // 只补展示内容，绝不沿用旧 Run 的原生中断或改写 running 为待确认。
  const result = display === clarification && phase === 'ui_confirmation' ? workflow : {
    ...workflow,
    summary: { ...workflow.summary, phase: 'ui_confirmation', clarification: display },
    state: { ...workflow.state, clarification: display }
  }
  previous.current = { workspaceRoot, workflow: result }
  return result
}

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
