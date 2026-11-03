import { randomUUID } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

export type BackendLogEntry = {
  id: string
  timestamp: string
  source: string
  level: string
  text: string
}

export type BackendLogsSnapshot = {
  entries: BackendLogEntry[]
  filePath: string
  updatedAt: string
  revision: number
  truncated: boolean
  storageError: string
}

type BackendLogsPayload = {
  status: 'in_progress' | 'completed' | 'failed'
  logs?: BackendLogsSnapshot
  error?: { message?: string }
}

/** 在 SDK 事件边界校验日志投影，传输与 SSE 解码完全由 AG-UI 负责。 */
function readPayload(value: unknown): BackendLogsPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const candidate = value as Partial<BackendLogsPayload>
  if (!['in_progress', 'completed', 'failed'].includes(candidate.status || '')) return undefined
  if (candidate.logs) {
    const logs = candidate.logs
    if (
      !Array.isArray(logs.entries) ||
      !logs.entries.every(
        (entry) =>
          entry &&
          ['id', 'timestamp', 'source', 'level', 'text'].every(
            (key) => typeof entry[key as keyof BackendLogEntry] === 'string'
          )
      ) ||
      typeof logs.filePath !== 'string' ||
      typeof logs.updatedAt !== 'string' ||
      typeof logs.revision !== 'number' ||
      typeof logs.truncated !== 'boolean' ||
      typeof logs.storageError !== 'string'
    )
      throw new Error('后端日志响应格式无效。')
  }
  return candidate as BackendLogsPayload
}

/** 通过独立 AG-UI 动作读取或订阅平台日志，不创建业务会话。 */
export async function runBackendLogs(
  action: 'get' | 'watch',
  options: {
    threadId?: string
    signal?: AbortSignal
    onUpdate?: (snapshot: BackendLogsSnapshot) => void
  } = {}
): Promise<BackendLogsSnapshot> {
  const origin = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '')
  const agent = createAgUiHttpAgent({
    url: origin ? `${origin}/backend-logs/run` : '/api/agent/backend-logs/run',
    threadId: options.threadId || randomUUID()
  })
  let latest: BackendLogsSnapshot | undefined
  let failure = ''
  /** 合并同一次流的事件与快照，只发布真正变化的日志窗口。 */
  const accept = (value: unknown): void => {
    const payload = readPayload(value)
    if (payload?.status === 'failed') failure = payload.error?.message || '读取后端日志失败。'
    const logs = payload?.logs
    if (logs && !options.signal?.aborted) {
      const changed =
        !latest ||
        logs.revision !== latest.revision ||
        logs.filePath !== latest.filePath ||
        logs.storageError !== latest.storageError
      latest = logs
      if (changed) options.onUpdate?.(logs)
    }
  }
  /** 只取消本次日志流，不影响后端采集或应用任务。 */
  const abort = (): void => agent.abortRun()
  options.signal?.addEventListener('abort', abort, { once: true })
  try {
    if (options.signal?.aborted) throw new Error('日志订阅已关闭。')
    const result = await agent.runAgent(
      { forwardedProps: { backendLogs: { action } } },
      {
        onCustomEvent: ({ event }) => {
          if (event.name === 'backend-logs') accept(event.value)
        },
        onStateSnapshotEvent: ({ event }) =>
          accept((event.snapshot as { backendLogs?: unknown }).backendLogs)
      }
    )
    accept((result.result as { backendLogs?: unknown } | undefined)?.backendLogs)
    if (failure) throw new Error(failure)
    if (!latest) throw new Error('后端没有返回日志快照。')
    return latest
  } finally {
    options.signal?.removeEventListener('abort', abort)
  }
}
