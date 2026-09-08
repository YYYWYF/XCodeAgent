import { useCallback, useEffect, useState } from 'react'
import type { ApplicationLifecycle } from '../typings'

const NON_TERMINAL_EXECUTION_STATUSES = new Set(['running', 'stopping', 'awaiting_user'])

/**
 * 按应用标识和单调 revision 合并持久化 lifecycle。
 * planningRefresh 是 Backend GET 时临时计算的恢复投影，不参与持久化 revision；
 * 因此同 revision 的重新校准允许只替换该 extension，不能让旧持久化字段倒退。
 */
export function latestApplicationLifecycle(
  current: ApplicationLifecycle | undefined,
  incoming: ApplicationLifecycle
): ApplicationLifecycle {
  if (!current || current.application.id !== incoming.application.id) return incoming
  if (incoming.revision > current.revision) return incoming
  if (incoming.revision < current.revision) return current

  const planningRefresh = incoming.extensions?.planningRefresh
  if (!planningRefresh) return current
  return {
    ...current,
    extensions: {
      ...current.extensions,
      planningRefresh
    }
  }
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

  // 实时事件、冷启动读取和重连校准共享持久化 revision；GET-time planningRefresh 单独刷新。
  const mergeLifecycle = useCallback((incoming: ApplicationLifecycle): void => {
    setLifecycle((current) => latestApplicationLifecycle(current, incoming))
  }, [])

  return { lifecycle: applicationLifecycle, mergeLifecycle }
}
