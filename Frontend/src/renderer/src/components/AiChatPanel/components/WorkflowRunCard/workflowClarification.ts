import type { WorkflowClarification, WorkflowRunPayload } from '../../../../typings'

const WORKFLOW_PHASE_CONFIRMATION_MODES: Record<string, string> = {
  test_phase_confirmation: 'test_phase_confirmation',
  review_phase_confirmation: 'review_phase_confirmation',
  acceptance_phase_confirmation: 'acceptance_phase_confirmation'
}

/** 从 Workflow 公开状态读取与当前 phase 匹配的权威确认载荷。 */
export function workflowClarification(
  workflow: WorkflowRunPayload
): WorkflowClarification | undefined {
  const candidates: unknown[] = [
    workflow.summary.clarification,
    workflow.summary.buildTaskPlanConfirmation,
    workflow.state?.clarification,
    workflow.result?.clarification
  ]
  const clarificationEvent = workflow.events
    .slice()
    .reverse()
    .find((event) => {
      const detail = event.data?.detail
      return Boolean(detail && typeof detail === 'object' && 'clarification' in detail)
    })
  const eventClarification = clarificationEvent?.data?.detail
  if (
    eventClarification &&
    typeof eventClarification === 'object' &&
    'clarification' in eventClarification
  ) {
    candidates.push((eventClarification as { clarification?: unknown }).clarification)
  }

  // 当前节点是确认卡的权威来源：流式合并可能短暂保留上一阶段 clarification，
  // 必须先选择与 phase 匹配的载荷，避免验收确认仍显示“进入审查阶段”。
  const phase = String(
    workflow.summary.phase || workflow.state?.phase || workflow.result?.phase || ''
  )
  const expectedMode = WORKFLOW_PHASE_CONFIRMATION_MODES[phase]
  if (expectedMode) {
    const matching = candidates.find(
      (candidate) =>
        isUsableWorkflowClarification(candidate) && candidate.mode === expectedMode
    )
    if (isUsableWorkflowClarification(matching)) return matching
    if (workflow.summary.status === 'requires_user_input') {
      return workflowPhaseConfirmationFallback(expectedMode)
    }
  }

  return candidates.find(isUsableWorkflowClarification)
}

/** 判断确认载荷是否包含可渲染语义，避免空对象遮蔽后续真实载荷。 */
function isUsableWorkflowClarification(value: unknown): value is WorkflowClarification {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const clarification = value as WorkflowClarification
  return Boolean(
    clarification.mode ||
      clarification.status ||
      clarification.message ||
      (Array.isArray(clarification.questions) && clarification.questions.length > 0)
  )
}

/** 为流式快照缺少当前确认载荷时生成最小可提交确认卡。 */
function workflowPhaseConfirmationFallback(mode: string): WorkflowClarification {
  const message =
    mode === 'acceptance_phase_confirmation'
      ? '代码审查已完成，是否进入验收阶段？'
      : mode === 'review_phase_confirmation'
        ? '测试已通过，是否进入审查阶段？'
        : '开发已完成，是否进入测试阶段？'
  return {
    mode,
    status: 'requires_user_input',
    message,
    questions: []
  }
}
