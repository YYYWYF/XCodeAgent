import type { BindingSelection } from '../../../../typings/endpointDesign'
import type { WorkflowApiDatabaseCondition, WorkflowApiDatabaseConditionOperator, WorkflowApiDatabaseOperation, WorkflowApiDesignDraft, WorkflowApiField, WorkflowApiSourceField } from '../../../../typings'
import type { ApiDesignDatabaseMetadata, ApiDesignExternalOperationMetadata, SelectedDataTable } from '../../../../service/dataSources'
import { apiDesignFieldKey, allowedDatabaseUsagesForOperation, defaultDatabaseUsageForOperation, defaultDatabaseOperation, endpointFieldSnapshot } from '../WorkflowRunCard/apiDesignSerialization'

export const DATABASE_OPERATION_LABELS: Record<WorkflowApiDatabaseOperation, string> = {
  create: '新增', read: '查询', update: '修改', delete: '删除'
}

/** 将 HTTP 方法和 Endpoint 字段转换为数据库操作的初始分区。 */
export function operationForEndpoint(method?: string): WorkflowApiDatabaseOperation | undefined {
  return defaultDatabaseOperation(method)
}

/** 改变 CRUD 操作后保留来源列并重排请求字段，避免跨操作残留用途。 */
export function resetDraftForDatabaseOperation(
  draft: WorkflowApiDesignDraft,
  operation: WorkflowApiDatabaseOperation
): WorkflowApiDesignDraft {
  return {
    ...draft,
    databaseOperation: operation,
    databaseConditions: operation === 'create' ? [] : (draft.databaseConditions || []),
    fieldMappings: draft.fieldMappings.map((mapping) => {
      if (mapping.mappingType !== 'source_mapping') return mapping
      const endpoint = mapping.endpointField
      if (endpoint.side !== 'request') return mapping
      const sourceFields = mapping.sourceFields.map((source) => {
        if (source.sourceType !== 'database') return source
        const usage = defaultDatabaseUsageForOperation(endpoint, operation)
        return { ...source, usage, filterOperator: usage === 'filter' ? 'eq' as const : undefined }
      })
      return {
        ...mapping,
        sourceFields
      }
    })
  }
}

/** 返回当前操作允许的用途选项，供紧凑行编辑器使用。 */
export function operationUsages(field: WorkflowApiField, operation?: WorkflowApiDatabaseOperation): Array<{ value: 'filter' | 'write'; label: string }> {
  return allowedDatabaseUsagesForOperation(field, operation)
    .filter((value): value is 'filter' | 'write' => value === 'filter' || value === 'write')
    .map((value) => ({ value, label: value === 'filter' ? '查询条件' : '写入字段' }))
}

/** 创建当前数据库表下的固定条件，空值运算符不保存无意义的固定值。 */
export function createDatabaseCondition(selection: BindingSelection, column: { name: string; type: string; description?: string }, operator: WorkflowApiDatabaseConditionOperator, value?: unknown): WorkflowApiDatabaseCondition {
  if (selection.sourceType !== 'database') throw new Error('固定数据库条件必须使用数据库表。')
  return {
    sourceType: 'database', sourceId: selection.sourceId, schema: selection.schema, table: selection.table,
    column: column.name, type: column.type, description: column.description, operator,
    value: operator === 'is_null' || operator === 'is_not_null' ? undefined : value
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
  // 仅有固定条件时也要恢复数据库表选择，避免重新打开工作台时丢失来源。
  for (const condition of draft.databaseConditions || []) {
    if (condition.sourceType !== 'database') continue
    const target: BindingSelection = {
      sourceType: 'database', sourceId: condition.sourceId, schema: condition.schema, table: condition.table
    }
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
    ...selection, column: column.name, type: column.type, description: column.description, usage: defaultDatabaseUsageForOperation(field), filterOperator: field.side === 'request' && field.location !== 'request_body' ? 'eq' : undefined
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
