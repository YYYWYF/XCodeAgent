import type { BindingSelection } from '../../../../typings/endpointDesign'
import type { WorkflowApiDatabaseOperation, WorkflowApiDesignDraft, WorkflowApiField, WorkflowApiSourceField } from '../../../../typings'
import type { ApiDesignDatabaseMetadata, ApiDesignExternalOperationMetadata, SelectedDataTable } from '../../../../service/dataSources'
import { apiDesignFieldKey, defaultDatabaseUsageForOperation, defaultDatabaseOperation, endpointFieldSnapshot } from '../WorkflowRunCard/apiDesignSerialization'

export const DATABASE_OPERATION_LABELS: Record<WorkflowApiDatabaseOperation, string> = {
  create: '新增', read: '查询', update: '修改', delete: '删除'
}

/** 将 HTTP 方法和 Endpoint 字段转换为数据库操作的初始分区。 */
export function operationForEndpoint(method?: string): WorkflowApiDatabaseOperation | undefined {
  return defaultDatabaseOperation(method)
}

/** 改变 CRUD 操作后只保留适用的查询树和写入列配置。 */
export function resetDraftForDatabaseOperation(
  draft: WorkflowApiDesignDraft,
  operation: WorkflowApiDatabaseOperation
): WorkflowApiDesignDraft {
  return {
    ...draft,
    databaseOperation: operation,
    databaseQuery: operation === 'create' ? undefined : draft.databaseQuery,
    databaseWrites: operation === 'create' || operation === 'update' ? draft.databaseWrites || [] : [],
    fieldMappings: draft.fieldMappings.map((mapping) => {
      if (mapping.mappingType !== 'source_mapping') return mapping
      const endpoint = mapping.endpointField
      if (endpoint.side !== 'request') return mapping
      const sourceFields: WorkflowApiSourceField[] = mapping.sourceFields.filter((source) => source.sourceType !== 'database')
      return {
        ...mapping,
        sourceFields
      }
    }).map((mapping) => mapping.mappingType === 'source_mapping' && !mapping.sourceFields.length
      ? { endpointField: mapping.endpointField, mappingType: 'unconfigured' as const }
      : mapping)
  }
}

/** 为来源对象生成稳定身份，显示名称变化不影响选中项。 */
export function selectionKey(value?: BindingSelection | null): string {
  return !value ? '' : value.sourceType === 'database'
    ? JSON.stringify([value.sourceType, value.sourceId, value.schema, value.table])
    : JSON.stringify([value.sourceType, value.sourceId, value.directoryId, value.operationId])
}

/** 从现有正式字段映射恢复单一来源，复杂映射由既有编辑器承担。 */
export function inferSelection(draft: WorkflowApiDesignDraft): { selection: BindingSelection | null; complex: boolean } {
  const selections = new Map<string, BindingSelection>()
  let complex = false
  for (const mapping of draft.fieldMappings) {
    if (mapping.mappingType === 'unconfigured') continue
    if (mapping.mappingType !== 'source_mapping' || mapping.processingType !== 'direct' || mapping.sourceFields.length !== 1) { complex = true; continue }
    for (const field of mapping.sourceFields) {
      const target: BindingSelection = field.sourceType === 'database'
        ? { sourceType: 'database', sourceId: field.sourceId, schema: field.schema || '', table: field.table }
        : { sourceType: 'external_api', sourceId: field.sourceId, directoryId: field.directoryId, operationId: field.operationId }
      selections.set(selectionKey(target), target)
    }
  }
  // 仅有查询条件时也要恢复数据库表选择。
  for (const condition of draft.databaseQuery?.items.flatMap((item) => item.kind === 'group' ? item.items : [item]) || []) {
    if (condition.sourceType !== 'database') continue
    const target: BindingSelection = {
      sourceType: 'database', sourceId: condition.sourceId, schema: condition.schema, table: condition.table
    }
    selections.set(selectionKey(target), target)
  }
  for (const write of draft.databaseWrites || []) {
    if (write.sourceType !== 'database' || !write.sourceId || !write.schema || !write.table) continue
    const target: BindingSelection = {
      sourceType: 'database', sourceId: write.sourceId, schema: write.schema, table: write.table
    }
    selections.set(selectionKey(target), target)
  }
  for (const item of draft.externalApiFixedValues || []) {
    const field = item.externalField
    if (field.sourceType !== 'external_api' || !field.sourceId || !field.directoryId || !field.operationId) continue
    const target: BindingSelection = { sourceType: 'external_api', sourceId: field.sourceId, directoryId: field.directoryId, operationId: field.operationId }
    selections.set(selectionKey(target), target)
  }
  return { selection: selections.size === 1 ? [...selections.values()][0] : null, complex: complex || selections.size > 1 }
}

/** 判断数据库来源是否仍在应用已添加清单中。 */
export function tableIsSelected(selection: BindingSelection, tables: SelectedDataTable[]): boolean {
  return selection.sourceType !== 'database' || tables.some((table) => selectionKey({ ...table, sourceType: 'database' }) === selectionKey(selection))
}

/** 根据字段方向生成单一来源的字段候选，不推测转换逻辑。 */
export function mappingCandidates(field: WorkflowApiField, selection: BindingSelection,
  metadata: ApiDesignDatabaseMetadata | ApiDesignExternalOperationMetadata): WorkflowApiSourceField[] {
  if (selection.sourceType === 'database') return ((metadata as ApiDesignDatabaseMetadata).columns || []).map((column) => ({
    ...selection, column: column.name, type: column.type, description: column.description, usage: defaultDatabaseUsageForOperation(field)
  }))
  return ((metadata as ApiDesignExternalOperationMetadata).fields || [])
    .filter((item) => field.side === 'response' ? item.section === 'response_body' : item.section !== 'response_body')
    .map((item) => ({ ...selection, section: item.section as WorkflowApiField['location'], path: item.path, type: item.type, description: item.description }))
}

/** 字段身份不使用类型和说明，避免元数据展示变化导致选中项丢失。 */
export function sourceFieldKey(field: WorkflowApiSourceField): string {
  return field.sourceType === 'database' ? field.column : `${field.section}:${field.path}`
}

/** 替换一条直接映射，保留其他字段和现有实现说明。 */
export function setDirectMapping(draft: WorkflowApiDesignDraft, field: WorkflowApiField, source?: WorkflowApiSourceField): WorkflowApiDesignDraft {
  const next = source
    ? { endpointField: endpointFieldSnapshot(field), mappingType: 'source_mapping' as const, processingType: 'direct' as const, sourceFields: [source] }
    : { endpointField: endpointFieldSnapshot(field), mappingType: 'unconfigured' as const }
  return { ...draft, fieldMappings: [...draft.fieldMappings.filter((item) => apiDesignFieldKey(item.endpointField) !== apiDesignFieldKey(field)), next] }
}
