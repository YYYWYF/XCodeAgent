import type { WorkbenchPhase } from '../../workbenchPhase'
import type { WorkflowRunPayload } from '../../typings'

/** 正在重建或已失败时将仍可阅读的已确认基线标为上一版，避免误认为本轮已成功。 */
export function technicalPlanBaselineStatus(
  workflow: WorkflowRunPayload | undefined,
  plan: Record<string, unknown> | undefined
): string | undefined {
  if (plan?.confirmation_status !== 'confirmed') return undefined
  const phase = String(workflow?.summary?.phase || workflow?.state?.phase || '')
  if (!phase.startsWith('technical_planning')) return undefined
  const status = workflow?.summary?.status
  return status === 'failed' ? '上一版 · 本轮已中断'
    : status === 'running' ? '上一版 · 正在生成' : undefined
}

/** 忽略节点重置留下的空对象，继续查找真正含有架构内容的技术规划快照。 */
export function technicalPlanFromWorkflow(
  workflow: WorkflowRunPayload | undefined
): Record<string, unknown> | undefined {
  for (const source of [workflow?.state, workflow?.result]) {
    const value = source?.technical_plan
    if (!value || typeof value !== 'object' || Array.isArray(value)) continue
    const plan = value as Record<string, unknown>
    if (plan.architecture && typeof plan.architecture === 'object' && !Array.isArray(plan.architecture)
      && Object.keys(plan.architecture).length > 0) return plan
  }
  return undefined
}

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
