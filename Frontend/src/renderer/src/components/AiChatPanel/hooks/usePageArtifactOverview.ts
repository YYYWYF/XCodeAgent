import { useEffect, useState } from 'react'
import { readPageArtifactOverview, type PageArtifactOverview } from '../../../service/pageArtifacts'

/** 按页面身份读取概览，刷新时清除旧正文，卸载和工作区切换后拒绝迟到回执。 */
export function usePageArtifactOverview(
  workspaceRoot: string,
  pageId: string,
  refreshKey?: string,
  catalogLoading?: boolean
): {
  overview?: PageArtifactOverview
  error?: string
  loading: boolean
  reload: () => void
} {
  const [attempt, setAttempt] = useState(0)
  const identity = JSON.stringify([workspaceRoot, pageId, refreshKey, catalogLoading, attempt])
  const [state, setState] = useState<{
    identity: string
    overview?: PageArtifactOverview
    error?: string
  }>()
  useEffect(() => {
    if (catalogLoading) return
    let cancelled = false
    const controller = new AbortController()
    const timeout = window.setTimeout(() => {
      controller.abort()
      if (!cancelled) setState({ identity, error: '读取页面概览超时，请重新读取。' })
    }, 15000)
    void readPageArtifactOverview(workspaceRoot, pageId, controller.signal)
      .then(
        (overview) => {
          if (!cancelled && !controller.signal.aborted) setState({ identity, overview })
        },
        (error: unknown) => {
          if (!cancelled && !controller.signal.aborted)
            setState({
              identity,
              error: error instanceof Error ? error.message : '页面概览读取失败。'
            })
        }
      )
      .finally(() => window.clearTimeout(timeout))
    return () => {
      cancelled = true
      window.clearTimeout(timeout)
      controller.abort()
    }
  }, [workspaceRoot, pageId, identity, catalogLoading])
  const current = state?.identity === identity ? state : undefined
  return { ...current, loading: !current, reload: () => setAttempt((value) => value + 1) }
}
