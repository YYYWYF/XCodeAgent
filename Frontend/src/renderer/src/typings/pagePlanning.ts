import type { ApplicationPlanningSnapshot } from './application'


export type ApplicationPlanningConfirmation = {
  path: string
  sha256: string
  confirmedAt: string
  planning: ApplicationPlanningSnapshot
}
