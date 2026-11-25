import type { PreviewRuntimePayload } from '../../service/previewRuntime'
import { composePreviewUrl, previewOrigin } from '../../utils/previewUrl'
import { externalBrowserPreviewUrl } from '../BrowserPreviewPanel/serviceStatusPolicy'
import type { WorkflowPreviewTarget } from './utils'

type AcceptancePreviewOwner = { workspace: string; sessionKey: string; runId: string }

export type AcceptancePreviewRequest = WorkflowPreviewTarget &
  AcceptancePreviewOwner & { attemptId: string }

type ReadyAcceptancePreview = WorkflowPreviewTarget & { reveal: 'service-ready' | 'iframe-load' }

/** 核对预览请求是否仍属于当前工作区、会话和运行。 */
export function acceptancePreviewForOwner(
  request: AcceptancePreviewRequest | undefined,
  owner: Partial<AcceptancePreviewOwner>
): AcceptancePreviewRequest | undefined {
  return request &&
    request.workspace === owner.workspace &&
    request.sessionKey === owner.sessionKey &&
    request.runId === owner.runId
    ? request
    : undefined
}

/** 本轮前端和所需后端就绪后，生成目标地址和对应模式的展示时机。 */
export function acceptancePreviewWhenReady(
  request: AcceptancePreviewRequest | undefined,
  runtime: PreviewRuntimePayload['runtime'],
  path: string,
  externalBrowser: boolean
): ReadyAcceptancePreview | undefined {
  if (
    !request ||
    request.attemptId !== runtime?.attemptId ||
    runtime.status !== 'running' ||
    runtime.frontend.status !== 'running' ||
    runtime.frontend.stoppedByUser ||
    !['running', 'skipped'].includes(runtime.backend.status)
  ) {
    return undefined
  }

  const runningUrl = runtime.previewUrl || runtime.frontend.url || ''
  if (previewOrigin(runningUrl) !== previewOrigin(request.url)) return undefined
  const url = externalBrowser
    ? externalBrowserPreviewUrl({ runtime, frontendChanging: false, pagePath: path })
    : composePreviewUrl(runningUrl, path)
  return url
    ? {
        key: JSON.stringify([request.key, request.attemptId, url]),
        url,
        reveal: externalBrowser ? 'service-ready' : 'iframe-load'
      }
    : undefined
}
