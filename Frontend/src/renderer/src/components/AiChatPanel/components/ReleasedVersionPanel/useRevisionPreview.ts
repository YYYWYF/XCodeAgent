import { useEffect, useRef, useState } from 'react'
import { startRevisionPreview, stopRevisionPreview } from '../../../../service/revisionPreview'

type Params = {
  workspaceRoot: string
  /** 该版本的 Git tag；为空（旧记录没有 tag）时不启动历史预览。 */
  revision?: string
  /**
   * 是否要发起启动。
   *
   * 只在「应用预览」tab 激活时为 true —— 这是懒启动的落点：在「应用文件」上翻文件
   * 不该白白拉起一个 dev server。
   *
   * 它只决定"要不要**开始**"，不决定"要不要停"：在应用文件与预览之间来回切不该反复
   * 重建 dev server（一次物化 + 起服务是几十秒级的）。
   */
  enabled: boolean
}

type PreviewTarget = {
  workspace: string
  revision: string
  /** 工作区 + 版本；用来判断"还是同一个目标吗"。 */
  key: string
}

export type RevisionPreviewState = {
  /** 该版本预览服务的实际地址；未就绪时为空串。 */
  url: string
  /** 是否复用了工作区已安装的依赖（决定这次启动是几秒还是几分钟）。 */
  reusedDependencies: boolean
  error: string
  retry: () => void
}

/** 预览 tab 该渲染什么：工作区预览、历史版本加载态、历史版本错误态，或历史版本预览。 */
export type RevisionPreviewPresentation =
  | { kind: 'workspace' }
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'revision'; url: string }

/**
 * 收敛「历史版本预览」的渲染分支。
 *
 * 抽成纯函数是为了可测：面板本身要跑 effect 才能拿到地址，静态渲染测不到那条路径。
 */
export function revisionPreviewPresentation(input: {
  revision?: string
  url: string
  error: string
}): RevisionPreviewPresentation {
  // 没有 tag 的版本退回工作区预览，行为与引入历史预览之前一致。
  if (!input.revision) return { kind: 'workspace' }
  if (input.error) return { kind: 'error', message: input.error }
  // 地址为空即"未就绪"：既包括还没开始启动，也包括正在物化/安装/启动。
  // 两者都只该看到加载态 —— 提前渲染 about:blank 的 iframe 会让人以为预览坏了。
  if (!input.url) return { kind: 'loading' }
  return { kind: 'revision', url: input.url }
}

/**
 * 是否该为这个版本拉起预览服务。
 *
 * 抽出来是为了让"懒启动"这条边界可测：三个条件缺一不可 —— 预览 tab 激活
 * （在应用文件上翻文件不该拉起 dev server）、版本有 tag、工作区路径已知。
 */
export function shouldRunRevisionPreview(input: {
  enabled: boolean
  revision?: string
  workspaceRoot: string
}): boolean {
  return input.enabled && Boolean(input.revision) && Boolean(input.workspaceRoot)
}

/**
 * 按需保证某个历史版本的预览服务处于运行态，返回它的地址。
 *
 * 启停都在后端（物化该版本的树 + 起一个独立 dev server），这里只负责在正确的时机发起
 * 请求：进入预览 tab 时启动，切换版本、切换工作区或面板卸载时停止。
 *
 * 复杂度几乎都来自 React 18 的 StrictMode：开发模式下挂载时的 effect 会跑两遍
 * （effect → cleanup → effect），于是第一次启动必然"作废"，而它拉起的正是第二次想要的
 * 那个服务。因此这里所有收尾判断都不看"某次调用是否作废"，只看**目标是否还是当前目标**
 * （`runningRef`），并在停止上做一拍延迟，让同目标的重新启动有机会把它撤销掉。
 */
export function useRevisionPreview({
  workspaceRoot,
  revision,
  enabled
}: Params): RevisionPreviewState {
  const [url, setUrl] = useState('')
  const [reusedDependencies, setReusedDependencies] = useState(false)
  const [error, setError] = useState('')
  const [retryNonce, setRetryNonce] = useState(0)

  // 已经（或正在）为其拉起服务的目标。空表示当前没有该由本 hook 负责的服务。
  const runningRef = useRef<PreviewTarget | undefined>(undefined)
  // 请求代号：只有最后一次发起的启动能写状态，过期结果一律丢弃。
  const sequenceRef = useRef(0)
  // 卸载时排下的停止，见 unmount effect 里的说明。
  const pendingStopRef = useRef<{ key: string; timer: number } | undefined>(undefined)

  const active = shouldRunRevisionPreview({ enabled, revision, workspaceRoot })
  const targetKey = `${workspaceRoot}\n${String(revision || '')}`

  useEffect(() => {
    // 撤销"同一个目标"被排下的停止。挂载时的清理（StrictMode 会跑一遍）也会排下停止，
    // 若不撤销，它就会把这里刚拉起的服务杀掉。
    if (pendingStopRef.current?.key === targetKey) {
      window.clearTimeout(pendingStopRef.current.timer)
      pendingStopRef.current = undefined
    }

    // 目标换了（版本或工作区）：上一个目标的服务已无归属，收掉，并清掉它的地址与错误 ——
    // 留着会先渲染出上一个版本的页面或错误，再被下面的启动覆盖。
    const previous = runningRef.current
    if (previous && previous.key !== targetKey) {
      runningRef.current = undefined
      setUrl('')
      setError('')
      setReusedDependencies(false)
      void stopRevisionPreview(previous).catch(() => undefined)
    }

    // 懒启动：没进过预览 tab 就不开始；已经为该目标启动过也不重复启动。
    if (!active || runningRef.current?.key === targetKey) return

    const target: PreviewTarget = {
      workspace: workspaceRoot,
      revision: String(revision || ''),
      key: targetKey
    }
    runningRef.current = target
    const sequence = ++sequenceRef.current
    let disposed = false
    setError('')
    setUrl('')
    setReusedDependencies(false)

    void startRevisionPreview(target)
      .then((result) => {
        if (disposed || sequenceRef.current !== sequence) {
          // 这次启动已被作废。只有目标确实换了才收掉它拉起的服务：StrictMode 的双挂载
          // 也会让第一次启动"作废"，但那个目标正被重新要回来，收掉就是自毁。
          if (runningRef.current?.key !== targetKey) {
            void stopRevisionPreview(target).catch(() => undefined)
          }
          return
        }
        setUrl(result.previewUrl)
        setReusedDependencies(Boolean(result.reusedDependencies))
      })
      .catch((cause) => {
        if (disposed || sequenceRef.current !== sequence) return
        // 启动失败就不再算"在跑"，否则点重试会被"已在跑"的守卫挡掉。
        if (runningRef.current?.key === targetKey) runningRef.current = undefined
        setError(cause instanceof Error ? cause.message : '历史版本预览启动失败')
      })

    return () => {
      disposed = true
    }
  }, [active, targetKey, workspaceRoot, revision, retryNonce])

  // 卸载时收掉正在跑的服务。单独一个 effect 是因为上面那个的 cleanup 每次依赖变化都会
  // 跑，而只有"面板真的没了"才该停 —— 切 tab、点重试都还留着这个面板。
  useEffect(() => {
    return () => {
      const target = runningRef.current
      runningRef.current = undefined
      if (!target) return
      // 延后一拍再停：StrictMode 的双挂载会紧接着重跑上面那个 effect，把同一目标要回来，
      // 那时它会撤销这次停止。真正卸载（以及启动还在路上、停止排得太早）时，上面那段
      // "作废后按目标收尾"的逻辑还会再收一次，停止是幂等的。
      const entry = { key: target.key, timer: 0 }
      entry.timer = window.setTimeout(() => {
        if (pendingStopRef.current === entry) pendingStopRef.current = undefined
        void stopRevisionPreview(target).catch(() => undefined)
      }, 0)
      pendingStopRef.current = entry
    }
  }, [])

  return {
    url,
    reusedDependencies,
    error,
    // 先摘掉"在跑"的标记再触发重跑：否则上面那条"已为该目标启动过"的守卫会直接跳过。
    retry: () => {
      runningRef.current = undefined
      setRetryNonce((nonce) => nonce + 1)
    }
  }
}
