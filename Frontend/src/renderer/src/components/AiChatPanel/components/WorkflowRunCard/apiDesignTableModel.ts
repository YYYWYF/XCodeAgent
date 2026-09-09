import type {
  WorkflowApiDatabaseFieldNode,
  WorkflowApiDesignDraft,
  WorkflowApiDesignPayload,
  WorkflowApiExternalFieldNode,
  WorkflowApiField,
  WorkflowApiFieldMapping,
  WorkflowApiSourceField
} from '../../../../typings'
import {
  apiDesignFieldKey,
  databaseSourceFieldId,
  endpointFieldSnapshot,
  findFieldMapping,
  replaceFieldMapping,
  resolveDatabaseUsage,
  sourceFieldSnapshot
} from './apiDesignSerialization'

/** 表格中可展示的 Endpoint 字段映射模式。 */
export type ApiFieldMappingMode = WorkflowApiFieldMapping['mappingType']

/** 表格中可展示的 Endpoint 字段状态。 */
export type ApiFieldMappingStatus = 'completed' | 'required_missing' | 'optional_unmapped' | 'error'

/** 表格一行的纯数据投影。 */
export type ApiFieldMappingRow = {
  key: string
  field: WorkflowApiField
  mode: ApiFieldMappingMode
  sourceLabels: string[]
  status: ApiFieldMappingStatus
  errorMessages: string[]
}

/** 将内部位置转换为表格中的可读位置文案。 */
export function apiFieldLocationLabel(location: WorkflowApiField['location']): string {
  return ({
    path: 'Path', query: 'Query', header: 'Header', request_body: 'Request Body', response_body: 'Response Body'
  } as Record<WorkflowApiField['location'], string>)[location]
}

/** 按来源目录名称解析 Source Field 的用户可读标签。 */
export function sourceFieldLabel(
  node: WorkflowApiDatabaseFieldNode | WorkflowApiExternalFieldNode,
  payload: WorkflowApiDesignPayload
): string {
  const source = (Array.isArray(payload.sources) ? payload.sources : []).find(
    (item) => String(item.id || '') === node.sourceId
  )
  const sourceName = String(source?.name || node.sourceId)
  if (node.sourceType === 'database') return `${sourceName}.${node.table}.${node.column}`
  return `${sourceName}.${node.section}.${node.path}`
}

/** 将当前投影中已加载的数据库列或外部 Operation 字段转换为候选节点。 */
export function loadedSourceFields(payload: WorkflowApiDesignPayload): Array<WorkflowApiDatabaseFieldNode | WorkflowApiExternalFieldNode> {
  const database = payload.databaseMetadata
  const databaseFields = database?.columns?.length && database.sourceId && database.table
    ? database.columns.map((column) => ({
      nodeType: 'source_field' as const,
      id: databaseSourceFieldId({
        sourceType: 'database', sourceId: String(database.sourceId),
        schema: String(database.schema || ''), table: String(database.table), column: column.name,
        type: column.type, usage: 'read'
      }),
      sourceType: 'database' as const, sourceId: String(database.sourceId), schema: String(database.schema || ''),
      table: String(database.table), column: column.name, type: column.type, usage: 'read' as const,
      description: column.description
    }))
    : []
  const external = payload.externalOperation
  const operationId = String(external?.operation?.id || '')
  const externalFields = external?.fields?.length && external.sourceId && external.directoryId && operationId
    ? external.fields.map((field) => ({
      nodeType: 'source_field' as const,
      id: `source:external:${external.sourceId}:${external.directoryId}:${operationId}:${field.section}:${field.path}`,
      sourceType: 'external_api' as const, sourceId: String(external.sourceId),
      directoryId: String(external.directoryId), operationId,
      section: field.section as WorkflowApiExternalFieldNode['section'], path: field.path,
      type: field.type, description: field.description
    }))
    : []
  return [...databaseFields, ...externalFields]
}

/** 返回字段映射使用的数据源摘要。 */
export function resolveSourceMappingLabels(
  mapping: WorkflowApiFieldMapping | undefined,
  payload: WorkflowApiDesignPayload
): string[] {
  if (mapping?.mappingType !== 'source_mapping') return []
  return mapping.sourceFields.map((source) => sourceFieldLabel(sourceSnapshotToNode(source), payload))
}

/** 判断一个 Endpoint 字段当前使用的映射模式。 */
export function resolveEndpointMappingMode(
  mapping: WorkflowApiFieldMapping | undefined
): ApiFieldMappingMode {
  return mapping?.mappingType || 'unconfigured'
}

/** 计算单个 Endpoint 字段的完成状态。 */
export function resolveFieldMappingStatus(
  field: WorkflowApiField,
  mapping: WorkflowApiFieldMapping | undefined,
  errors: Record<string, string>
): { status: ApiFieldMappingStatus; errorMessages: string[] } {
  const key = apiDesignFieldKey(field)
  const errorMessages = Object.entries(errors)
    .filter(([errorKey]) => errorKey === key)
    .map(([, value]) => value)
  if (errorMessages.length) return { status: 'error', errorMessages: Array.from(new Set(errorMessages)) }
  if (mapping && mapping.mappingType !== 'unconfigured') return { status: 'completed', errorMessages: [] }
  return field.required
    ? { status: 'required_missing', errorMessages: [] }
    : { status: 'optional_unmapped', errorMessages: [] }
}

/** 将字段映射投影为 Request 或 Response 表格行。 */
export function projectApiFieldMappingRows(
  draft: WorkflowApiDesignDraft,
  payload: WorkflowApiDesignPayload,
  side: WorkflowApiField['side'],
  errors: Record<string, string> = {}
): ApiFieldMappingRow[] {
  const endpointFields = (Array.isArray(payload.endpointFields) ? payload.endpointFields : [])
    .filter((field) => field.side === side)
  return endpointFields.map((field) => {
    const mapping = findFieldMapping(draft, field)
    return {
      key: apiDesignFieldKey(field),
      field,
      mode: resolveEndpointMappingMode(mapping),
      sourceLabels: resolveSourceMappingLabels(mapping, payload),
      ...resolveFieldMappingStatus(field, mapping, errors)
    }
  })
}

/** 创建一个直接连接数据源的字段映射。 */
export function applySourceMapping(
  draft: WorkflowApiDesignDraft,
  endpoint: WorkflowApiField,
  source: WorkflowApiDatabaseFieldNode | WorkflowApiExternalFieldNode,
  usage?: WorkflowApiDatabaseFieldNode['usage']
): WorkflowApiDesignDraft {
  const normalized = source.sourceType === 'database'
    ? { ...source, usage: resolveDatabaseUsage(endpoint, usage || source.usage) }
    : source
  return replaceFieldMapping(draft, {
    endpointField: endpointFieldSnapshot(endpoint),
    mappingType: 'source_mapping',
    processingType: 'direct',
    sourceFields: [sourceFieldSnapshot(normalized, endpoint)]
  })
}

/** 把内嵌来源字段恢复为选择器可用的候选节点。 */
export function findSelectedSourceNode(
  mapping: WorkflowApiFieldMapping | undefined
): WorkflowApiDatabaseFieldNode | WorkflowApiExternalFieldNode | undefined {
  if (mapping?.mappingType !== 'source_mapping' || mapping.sourceFields.length !== 1) return undefined
  return sourceSnapshotToNode(mapping.sourceFields[0])
}

/** 把正式来源快照补充为只供选择器使用的候选节点。 */
export function sourceSnapshotToNode(
  source: WorkflowApiSourceField
): WorkflowApiDatabaseFieldNode | WorkflowApiExternalFieldNode {
  if (source.sourceType === 'database') {
    return {
      ...source,
      nodeType: 'source_field',
      id: databaseSourceFieldId(source)
    }
  }
  return {
    ...source,
    nodeType: 'source_field',
    id: `source:external:${source.sourceId}:${source.directoryId}:${source.operationId}:${source.section}:${source.path}`
  }
}
