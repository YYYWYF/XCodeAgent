import type { ApplicationPlanningCurrentState } from './activeApplicationPlanning'
import type {
  ApplicationPlanningFailureDiagnostic,
  ApplicationPlanningRecoveryAction,
  ApplicationPlanningRecoveryActionPlan
} from './applicationPlanningRecovery'

export type ApplicationPlanningRecoveryIncident =
  | {
      kind: 'sync_error'
      title: string
      message: string
      actionLabel: '重新同步状态'
    }
  | {
      kind: 'recoverable'
      title: string
      failureDiagnostic?: ApplicationPlanningFailureDiagnostic | null
      failureMessage?: string
      recoveryMessage: string
      action: ApplicationPlanningRecoveryAction
      incidentId: string
    }
  | {
      kind: 'needs_attention'
      title: string
      failureDiagnostic?: ApplicationPlanningFailureDiagnostic | null
      failureMessage?: string
      recoveryMessage: string
      reasonCode: string
    }
  | {
      kind: 'reconciling_failure'
      title: string
      failureMessage: string
    }

/** 从当前 Planning State 提取用户可读的真实失败摘要，不把恢复说明当成错误。 */
function currentFailureMessage(
  state: ApplicationPlanningCurrentState,
  actionPlan?: ApplicationPlanningRecoveryActionPlan | null
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

/** 按 sync、Backend ActionPlan、reconcile failure 的固定优先级生成唯一当前 Incident。 */
export function applicationPlanningRecoveryIncident(
  state?: ApplicationPlanningCurrentState
): ApplicationPlanningRecoveryIncident | undefined {
  if (!state) return undefined
  const syncError = state.syncError?.trim()
  if (syncError) {
    return {
      kind: 'sync_error',
      title: '规划状态尚未同步',
      message: syncError,
      actionLabel: '重新同步状态'
    }
  }

  const recovery = state.recovery
  const actionPlan = recovery?.recoveryActionPlan
  if (actionPlan?.status === 'recoverable' && actionPlan.primaryAction) {
    return {
      kind: 'recoverable',
      title: '规划执行已中断',
      failureDiagnostic: recovery.failureDiagnostic,
      failureMessage: currentFailureMessage(state, actionPlan),
      recoveryMessage: actionPlan.message,
      action: actionPlan.primaryAction,
      incidentId: actionPlan.incidentId
    }
  }
  if (actionPlan?.status === 'needs_attention') {
    return {
      kind: 'needs_attention',
      title: '规划执行需要处理',
      failureDiagnostic: recovery?.failureDiagnostic,
      failureMessage: currentFailureMessage(state, actionPlan),
      recoveryMessage: actionPlan.message,
      reasonCode: actionPlan.reasonCode
    }
  }

  // awaiting_user 的业务确认卡已经是唯一当前控制面，Recovery Incident 必须退让。
  if (actionPlan?.status === 'awaiting_user' || recovery?.classification === 'awaiting_user') {
    return undefined
  }

  const failureMessage = currentFailureMessage(state, actionPlan)
  if (failureMessage && (state.transportState === 'reconciling' || !actionPlan)) {
    return {
      kind: 'reconciling_failure',
      title: '规划状态同步失败',
      failureMessage
    }
  }
  return undefined
}
