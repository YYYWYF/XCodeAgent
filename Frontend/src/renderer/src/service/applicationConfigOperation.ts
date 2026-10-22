import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

export type ConfigChanges = Record<string, string | boolean | string[]>

export type ConfigOperation = {
  operationId?: string
  status?: 'APPLYING' | 'APPLIED' | 'FAILED'
  configRevision?: number
  expectedConfigRevision?: number
  changedKeys?: string[]
  changedFiles?: string[]
  installedCapabilities?: string[]
  toInstall?: string[]
  generated?: boolean
  requiresConfirmation?: boolean
  failureStage?: string
  errorCode?: string
  failureSummary?: string
}

type ConfigPayload = {
  schemaVersion: 1
  status: 'completed' | 'failed'
  operation?: ConfigOperation
  error?: { type?: string; message?: string }
}

/** 定位独立配置 AG-UI 服务。 */
function configUrl(): string {
  const base = window.devAgentStudio?.agentBaseUrl
  return base ? `${base.replace(/\/$/, '')}/application-config/run` : '/api/agent/application-config/run'
}

/** 只读取符合动作信封的结构化结果。 */
function payloadOf(value: unknown): ConfigPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const candidate = value as Partial<ConfigPayload>
  return candidate.schemaVersion === 1 && (candidate.status === 'completed' || candidate.status === 'failed')
    ? candidate as ConfigPayload : undefined
}

/** 经 @ag-ui/client 执行配置动作，敏感字段只放请求正文，不放消息。 */
export async function runConfigOperation(
  workspaceRoot: string,
  action: 'preview' | 'apply' | 'get',
  options: { expectedConfigRevision?: number; changes?: ConfigChanges; operationId?: string; requestId?: string }
): Promise<ConfigOperation> {
  const agent = createAgUiHttpAgent({ url: configUrl(), threadId: `application-config:${workspaceRoot}` })
  agent.addMessage({ id: randomUUID(), role: 'user', content: '应用配置操作' })
  let response: ConfigPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'application-config') response = payloadOf(event.value) ?? response
    },
    onStateSnapshotEvent: ({ event }) => {
      const state = event.snapshot as { applicationConfigOperation?: unknown }
      response = payloadOf(state?.applicationConfigOperation) ?? response
    }
  }
  const result = await agent.runAgent({
    forwardedProps: { applicationConfig: { action, workspaceRoot, ...options } }
  }, subscriber)
  const finalState = (result.result as { applicationConfigOperation?: unknown } | undefined)?.applicationConfigOperation
  response = payloadOf(finalState) ?? response
  if (!response) throw new Error('配置服务未返回有效的 AG-UI 结果。')
  if (response.status === 'failed') throw new Error(response.error?.message || '配置操作失败。')
  if (!response.operation) throw new Error('配置服务未返回操作状态。')
  return response.operation
}

/** 使用同一提案做只读预检和确定性提交。 */
export async function applyConfigChanges(
  workspaceRoot: string,
  expectedConfigRevision: number,
  changes: ConfigChanges,
  operationId?: string,
  requestId?: string
): Promise<ConfigOperation> {
  const storageKey = `application-config-operation:${workspaceRoot}:${expectedConfigRevision}:${requestId || 'settings'}`
  const pendingId = operationId || window.localStorage.getItem(storageKey)
  if (pendingId) {
    try {
      const previous = await runConfigOperation(workspaceRoot, 'get', { operationId: pendingId })
      if (previous.status === 'APPLIED') {
        window.localStorage.removeItem(storageKey)
        return previous
      }
      const retried = await runConfigOperation(workspaceRoot, 'apply', {
        expectedConfigRevision, changes, operationId: pendingId, requestId
      })
      if (retried.status === 'APPLIED') window.localStorage.removeItem(storageKey)
      return retried
    } catch (error) {
      if (!(error instanceof Error) || !error.message.includes('CONFIG_OPERATION_MISSING')) throw error
    }
  }
  await runConfigOperation(workspaceRoot, 'preview', { expectedConfigRevision, changes })
  const nextId = pendingId || randomUUID()
  window.localStorage.setItem(storageKey, nextId)
  const applied = await runConfigOperation(workspaceRoot, 'apply', {
    expectedConfigRevision, changes, operationId: nextId, requestId
  })
  if (applied.status === 'APPLIED') window.localStorage.removeItem(storageKey)
  return applied
}
