import type { ReactElement } from 'react'
import { Modal } from 'antd'
import type { ApplicationPlanningCurrentState } from '../../service/activeApplicationPlanning'
import {
  applicationPlanningRecoveryIncident,
  workbenchRecoveryIncident
} from '../../service/recoveryIncident'
import type { ExecutionRecoveryCandidate, WorkflowRunPayload } from '../../typings'
import RecoveryIncidentCard from '../RecoveryIncidentCard/RecoveryIncidentCard'
import AgentErrorCard from '../AgentErrorCard'
import { recoveryFailureMessage } from '../../service/recoveryFailureMessage'
import { isConversationWorkflow } from './conversationMode'

export type RecoverySurfaceProps = {
  isApplicationPlanningPhase: boolean
  planningState?: ApplicationPlanningCurrentState
  onRetryPlanning?: () => void
  activeExecutionRecovery?: ExecutionRecoveryCandidate
  currentWorkflow?: WorkflowRunPayload
  recoveryError?: string
  recoveryRunning: boolean
  actionDisabled?: boolean
  onExecuteRecoveryAction: (candidate: ExecutionRecoveryCandidate) => void
  onRetryCurrentRecovery?: () => void
}

/** 统一渲染当前 Recovery Incident，Planning 与 Workbench 不再并列暴露旧恢复卡。 */
export default function RecoverySurface({
  isApplicationPlanningPhase,
  planningState,
  onRetryPlanning,
  activeExecutionRecovery,
  currentWorkflow,
  recoveryError,
  recoveryRunning,
  actionDisabled = false,
  onExecuteRecoveryAction,
  onRetryCurrentRecovery
}: RecoverySurfaceProps): ReactElement | null {
  const incident = isApplicationPlanningPhase
    ? applicationPlanningRecoveryIncident(planningState)
    : workbenchRecoveryIncident(activeExecutionRecovery, currentWorkflow)
  if (!incident) return null

  // 仅二次修改复用普通错误重试入口，其他阶段保留原恢复卡和操作。
  if (!isApplicationPlanningPhase && (activeExecutionRecovery
    ? activeExecutionRecovery.workflowScope === 'conversation'
    : isConversationWorkflow(currentWorkflow))) {
    /** 重试前保留后端动作的确认要求，并沿用统一先对账再恢复的调用。 */
    const retry = (): void => {
      if (actionDisabled || recoveryRunning) return
      const execute = onRetryCurrentRecovery ?? (activeExecutionRecovery
        ? () => onExecuteRecoveryAction(activeExecutionRecovery) : undefined)
      const action = incident.kind === 'recoverable' ? incident.action : undefined
      if (action?.requiresConfirmation) {
        Modal.confirm({
          title: '确定执行此恢复操作？', content: action.description,
          cancelText: '取消', okText: '重试', onOk: execute
        })
      } else execute?.()
    }
    return (
      <div data-testid="workbench-recovery-incident">
        <AgentErrorCard
          title="任务执行异常"
          error={recoveryError || recoveryFailureMessage(incident.failureDiagnostic) ||
            incident.failureMessage || (activeExecutionRecovery?.executionStatus === 'interrupted'
              ? '执行已中断，未收到完成结果。请重试同步当前节点状态。'
              : incident.recoveryMessage)}
          onRetry={retry}
          retryDisabled={actionDisabled}
          retrying={recoveryRunning}
        />
      </div>
    )
  }

  return (
    <RecoveryIncidentCard
      incident={incident}
      disabled={actionDisabled}
      error={isApplicationPlanningPhase ? recoveryError : undefined}
      onAction={
        isApplicationPlanningPhase
          ? onRetryPlanning
          : activeExecutionRecovery
            ? onRetryCurrentRecovery ?? (() => onExecuteRecoveryAction(activeExecutionRecovery))
            : undefined
      }
      retrying={recoveryRunning}
      testId={
        isApplicationPlanningPhase
          ? 'application-planning-recovery-incident'
          : 'workbench-recovery-incident'
      }
    />
  )
}
