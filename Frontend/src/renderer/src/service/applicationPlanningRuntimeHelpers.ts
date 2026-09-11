import type {
  ApplicationConfig,
  ApplicationLifecycle,
  ApplicationPlanningConfirmation,
  ApplicationPlanningInteraction,
  WorkflowClarificationAnswers,
  WorkflowRunPayload
} from '../typings'
import type { SendWorkflowMessageOptions } from './agUiAgent'
import { getApplicationLifecycle } from './applicationLifecycle'
import type { saveRequirementSpecDraft } from './applicationPagePlanning'
import { planningTechnicalPlanConfirmed } from './applicationPlanningWorkflowState'
import { buildProductConversationInteraction } from './applicationPlanningProductConversation'

export const MISSING_INTERRUPT_ERROR = '当前规划确认卡缺少可恢复的服务端中断，请刷新后重试。'

/** 从已确认的技术规划快照读取模板准备输入。 */
export function workflowConfirmation(
  workflow?: WorkflowRunPayload
): ApplicationPlanningConfirmation | undefined {
  if (!planningTechnicalPlanConfirmed(workflow)) return undefined
  for (const source of [workflow?.result, workflow?.state]) {
    const value = source?.application_planning_confirmation
    if (value && typeof value === 'object') return value as ApplicationPlanningConfirmation
  }
  return undefined
}

/** 只更新展示快照里的权威生命周期，不构造 Graph 恢复状态。 */
export function withAuthoritativeLifecycle(
  workflow: WorkflowRunPayload,
  lifecycle: ApplicationLifecycle
): WorkflowRunPayload {
  return { ...workflow, state: { ...workflow.state, lifecycle }, result: { ...workflow.result, lifecycle } }
}

/** 把保存服务返回的需求文档和 Markdown 确认产物合并到最新快照。 */
export function withSavedRequirementSpec(
  workflow: WorkflowRunPayload,
  saved: Awaited<ReturnType<typeof saveRequirementSpecDraft>>
): WorkflowRunPayload {
  return {
    ...workflow,
    confirmationArtifact: saved.artifact,
    state: { ...workflow.state, requirement_spec: saved.requirementSpec },
    result: { ...workflow.result, requirement_spec: saved.requirementSpec }
  }
}

/** 读取服务端 checkpoint 投影的原生审阅中断。 */
export function planningInterrupt(workflow: WorkflowRunPayload): Record<string, unknown> {
  for (const source of [workflow.result, workflow.state]) {
    const value = source?.application_planning_interrupt
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
  }
  throw new Error(MISSING_INTERRUPT_ERROR)
}

/** 判断确认卡是否已经携带可恢复的审阅中断。 */
export function hasPlanningInterrupt(workflow: WorkflowRunPayload): boolean {
  try {
    planningInterrupt(workflow)
    return true
  } catch {
    return false
  }
}

/** 返回服务端中断的稳定门身份，供断线后判断提交是否已被 Graph 消费。 */
export function planningInterruptIdentity(workflow?: WorkflowRunPayload): string {
  if (!workflow) return ''
  try {
    const interrupt = planningInterrupt(workflow)
    const gateId = String(interrupt.gateId || '')
    const artifactRevision = String(interrupt.artifactRevision || '')
    return gateId && artifactRevision ? `${gateId}:${artifactRevision}` : ''
  } catch {
    return ''
  }
}

/** 用当前服务端审阅门身份构造类型化交互，禁止从历史卡片猜测恢复节点。 */
export function buildPlanningInteraction(
  workflow: WorkflowRunPayload,
  answers: WorkflowClarificationAnswers,
  editedRequirementSpec?: Record<string, unknown>,
  requirementSpecFeedback?: string,
  designChangeRequest?: string
): ApplicationPlanningInteraction {
  const pending = planningInterrupt(workflow)
  const gateId = String(pending.gateId || '')
  const artifactRevision = String(pending.artifactRevision || '')
  const artifact = String(pending.artifact || '') as ApplicationPlanningInteraction['artifact']
  if (!gateId || !artifactRevision) {
    throw new Error('当前规划确认卡版本信息不完整，请刷新后重试。')
  }
  if (designChangeRequest?.trim()) {
    return buildProductConversationInteraction({ gateId, artifact, artifactRevision }, designChangeRequest)
  }
  const action = answers.__applicationPlanningAction
  if (!action) throw new Error('当前规划提交缺少明确的交互动作，请从确认卡重新提交。')
  const visibleAnswers = { ...answers }
  delete visibleAnswers.__applicationPlanningAction
  const rawUiAction = visibleAnswers.ui_design_action
  if (action === 'ui_action') {
    if (!rawUiAction || typeof rawUiAction !== 'object' || Array.isArray(rawUiAction)) {
      throw new Error('UI 操作提交缺少结构化 uiAction，请重试。')
    }
    return { gateId, artifact, artifactRevision, action, answers: visibleAnswers, uiAction: rawUiAction as Record<string, unknown> }
  }
  const confirmationValue = [
    visibleAnswers.requirement_document_confirmation,
    visibleAnswers.ui_design_confirmation,
    visibleAnswers.technical_plan_confirmation
  ].find((value) => typeof value === 'string')
  const feedback = requirementSpecFeedback?.trim() || ''
  const planningRecovery = typeof visibleAnswers.planning_recovery === 'string'
    ? visibleAnswers.planning_recovery.trim() : ''
  const request = feedback || (typeof confirmationValue === 'string' ? confirmationValue.trim() : '') || planningRecovery
  if ((action === 'revise' || action === 'design_change') && !request) {
    throw new Error('修改动作必须提供明确的修改意见。')
  }
  return {
    gateId, artifact, artifactRevision, action, request, answers: visibleAnswers,
    editedRequirementSpec, requirementSpecFeedback: feedback || undefined
  }
}

/** 根据本轮读取的权威阶段保持原有恢复节点映射。 */
export function planningResumeFrom(
  lifecycle: ApplicationLifecycle
): NonNullable<SendWorkflowMessageOptions['workflowDebug']>['resumeFrom'] {
  switch (lifecycle.initialization.stage) {
    case 'generating_technical_plan':
    case 'awaiting_technical_plan_confirmation': return 'technical_planning'
    case 'generating_ui_designs':
    case 'awaiting_ui_design_confirmation': return 'ui_confirmation'
    case 'generating_requirement_document':
    case 'awaiting_requirement_document_confirmation': return 'product_planning'
    default: return 'requirements'
  }
}

/** 等待取消动作落入权威生命周期，保持原有最多二十次的有界等待。 */
export async function waitForStoppedPlanningLifecycle(
  getApplication: () => ApplicationConfig,
  threadId: string
): Promise<ApplicationLifecycle> {
  let latest = await getApplicationLifecycle(getApplication(), threadId)
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (!['pending', 'running', 'stopping'].includes(latest.initialization.status)) return latest
    await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 100))
    latest = await getApplicationLifecycle(getApplication(), threadId)
  }
  throw new Error('规划停止后生命周期仍处于运行状态，请重试。')
}

/** 将未知异常转换成运行错误文案，保持原界面的兜底规则。 */
export function planningRuntimeError(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback
}
