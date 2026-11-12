import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import { loadWorkspaceApplicationConfig } from './applicationStorage'
import { createAgUiHttpAgent } from './authentication'

export type ConfigChanges = Record<string, string | boolean | string[]>

export type ConfigOperation = {
  operationId?: string
  requestId?: string | null
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

type PendingConfigIdentity =
  | { kind: 'none' }
  | { kind: 'invalid' }
  | { kind: 'other'; requestId?: string }
  | { kind: 'mine'; operationId: string }

/** 使用现有配置操作键绑定工作区。 */
function pendingKey(workspaceRoot: string): string {
  return `application-config-operation:${workspaceRoot}`
}

/** 校验当前存储格式；损坏记录留在原处。 */
function readPendingOperation(workspaceRoot: string): PendingOperation | undefined {
  const raw = window.localStorage.getItem(pendingKey(workspaceRoot))
  if (raw === null) return undefined
  try {
    const pending = JSON.parse(raw) as PendingOperation
    if (pending?.version !== 1 || typeof pending.operationId !== 'string' ||
      !/^[A-Za-z0-9_-]{8,128}$/.test(pending.operationId) ||
      typeof pending.digest !== 'string' || !/^[a-f0-9]{64}$/.test(pending.digest) ||
      !Number.isInteger(pending.expectedConfigRevision) || pending.expectedConfigRevision < 1 ||
      (pending.requestId !== undefined && (typeof pending.requestId !== 'string' || !pending.requestId.trim()))) {
      throw new Error()
    }
    return pending
  } catch {
    throw new Error('CONFIG_OPERATION_CONFLICT：本地操作身份无效，已保留原记录。')
  }
}

/** 只读识别归属；无请求 ID 的设置页操作只归设置页。 */
export function inspectPendingConfigIdentity(workspaceRoot: string, requestId?: string): PendingConfigIdentity {
  try {
    const pending = readPendingOperation(workspaceRoot)
    if (!pending) return { kind: 'none' }
    return pending.requestId === requestId
      ? { kind: 'mine', operationId: pending.operationId }
      : { kind: 'other', requestId: pending.requestId }
  } catch {
    return { kind: 'invalid' }
  }
}

/** 按工作区与请求隔离续跑身份，配置提案和凭据不进入该记录。 */
function continuationKey(workspaceRoot: string, requestId: string): string {
  return `application-config-continuation:${JSON.stringify([workspaceRoot, requestId])}`
}

/** 读取已完成配置的续跑身份。 */
export function readConfigContinuation(workspaceRoot: string, requestId: string): string | undefined {
  const operationId = window.localStorage.getItem(continuationKey(workspaceRoot, requestId))
  if (operationId === null) return undefined
  if (!/^[A-Za-z0-9_-]{8,128}$/.test(operationId)) throw new Error('本地配置续跑身份无效，已保留原记录。')
  return operationId
}

/** 仅在同一请求的配置续跑已被接收后释放身份。 */
export function clearConfigContinuation(workspaceRoot: string, requestId: string, operationId: string): void {
  if (readConfigContinuation(workspaceRoot, requestId) === operationId) {
    window.localStorage.removeItem(continuationKey(workspaceRoot, requestId))
  }
}

/** 读取成功之后才允许丢弃恢复身份，并通知工作台刷新磁盘事实。 */
async function finishOperation(workspaceRoot: string, operation: ConfigOperation, requestId?: string): Promise<ConfigOperation> {
  if (operation.status !== 'APPLIED') return operation
  try {
    await loadWorkspaceApplicationConfig(workspaceRoot)
  } catch {
    throw new ConfigOperationError('配置操作已完成，但 application.json 读取失败；请使用原操作重试读取。', {
      failureStage: 'config_snapshot_read', failureCode: 'CONFIG_SNAPSHOT_INVALID', failureTarget: '.devagentstudio/application.json'
    })
  }
  const pending = readPendingOperation(workspaceRoot)
  if (pending && pending.operationId === operation.operationId && pending.requestId === requestId) {
    // 先保留续跑身份，刷新或退出时至少能找回一个原操作身份。
    if (requestId) window.localStorage.setItem(continuationKey(workspaceRoot, requestId), pending.operationId)
    window.localStorage.removeItem(pendingKey(workspaceRoot))
  }
  window.dispatchEvent(new CustomEvent(APPLICATION_CONFIG_REFRESHED_EVENT, { detail: { workspaceRoot } }))
  return operation
}

/** 向服务端核实原操作状态，已应用时读盘并转入会话续跑。 */
export async function checkPendingConfigOperation(workspaceRoot: string, requestId: string, operationId: string): Promise<ConfigOperation> {
  const operation = await runConfigOperation(workspaceRoot, 'get', { operationId })
  if (operation.operationId !== operationId || operation.requestId !== requestId ||
    !operation.status || !['APPLYING', 'APPLIED', 'FAILED'].includes(operation.status)) {
    throw new Error('服务端配置操作与当前请求不匹配。')
  }
  if (operation.status !== 'APPLIED') return operation
  const current = inspectPendingConfigIdentity(workspaceRoot, requestId)
  if (current.kind === 'mine' && current.operationId === operationId) {
    return finishOperation(workspaceRoot, operation, requestId)
  }
  if (readConfigContinuation(workspaceRoot, requestId) === operationId) return operation
  throw new Error('配置操作身份已变化，请重新检查。')
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
  const digest = await configChangesDigest(changes)
  const pending = readPendingOperation(workspaceRoot)
  if (pending && pending.requestId !== requestId) {
    throw new Error('CONFIG_OPERATION_CONFLICT：此应用存在其他来源尚未完成的配置，请回到原入口处理。')
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
    if ((pending && pending.digest !== digest) || (previous?.changesDigest && previous.changesDigest !== digest)) {
      if (previous?.status === 'APPLIED') {
        await finishOperation(workspaceRoot, previous, requestId)
        throw new Error('CONFIG_REVISION_CONFLICT：原操作已完成，请刷新配置后重新提交新提案。')
      }
      throw new Error(`CONFIG_OPERATION_CONFLICT：提案已变更，原操作 ${pendingId} 尚未完成；请恢复原提案并重试。原操作 ID 已保留。`)
    }
    if (previous?.status === 'APPLIED') return finishOperation(workspaceRoot, previous, requestId)
    const retried = await runConfigOperation(workspaceRoot, 'apply', {
      expectedConfigRevision: pending?.expectedConfigRevision ?? expectedConfigRevision,
      changes, operationId: pendingId, requestId: pending?.requestId ?? requestId
    })
    return finishOperation(workspaceRoot, retried, requestId)
  }
  await runConfigOperation(workspaceRoot, 'preview', { expectedConfigRevision, changes })
  const nextId = randomUUID()
  const next: PendingOperation = { version: 1, operationId: nextId, expectedConfigRevision, requestId, digest }
  window.localStorage.setItem(pendingKey(workspaceRoot), JSON.stringify(next))
  const applied = await runConfigOperation(workspaceRoot, 'apply', {
    expectedConfigRevision, changes, operationId: nextId, requestId
  })
  return finishOperation(workspaceRoot, applied, requestId)
}


const inFlightOperations = new Map<string, { digest?: string; operationId?: string; requestId?: string; promise: Promise<ConfigOperation> }>()

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
    if (current.digest !== digest || current.operationId !== operationId || current.requestId !== requestId) throw new Error('CONFIG_OPERATION_CONFLICT：原提案正在提交，请等待完成。')
    return current.promise
  }
  const promise = applyConfigChangesOnce(workspaceRoot, expectedConfigRevision, changes, operationId, requestId)
  inFlightOperations.set(key, { digest, operationId, requestId, promise })
  try {
    return await promise
  } finally {
    inFlightOperations.delete(key)
  }
}


/** 判断当前工作区是否仍持有需要显式恢复的操作身份。 */
export function hasPendingConfigOperation(workspaceRoot: string): boolean {
  return window.localStorage.getItem(pendingKey(workspaceRoot)) !== null
}

/** 仅发送原操作身份恢复磁盘 Desired，成功读盘后再释放 pending。 */
export async function resumePendingConfigOperation(workspaceRoot: string, requestId?: string): Promise<ConfigOperation> {
  const pending = readPendingOperation(workspaceRoot)
  if (!pending) throw new Error('CONFIG_OPERATION_MISSING：没有待完成的配置操作。')
  if (pending.requestId !== requestId) throw new Error('CONFIG_OPERATION_CONFLICT：请回到原配置请求继续操作。')
  const current = inFlightOperations.get(workspaceRoot)
  if (current) {
    if (current.digest || current.operationId !== pending.operationId || current.requestId !== requestId) {
      throw new Error('CONFIG_OPERATION_CONFLICT：配置正在提交，请等待完成。')
    }
    return current.promise
  }
  const promise = runConfigOperation(workspaceRoot, 'resume', { operationId: pending.operationId, requestId })
    .then((operation) => finishOperation(workspaceRoot, operation, requestId))
  inFlightOperations.set(workspaceRoot, { operationId: pending.operationId, requestId, promise })
  try {
    return await promise
  } finally {
    inFlightOperations.delete(workspaceRoot)
  }
}
