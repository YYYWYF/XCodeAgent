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
}

export type EntityDdlPreview = {
  entityId: string
  entityName: string
  tableName: string
  sql: string
  draftId: string
  confirmationStatus: 'draft' | 'confirmed'
}

type EntityDdlCommand =
  | { action: 'preview_ddl' }
  | { action: 'revise_ddl'; draftId: string; feedback: string }
  | { action: 'confirm_ddl'; draftId: string; confirmed: true }

type EntityTableCommand =
  | EntityDdlCommand
  | { action: 'prepare' | 'refresh' }
  | { action: 'execute'; previewHash?: string; confirmed: true }
  | { action: 'rebuild'; previewHash?: string; confirmed: true; allowDataLoss: boolean }

export type EntityTableIssue = {
  code: string
  title: string
  message: string
  action: 'rebuild' | 'refresh' | 'review_ddl'
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
  ddlPreview?: EntityDdlPreview
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
  if (
    data.schemaVersion !== 1 ||
    typeof data.runId !== 'string' ||
    typeof data.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(data.status))
  )
    return undefined
  return data as EntityTablePayload
}

/** 共用官方 AG-UI 客户端传输，DDL 预览与建表动作保持独立运行身份。 */
async function requestEntityTableAction(
  workspaceRoot: string,
  entityId: string,
  command: EntityTableCommand
): Promise<EntityTablePayload> {
  const base = window.devAgentStudio?.agentBaseUrl
  const url = base ? `${base.replace(/\/$/, '')}/entity-tables/run` : '/api/agent/entity-tables/run'
  const agent = createAgUiHttpAgent({ url, threadId: randomUUID() })
  agent.addMessage({
    id: randomUUID(),
    role: 'user',
    content: {
      preview_ddl: '查看实体建表 DDL。',
      revise_ddl: '按修改意见重新生成实体建表 DDL。',
      confirm_ddl: '确认保存本版实体建表 DDL。',
      prepare: '读取已确认的实体建表 DDL。',
      execute: '确认执行当前实体建表 SQL。',
      rebuild: '确认清空该表数据并按当前 SQL 重建。',
      refresh: '刷新实体表结构及原操作结果。'
    }[command.action]
  })
  let payload: EntityTablePayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'entity-tables') payload = readPayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readPayload((event.snapshot as { entityTables?: unknown })?.entityTables) ?? payload
    }
  }
  await agent.runAgent(
    {
      forwardedProps: {
        entityTables: {
          workspaceRoot,
          entityId,
          ...command
        }
      }
    },
    subscriber
  )
  if (!payload) throw new Error('实体建表接口没有返回完整的 AG-UI 结果。')
  if (payload.status === 'failed') {
    if (payload.issue) throw new EntityTableActionError(payload.issue)
    throw new AgUiBusinessError(payload.error?.message || '实体建表失败。')
  }
  return payload
}

/** 调用受控建表动作，确认及数据丢弃仍沿用既有合同。 */
export async function requestEntityTable(
  workspaceRoot: string,
  entityId: string,
  action: 'prepare' | 'execute' | 'rebuild' | 'refresh',
  previewHash?: string,
  allowDataLoss = false
): Promise<EntityTablePayload> {
  const command: EntityTableCommand =
    action === 'rebuild'
      ? { action, previewHash, confirmed: true, allowDataLoss }
      : action === 'execute'
        ? { action, previewHash, confirmed: true }
        : { action }
  const payload = await requestEntityTableAction(workspaceRoot, entityId, command)
  if (!payload.preview) throw new Error('实体建表接口未返回当前 SQL。')
  return payload
}

/** 加载、按意见修订或明确确认当前实体 DDL，核对返回的审阅版本。 */
export async function requestEntityDdl(
  workspaceRoot: string,
  entityId: string,
  command: EntityDdlCommand = { action: 'preview_ddl' }
): Promise<EntityDdlPreview> {
  const payload = await requestEntityTableAction(workspaceRoot, entityId, command)
  const preview = payload.ddlPreview
  if (
    !preview ||
    preview.entityId !== entityId ||
    typeof preview.sql !== 'string' ||
    !preview.sql.trim() ||
    typeof preview.tableName !== 'string' ||
    !preview.tableName.trim() ||
    typeof preview.draftId !== 'string' ||
    !/^[0-9a-f]{32}$/.test(preview.draftId) ||
    !['draft', 'confirmed'].includes(preview.confirmationStatus) ||
    (command.action === 'revise_ddl' &&
      (preview.confirmationStatus !== 'draft' || preview.draftId === command.draftId)) ||
    (command.action === 'confirm_ddl' &&
      (preview.confirmationStatus !== 'confirmed' || preview.draftId !== command.draftId))
  ) {
    throw new Error('实体接口未返回完整的建表 DDL。')
  }
  return preview
}
