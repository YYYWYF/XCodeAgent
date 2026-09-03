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

type DataSourceAction = 'list' | 'create' | 'update' | 'delete' | 'validate' | 'detail'

type DataSourcesPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: DataSourceAction
  catalog?: DataSourceCatalog
  validation?: DataSourceValidation
  error?: { type?: string; message?: string }
}

function getDataSourcesUrl(action: DataSourceAction): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/data-sources/${action}`
    : `/api/agent/data-sources/${action}`
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

/** 读取当前工作区的独立数据源目录。 */
export async function requestDataSources(workspaceRoot: string): Promise<DataSourceCatalog> {
  return requireCatalog(await runDataSourceAction(workspaceRoot, 'list', {}, '读取独立数据源。'))
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
