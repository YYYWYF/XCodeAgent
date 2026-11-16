import { useEffect, useRef, useState } from 'react'
import { runBuiltinPatch, type BuiltinPatchResult } from '../../service/builtinAuthorization'
import { attachApplicationWorkspace } from '../../service/applicationLifecycle'
import { builtinAuthorizationVisible, subscribeBuiltinAuthorizationChanged } from './builtinAuthorizationEvents'

type Snapshot = { value?: BuiltinPatchResult | null; refreshing: boolean; error?: string }

/** 当前工作区只读轮询；null 表示服务端确认无任务，undefined 表示尚无响应。 */
export function useBuiltinAuthorizationTask(workspace?: string) {
  const [snapshot, setSnapshot] = useState<{ workspace: string; state: Snapshot }>()
  const scope = useRef(workspace)
  scope.current = workspace
  const refreshRef = useRef<() => void>()
  useEffect(() => {
    if (!workspace) return
    const root = workspace
    let disposed = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let querying = false
    let terminal = false
    let revision = 0
    let backendInstance: string | undefined
    let needsAttach = false
    let state: Snapshot = { refreshing: true }

    /** 发布当前工作区的快照，丢弃切换或卸载后的更新。 */
    function publish(next: Snapshot): void {
      if (disposed || scope.current !== root) return
      state = next
      setSnapshot({ workspace: root, state })
    }

    /** 每轮仅发送 get，界面状态不触发 ensure 或远端 POST。 */
    async function query(): Promise<void> {
      if (disposed || querying || terminal || !builtinAuthorizationVisible()) return
      querying = true
      const owner = revision
      /** 判断读取结果是否已被动作通知或工作区切换作废。 */
      const isCurrent = () => !disposed && scope.current === root && owner === revision
      try {
        const value = await runBuiltinPatch(root, 'get')
        if (!isCurrent() || !builtinAuthorizationVisible()) return
        // 仅在当前任务可见且读通后，调用既有 attach 接管原任务。
        if (value && (needsAttach || backendInstance && value.backendInstanceId && backendInstance !== value.backendInstanceId)) {
          await attachApplicationWorkspace({ workspaceRoot: root })
          needsAttach = false
        }
        if (!isCurrent() || !builtinAuthorizationVisible()) return
        backendInstance = value?.backendInstanceId
        terminal = !value || !['pending', 'running', 'waiting'].includes(value.status)
        publish({ value, refreshing: false })
      } catch (error) {
        if (!isCurrent() || !builtinAuthorizationVisible()) return
        needsAttach = true
        publish({ value: state.value, refreshing: false, error: error instanceof Error ? error.message : '读取权限初始化状态失败。' })
      } finally {
        querying = false
        if (!disposed && builtinAuthorizationVisible()) {
          if (owner !== revision) void query()
          else if (!terminal) timer = setTimeout(() => { void query() }, 3000)
        }
      }
    }

    /** 使在途旧响应失效，合并通知并在可见时重读。 */
    function refresh(): void {
      revision += 1
      terminal = false
      clearTimeout(timer)
      publish({ ...state, refreshing: true })
      void query()
    }

    /** 暂停隐藏视图，恢复可见时读取包含空态和终态在内的最新结果。 */
    function onVisibilityChange(): void {
      if (!builtinAuthorizationVisible()) {
        clearTimeout(timer)
      } else {
        if (!terminal) needsAttach = true
        refresh()
      }
    }

    refreshRef.current = refresh
    const unsubscribe = subscribeBuiltinAuthorizationChanged(root, refresh)
    publish(state)
    void query()
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      disposed = true
      refreshRef.current = undefined
      clearTimeout(timer)
      unsubscribe()
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [workspace])

  /** 用户请求重新读取时继续走只读 get。 */
  function refresh(): void {
    refreshRef.current?.()
  }

  return { ...(snapshot?.workspace === workspace ? snapshot?.state : undefined), refresh }
}
