import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import { createAgUiHttpAgent } from './authentication'

export type VersionPublishPayload = {
  status: 'in_progress' | 'completed' | 'failed'
  progress?: { stage: string; message: string; detail?: string; percent: number }
  commitSha?: string
  branch?: string
  workspaceRoot?: string
  repositoryRoot?: string
  error?: { type?: string; message?: string }
}

export type VersionPublishInput = {
  workspaceRoot: string
  repoUrl: string
  /** 提交推送的目标分支：应用当前正在开发的那条。 */
  branchName: string
  description: string
}

type VersionPublishAgUiPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'in_progress' | 'completed' | 'failed'
  progress?: { stage: string; message: string; detail?: string; percent: number }
  action?: 'publish'
  workspaceRoot?: string
  repositoryRoot?: string
  branch?: string
  commitSha?: string
  error?: { type?: string; message?: string }
}

/** 返回提交推送 AG-UI 动作地址。 */
function getVersionPublishUrl(): string {
  const agentBaseUrl = window.devAgentStudio?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/version-publish/run`
    : '/api/agent/version-publish/run'
}

/** 合并进度与终态，避免进度事件清空已有运行事实。 */
function acceptPayload(
  latest: VersionPublishPayload | undefined,
  value: unknown
): VersionPublishPayload | undefined {
  if (!value || typeof value !== 'object') return latest
  const incoming = value as Partial<VersionPublishAgUiPayload>
  if (
    incoming.schemaVersion !== 1 ||
    typeof incoming.runId !== 'string' ||
    typeof incoming.threadId !== 'string' ||
    !['in_progress', 'completed', 'failed'].includes(String(incoming.status))
  ) {
    return latest
  }
  const merged: VersionPublishPayload = {
    ...(latest || {}),
    status: incoming.status as VersionPublishPayload['status'],
    progress: incoming.progress,
    commitSha: incoming.commitSha,
    branch: incoming.branch,
    workspaceRoot: incoming.workspaceRoot,
    repositoryRoot: incoming.repositoryRoot,
    error: incoming.error
  }
  return merged
}

/** 通过标准 AG-UI 客户端执行提交推送，并持续发布进度与终态。 */
export async function publishVersion(
  input: VersionPublishInput,
  options: {
    threadId?: string
    signal?: AbortSignal
    onUpdate?: (value: VersionPublishPayload) => void
  } = {}
): Promise<VersionPublishPayload> {
  const agent = createAgUiHttpAgent({
    url: getVersionPublishUrl(),
    threadId: options.threadId || randomUUID()
  })
  const message: Message = {
    id: randomUUID(),
    role: 'user',
    content: `提交并推送：把本次改动推送到分支 ${input.branchName}。`
  }
  agent.addMessage(message)

  let latest: VersionPublishPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'version-publish') {
        latest = acceptPayload(latest, event.value)
        if (latest) options.onUpdate?.(latest)
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      const snapshot = (event.snapshot as { versionPublish?: unknown }).versionPublish
      latest = acceptPayload(latest, snapshot)
      if (latest) options.onUpdate?.(latest)
    }
  }

  const abort = (): void => agent.abortRun()
  options.signal?.addEventListener('abort', abort, { once: true })
  try {
    if (options.signal?.aborted) throw new Error('提交已取消')
    const result = await agent.runAgent(
      { forwardedProps: { versionPublish: { action: 'publish', ...input } } },
      subscriber
    )
    latest = acceptPayload(
      latest,
      (result.result as { versionPublish?: unknown } | undefined)?.versionPublish
    )
    if (!latest) throw new Error('提交接口没有返回有效状态。')
    if (latest.status === 'failed') {
      throw new Error(latest.error?.message || '提交并推送失败。')
    }
    if (!latest.commitSha) {
      throw new Error('提交接口没有返回完整的提交结果。')
    }
    return latest
  } finally {
    options.signal?.removeEventListener('abort', abort)
  }
}
