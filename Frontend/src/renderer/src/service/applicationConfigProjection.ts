import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

export type ApplicationConfigProjectionResult = {
  status: 'CHANGED' | 'NO_CHANGE' | 'SKIPPED_NO_TEMPLATE'
  configRevision: number
  contentSha256?: string | null
}

type ApplicationConfigProjectionPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  projection?: ApplicationConfigProjectionResult
  code?: string
  error?: { message?: string }
}

/** 返回应用公开配置投影的独立 AG-UI 地址。 */
function getApplicationConfigProjectionUrl(): string {
  const agentBaseUrl = window.devAgentStudio?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/application-config/run`
    : '/api/agent/application-config/run'
}

/** 校验投影动作的统一 AG-UI 响应信封。 */
function readProjectionPayload(value: unknown): ApplicationConfigProjectionPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<ApplicationConfigProjectionPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as ApplicationConfigProjectionPayload
}

/** 从 AG-UI StateSnapshot 中读取应用公开配置投影结果。 */
function readProjectionState(value: unknown): ApplicationConfigProjectionPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readProjectionPayload(
    (value as { applicationConfigProjection?: unknown }).applicationConfigProjection
  )
}

/** 以保存后的 configRevision 同步 canonical 配置到已有模板工程。 */
export async function projectApplicationConfig(
  workspaceRoot: string,
  expectedConfigRevision: number
): Promise<ApplicationConfigProjectionResult> {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: getApplicationConfigProjectionUrl(), threadId })
  agent.addMessage({ id: randomUUID(), role: 'user', content: '同步应用公开配置。' })
  let payload: ApplicationConfigProjectionPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'application-config-projection') return
      payload = readProjectionPayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readProjectionState(event.snapshot) ?? payload
    }
  }
  const result = await agent.runAgent(
    {
      forwardedProps: {
        applicationConfigProjection: {
          action: 'project',
          workspaceRoot,
          expectedConfigRevision
        }
      }
    },
    subscriber
  )
  payload = readProjectionState(result.result) ?? payload
  if (!payload) throw new Error('应用配置同步接口没有返回有效的 AG-UI 状态。')
  if (payload.status === 'failed') {
    throw new Error(payload.error?.message || payload.code || '应用配置同步失败。')
  }
  if (!payload.projection) throw new Error('应用配置同步接口没有返回投影结果。')
  return payload.projection
}
