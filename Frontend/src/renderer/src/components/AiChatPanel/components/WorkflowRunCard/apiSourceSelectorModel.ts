import type {
  WorkflowApiDatabaseFieldNode,
  WorkflowApiExternalFieldNode,
  WorkflowApiField,
  WorkflowApiDesignPayload
} from '../../../../typings'
import { defaultDatabaseUsage, resolveDatabaseUsage } from './apiDesignSerialization'

/** API 设计面板使用的独立数据源元数据动作，统一由此处维护避免组件写错协议名称。 */
export const API_SOURCE_METADATA_ACTIONS = {
  databaseTables: 'database_tables',
  databaseColumns: 'database_columns',
  externalOperation: 'external_operation'
} as const

/** API 设计面板可提交的独立数据源元数据动作类型。 */
export type ApiSourceMetadataAction = typeof API_SOURCE_METADATA_ACTIONS[keyof typeof API_SOURCE_METADATA_ACTIONS]

/** API 设计面板查询来源元数据时需要携带的级联上下文。 */
export type ApiSourceMetadataContext = {
  sourceId?: string
  table?: string
  directoryId?: string
  operationId?: string
}

/** 描述一次元数据查询的生命周期，供抽屉显示加载、失败和空结果状态。 */
export type ApiSourceMetadataRequestState = {
  action?: ApiSourceMetadataAction
  sourceId?: string
  table?: string
  directoryId?: string
  operationId?: string
  status: 'idle' | 'loading' | 'success' | 'error'
  message?: string
}

/** 判断元数据请求状态是否仍然对应当前选择，避免过期响应污染新来源。 */
export function matchesApiSourceMetadataRequest(
  state: ApiSourceMetadataRequestState | undefined,
  action: ApiSourceMetadataAction,
  context: ApiSourceMetadataContext
): boolean {
  if (!state || state.action !== action) return false
  return state.sourceId === context.sourceId
    && state.table === context.table
    && state.directoryId === context.directoryId
    && state.operationId === context.operationId
}
/** 数据源级联选择器的临时状态，不代表已经写入 Endpoint 草稿。 */
export type ApiSourceSelectorState = {
  sourceId: string
  table: string
  column: string
  directoryId: string
  operationId: string
  externalFieldKey: string
  usage: WorkflowApiDatabaseFieldNode['usage']
}

/** 外部 Operation 字段的轻量元数据，避免把投影对象直接绑定到组件状态。 */
export type ApiExternalFieldMetadata = {
  section: string
  path: string
  type: string
  description?: string
}

export type ApiExternalFieldDirection =
  | 'request'
  | 'response'
  | 'business_input'
  | 'business_output'

const sectionLabels: Record<string, string> = {
  path: 'Path',
  query: 'Query',
  header: 'Header',
  request_body: 'Request Body',
  response_body: 'Response Body'
}

/** 根据已保存的 Source Field 初始化编辑器，元数据流式更新不会触发此函数。 */
export function createApiSourceSelectorState(
  endpoint: WorkflowApiField,
  selectedSourceNode?: WorkflowApiDatabaseFieldNode | WorkflowApiExternalFieldNode
): ApiSourceSelectorState {
  const initial: ApiSourceSelectorState = {
    sourceId: '',
    table: '',
    column: '',
    directoryId: '',
    operationId: '',
    externalFieldKey: '',
    usage: defaultDatabaseUsage(endpoint)
  }
  if (!selectedSourceNode) return initial
  if (selectedSourceNode.sourceType === 'database') {
    return {
      ...initial,
      sourceId: selectedSourceNode.sourceId,
      table: selectedSourceNode.table,
      column: selectedSourceNode.column,
      usage: resolveDatabaseUsage(endpoint, selectedSourceNode.usage)
    }
  }
  return {
    ...initial,
    sourceId: selectedSourceNode.sourceId,
    directoryId: selectedSourceNode.directoryId,
    operationId: selectedSourceNode.operationId,
    externalFieldKey: `${selectedSourceNode.section}:${selectedSourceNode.path}`
  }
}

/** 切换数据源时仅清除下级元数据选择，避免残留旧来源字段。 */
export function resetApiSourceForSource(
  state: ApiSourceSelectorState,
  sourceId: string,
  endpoint: WorkflowApiField
): ApiSourceSelectorState {
  return {
    ...state,
    sourceId,
    table: '',
    column: '',
    directoryId: '',
    operationId: '',
    externalFieldKey: '',
    usage: defaultDatabaseUsage(endpoint)
  }
}

/** 切换数据库表时清除列选择。 */
export function resetApiSourceForTable(state: ApiSourceSelectorState, table: string): ApiSourceSelectorState {
  return { ...state, table, column: '' }
}

/** 切换外部目录时清除 Operation 和字段选择。 */
export function resetApiSourceForDirectory(state: ApiSourceSelectorState, directoryId: string): ApiSourceSelectorState {
  return { ...state, directoryId, operationId: '', externalFieldKey: '' }
}

/** 切换外部 Operation 时清除旧字段选择。 */
export function resetApiSourceForOperation(state: ApiSourceSelectorState, operationId: string): ApiSourceSelectorState {
  return { ...state, operationId, externalFieldKey: '' }
}

/** 按映射方向筛选允许使用的外部 API 字段。 */
export function filterExternalFields(
  fields: ApiExternalFieldMetadata[],
  direction: ApiExternalFieldDirection
): ApiExternalFieldMetadata[] {
  return fields.filter((field) => {
    if (direction === 'response' || direction === 'business_input') return field.section === 'response_body'
    return field.section !== 'response_body'
  })
}

/** 生成按 Path、Query、Header、Body 分组的外部字段选择项。 */
export function groupExternalFields(
  fields: ApiExternalFieldMetadata[],
  direction: ApiExternalFieldDirection
): Array<{ label: string; options: Array<{ value: string; label: string }> }> {
  const groups = new Map<string, Array<{ value: string; label: string }>>()
  filterExternalFields(fields, direction).forEach((field) => {
    const options = groups.get(field.section) || []
    options.push({
      value: `${field.section}:${field.path}`,
      label: `${field.path} · ${field.type}`
    })
    groups.set(field.section, options)
  })
  return Array.from(groups.entries()).map(([section, options]) => ({
    label: sectionLabels[section] || section,
    options
  }))
}

/** 选择器使用当前来源快照，不从其他来源的投影中回填表或 Operation。 */
export function hasMatchingDatabaseMetadata(
  payload: WorkflowApiDesignPayload,
  sourceId: string,
  table?: string
): boolean {
  return payload.databaseMetadata?.sourceId === sourceId
    && (!table || payload.databaseMetadata.table === table)
}

/** 判断外部 Operation 投影是否属于当前来源级联选择。 */
export function hasMatchingExternalOperation(
  payload: WorkflowApiDesignPayload,
  sourceId: string,
  directoryId: string,
  operationId: string
): boolean {
  const operation = payload.externalOperation
  return operation?.sourceId === sourceId
    && operation.directoryId === directoryId
    && String(operation.operation?.id || '') === operationId
}
