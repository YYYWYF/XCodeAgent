import { useCallback, useEffect, useState } from 'react'
import type { ApplicationLifecycle } from '../typings'

const NON_TERMINAL_EXECUTION_STATUSES = new Set(['running', 'stopping', 'awaiting_user'])

/** 按应用标识和单调 revision 合并 lifecycle，拒绝冷启动读取覆盖更新的实时投影。 */
export function latestApplicationLifecycle(
  current: ApplicationLifecycle | undefined,
  incoming: ApplicationLifecycle
): ApplicationLifecycle {
  if (!current || current.application.id !== incoming.application.id) return incoming
  return incoming.revision > current.revision ? incoming : current
}

/** 判断应用是否仍有需要在后台继续持有的非终态执行。 */
export function hasNonTerminalApplicationExecution(lifecycle?: ApplicationLifecycle): boolean {
  return Object.values(lifecycle?.activeExecutions || {}).some((execution) =>
    NON_TERMINAL_EXECUTION_STATUSES.has(execution.status)
  )
}

/** 为整个工作台提供单一 application lifecycle store，统一接收恢复读取与 AG-UI 事件。 */
export function useApplicationLifecycleStore(applicationId: string): {
  lifecycle?: ApplicationLifecycle
  mergeLifecycle: (lifecycle: ApplicationLifecycle) => void
} {
  const [lifecycle, setLifecycle] = useState<ApplicationLifecycle>()
  const applicationLifecycle = lifecycle?.application.id === applicationId ? lifecycle : undefined

  // 切换应用时立即丢弃上一应用的快照，避免校准请求返回前短暂串用资源锁。
  useEffect(() => {
    setLifecycle((current) => (current?.application.id === applicationId ? current : undefined))
  }, [applicationId])

  // 实时事件、冷启动读取和重连校准都走相同的 revision 合并规则。
  const mergeLifecycle = useCallback((incoming: ApplicationLifecycle): void => {
    setLifecycle((current) => latestApplicationLifecycle(current, incoming))
  }, [])

  return { lifecycle: applicationLifecycle, mergeLifecycle }
}
