import { useCallback, useEffect, useRef, useState } from 'react'
import { applicationWorkspaceKey } from '../service/applicationWorkspace'
import { latestApplicationLifecycle } from '../service/activeApplicationPlanning'
import type { ApplicationLifecycle } from '../typings'

const NON_TERMINAL_EXECUTION_STATUSES = new Set(['running', 'stopping', 'awaiting_user'])

export { latestApplicationLifecycle }
/** 判断应用是否仍有需要在后台继续持有的非终态执行。 */
export function hasNonTerminalApplicationExecution(lifecycle?: ApplicationLifecycle): boolean {
  return Object.values(lifecycle?.activeExecutions || {}).some((execution) =>
    NON_TERMINAL_EXECUTION_STATUSES.has(execution.status)
  )
}

/** 为整个工作台提供单一 application lifecycle store，统一接收恢复读取与 AG-UI 事件。 */
export function useApplicationLifecycleStore(applicationId: string, workspaceRoot: string | undefined): {
  lifecycle?: ApplicationLifecycle
  mergeLifecycle: (lifecycle: ApplicationLifecycle) => void
  resetLifecycle: (lifecycle: ApplicationLifecycle) => void
} {
  const scope = applicationWorkspaceKey({ id: applicationId, workspaceRoot })
  const scopeRef = useRef(scope)
  scopeRef.current = scope
  const [stored, setStored] = useState<{ scope: string; lifecycle: ApplicationLifecycle }>()
  const applicationLifecycle = stored?.scope === scope ? stored.lifecycle : undefined

  // 切换应用时立即丢弃上一应用的快照，避免校准请求返回前短暂串用资源锁。
  useEffect(() => {
    setStored((current) => (current?.scope === scope ? current : undefined))
  }, [scope])

  // 实时事件、冷启动读取和重连校准共享持久化 revision；GET-time planningRefresh 单独刷新。
  const mergeLifecycle = useCallback((incoming: ApplicationLifecycle): void => {
    if (scopeRef.current !== scope || incoming.application.id !== applicationId) return
    setStored((current) => ({ scope, lifecycle: latestApplicationLifecycle(current?.scope === scope ? current.lifecycle : undefined, incoming) }))
  }, [applicationId, scope])

  // 强制覆盖 lifecycle（发起新迭代时 revision 从 1 重新开始，需绕过 revision 单调合并）。
  const resetLifecycle = useCallback((incoming: ApplicationLifecycle): void => {
    if (scopeRef.current !== scope || incoming.application.id !== applicationId) return
    setStored({ scope, lifecycle: incoming })
  }, [applicationId, scope])

  return { lifecycle: applicationLifecycle, mergeLifecycle, resetLifecycle }
}
