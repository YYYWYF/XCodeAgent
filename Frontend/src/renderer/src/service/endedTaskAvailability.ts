import type { ApplicationLifecycle } from '../typings'

/** 工作区资格属于 GET 时计算的投影，同 revision 也要更新，迟到响应不能覆盖较新的检查。 */
export function mergeEndedTaskAvailability(
  base: ApplicationLifecycle,
  current: ApplicationLifecycle,
  incoming: ApplicationLifecycle
): ApplicationLifecycle {
  if (incoming.revision !== base.revision || !incoming.endedExecutionsCheckedAt) return base
  const latestCheck = current.endedExecutionsCheckedAt || ''
  if (incoming.endedExecutionsCheckedAt <= latestCheck) return base
  return { ...base, endedExecutions: incoming.endedExecutions,
    endedExecutionsCheckedAt: incoming.endedExecutionsCheckedAt }
}
