import type { WorkbenchPhase } from '../../workbenchPhase'

export type PlanningArtifactRecoveryKey =
  | 'requirement-spec'
  | 'product-plan'
  | 'ui-design'
  | 'technical-plan'

const DESIGN_RECOVERY_KEYS: readonly PlanningArtifactRecoveryKey[] = [
  'requirement-spec',
  'product-plan',
  'ui-design'
]

const TECHNICAL_PLANNING_RECOVERY_KEYS: readonly PlanningArtifactRecoveryKey[] = [
  'product-plan',
  'technical-plan'
]

/** 只为明确的冷恢复返回当前阶段必需的本地产物，实时流程始终返回空集合。 */
export function planningArtifactRecoveryKeys(
  restoreFromDisk: boolean,
  phase: WorkbenchPhase
): readonly PlanningArtifactRecoveryKey[] {
  if (!restoreFromDisk) return []
  if (phase === 'product') return DESIGN_RECOVERY_KEYS
  if (phase === 'planning') return TECHNICAL_PLANNING_RECOVERY_KEYS
  return []
}
