import type { ApplicationLifecycle } from '../typings'

/** 合并同一运行的只读进度；没有变化时保留生命周期引用，不触发重复会话恢复。 */
export function mergeWorkbenchProgress(
  base: ApplicationLifecycle,
  current: ApplicationLifecycle,
  incoming: ApplicationLifecycle
): ApplicationLifecycle {
  const previousProgress = current.extensions.workbenchProgress || {}
  const progress = { ...previousProgress }
  // GET 进度独立于 lifecycle revision，按当前执行身份校验后才允许更新。
  for (const [runId, item] of Object.entries(incoming.extensions.workbenchProgress || {})) {
    const execution = base.activeExecutions?.[runId]
    if (
      !execution ||
      execution.threadId !== item.threadId ||
      execution.ownerSessionId !== item.ownerSessionId ||
      execution.updatedAt !== item.updatedAt
    )
      continue
    const before = progress[runId]?.dagGeneration
    const after = item.dagGeneration
    progress[runId] =
      before &&
      after &&
      before.planningRunId === after.planningRunId &&
      before.revision > after.revision
        ? { ...item, dagGeneration: before }
        : item
  }
  // 执行移除或归属改变后清除旧进度，不能跨会话或 Run 复用。
  const ownedProgress = Object.fromEntries(
    Object.entries(progress).filter(([runId, item]) => {
      const execution = base.activeExecutions?.[runId]
      return (
        execution &&
        execution.threadId === item.threadId &&
        execution.ownerSessionId === item.ownerSessionId &&
        execution.updatedAt === item.updatedAt
      )
    })
  )
  const baseProgress = base.extensions.workbenchProgress
  const unchanged =
    Object.keys(baseProgress || {}).length === Object.keys(ownedProgress).length &&
    Object.entries(ownedProgress).every(
      ([runId, item]) =>
        // 进度是 AG-UI JSON 数据；逐 Run 比较，不依赖映射的枚举顺序。
        JSON.stringify(baseProgress?.[runId]) === JSON.stringify(item)
    )
  // 没有进度时不凭空添加空投影；已有进度被清除时仍返回明确的空结果。
  const clearedPrevious = !baseProgress && Object.keys(previousProgress).length > 0
  if (unchanged && !clearedPrevious) return base
  return { ...base, extensions: { ...base.extensions, workbenchProgress: ownedProgress } }
}
