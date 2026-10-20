import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'
import { AgUiBusinessError } from './agUiBusinessError'
import type { DatabaseTableOperation } from '../typings/managedDatabase'

export type EntityTablePreview = {
  entityId: string
  entityName: string
  datasourceKey: string
  datasourceId: number
  tableName: string
  sql: string
  previewHash: string
  columns: Array<{ name: string; type: string; nullable: boolean; comment: string; auto_increment?: boolean }>
}

export type EntityTableIssue = {
  code: string
  title: string
  message: string
  action: 'rebuild' | 'refresh'
}

/** 保留后端签发的明确业务提示，不根据错误文案判断能否重建。 */
export class EntityTableActionError extends AgUiBusinessError {
  /** 保存安全提示和建议动作，供实体面板展示及处理明确的建表拒绝。 */
  constructor(readonly issue: EntityTableIssue) {
    super(issue.message)
    this.name = 'EntityTableActionError'
  }
}

export type EntityTablePayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  preview?: EntityTablePreview
  receipt?: { operationId: string; status: 'RUNNING' }
  operation?: DatabaseTableOperation | null
  readiness?: {
    ready: boolean
    reason?: string
    operationId?: string
    operationStatus?: string
    tables: Array<{
      tableName: string
      status: 'SYNCED' | 'NOT_CREATED' | 'STRUCTURE_MISMATCH' | 'TABLE_CONFLICT'
      differences: Array<{ category: string; target: string; expected?: string; actual?: string }>
    }>
  }
  error?: { message?: string }
  issue?: EntityTableIssue
}

/** 校验标准 AG-UI 业务载荷，不自行处理事件传输或 SSE 文本。 */
function readPayload(value: unknown): EntityTablePayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const data = value as Partial<EntityTablePayload>
  if (data.schemaVersion !== 1 || typeof data.runId !== 'string' || typeof data.threadId !== 'string'
    || !['completed', 'failed'].includes(String(data.status))) return undefined
  return data as EntityTablePayload
}

/** 调用独立实体表动作，创建及重建只提交一次，数据丢弃必须明确传入。 */
export async function requestEntityTable(
  workspaceRoot: string,
  entityId: string,
  action: 'prepare' | 'execute' | 'rebuild' | 'refresh',
  previewHash?: string,
  allowDataLoss = false
): Promise<EntityTablePayload> {
  const base = window.devAgentStudio?.agentBaseUrl
  const url = base ? `${base.replace(/\/$/, '')}/entity-tables/run` : '/api/agent/entity-tables/run'
  const agent = createAgUiHttpAgent({ url, threadId: randomUUID() })
  agent.addMessage({ id: randomUUID(), role: 'user', content: {
    prepare: '生成实体建表 SQL。', execute: '确认执行当前实体建表 SQL。',
    rebuild: '确认清空该表数据并按当前 SQL 重建。', refresh: '刷新实体表结构及原操作结果。'
  }[action] })
  let payload: EntityTablePayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'entity-tables') payload = readPayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readPayload((event.snapshot as { entityTables?: unknown })?.entityTables) ?? payload
    }
  }
  await agent.runAgent({ forwardedProps: { entityTables: {
    workspaceRoot, entityId, action,
    ...(['execute', 'rebuild'].includes(action) ? { previewHash, confirmed: true } : {}),
    ...(action === 'rebuild' ? { allowDataLoss } : {})
  } } }, subscriber)
  if (!payload) throw new Error('实体建表接口没有返回完整的 AG-UI 结果。')
  if (payload.status === 'failed') {
    if (payload.issue) throw new EntityTableActionError(payload.issue)
    throw new AgUiBusinessError(payload.error?.message || '实体建表失败。')
  }
  if (!payload.preview) throw new Error('实体建表接口未返回当前 SQL。')
  return payload
}
