import { useContext, useEffect, useRef, useState } from 'react'
import { UiReadVisibility } from '../../../../hooks/uiReadVisibility'
import { waitForPreviewRuntimeLeave } from '../../../../service/previewRuntime'
import { startRevisionPreview, stopRevisionPreview } from '../../../../service/revisionPreview'

type Params = {
  workspaceRoot: string
  /** 要预览的 Git 分支名，未指定时使用工作区预览。 */
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
  // 未指定历史分支时继续使用工作区预览。
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
 * （在应用文件上翻文件不该拉起服务）、分支名和工作区路径已知。
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
 * 预览后端负责检出代码和启停该分支的服务，这里只负责在正确的时机发起
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
  const visible = useContext(UiReadVisibility)

  // 已经（或正在）为其拉起服务的目标。空表示当前没有该由本 hook 负责的服务。
  const runningRef = useRef<PreviewTarget | undefined>(undefined)
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

    // 欢迎页隐藏工作台但保留组件。退出动作负责停机，这里立即丢弃旧地址和迟到回执。
    if (!visible) {
      runningRef.current = undefined
      setUrl('')
      setError('')
      setReusedDependencies(false)
      return
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
    setError('')
    setUrl('')
    setReusedDependencies(false)

    void waitForPreviewRuntimeLeave(target.workspace)
      .then(() => (runningRef.current === target ? startRevisionPreview(target) : undefined))
      .then((result) => {
        // 切到代码标签页不释放该目标，Java 构建期间切回预览仍可收到这一轮地址。
        if (!result || runningRef.current !== target) return
        setUrl(result.previewUrl)
        setReusedDependencies(Boolean(result.reusedDependencies))
      })
      .catch((cause) => {
        if (runningRef.current !== target) return
        // 启动失败就不再算"在跑"，否则点重试会被"已在跑"的守卫挡掉。
        runningRef.current = undefined
        setError(cause instanceof Error ? cause.message : '历史版本预览启动失败')
      })
  }, [active, targetKey, workspaceRoot, revision, retryNonce, visible])

  // 只有面板卸载时才收掉当前目标。切标签页仍保留同一次服务启动。
  useEffect(() => {
    return () => {
      const target = runningRef.current
      runningRef.current = undefined
      if (!target) return
      // 延后一拍再停：StrictMode 的双挂载会紧接着重跑上面那个 effect，把同一目标要回来，
      // 那时它会撤销这次停止。真正卸载时，后端会把停止和同一分支的启动串行执行。
      const entry = { key: target.key, timer: 0 }
      entry.timer = window.setTimeout(() => {
        if (pendingStopRef.current === entry) pendingStopRef.current = undefined
        void stopRevisionPreview(target).catch(() => undefined)
      }, 0)
      pendingStopRef.current = entry
    }
  }, [])

  return {
    url: visible ? url : '',
    reusedDependencies: visible && reusedDependencies,
    error: visible ? error : '',
    // 先摘掉"在跑"的标记再触发重跑：否则上面那条"已为该目标启动过"的守卫会直接跳过。
    retry: () => {
      runningRef.current = undefined
      setRetryNonce((nonce) => nonce + 1)
    }
  }
}
