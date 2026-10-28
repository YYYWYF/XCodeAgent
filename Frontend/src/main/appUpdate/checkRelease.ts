import { randomUUID } from 'node:crypto'
import { HttpAgent } from '@ag-ui/client'
import type { ReleaseCheck } from './coordinator'

/** 校验 AG-UI 返回的更新数据，不接受缺失字段或任意协议下载地址。 */
export function parseReleaseCheck(value: unknown): ReleaseCheck {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('版本检测响应格式错误')
  const record = value as Record<string, unknown>
  if (
    typeof record.updateAvailable !== 'boolean' ||
    typeof record.latestAppVersion !== 'string' ||
    !record.latestAppVersion
  )
    throw new Error('版本检测响应字段错误')
  if (record.updateAvailable) {
    if (typeof record.appDownloadUrl !== 'string') throw new Error('更新地址缺失')
    const url = new URL(record.appDownloadUrl)
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      !url.hostname ||
      url.username ||
      url.password
    )
      throw new Error('更新地址无效')
  } else if (record.appDownloadUrl !== null) throw new Error('无更新响应不应提供下载地址')
  return {
    updateAvailable: record.updateAvailable,
    latestAppVersion: record.latestAppVersion,
    appDownloadUrl: record.appDownloadUrl as string | null
  }
}

/** 通过隔离的 AG-UI Run 调用 Python，不读取 .env 或访问 Java 地址。 */
export async function checkAppRelease(
  backendBaseUrl: string,
  currentVersion: string,
  signal: AbortSignal
): Promise<ReleaseCheck> {
  if (signal.aborted) throw new Error('版本检测已取消')
  const agent = new HttpAgent({
    url: `${backendBaseUrl.replace(/\/$/, '')}/app-update/run`,
    threadId: `app-update-${randomUUID()}`
  })
  let release: ReleaseCheck | undefined
  let failure: Error | undefined
  let finished = false
  /** 将协调器超时或退出传递给 SDK，取消其 HTTP 流读取。 */
  const cancel = (): void => {
    agent.abortRun()
  }
  signal.addEventListener('abort', cancel, { once: true })
  try {
    await agent.runAgent(
      { runId: randomUUID(), forwardedProps: { appUpdate: { action: 'check', currentVersion } } },
      {
        onCustomEvent: ({ event }) => {
          if (event.name !== 'app-update') return
          const value: unknown = event.value
          if (!value || typeof value !== 'object' || Array.isArray(value)) {
            failure = new Error('版本检测结果无效')
            return
          }
          const payload = value as Record<string, unknown>
          if (payload.status === 'failed') {
            failure = new Error('Python 版本检测失败')
          } else if (payload.status === 'completed') {
            try {
              release = parseReleaseCheck(payload)
            } catch {
              failure = new Error('版本检测结果无效')
            }
          }
        },
        onRunFailed: () => {
          failure = new Error('版本检测 AG-UI 请求失败')
        },
        onRunFinishedEvent: () => {
          finished = true
        }
      }
    )
    if (signal.aborted) throw new Error('版本检测已取消')
    if (failure) throw failure
    if (!finished || !release) throw new Error('版本检测未返回完整结果')
    return release
  } finally {
    signal.removeEventListener('abort', cancel)
  }
}
