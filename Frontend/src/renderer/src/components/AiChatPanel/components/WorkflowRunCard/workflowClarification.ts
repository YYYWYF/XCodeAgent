import type { ApplicationLifecycle, WorkflowClarification, WorkflowRunPayload } from '../../../../typings'
import { isConversationWorkflow } from '../../conversationMode'

/** 同 Run/Thread 生命周期确认单测正在执行，扫描进度的临时 phase 不改变业务阶段。 */
export function workflowUnitTestRunning(workflow: WorkflowRunPayload): boolean {
  const lifecycle = workflow.summary.lifecycle || workflow.state?.lifecycle || workflow.result?.lifecycle
  const execution = lifecycle && typeof lifecycle === 'object' && 'activeExecutions' in lifecycle
    ? (lifecycle as ApplicationLifecycle).activeExecutions?.[workflow.runId] : undefined
  return workflow.summary.status === 'running' && execution?.status === 'running' &&
    execution.phase === 'unit_test' && execution.threadId === workflow.threadId && !execution.pendingInteraction
}

const WORKFLOW_PHASE_CONFIRMATION_MODES: Record<string, string> = {
  test_phase_confirmation: 'test_phase_confirmation',
  review_phase_confirmation: 'review_phase_confirmation',
  acceptance_phase_confirmation: 'acceptance_phase_confirmation'
}

/** 从 Workflow 公开状态读取与当前 phase 匹配的权威确认载荷。 */
export function workflowClarification(
  workflow: WorkflowRunPayload
): WorkflowClarification | undefined {
  // 二次修改只有当前轮等待输入时才展示确认，扫描/分类期间不读取旧 checkpoint 或事件中的表单。
  if (isConversationWorkflow(workflow) && workflow.summary.status !== 'requires_user_input') {
    return undefined
  }
  const candidates: unknown[] = [
    workflow.summary.clarification,
    workflow.state?.clarification,
    workflow.result?.clarification,
    workflow.summary.reviewPhaseConfirmation,
    workflow.summary.acceptancePhaseConfirmation
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
      (candidate) => isUsableWorkflowClarification(candidate) && candidate.mode === expectedMode
    )
    if (isUsableWorkflowClarification(matching)) return matching
    if (workflow.summary.status === 'requires_user_input') {
      return workflowPhaseConfirmationFallback(expectedMode)
    }
    // 已进入确认阶段但当前载荷尚未到达时，不回退到上一阶段 clarification。
    return undefined
  }

  const currentClarification = candidates.find(isUsableWorkflowClarification)
  // 单测恢复已沿用 run 决策并进入执行时，旧确认载荷不能再显示为待答表单。
  // 必须同时有同 Run/Thread 的 running 生命周期且没有 pendingInteraction，避免帧间误隐藏真实确认。
  if (
    currentClarification?.mode === 'unit_test_confirmation' && workflowUnitTestRunning(workflow)
  ) return undefined
  if (currentClarification) return currentClarification

  // 只有当前 clarification 完全缺失时，才用历史 DAG 投影支持 PendingPlan 恢复。
  const historicalProjection = workflow.summary.buildTaskPlanConfirmation
  return isUsableWorkflowClarification(historicalProjection) ? historicalProjection : undefined
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
