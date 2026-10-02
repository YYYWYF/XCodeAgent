import type { ApplicationPlanningCurrentState } from './activeApplicationPlanning'
import type { ExecutionRecoveryCandidate } from '../typings'
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
      incidentId: string
    }
  | {
      kind: 'needs_attention'
      title: string
      failureDiagnostic?: RecoveryFailureDiagnostic | null
      failureMessage?: string
      recoveryMessage: string
      reasonCode: string
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
  candidate?: ExecutionRecoveryCandidate
): RecoveryIncidentPresentation | undefined {
  if (
    !candidate ||
    candidate.executionKind !== 'workbench' ||
    candidate.recoveryActionPlan.executionKind !== 'workbench'
  ) {
    return undefined
  }
  const actionPlan = candidate.recoveryActionPlan
  if (actionPlan.status === 'recoverable' && actionPlan.primaryAction) {
    return {
      kind: 'recoverable',
      title: '工作台执行需要恢复',
      failureDiagnostic: candidate.failureDiagnostic,
      failureMessage: candidate.failureDiagnostic?.message || candidate.message,
      recoveryMessage: actionPlan.message,
      action: actionPlan.primaryAction,
      incidentId: actionPlan.incidentId
    }
  }
  if (actionPlan.status === 'needs_attention') {
    return {
      kind: 'needs_attention',
      title: '工作台执行需要处理',
      failureDiagnostic: candidate.failureDiagnostic,
      failureMessage: candidate.failureDiagnostic?.message || candidate.message,
      recoveryMessage: actionPlan.message,
      reasonCode: actionPlan.reasonCode
    }
  }
  return undefined
}
