import type { ApplicationPlanningCurrentState } from './activeApplicationPlanning'
import type { ExecutionRecoveryCandidate, WorkflowRunPayload } from '../typings'
import type {
  RecoveryAction,
  RecoveryActionPlan,
  RecoveryFailureDiagnostic
} from './recoveryActionPlan'

export type RecoveryIncidentPresentation =
  | {
      kind: 'recoverable'
      title: string
      failureDiagnostic?: RecoveryFailureDiagnostic | null
      failureMessage?: string
      recoveryMessage: string
      action: RecoveryAction
      retryNode?: string
      incidentId: string
    }
  | {
      kind: 'needs_attention'
      title: string
      failureDiagnostic?: RecoveryFailureDiagnostic | null
      failureMessage?: string
      recoveryMessage: string
      reasonCode: string
      technicalMessage?: string
    }

const MODEL_PLANNING_FAILURE_CODE = 'UNIT_GENERATION_INFRASTRUCTURE_FAILURE'

/** 仅识别已定义的执行计划模型基础设施错误，不用关键词归因其他异常。 */
export function isWorkbenchModelPlanningFailure(
  errorCode?: string | null
): boolean {
  return errorCode === MODEL_PLANNING_FAILURE_CODE
}

/** 仅用与当前 durable source 同 Run 的结构化错误码生成工作台失败摘要。 */
function currentWorkbenchFailureMessage(
  candidate: ExecutionRecoveryCandidate,
  workflow?: WorkflowRunPayload
): string | undefined {
  if (
    candidate.executionStatus !== 'failed' ||
    workflow?.runId !== candidate.sourceRunId ||
    workflow.summary.status !== 'failed'
  ) return undefined
  return workflow.summary.errorCode === MODEL_PLANNING_FAILURE_CODE
    ? '生成执行计划时，模型准备或调用失败。'
    : undefined
}

/** 从当前 Planning State 提取真实失败摘要，不把恢复说明误当成原始错误。 */
function currentPlanningFailureMessage(
  state: ApplicationPlanningCurrentState,
  actionPlan?: RecoveryActionPlan<'application_planning'> | null
): string | undefined {
  const recoveryMessage = actionPlan?.message.trim()
  const candidates = [
    state.recovery?.failureDiagnostic?.message,
    state.error,
    state.workflow?.summary?.status === 'failed' ? state.workflow.summary.message : undefined,
    state.lifecycle.initialization.status === 'failed'
      ? state.lifecycle.error?.message
      : undefined
  ]
  return candidates.find((value) => {
    const normalized = value?.trim()
    return Boolean(normalized && normalized !== recoveryMessage)
  })?.trim()
}

/** 只从 Backend durable ActionPlan 生成 Planning Recovery Incident。 */
export function applicationPlanningRecoveryIncident(
  state?: ApplicationPlanningCurrentState
): RecoveryIncidentPresentation | undefined {
  if (!state) return undefined
  const recovery = state.recovery
  const actionPlan = recovery?.recoveryActionPlan
  if (recovery && actionPlan?.status === 'recoverable' && actionPlan.primaryAction) {
    return {
      kind: 'recoverable',
      title: '执行已中断',
      failureDiagnostic: recovery.failureDiagnostic,
      failureMessage: currentPlanningFailureMessage(state, actionPlan),
      recoveryMessage: actionPlan.message,
      action: actionPlan.primaryAction,
      incidentId: actionPlan.incidentId
    }
  }
  if (actionPlan?.status === 'needs_attention') {
    return {
      kind: 'needs_attention',
      title: '执行需要处理',
      failureDiagnostic: recovery?.failureDiagnostic,
      failureMessage: currentPlanningFailureMessage(state, actionPlan),
      recoveryMessage: actionPlan.message,
      reasonCode: actionPlan.reasonCode
    }
  }

  // awaiting_user 的业务确认卡已经是唯一当前控制面，Recovery Incident 必须退让。
  if (actionPlan?.status === 'awaiting_user' || recovery?.classification === 'awaiting_user') {
    return undefined
  }
  return undefined
}

/** 只从 Workbench ActionPlan 生成当前 Incident，禁止回退到 availability/canContinue 猜动作。 */
export function workbenchRecoveryIncident(
  candidate?: ExecutionRecoveryCandidate,
  workflow?: WorkflowRunPayload
): RecoveryIncidentPresentation | undefined {
  if (
    !candidate ||
    candidate.executionKind !== 'workbench' ||
    candidate.recoveryActionPlan.executionKind !== 'workbench'
  ) {
    return undefined
  }
  const actionPlan = candidate.recoveryActionPlan
  const failureMessage = currentWorkbenchFailureMessage(candidate, workflow)
  if (actionPlan.status === 'recoverable' && actionPlan.primaryAction) {
    const retryingNode = ['retry_failed_node', 'retry_business_node'].includes(
      actionPlan.primaryAction.kind
    )
    const targetNode = actionPlan.primaryAction.targetNode
    const nodeLabel = targetNode ? WORKBENCH_RETRY_NODE_LABELS[targetNode] : undefined
    return {
      kind: 'recoverable',
      title: retryingNode
        ? '当前执行失败'
        : '工作台执行需要恢复',
      failureMessage,
      recoveryMessage: retryingNode
        ? nodeLabel ? `将从「${nodeLabel}」节点重试。` : '重试时将重新确认执行起点。'
        : '可以继续当前执行。',
      action: retryingNode
        ? { ...actionPlan.primaryAction, label: '重试' }
        : actionPlan.primaryAction,
      incidentId: actionPlan.incidentId
    }
  }
  if (actionPlan.status === 'needs_attention') {
    return {
      kind: 'needs_attention',
      title: candidate.executionStatus === 'failed' ? '当前执行失败' : '执行未完成',
      failureMessage,
      recoveryMessage: '请重试；系统会重新确认执行起点。',
      reasonCode: actionPlan.reasonCode
    }
  }
  return undefined
}

/** 将后端确认的工作台重试节点转换为用户可读名称。 */
const WORKBENCH_RETRY_NODE_LABELS: Record<string, string> = {
  prepare_build_tasks: '生成执行计划',
  build: '开发实现',
  code_review: '前后端代码审查',
  technical_planning: '生成技术规划'
}
