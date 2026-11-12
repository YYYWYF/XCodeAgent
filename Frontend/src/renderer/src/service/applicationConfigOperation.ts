import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import { loadWorkspaceApplicationConfig } from './applicationStorage'
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
  progressStage?: 'DESIRED_SAVED' | 'CAPABILITY_APPLIED' | 'PARAMETERS_APPLIED'
  failureCode?: string
  failureTarget?: string
  changesDigest?: string
  failureStage?: string
  errorCode?: string
  failureSummary?: string
}

type ConfigPayload = {
  schemaVersion: 1
  status: 'completed' | 'failed'
  operation?: ConfigOperation
  error?: { type?: string; message?: string }
  failureStage?: string
  failureCode?: string
  failureTarget?: string
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
  action: 'preview' | 'apply' | 'get' | 'resume',
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
  if (response.status === 'failed') throw new ConfigOperationError(response.error?.message || '配置操作失败。', response)
  if (!response.operation) throw new Error('配置服务未返回操作状态。')
  return response.operation
}

export const APPLICATION_CONFIG_REFRESHED_EVENT = 'application-config-refreshed'

/** 透传后端脱敏阶段诊断，界面不能从提案字段推测失败环节。 */
export class ConfigOperationError extends Error {
  failureStage?: string
  failureCode?: string
  failureTarget?: string

  /** 保存结构化字段，保持原业务错误可诊断。 */
  constructor(message: string, failure: { failureStage?: string; failureCode?: string; failureTarget?: string } = {}) {
    super(message)
    Object.assign(this, failure)
  }
}

/** 将排序 JSON 编码为与 Python changesDigest 一致的 SHA-256。 */
export async function configChangesDigest(changes: ConfigChanges): Promise<string> {
  const sorted = Object.fromEntries(Object.keys(changes).sort().map((key) => [key, changes[key]]))
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(sorted)))
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join('')
}

type PendingOperation = {
  version: 1
  operationId: string
  expectedConfigRevision: number
  requestId?: string
  digest: string
}

/** 读取成功之后才允许丢弃恢复身份，并通知工作台刷新磁盘事实。 */
async function finishOperation(workspaceRoot: string, storageKey: string, operation: ConfigOperation): Promise<ConfigOperation> {
  if (operation.status !== 'APPLIED') return operation
  try {
    await loadWorkspaceApplicationConfig(workspaceRoot)
  } catch {
    throw new ConfigOperationError('配置操作已完成，但 application.json 读取失败；请使用原操作重试读取。', {
      failureStage: 'config_snapshot_read', failureCode: 'CONFIG_SNAPSHOT_INVALID', failureTarget: '.devagentstudio/application.json'
    })
  }
  window.dispatchEvent(new CustomEvent(APPLICATION_CONFIG_REFRESHED_EVENT, { detail: { workspaceRoot } }))
  window.localStorage.removeItem(storageKey)
  return operation
}

/** 使用提案指纹绑定原操作；变更提案必须先恢复旧操作并刷新修订号。 */
async function applyConfigChangesOnce(
  workspaceRoot: string,
  expectedConfigRevision: number,
  changes: ConfigChanges,
  operationId?: string,
  requestId?: string
): Promise<ConfigOperation> {
  // 不把 revision 放入键：Desired 已保存后的刷新仍须找回未完成操作。
  const storageKey = `application-config-operation:${workspaceRoot}`
  const digest = await configChangesDigest(changes)
  const raw = window.localStorage.getItem(storageKey)
  let pending: PendingOperation | undefined
  if (raw) {
    try {
      pending = JSON.parse(raw) as PendingOperation
      if (pending?.version !== 1 || !pending.operationId || !pending.digest || !Number.isInteger(pending.expectedConfigRevision)) throw new Error()
    } catch {
      throw new Error('CONFIG_OPERATION_CONFLICT：本地操作身份无效，已保留记录，请检查原操作。')
    }
  }
  if (operationId && pending && operationId !== pending.operationId) throw new Error('CONFIG_OPERATION_CONFLICT：已有未完成操作，请先恢复。')
  const pendingId = pending?.operationId || operationId
  if (pendingId) {
    let previous: ConfigOperation | undefined
    try {
      previous = await runConfigOperation(workspaceRoot, 'get', { operationId: pendingId })
    } catch (error) {
      if (!(error instanceof Error) || !error.message.includes('CONFIG_OPERATION_MISSING')) throw error
    }
    if ((pending && (pending.digest !== digest || pending.requestId !== requestId)) || (previous?.changesDigest && previous.changesDigest !== digest)) {
      if (previous?.status === 'APPLIED') {
        await finishOperation(workspaceRoot, storageKey, previous)
        throw new Error('CONFIG_REVISION_CONFLICT：原操作已完成，请刷新配置后重新提交新提案。')
      }
      throw new Error(`CONFIG_OPERATION_CONFLICT：提案已变更，原操作 ${pendingId} 尚未完成；请恢复原提案并重试。原操作 ID 已保留。`)
    }
    if (previous?.status === 'APPLIED') return finishOperation(workspaceRoot, storageKey, previous)
    const retried = await runConfigOperation(workspaceRoot, 'apply', {
      expectedConfigRevision: pending?.expectedConfigRevision ?? expectedConfigRevision,
      changes, operationId: pendingId, requestId: pending?.requestId ?? requestId
    })
    return finishOperation(workspaceRoot, storageKey, retried)
  }
  await runConfigOperation(workspaceRoot, 'preview', { expectedConfigRevision, changes })
  const nextId = randomUUID()
  const next: PendingOperation = { version: 1, operationId: nextId, expectedConfigRevision, requestId, digest }
  window.localStorage.setItem(storageKey, JSON.stringify(next))
  const applied = await runConfigOperation(workspaceRoot, 'apply', {
    expectedConfigRevision, changes, operationId: nextId, requestId
  })
  return finishOperation(workspaceRoot, storageKey, applied)
}


const inFlightOperations = new Map<string, { digest: string; operationId?: string; promise: Promise<ConfigOperation> }>()

/** 串行化同一表单提案，快速重复点击不会覆盖 pending 身份或生成第二个操作。 */
export async function applyConfigChanges(
  workspaceRoot: string,
  expectedConfigRevision: number,
  changes: ConfigChanges,
  operationId?: string,
  requestId?: string
): Promise<ConfigOperation> {
  const key = workspaceRoot
  const digest = await configChangesDigest(changes)
  const current = inFlightOperations.get(key)
  if (current) {
    if (current.digest !== digest || current.operationId !== operationId) throw new Error('CONFIG_OPERATION_CONFLICT：原提案正在提交，请等待完成。')
    return current.promise
  }
  const promise = applyConfigChangesOnce(workspaceRoot, expectedConfigRevision, changes, operationId, requestId)
  inFlightOperations.set(key, { digest, operationId, promise })
  try {
    return await promise
  } finally {
    inFlightOperations.delete(key)
  }
}


/** 判断当前工作区是否仍持有需要显式恢复的操作身份。 */
export function hasPendingConfigOperation(workspaceRoot: string): boolean {
  return Boolean(window.localStorage.getItem(`application-config-operation:${workspaceRoot}`))
}

/** 仅发送原操作身份恢复磁盘 Desired，成功读盘后再释放 pending。 */
export async function resumePendingConfigOperation(workspaceRoot: string, requestId?: string): Promise<ConfigOperation> {
  const key = `application-config-operation:${workspaceRoot}`
  const raw = window.localStorage.getItem(key)
  if (!raw) throw new Error('CONFIG_OPERATION_MISSING：没有待恢复配置操作。')
  const pending = JSON.parse(raw) as PendingOperation
  if (pending.version !== 1 || !pending.operationId || pending.requestId !== requestId) throw new Error('CONFIG_OPERATION_CONFLICT：请回到原配置请求恢复操作。')
  const operation = await runConfigOperation(workspaceRoot, 'resume', { operationId: pending.operationId, requestId })
  return finishOperation(workspaceRoot, key, operation)
}
