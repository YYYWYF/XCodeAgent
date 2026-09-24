import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type { ApplicationLifecycle } from '../typings'
import { createAgUiHttpAgent } from './authentication'

export type DirectEntityDesign = {
  entityId: string
  tableName: string
  sql: string
  sqlSha256: string
  technicalPlanSha256: string
  sqlPath: string
  status: 'confirmed' | 'pending' | 'stale'
  fields: string[]
}

export type DirectEndpointContract = {
  apiContractId: string
  endpointId: string
  method: string
  path: string
  summary: string
  entityIds: string[]
  requestSchema: Record<string, unknown> | null
  responseSchema: Record<string, unknown>
  technicalPlanSha256: string
  status: 'confirmed' | 'pending' | 'stale'
}

type DirectDevelopmentPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  entity?: DirectEntityDesign
  endpoint?: DirectEndpointContract
  lifecycle?: ApplicationLifecycle
  error?: { message?: string }
}

/** 返回 Direct 开发产物的独立 AG-UI 地址。 */
function directDevelopmentUrl(): string {
  const baseUrl = window.xcodeAgent?.agentBaseUrl
  return baseUrl
    ? `${baseUrl.replace(/\/$/, '')}/direct-development/run`
    : '/api/agent/direct-development/run'
}

/** 仅接受具有完整 AG-UI 结果身份的 Direct 响应。 */
function readDirectPayload(value: unknown): DirectDevelopmentPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<DirectDevelopmentPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) return undefined
  return payload as DirectDevelopmentPayload
}

/** 通过现有 AG-UI 客户端执行一次 Direct 正式产物动作。 */
async function runDirectAction(
  action: 'read_entity' | 'confirm_entity' | 'read_endpoint' | 'generate_endpoint' | 'save_endpoint',
  workspaceRoot: string,
  target: Record<string, unknown>,
  extra: Record<string, unknown> = {}
): Promise<DirectDevelopmentPayload> {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: directDevelopmentUrl(), threadId })
  const message: Message = { id: randomUUID(), role: 'user', content: '处理 Direct 开发产物。' }
  agent.addMessage(message)
  let payload: DirectDevelopmentPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'direct-development') payload = readDirectPayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      const state = event.snapshot as { directDevelopment?: unknown }
      payload = readDirectPayload(state?.directDevelopment) ?? payload
    }
  }
  const result = await agent.runAgent({
    forwardedProps: {
      directDevelopment: { action, workspaceRoot, ...target, ...extra }
    }
  }, subscriber)
  const state = result.result as { directDevelopment?: unknown }
  payload = readDirectPayload(state?.directDevelopment) ?? payload
  if (!payload) throw new Error('Direct 开发产物操作没有返回有效状态。')
  if (payload.status === 'failed') throw new Error(payload.error?.message || 'Direct 开发产物操作失败。')
  return payload
}

/** 读取当前实体字段对应的建表 SQL 及确认状态。 */
export async function readDirectEntityDesign(
  workspaceRoot: string,
  entityId: string
): Promise<DirectEntityDesign> {
  const payload = await runDirectAction('read_entity', workspaceRoot, { entityId })
  if (!payload.entity) throw new Error('Direct 实体设计缺少 SQL 结果。')
  return payload.entity
}

/** 确认用户实际预览过的 SQL 摘要并返回最新生命周期状态。 */
export async function confirmDirectEntityDesign(
  workspaceRoot: string,
  entityId: string,
  sqlSha256: string
): Promise<{ entity: DirectEntityDesign; lifecycle: ApplicationLifecycle }> {
  const payload = await runDirectAction('confirm_entity', workspaceRoot, { entityId }, { sqlSha256 })
  if (!payload.entity || !payload.lifecycle) throw new Error('实体 SQL 确认缺少产物或生命周期结果。')
  return { entity: payload.entity, lifecycle: payload.lifecycle }
}

/** 读取当前 Endpoint 正式 Schema 草稿及保存状态。 */
export async function readDirectEndpointContract(
  workspaceRoot: string, apiContractId: string, endpointId: string
): Promise<DirectEndpointContract> {
  const payload = await runDirectAction('read_endpoint', workspaceRoot, { apiContractId, endpointId })
  if (!payload.endpoint) throw new Error('Direct API 契约读取结果为空。')
  return payload.endpoint
}

/** 单次调用模型生成可编辑的 API 请求体和响应体草稿。 */
export async function generateDirectEndpointDraft(
  workspaceRoot: string, apiContractId: string, endpointId: string
): Promise<DirectEndpointContract> {
  const payload = await runDirectAction('generate_endpoint', workspaceRoot, { apiContractId, endpointId })
  if (!payload.endpoint) throw new Error('模型没有返回 Direct API 契约草稿。')
  return payload.endpoint
}

/** 保存用户审核后的 Endpoint Schema，不要求实体 SQL 已确认。 */
export async function saveDirectEndpointContract(
  workspaceRoot: string,
  contract: DirectEndpointContract,
  requestSchema: Record<string, unknown> | null,
  responseSchema: Record<string, unknown>
): Promise<DirectEndpointContract> {
  const payload = await runDirectAction('save_endpoint', workspaceRoot, {
    apiContractId: contract.apiContractId,
    endpointId: contract.endpointId,
    technicalPlanSha256: contract.technicalPlanSha256,
    requestSchema,
    responseSchema
  })
  if (!payload.endpoint) throw new Error('Direct API 契约保存结果为空。')
  return payload.endpoint
}
