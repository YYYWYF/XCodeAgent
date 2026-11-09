import { useEffect, useRef, useState } from 'react'
import { runBuiltinPatch, type BuiltinPatchResult } from '../../../../service/builtinAuthorization'
import { attachApplicationWorkspace } from '../../../../service/applicationLifecycle'

/** 当前工作区只读轮询，隐藏页面暂停，切换、卸载和迟到结果均撤销原代次。 */
export function useBuiltinAuthorizationTask(workspace?: string, revision = 0): BuiltinPatchResult | undefined {
  const [snapshot, setSnapshot] = useState<{ workspace: string; revision: number; value: BuiltinPatchResult }>()
  const scope = useRef(workspace)
  scope.current = workspace
  useEffect(() => {
    if (!workspace) return
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let querying = false
    let terminal = false
    let backendInstance: string | undefined
    let needsAttach = false
    /** 每轮仅发送 get，界面状态不触发 ensure 或远端 POST。 */
    async function query(): Promise<void> {
      if (disposed || querying || terminal || document.visibilityState === 'hidden') return
      querying = true
      try {
        const value = await runBuiltinPatch(workspace!, 'get')
        if (!disposed && scope.current === workspace && value) {
          // 只读状态请求证明恢复可达后，单独执行既有 attach 接管原任务。
          // 实例变化也能覆盖 Backend 在两轮查询之间快速重启的情况。
          if (needsAttach || backendInstance && value.backendInstanceId && backendInstance !== value.backendInstanceId) {
            await attachApplicationWorkspace({ workspaceRoot: workspace! })
            needsAttach = false
          }
          backendInstance = value.backendInstanceId
          if (disposed || scope.current !== workspace) return
          setSnapshot({ workspace: workspace!, revision, value })
          terminal = !['pending', 'running', 'waiting'].includes(value.status)
        } else if (!value) {
          terminal = true
        }
      } catch {
        // 网络错误只保留已知快照，下一轮继续只读查询。
        needsAttach = true
      } finally {
        querying = false
        if (!disposed && !terminal) timer = setTimeout(() => { void query() }, 3000)
      }
    }
    /** 恢复可见时立即读取当前任务，避免等待旧计时器。 */
    function onVisible(): void {
      if (document.visibilityState !== 'hidden') {
        needsAttach = true
        clearTimeout(timer)
        void query()
      }
    }
    void query()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      disposed = true
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [workspace, revision])
  return snapshot && snapshot.workspace === workspace && snapshot.revision === revision ? snapshot.value : undefined
}
