import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { inspectAllVersionControl } from '../service/versionControl'
import type { VersionControlSnapshot } from '../typings'

export type UncommittedChangesStore = {
  /** 当前工作区的 Git 快照；Git 不可用或尚未读取时为 undefined。 */
  snapshot?: VersionControlSnapshot
  /** 可提交文件数，与提交弹窗"N 个文件可提交"同一口径。 */
  count: number
  /** 主动重新读取 Git 状态。 */
  refresh: () => Promise<void>
  /** 直接采纳一份已知快照（提交成功后由调用方回填，省一次往返）。 */
  apply: (snapshot: VersionControlSnapshot) => void
}

/**
 * 常驻的"未提交变更"状态：供角标与各档提交提醒共用。
 *
 * 刷新走**事件驱动**而不是轮询：打开/切换工作区、代码变更落盘、
 * 提交成功。每次读取都要跑 git 进程，轮询在长时间开着工作台时开销明显。
 *
 * 读取失败**静默降级**为"无角标"：提醒是辅助信息，Git 不可用（未安装、非仓库、
 * 权限问题）不该在界面上抛错打断主流程。
 */
export function useUncommittedChangesStore(
  workspaceRoot: string,
  /**
   * 会随"工作区内容发生变化"而变的信号（调用方传 lifecycle revision 之类）。
   *
   * 只按 workspaceRoot 读一次会停在挂载那一刻：构建跑十分钟、窗口一直没失焦，
   * 快照就还是构建前的（通常是模板刚提交完的 0），角标因此不出现。
   * 传这个 key 让构建推进时重读。
   */
  refreshKey?: string
): UncommittedChangesStore {
  const [snapshot, setSnapshot] = useState<VersionControlSnapshot>()
  // 用 ref 而不是闭包里的 workspaceRoot：在途请求回来时工作区可能已经切走，
  // 闭包拿到的是旧值，会把上一个工作区的快照写进当前视图。
  const workspaceRef = useRef(workspaceRoot)
  workspaceRef.current = workspaceRoot
  // 每次切换都有独立身份，防止 A → B → A 时首次读取的迟到结果覆盖新现场。
  const scope = useMemo(() => ({ workspaceRoot }), [workspaceRoot])
  const scopeRef = useRef(scope)
  scopeRef.current = scope
  const inFlight = useRef(new Map<string, Promise<void>>())

  const apply = useCallback((next: VersionControlSnapshot): void => {
    if (next.workspaceRoot !== workspaceRef.current) return
    setSnapshot(next)
  }, [])

  /** 同一工作区的重叠刷新共用请求，失败后只等待下一次真实刷新事件。 */
  const refresh = useCallback((): Promise<void> => {
    if (!workspaceRoot) return Promise.resolve()
    const pending = inFlight.current.get(workspaceRoot)
    if (pending) return pending
    const request = (async (): Promise<void> => {
      try {
        const next = await inspectAllVersionControl(workspaceRoot)
        if (scopeRef.current === scope) apply(next)
      } catch {
        // 静默降级：读不到就当作没有未提交变更，不打扰用户。
      } finally {
        inFlight.current.delete(workspaceRoot)
      }
    })()
    inFlight.current.set(workspaceRoot, request)
    return request
  }, [workspaceRoot, scope, apply])

  // 切换工作区时先丢弃上一份快照，避免短暂串用另一个应用的未提交数；
  // 同一工作区内 refreshKey 变化（构建推进、文件落盘）时重读。
  useEffect(() => {
    let active = true
    setSnapshot((current) => (current?.workspaceRoot === workspaceRoot ? current : undefined))
    /** 内容变化发生在读取期间时，收口后只为最新一次变化补读，避免遗漏构建结果。 */
    const refreshLatest = async (): Promise<void> => {
      const pending = inFlight.current.get(workspaceRoot)
      if (pending) await pending
      if (active) await refresh()
    }
    void refreshLatest()
    return () => {
      active = false
    }
  }, [workspaceRoot, refresh, refreshKey])

  // 不按 focus 读取：Windows 后台命令窗口关闭会让 Electron 重新聚焦，可能自激循环。

  return {
    snapshot,
    // 计数只算业务代码：`.devagentstudio` 下的规划产物与状态快照会随每个设计步骤变化，
    // 把它们算进来会让角标在用户一行业务代码都没写时就亮起、并一直涨。
    count: snapshot?.codePaths.length ?? 0,
    refresh,
    apply
  }
}
