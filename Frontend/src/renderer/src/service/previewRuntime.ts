import { randomUUID } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'
import type { ProjectLaunchResult } from './projectLaunch'
import type { WorkspaceCodeChangeSet } from '../typings'

export type PreviewAction =
  | 'get'
  | 'watch'
  | 'start'
  | 'restart'
  | 'stop'
  | 'diagnose'
  | 'confirm'
  | 'revise'
  | 'cancel'
  | 'leave'
export type PreviewServiceState = {
  status: 'starting' | 'running' | 'failed' | 'stopped' | 'skipped'
  message?: string
  port?: number
  url?: string
}
export type PreviewLog = {
  name: string
  stage: string
  stream: string
  content: string
  truncated: boolean
}
export type PreviewRepair = {
  status?:
    | 'awaiting_confirmation'
    | 'running'
    | 'stopping'
    | 'completed'
    | 'failed'
    | 'stopped'
    | 'requires_revision'
  message?: string
  markdown?: string
  planId?: string
  iteration?: number
  codeChangeSets?: WorkspaceCodeChangeSet[]
}
export type PreviewRuntimePayload = {
  status: 'in_progress' | 'completed' | 'failed'
  runtime?: {
    attemptId: string
    status: PreviewServiceState['status']
    frontend: PreviewServiceState
    backend: PreviewServiceState
    previewUrl?: string
    failedStage?: string
    repairAvailable?: boolean
    logs?: Record<'frontend' | 'backend', PreviewLog[]>
    maintenance?: { threadId: string; action: string }
  }
  blockedBy?: { threadId?: string; runId?: string; message: string }
  repair?: PreviewRepair
  launchResult?: ProjectLaunchResult
  error?: { message?: string }
  progress?: { stage: string; message: string }
}

/** 通过标准 AG-UI 客户端执行预览动作，并持续发布业务状态。 */
export async function runPreviewRuntime(
  input: {
    workspace: string
    action: PreviewAction
    attemptId?: string
    planId?: string
    feedback?: string
    includeLogs?: boolean
  },
  options: {
    threadId?: string
    signal?: AbortSignal
    onUpdate?: (value: PreviewRuntimePayload) => void
  } = {}
): Promise<PreviewRuntimePayload> {
  const origin = window.xcodeAgent?.agentBaseUrl?.replace(/\/$/, '')
  const agent = createAgUiHttpAgent({
    url: origin ? `${origin}/preview-runtime/run` : '/api/agent/preview-runtime/run',
    threadId: options.threadId || randomUUID()
  })
  let latest: PreviewRuntimePayload | undefined
  /** 合并进度与终态，避免进度事件清空已有运行事实。 */
  const accept = (value: unknown): void => {
    if (!value || typeof value !== 'object') return
    latest = { ...latest, ...(value as PreviewRuntimePayload) }
    options.onUpdate?.(latest)
  }
  /** 关闭日志订阅时中止当前读取，不影响独立服务端维护任务。 */
  const abort = (): void => agent.abortRun()
  options.signal?.addEventListener('abort', abort, { once: true })
  try {
    if (options.signal?.aborted) throw new Error('订阅已关闭')
    const result = await agent.runAgent(
      { forwardedProps: { previewRuntime: input } },
      {
        onCustomEvent: ({ event }) => {
          if (event.name === 'preview-runtime') accept(event.value)
        },
        onStateSnapshotEvent: ({ event }) =>
          accept((event.snapshot as { previewRuntime?: unknown }).previewRuntime)
      }
    )
    accept((result.result as { previewRuntime?: unknown } | undefined)?.previewRuntime)
    if (!latest) throw new Error('预览服务没有返回有效状态。')
    if (latest.status === 'failed') throw new Error(latest.error?.message || '预览服务操作失败。')
    return latest
  } finally {
    options.signal?.removeEventListener('abort', abort)
  }
}

/** 离开工作台时停止当前应用的预览维护并释放应用级占用。 */
export async function leavePreviewRuntime(workspace: string): Promise<void> {
  if (!workspace) return
  await runPreviewRuntime({ workspace, action: 'leave', includeLogs: false })
}
