import type { ReactElement } from 'react'
import type { ApplicationPlanningCurrentState } from '../../service/activeApplicationPlanning'
import {
  applicationPlanningRecoveryIncident,
  workbenchRecoveryIncident
} from '../../service/recoveryIncident'
import type { ExecutionRecoveryCandidate } from '../../typings'
import RecoveryIncidentCard from '../RecoveryIncidentCard/RecoveryIncidentCard'

export type RecoverySurfaceProps = {
  isApplicationPlanningPhase: boolean
  planningState?: ApplicationPlanningCurrentState
  onRetryPlanning?: () => void
  activeExecutionRecovery?: ExecutionRecoveryCandidate
  recoveryError?: string
  recoveryRunning: boolean
  actionDisabled?: boolean
  onExecuteRecoveryAction: (candidate: ExecutionRecoveryCandidate) => void
}

/** 统一渲染当前 Recovery Incident，Planning 与 Workbench 不再并列暴露旧恢复卡。 */
export default function RecoverySurface({
  isApplicationPlanningPhase,
  planningState,
  onRetryPlanning,
  activeExecutionRecovery,
  recoveryError,
  recoveryRunning,
  actionDisabled = false,
  onExecuteRecoveryAction
}: RecoverySurfaceProps): ReactElement | null {
  const incident = isApplicationPlanningPhase
    ? applicationPlanningRecoveryIncident(planningState)
    : workbenchRecoveryIncident(activeExecutionRecovery)
  if (!incident) return null

  return (
    <RecoveryIncidentCard
      incident={incident}
      disabled={actionDisabled}
      error={recoveryError}
      onAction={
        isApplicationPlanningPhase
          ? onRetryPlanning
          : activeExecutionRecovery
            ? () => onExecuteRecoveryAction(activeExecutionRecovery)
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
