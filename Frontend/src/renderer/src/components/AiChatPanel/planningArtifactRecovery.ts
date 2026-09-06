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

/** 为冷恢复或阶段切回缺少内存快照时返回当前阶段必需的本地产物。 */
export function planningArtifactRecoveryKeys(
  shouldRestoreFromDisk: boolean,
  phase: WorkbenchPhase
): readonly PlanningArtifactRecoveryKey[] {
  if (!shouldRestoreFromDisk) return []
  if (phase === 'product') return DESIGN_RECOVERY_KEYS
  if (phase === 'planning') return TECHNICAL_PLANNING_RECOVERY_KEYS
  return []
}
