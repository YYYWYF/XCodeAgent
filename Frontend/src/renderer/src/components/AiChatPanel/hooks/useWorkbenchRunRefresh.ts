import { useEffect, useRef } from 'react'
import { getApplicationLifecycle } from '../../../service/applicationLifecycle'
import type { ApplicationConfig, ApplicationLifecycle } from '../../../typings'

export type WorkbenchRunRefreshConnection = {
  scopeKey?: string
  start: () => number
  settle: (generation: number, error?: string) => void
}

/** 单轮只读刷新只报告通信结果，不根据断流改写执行状态或恢复候选。 */
export async function refreshWorkbenchRun(
  read: () => Promise<ApplicationLifecycle>,
  onLifecycle: (lifecycle: ApplicationLifecycle) => void,
  connection: WorkbenchRunRefreshConnection,
  isCurrent: () => boolean
): Promise<void> {
  const generation = connection.start()
  try {
    const lifecycle = await read()
    if (!isCurrent()) return
    connection.settle(generation)
    onLifecycle(lifecycle)
  } catch (reason) {
    if (!isCurrent()) return
    connection.settle(generation, reason instanceof Error ? reason.message : 'Backend 暂时不可用，无法同步最新状态。')
  }
}

/** 原实时连接不存在时只读刷新当前 Run；离开会话或到达终态即停止刷新。 */
export function useWorkbenchRunRefresh(
  application: ApplicationConfig,
  runId: string | undefined,
  onLifecycle: (lifecycle: ApplicationLifecycle) => void,
  connection: WorkbenchRunRefreshConnection
): void {
  const onLifecycleRef = useRef(onLifecycle)
  onLifecycleRef.current = onLifecycle
  const connectionRef = useRef(connection)
  connectionRef.current = connection
  useEffect(() => {
    if (!runId || !application.workspaceRoot) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const scopeKey = connection.scopeKey
    /** 串行查询避免重叠，旧会话的迟到结果不再回写当前视图。 */
    const refresh = async (): Promise<void> => {
      try {
        await refreshWorkbenchRun(
          () => getApplicationLifecycle(application),
          onLifecycleRef.current,
          connectionRef.current,
          () => !cancelled && connectionRef.current.scopeKey === scopeKey
        )
      } finally {
        if (!cancelled) timer = setTimeout(() => void refresh(), 3000)
      }
    }
    void refresh()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [application.id, application.workspaceRoot, runId, connection.scopeKey])
}
