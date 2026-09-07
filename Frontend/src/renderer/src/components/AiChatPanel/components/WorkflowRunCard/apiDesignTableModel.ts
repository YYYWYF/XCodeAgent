import type {
  WorkflowApiDatabaseFieldNode,
  WorkflowApiDesignDraft,
  WorkflowApiDesignPayload,
  WorkflowApiEntityFieldReference,
  WorkflowApiEntityTemplate,
  WorkflowApiExternalFieldNode,
  WorkflowApiField,
  WorkflowApiFieldMapping,
  WorkflowApiSceneEntity,
  WorkflowApiSourceField
} from '../../../../typings'
import {
  apiDesignFieldKey,
  copyEntityTemplate,
  databaseSourceFieldId,
  endpointFieldSnapshot,
  findFieldMapping,
  pruneUnusedSceneEntities as pruneUnusedEntities,
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
  entityLabels: string[]
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

/** 返回字段映射使用的实体摘要。 */
export function resolveEntityMappingLabels(
  draft: WorkflowApiDesignDraft,
  mapping: WorkflowApiFieldMapping | undefined
): string[] {
  if (!mapping || mapping.mappingType !== 'through_entity') return []
  const entity = draft.sceneEntities.find((item) => item.id === mapping.entityField.entityId)
  return [`${entity?.name || mapping.entityField.entityId}.${mapping.entityField.path}`]
}

/** 返回字段映射使用的数据源摘要。 */
export function resolveSourceMappingLabels(
  mapping: WorkflowApiFieldMapping | undefined,
  payload: WorkflowApiDesignPayload,
  endpoint: WorkflowApiField
): string[] {
  if (!mapping || !('sourceField' in mapping) || !mapping.sourceField) return []
  const candidate = sourceSnapshotToNode(mapping.sourceField)
  const label = sourceFieldLabel(candidate, payload)
  if (mapping.mappingType === 'through_entity') {
    return endpoint.side === 'request' ? [`→ ${label}`] : [`${label} →`]
  }
  return [label]
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
  const entityId = mapping?.mappingType === 'through_entity' ? mapping.entityField.entityId : ''
  const errorMessages = Object.entries(errors)
    .filter(([errorKey]) => errorKey === key || (entityId && errorKey === `entity:${entityId}`))
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
      entityLabels: resolveEntityMappingLabels(draft, mapping),
      sourceLabels: resolveSourceMappingLabels(mapping, payload, field),
      ...resolveFieldMappingStatus(field, mapping, errors)
    }
  })
}

/** 确保模板只复制一次，并返回所选场景实体字段引用。 */
export function ensureTemplateEntityField(
  draft: WorkflowApiDesignDraft,
  template: WorkflowApiEntityTemplate,
  fieldName: string
): { draft: WorkflowApiDesignDraft; entity: WorkflowApiSceneEntity; field: WorkflowApiEntityFieldReference } {
  const existing = draft.sceneEntities.find((entity) => entity.templateEntityId === template.id)
  const entity = existing || copyEntityTemplate(template)
  const withEntity = existing ? draft : { ...draft, sceneEntities: [...draft.sceneEntities, entity] }
  const field = entity.fields.find((item) => item.name === fieldName)
  if (!field) throw new Error('所选实体字段不存在于 TechnicalPlan 模板。')
  return {
    draft: withEntity,
    entity,
    field: { entityId: entity.id, fieldId: field.id, path: field.name, type: field.type }
  }
}

/** 创建一个直接连接数据源的字段映射。 */
export function applyDirectSourceMapping(
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
    mappingType: 'direct_source',
    sourceField: sourceFieldSnapshot(normalized, endpoint)
  })
}

/** 创建 Endpoint 经场景实体的字段映射，并可选内嵌一个来源字段。 */
export function applyEntityMapping(
  draft: WorkflowApiDesignDraft,
  endpoint: WorkflowApiField,
  entityField: WorkflowApiEntityFieldReference,
  source?: WorkflowApiDatabaseFieldNode | WorkflowApiExternalFieldNode,
  usage?: WorkflowApiDatabaseFieldNode['usage']
): WorkflowApiDesignDraft {
  const normalized = source?.sourceType === 'database'
    ? { ...source, usage: resolveDatabaseUsage(endpoint, usage || source.usage) }
    : source
  return replaceFieldMapping(draft, {
    endpointField: endpointFieldSnapshot(endpoint),
    mappingType: 'through_entity',
    entityField,
    ...(normalized ? { sourceField: sourceFieldSnapshot(normalized, endpoint) } : {})
  })
}

/** 从经实体字段映射中移除来源字段。 */
export function removeSourceFromEntityMapping(
  draft: WorkflowApiDesignDraft,
  endpoint: WorkflowApiField
): WorkflowApiDesignDraft {
  const mapping = findFieldMapping(draft, endpoint)
  if (!mapping || mapping.mappingType !== 'through_entity') return draft
  return replaceFieldMapping(draft, {
    endpointField: endpointFieldSnapshot(endpoint),
    mappingType: 'through_entity',
    entityField: mapping.entityField
  })
}

/** 删除未被任一字段映射引用的场景实体。 */
export function pruneUnusedSceneEntities(draft: WorkflowApiDesignDraft): WorkflowApiDesignDraft {
  return pruneUnusedEntities(draft)
}

/** 把内嵌来源字段恢复为选择器可用的候选节点。 */
export function findSelectedSourceNode(
  mapping: WorkflowApiFieldMapping | undefined
): WorkflowApiDatabaseFieldNode | WorkflowApiExternalFieldNode | undefined {
  if (!mapping || !('sourceField' in mapping) || !mapping.sourceField) return undefined
  return sourceSnapshotToNode(mapping.sourceField)
}

/** 判断实体字段是否仍被字段映射引用。 */
export function isSceneEntityFieldReferenced(
  draft: WorkflowApiDesignDraft,
  entityId: string,
  fieldId: string
): boolean {
  return draft.fieldMappings.some(
    (mapping) => mapping.mappingType === 'through_entity' &&
      mapping.entityField.entityId === entityId &&
      mapping.entityField.fieldId === fieldId
  )
}

/** 把正式来源快照补充为只供选择器使用的候选节点。 */
function sourceSnapshotToNode(
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
