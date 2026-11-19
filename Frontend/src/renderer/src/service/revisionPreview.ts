import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import { createAgUiHttpAgent } from './authentication'

/** 历史版本预览的业务结果。 */
export type RevisionPreviewResult = {
  action: 'get' | 'start' | 'stop'
  status: 'running' | 'idle' | 'stopped'
  revision: string
  previewUrl: string
  /** 是否复用了工作区已安装的依赖（决定启动是"几秒"还是"几分钟"）。 */
  reusedDependencies?: boolean
  /** 物化目录是否已存在。 */
  materialized?: boolean
}

type PreviewIdentity = { workspace: string; revision: string; previewId: string }
const pendingAdmissions = new Map<string, Promise<void>>()

type RevisionPreviewAgUiPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'get' | 'start' | 'stop'
  revision?: string
  previewId?: string
  /**
   * 预览服务的运行态。
   *
   * 刻意不叫 `status`：那是 AG-UI 信封自己的字段（completed / failed），动作数据里的
   * 同名键会把信封值顶掉，而下面的校验只认 completed / failed —— 一旦顶掉，整条结果
   * 就会被判成无效（"没有返回有效的 AG-UI 状态"）。
   */
  previewStatus?: 'running' | 'idle' | 'stopped'
  preview_url?: string
  reused_dependencies?: boolean
  materialized?: boolean
  error?: { type?: string; message?: string }
}

/** 返回独立历史版本预览动作地址。 */
function getRevisionPreviewUrl(): string {
  const agentBaseUrl = window.devAgentStudio?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/revision-preview/run`
    : '/api/agent/revision-preview/run'
}

/** 校验 AG-UI 自定义事件或状态快照中的历史版本预览载荷。 */
function readRevisionPreviewPayload(value: unknown): RevisionPreviewAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<RevisionPreviewAgUiPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as RevisionPreviewAgUiPayload
}

function readRevisionPreviewFromState(value: unknown): RevisionPreviewAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readRevisionPreviewPayload((value as { revisionPreview?: unknown }).revisionPreview)
}

function readRevisionPreviewFromResult(value: unknown): RevisionPreviewAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readRevisionPreviewPayload((value as { revisionPreview?: unknown }).revisionPreview)
}

/** 发送一次历史版本预览动作并统一收敛结果。 */
async function runRevisionPreview(
  input: PreviewIdentity & { action: 'start' | 'stop' },
  messageContent: string,
  onRegistered?: () => void
): Promise<RevisionPreviewAgUiPayload> {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: getRevisionPreviewUrl(), threadId })
  const message: Message = { id: randomUUID(), role: 'user', content: messageContent }
  agent.addMessage(message)

  let revisionPreview: RevisionPreviewAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'revision-preview') {
        const progress = event.value as
          | { previewId?: string; progress?: { stage?: string } }
          | undefined
        if (progress?.previewId === input.previewId && progress.progress?.stage === 'registered') {
          onRegistered?.()
        }
        revisionPreview = readRevisionPreviewPayload(event.value) ?? revisionPreview
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      revisionPreview = readRevisionPreviewFromState(event.snapshot) ?? revisionPreview
    }
  }
  const result = await agent.runAgent({ forwardedProps: { revisionPreview: input } }, subscriber)
  revisionPreview = readRevisionPreviewFromResult(result.result) ?? revisionPreview
  if (!revisionPreview) throw new Error('历史版本预览接口没有返回有效的 AG-UI 状态。')
  if (revisionPreview.status === 'failed') {
    throw new Error(revisionPreview.error?.message || '历史版本预览失败。')
  }
  return revisionPreview
}

/**
 * 物化该版本并启动它的前端预览。
 *
 * 首次可能要安装依赖（分钟级），调用方应展示加载态。
 */
export async function startRevisionPreview(
  input: PreviewIdentity,
  options: { isCurrent: () => boolean; onRegistered: () => void }
): Promise<RevisionPreviewResult | undefined> {
  const previous = pendingAdmissions.get(input.workspace)
  let release!: () => void
  const admission = new Promise<void>((resolve) => {
    release = resolve
  })
  pendingAdmissions.set(input.workspace, admission)
  let registered = false

  /** 收到登记帧即允许后续版本发起启动，无需等待当前版本安装完成。 */
  const acknowledge = (): void => {
    if (registered) return
    registered = true
    release()
    options.onRegistered()
  }

  try {
    await previous
    if (!options.isCurrent()) return undefined
    const response = await runRevisionPreview(
      { action: 'start', ...input },
      `启动版本 ${input.revision} 的预览。`,
      acknowledge
    )
    acknowledge()
    if (response.previewId !== input.previewId || !response.preview_url) {
      throw new Error('历史预览没有返回本轮可用的地址。')
    }
    return {
      action: 'start',
      status: 'running',
      revision: input.revision,
      previewUrl: response.preview_url,
      reusedDependencies: Boolean(response.reused_dependencies)
    }
  } finally {
    release()
    if (pendingAdmissions.get(input.workspace) === admission)
      pendingAdmissions.delete(input.workspace)
  }
}

/** 停止指定轮次的服务并摘除物化目录。 */
export async function stopRevisionPreview(input: PreviewIdentity): Promise<void> {
  await runRevisionPreview({ action: 'stop', ...input }, `停止版本 ${input.revision} 的预览。`)
}
