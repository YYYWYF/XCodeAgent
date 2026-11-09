import type { ProcessStepRecord } from '../../service/agUiAgent'

/** 保留原 Build 之前的步骤，新进度到达后替换原 Build 卡，准备期间不让卡片消失。 */
export function mergeBuildRetrySteps(
  original: ProcessStepRecord[] | undefined,
  incoming: ProcessStepRecord[]
): ProcessStepRecord[] {
  const buildIndex = original?.findIndex((step) => Boolean(step.buildExecutionSlice)) ?? -1
  if (!original || buildIndex < 0) return incoming
  if (!incoming.some((step) => step.buildExecutionSlice)) return original
  return [...original.slice(0, buildIndex), ...incoming]
}
