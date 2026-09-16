import type { ReactElement } from 'react'
import {
  planningTransportBusy,
  type ApplicationPlanningCurrentState
} from '../../service/activeApplicationPlanning'
import { applicationPlanningRecoveryIncident } from '../../service/recoveryIncident'
import { connectionAllowsMutation } from '../../service/connectionState'
import RecoveryIncidentCard from '../RecoveryIncidentCard/RecoveryIncidentCard'

export type ApplicationPlanningRecoveryIncidentCardProps = {
  planning: ApplicationPlanningCurrentState
  onAction?: () => void
  retrying?: boolean
}

/** 保留 Planning 组件入口兼容现有布局，实际展示统一交给 RecoveryIncidentCard。 */
export default function ApplicationPlanningRecoveryIncidentCard({
  planning,
  onAction,
  retrying
}: ApplicationPlanningRecoveryIncidentCardProps): ReactElement | null {
  const incident = applicationPlanningRecoveryIncident(planning)
  if (!incident) return null
  return (
    <RecoveryIncidentCard
      incident={incident}
      disabled={!connectionAllowsMutation(planning.connection)}
      onAction={onAction}
      retrying={retrying ?? planningTransportBusy(planning)}
      testId="application-planning-recovery-incident"
    />
  )
}
