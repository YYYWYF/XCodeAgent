import type { ReactElement } from 'react'
import type { ApplicationPlanningCurrentState } from '../../service/activeApplicationPlanning'
import type { ExecutionRecoveryCandidate } from '../../typings'
import ApplicationPlanningRecoveryIncidentCard from '../ApplicationPlanningRecoveryIncidentCard'
import ExecutionRecoveryCard from './components/ExecutionRecoveryCard'
import { shouldShowLegacyExecutionRecovery } from './executionRecoveryState'

export type RecoverySurfaceProps = {
  isApplicationPlanningPhase: boolean
  planningState?: ApplicationPlanningCurrentState
  onRetryPlanning?: () => void
  activeExecutionRecovery?: ExecutionRecoveryCandidate
  hasBusinessInteraction: boolean
  acceptanceAwaiting: boolean
  workflowInputLocked: boolean
  otherSessionExecutionLocked: boolean
  recoveryError?: string
  recoveryRunning: boolean
  onContinueInterruptedExecution: (candidate: ExecutionRecoveryCandidate) => void
}

/** 统一渲染 Planning 当前 Incident 与 Workbench 旧恢复卡，确保两者不会串成双控制面。 */
export default function RecoverySurface({
  isApplicationPlanningPhase,
  planningState,
  onRetryPlanning,
  activeExecutionRecovery,
  hasBusinessInteraction,
  acceptanceAwaiting,
  workflowInputLocked,
  otherSessionExecutionLocked,
  recoveryError,
  recoveryRunning,
  onContinueInterruptedExecution
}: RecoverySurfaceProps): ReactElement {
  const showLegacyExecutionRecovery = shouldShowLegacyExecutionRecovery(activeExecutionRecovery, {
    isApplicationPlanningPhase,
    hasBusinessInteraction,
    acceptanceAwaiting
  })

  return (
    <>
      {isApplicationPlanningPhase && planningState ? (
        <ApplicationPlanningRecoveryIncidentCard
          onAction={onRetryPlanning}
          planning={planningState}
        />
      ) : null}

      {showLegacyExecutionRecovery && activeExecutionRecovery ? (
        <ExecutionRecoveryCard
          disabled={workflowInputLocked || otherSessionExecutionLocked}
          error={recoveryError}
          loading={recoveryRunning}
          onContinue={() => {
            onContinueInterruptedExecution(activeExecutionRecovery)
          }}
          recovery={activeExecutionRecovery}
        />
      ) : null}
    </>
  )
}
