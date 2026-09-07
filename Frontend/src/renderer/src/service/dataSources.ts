import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type {
  DataSourceCatalog,
  DataSourceValidation,
  DatabaseDataSourceInput,
  ExternalApiDataSourceInput
} from '../typings'
import { createAgUiHttpAgent } from './authentication'

type DataSourceAction =
  | 'list'
  | 'create'
  | 'update'
  | 'delete'
  | 'validate'
  | 'detail'
  | 'database_tables'
  | 'database_columns'
  | 'external_operation'

export type ApiDesignDatabaseMetadata = {
  sourceId?: string
  schema?: string
  table?: string
  tables?: Array<{ name: string; description?: string }>
  columns?: Array<{ name: string; type: string; required?: boolean; description?: string }>
}

export type ApiDesignExternalOperationMetadata = {
  sourceId?: string
  sourceName?: string
  directoryId?: string
  directoryName?: string
  operation?: Record<string, unknown>
  fields?: Array<{
    section: string
    path: string
    type: string
    required?: boolean
    description?: string
  }>
}

type DataSourcesPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: DataSourceAction
  catalog?: DataSourceCatalog
  metadata?: ApiDesignDatabaseMetadata | ApiDesignExternalOperationMetadata
  validation?: DataSourceValidation
  error?: { type?: string; message?: string }
}

/** 将内部动作名转换为独立数据源路由的短横线路径。 */
function getDataSourcesUrl(action: DataSourceAction): string {
  const pathAction = action.replace(/_/g, '-')
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/data-sources/${pathAction}`
    : `/api/agent/data-sources/${pathAction}`
}

function readDataSourcesPayload(value: unknown): DataSourcesPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<DataSourcesPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as DataSourcesPayload
}

function readDataSourcesFromState(value: unknown): DataSourcesPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readDataSourcesPayload((value as { dataSources?: unknown }).dataSources)
}

function readDataSourcesFromResult(value: unknown): DataSourcesPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readDataSourcesPayload((value as { dataSources?: unknown }).dataSources)
}

async function runDataSourceAction(
  workspaceRoot: string,
  action: DataSourceAction,
  actionInput: Record<string, unknown>,
  messageContent: string
): Promise<DataSourcesPayload> {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: getDataSourcesUrl(action), threadId })
  const message: Message = { id: randomUUID(), role: 'user', content: messageContent }
  agent.addMessage(message)

  let dataSources: DataSourcesPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'data-sources') {
        dataSources = readDataSourcesPayload(event.value) ?? dataSources
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      dataSources = readDataSourcesFromState(event.snapshot) ?? dataSources
    }
  }
  const result = await agent.runAgent(
    { forwardedProps: { dataSources: { workspaceRoot, ...actionInput } } },
    subscriber
  )
  dataSources = readDataSourcesFromResult(result.result) ?? dataSources
  if (!dataSources) throw new Error('数据源接口没有返回有效的 AG-UI 状态。')
  if (dataSources.status === 'failed') {
    throw new Error(dataSources.error?.message || '数据源操作失败。')
  }
  return dataSources
}

function requireCatalog(payload: DataSourcesPayload): DataSourceCatalog {
  if (!payload.catalog) throw new Error('数据源接口没有返回目录。')
  return payload.catalog
}

/** 从独立数据源 AG-UI 动作中提取 API 设计需要的数据库元数据。 */
function requireDatabaseMetadata(payload: DataSourcesPayload): ApiDesignDatabaseMetadata {
  if (!payload.metadata || !Array.isArray((payload.metadata as ApiDesignDatabaseMetadata).tables)) {
    throw new Error('数据库元数据接口没有返回有效的表结构。')
  }
  return payload.metadata as ApiDesignDatabaseMetadata
}

/** 从独立数据源 AG-UI 动作中提取 API 设计需要的外部 Operation Schema。 */
function requireExternalOperationMetadata(payload: DataSourcesPayload): ApiDesignExternalOperationMetadata {
  if (!payload.metadata || !Array.isArray((payload.metadata as ApiDesignExternalOperationMetadata).fields)) {
    throw new Error('外部 Operation 接口没有返回有效的字段结构。')
  }
  return payload.metadata as ApiDesignExternalOperationMetadata
}

/** 读取当前工作区的独立数据源目录。 */
export async function requestDataSources(workspaceRoot: string): Promise<DataSourceCatalog> {
  return requireCatalog(await runDataSourceAction(workspaceRoot, 'list', {}, '读取独立数据源。'))
}

/** 通过独立接口读取 API 设计可用的直属 MySQL 表清单。 */
export async function requestApiDesignDatabaseTables(
  workspaceRoot: string,
  sourceId: string,
): Promise<ApiDesignDatabaseMetadata> {
  return requireDatabaseMetadata(
    await runDataSourceAction(
      workspaceRoot,
      'database_tables',
      { sourceId },
      '读取 API 设计数据库表清单。',
    ),
  )
}

/** 通过独立接口读取 API 设计选定数据库表的字段。 */
export async function requestApiDesignDatabaseColumns(
  workspaceRoot: string,
  sourceId: string,
  table: string,
): Promise<ApiDesignDatabaseMetadata> {
  return requireDatabaseMetadata(
    await runDataSourceAction(
      workspaceRoot,
      'database_columns',
      { sourceId, table },
      '读取 API 设计数据库字段。',
    ),
  )
}

/** 通过独立接口读取 API 设计选定的外部 Operation Schema。 */
export async function requestApiDesignExternalOperation(
  workspaceRoot: string,
  sourceId: string,
  directoryId: string,
  operationId: string,
): Promise<ApiDesignExternalOperationMetadata> {
  return requireExternalOperationMetadata(
    await runDataSourceAction(
      workspaceRoot,
      'external_operation',
      { sourceId, directoryId, operationId },
      '读取 API 设计外部 Operation Schema。',
    ),
  )
}

/** 读取指定外部 API 接口的完整配置。 */
export async function requestDataSourceOperation(
  workspaceRoot: string,
  sourceId: string,
  operationId: string
): Promise<DataSourceCatalog> {
  return requireCatalog(
    await runDataSourceAction(
      workspaceRoot,
      'detail',
      { sourceId, operationId },
      '读取外部 API 接口详情。'
    )
  )
}

/** 读取指定外部 API 域名的完整配置，用于保存目录或接口变更时保留未选接口详情。 */
export async function requestDataSourceDetails(
  workspaceRoot: string,
  sourceId: string
): Promise<DataSourceCatalog> {
  return requireCatalog(
    await runDataSourceAction(
      workspaceRoot,
      'detail',
      { sourceId },
      '读取外部 API 域名详情。'
    )
  )
}

/** 创建一个独立数据源。 */
export async function createDataSource(
  workspaceRoot: string,
  source: DatabaseDataSourceInput | ExternalApiDataSourceInput
): Promise<DataSourceCatalog> {
  return requireCatalog(
    await runDataSourceAction(
      workspaceRoot,
      'create',
      { source },
      `创建数据源 ${source.name}。`
    )
  )
}

/** 更新一个独立数据源。 */
export async function updateDataSource(
  workspaceRoot: string,
  source: DatabaseDataSourceInput | ExternalApiDataSourceInput
): Promise<DataSourceCatalog> {
  return requireCatalog(
    await runDataSourceAction(
      workspaceRoot,
      'update',
      { source },
      `更新数据源 ${source.name}。`
    )
  )
}

/** 删除一个独立数据源。 */
export async function deleteDataSource(
  workspaceRoot: string,
  sourceId: string
): Promise<DataSourceCatalog> {
  return requireCatalog(
    await runDataSourceAction(
      workspaceRoot,
      'delete',
      { sourceId },
      '删除独立数据源。'
    )
  )
}

/** 校验一个尚未保存的数据源。 */
export async function validateDataSource(
  workspaceRoot: string,
  source: DatabaseDataSourceInput | ExternalApiDataSourceInput | { sourceId: string }
): Promise<DataSourceValidation> {
  const actionInput = 'sourceId' in source ? { sourceId: source.sourceId } : { source }
  const response = await runDataSourceAction(
    workspaceRoot,
    'validate',
    actionInput,
    'name' in source ? `校验数据源 ${source.name}。` : '校验已保存数据源。'
  )
  if (!response.validation) throw new Error('数据源接口没有返回校验结果。')
  return response.validation
}
