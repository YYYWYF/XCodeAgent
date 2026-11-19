import { randomUUID } from '@ag-ui/client'
import { message } from 'antd'
import { useContext, useEffect, useState } from 'react'
import { UiReadVisibility } from '../../../../hooks/uiReadVisibility'
import { waitForPreviewRuntimeLeave } from '../../../../service/previewRuntime'
import { startRevisionPreview, stopRevisionPreview } from '../../../../service/revisionPreview'

type Params = {
  workspaceRoot: string
  /** 要预览的 Git 分支名，未指定时使用工作区预览。 */
  revision?: string
  /** 只有「应用预览」标签页处于激活状态时才保有历史预览。 */
  enabled: boolean
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
 * 每次进入历史预览申请新轮次，退出时只停止该轮次，并丢弃迟到的结果。
 */
export function useRevisionPreview({
  workspaceRoot,
  revision,
  enabled
}: Params): RevisionPreviewState {
  const [view, setView] = useState<Omit<RevisionPreviewState, 'retry'> & { key: string }>()
  const [retryNonce, setRetryNonce] = useState(0)
  const visible = useContext(UiReadVisibility)
  const active = visible && shouldRunRevisionPreview({ enabled, revision, workspaceRoot })
  const targetKey = JSON.stringify([workspaceRoot, revision, retryNonce])

  useEffect(() => {
    setView(undefined)
    if (!active || !revision) return

    const target = { workspace: workspaceRoot, revision, previewId: randomUUID() }
    let current = true
    let registered = false

    /** 停止已登记的轮次，向用户报告实际的清理错误。 */
    const stop = (): void => {
      void stopRevisionPreview(target).catch((cause) => {
        void message.error(cause instanceof Error ? cause.message : '历史预览清理失败。')
      })
    }

    /** 等后端登记后才允许停止，防止关闭动作先于启动请求到达。 */
    const onRegistered = (): void => {
      registered = true
      if (!current) stop()
    }

    void waitForPreviewRuntimeLeave(target.workspace)
      .then(() => startRevisionPreview(target, { isCurrent: () => current, onRegistered }))
      .then((result) => {
        if (result && current) {
          setView({
            key: targetKey,
            url: result.previewUrl,
            reusedDependencies: Boolean(result.reusedDependencies),
            error: ''
          })
        }
      })
      .catch((cause) => {
        if (current) {
          setView({
            key: targetKey,
            url: '',
            reusedDependencies: false,
            error: cause instanceof Error ? cause.message : '历史版本预览启动失败'
          })
        }
      })

    return () => {
      current = false
      if (registered) stop()
    }
  }, [active, targetKey, workspaceRoot, revision])

  const currentView = active && view?.key === targetKey ? view : undefined
  return {
    url: currentView?.url ?? '',
    reusedDependencies: currentView?.reusedDependencies ?? false,
    error: currentView?.error ?? '',
    retry: () => setRetryNonce((nonce) => nonce + 1)
  }
}
