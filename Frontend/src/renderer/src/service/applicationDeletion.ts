import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { ApplicationConfig } from '../typings'
import { createAgUiHttpAgent } from './authentication'

type ApplicationDeletionPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  applicationId?: string
  workspaceRoot?: string
  readyForTrash?: boolean
  error?: { message?: string }
}

/** 返回后端工作区级应用销毁准备 AG-UI 地址。 */
function getApplicationDeletionUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/application-deletion/run`
    : '/api/agent/application-deletion/run'
}

/** 校验应用销毁准备的 AG-UI 结果信封。 */
function readApplicationDeletionPayload(value: unknown): ApplicationDeletionPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<ApplicationDeletionPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as ApplicationDeletionPayload
}

/** 从 AG-UI StateSnapshot 中读取应用销毁准备结果。 */
function readApplicationDeletionState(value: unknown): ApplicationDeletionPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readApplicationDeletionPayload(
    (value as { applicationDeletion?: unknown }).applicationDeletion
  )
}

/** 停止并释放目标应用全部后端运行资源，成功后才允许移动项目目录。 */
export async function prepareApplicationDeletion(application: ApplicationConfig): Promise<void> {
  const workspaceRoot = application.workspaceRoot?.trim()
  if (!workspaceRoot) throw new Error('该项目没有可删除的本地目录')

  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: getApplicationDeletionUrl(), threadId })
  agent.addMessage({ id: randomUUID(), role: 'user', content: '停止并删除当前应用。' })
  let payload: ApplicationDeletionPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'application-deletion') return
      payload = readApplicationDeletionPayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readApplicationDeletionState(event.snapshot) ?? payload
    }
  }
  const result = await agent.runAgent(
    {
      forwardedProps: {
        applicationDeletion: {
          action: 'prepare',
          applicationId: application.id,
          workspaceRoot
        }
      }
    },
    subscriber
  )
  payload = readApplicationDeletionState(result.result) ?? payload
  if (!payload) throw new Error('应用删除接口没有返回有效的 AG-UI 状态。')
  if (payload.status === 'failed') {
    throw new Error(payload.error?.message || '应用运行资源停止失败。')
  }
  if (
    payload.readyForTrash !== true ||
    payload.applicationId !== application.id ||
    payload.workspaceRoot !== workspaceRoot
  ) {
    throw new Error('应用删除接口没有确认目标工作区已经停止。')
  }
}
