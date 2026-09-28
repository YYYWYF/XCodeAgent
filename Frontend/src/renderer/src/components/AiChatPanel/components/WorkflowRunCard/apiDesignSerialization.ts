import type {
  WorkflowApiDatabaseFieldNode,
  WorkflowApiDatabaseCondition,
  WorkflowApiDatabaseConditionOperator,
  WorkflowApiDatabaseQuery,
  WorkflowApiDatabaseWriteDraft,
  WorkflowApiDatabaseOperation,
  WorkflowApiFilterOperator,
  WorkflowApiDesignAction,
  WorkflowApiDesignDraft,
  WorkflowApiDesignPayload,
  WorkflowApiEndpointFieldSnapshot,
  WorkflowApiExternalFieldNode,
  WorkflowApiField,
  WorkflowApiFieldMapping,
  WorkflowApiSourceField,
  WorkflowApiValueRight
} from '../../../../typings'

import { valueSummary } from '../FieldMapping/valueRules'

export type ApiDesignValidationErrors = Record<string, string>
export type ApiDatabaseUsage = NonNullable<WorkflowApiDatabaseFieldNode['usage']>
export const FILTER_OPERATORS: WorkflowApiFilterOperator[] = [
  'eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'not_contains',
  'starts_with', 'ends_with', 'in', 'not_in', 'between', 'not_between'
]

/** 按数据库列类型返回固定条件允许的运算符。 */
export function allowedDatabaseConditionOperators(columnType: string): WorkflowApiDatabaseConditionOperator[] {
  const family = typeFamily(columnType)
  const operators: WorkflowApiDatabaseConditionOperator[] = ['eq', 'ne']
  if (family === 'number' || family === 'temporal') operators.push('gt', 'gte', 'lt', 'lte', 'between', 'not_between', 'in', 'not_in')
  if (family === 'string') operators.push('contains', 'not_contains', 'starts_with', 'ends_with', 'in', 'not_in')
  operators.push('is_null', 'is_not_null')
  return operators
}

/** 校验固定条件值与列类型、集合长度及区间顺序是否一致。 */
export function databaseConditionValueValid(condition: WorkflowApiDatabaseCondition): boolean {
  const { operator, right } = condition
  if (!allowedDatabaseConditionOperators(condition.type).includes(operator)) return false
  if (operator === 'is_null' || operator === 'is_not_null') return right === undefined
  if (!right) return false
  if (right.kind === 'business') return valueRightValid(right, condition.type, true, operator)
  if (right.kind === 'endpoint') return Boolean(right.endpointField && queryParameterCompatible(right.endpointField.type, condition.type, operator))
  const value = right.value
  const family = typeFamily(condition.type)
  const scalarValid = (item: unknown): boolean => {
    if (family === 'number') return typeof item === 'number' && Number.isFinite(item)
    if (family === 'boolean') return typeof item === 'boolean'
    if (family === 'string' || family === 'temporal') return typeof item === 'string' && item.trim().length > 0
    return item !== undefined && item !== null && !Array.isArray(item)
  }
  if (operator === 'in' || operator === 'not_in') return Array.isArray(value) && value.length > 0 && value.every(scalarValid)
  if (operator === 'between' || operator === 'not_between') {
    if (!Array.isArray(value) || value.length !== 2 || !value.every(scalarValid)) return false
    return family === 'number'
      ? Number(value[0]) <= Number(value[1])
      : String(value[0]) <= String(value[1])
  }
  return scalarValid(value)
}

/** 按 HTTP 方法推导数据库操作；未知方法必须由用户显式选择。 */
export function defaultDatabaseOperation(method?: string): WorkflowApiDatabaseOperation | undefined {
  switch (String(method || '').toUpperCase()) {
    case 'POST': return 'create'
    case 'GET':
    case 'HEAD': return 'read'
    case 'PUT':
    case 'PATCH': return 'update'
    case 'DELETE': return 'delete'
    default: return undefined
  }
}

type DatabaseSourceIdentity = Pick<
  WorkflowApiDatabaseFieldNode,
  'sourceType' | 'sourceId' | 'schema' | 'table' | 'column' | 'type' | 'usage'
>

/** 根据 Endpoint 字段位置给数据库来源选择默认用途。 */
export function defaultDatabaseUsage(field: WorkflowApiField): ApiDatabaseUsage {
  if (field.side === 'response') return 'read'
  return 'write'
}

/** 根据单一 CRUD 操作和字段位置计算请求字段默认用途。 */
export function defaultDatabaseUsageForOperation(
  field: Pick<WorkflowApiField, 'side' | 'location'>,
  operation?: WorkflowApiDatabaseOperation
): ApiDatabaseUsage {
  if (field.side === 'response') return 'read'
  if (operation === 'create') return 'write'
  return 'write'
}

/** 返回单一 CRUD 操作允许的请求字段用途。 */
export function allowedDatabaseUsagesForOperation(
  field: Pick<WorkflowApiField, 'side' | 'location'>,
  operation?: WorkflowApiDatabaseOperation
): ApiDatabaseUsage[] {
  if (field.side === 'response') return ['read']
  if (operation === 'create') return ['write']
  if (operation === 'read' || operation === 'delete') return []
  return ['write']
}

/** 将完整编辑器的数据库用途恢复到当前 CRUD 操作允许的范围。 */
export function resolveDatabaseUsageForOperation(
  field: WorkflowApiField,
  requested: ApiDatabaseUsage | null | undefined,
  operation?: WorkflowApiDatabaseOperation
): ApiDatabaseUsage {
  const allowed = allowedDatabaseUsagesForOperation(field, operation)
  return requested && allowed.includes(requested)
    ? requested
    : defaultDatabaseUsageForOperation(field, operation)
}

/** 返回适合 Endpoint 与数据库列类型的查询运算符。 */
export function allowedFilterOperators(endpointType: string, sourceType = ''): WorkflowApiFilterOperator[] {
  const endpointFamily = typeFamily(endpointType)
  const sourceFamily = typeFamily(sourceType)
  const valueFamily = endpointFamily === 'array' ? arrayElementFamily(endpointType) : endpointFamily
  // JSON 日期时间以 string 传输；查询语义仍由数据库时间列决定。
  const family = sourceFamily === 'temporal' && valueFamily === 'string'
    ? sourceFamily
    : valueFamily === 'unknown' ? sourceFamily : valueFamily
  const result: WorkflowApiFilterOperator[] = ['eq', 'ne']
  if (family === 'number' || family === 'temporal') result.push('gt', 'gte', 'lt', 'lte')
  if (family === 'string') result.push('contains', 'not_contains', 'starts_with', 'ends_with')
  if (endpointFamily === 'array' && sourceFamily !== 'array') {
    result.push('in', 'not_in')
    if (family === 'number' || family === 'temporal') result.push('between', 'not_between')
  }
  return result
}

/** 校验接口参数作为查询右值时的元素类型和运算符。 */
export function queryParameterCompatible(endpointType: string, columnType: string, operator: WorkflowApiDatabaseConditionOperator): boolean {
  if (operator === 'is_null' || operator === 'is_not_null') return false
  if (!allowedFilterOperators(endpointType, columnType).some((candidate) => candidate === operator)) return false
  const endpointFamily = typeFamily(endpointType)
  const valueFamily = endpointFamily === 'array' ? arrayElementFamily(endpointType) : endpointFamily
  const columnFamily = typeFamily(columnType)
  if (endpointFamily === 'array' && !['in', 'not_in', 'between', 'not_between'].includes(operator)) return false
  if (['in', 'not_in', 'between', 'not_between'].includes(operator) && endpointFamily !== 'array') return false
  return valueFamily === 'unknown' || columnFamily === 'unknown' || valueFamily === columnFamily
    || (valueFamily === 'string' && columnFamily === 'temporal')
}

/** 从前端数组类型中提取元素类型族，供集合和区间运算符判断。 */
function arrayElementFamily(value: string): string {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized.endsWith('[]')) return typeFamily(normalized.slice(0, -2))
  const match = normalized.match(/^(?:array|list)<(.+)>$/)
  return match ? typeFamily(match[1]) : 'unknown'
}

/** 判断类型字符串的基础族，统一前端 CRUD 条件校验。 */
export function typeFamily(value: string): string {
  const normalized = String(value || '').trim().toLowerCase().split('(', 1)[0]
  if (['array', 'list', '[]'].some((token) => normalized.includes(token))) return 'array'
  if (['int', 'decimal', 'numeric', 'float', 'double', 'number'].some((token) => normalized.includes(token))) return 'number'
  if (['bool', 'bit'].some((token) => normalized.includes(token))) return 'boolean'
  if (['timestamp', 'datetime', 'date', 'time'].some((token) => normalized.includes(token))) return 'temporal'
  if (['object', 'json', 'map', 'record'].some((token) => normalized.includes(token))) return 'object'
  if (['char', 'text', 'string', 'uuid', 'enum'].some((token) => normalized.includes(token))) return 'string'
  return normalized || 'unknown'
}

/** 返回当前 Endpoint 方向允许选择的数据库用途。 */
export function allowedDatabaseUsages(field: WorkflowApiField): ApiDatabaseUsage[] {
  return field.side === 'response' ? ['read'] : ['write']
}

/** 将已有数据库字段用途恢复为当前 Endpoint 允许的用途。 */
export function resolveDatabaseUsage(
  field: WorkflowApiField,
  requested?: ApiDatabaseUsage | null
): ApiDatabaseUsage {
  const allowed = allowedDatabaseUsages(field)
  return requested && allowed.includes(requested) ? requested : defaultDatabaseUsage(field)
}

/** 从当前投影恢复数据源选择，优先保留仍存在的用户选择。 */
export function restoreSourceSelection(
  payload: WorkflowApiDesignPayload,
  currentSourceId = ''
): string {
  const available = new Set(
    (Array.isArray(payload.sources) ? payload.sources : [])
      .map((source) => String(source.id || ''))
      .filter(Boolean)
  )
  if (currentSourceId && available.has(currentSourceId)) return currentSourceId
  const loadedSourceId = String(
    payload.databaseMetadata?.sourceId || payload.externalOperation?.sourceId || ''
  )
  return loadedSourceId && available.has(loadedSourceId) ? loadedSourceId : ''
}

/** 为数据库字段生成包含用途的稳定身份键。 */
export function databaseSourceFieldKey(source: DatabaseSourceIdentity): string {
  return `${source.sourceType}:${source.sourceId}:${source.schema || ''}:${source.table}:${source.column}:${source.usage || 'read'}`
}

/** 为界面数据库候选生成本地稳定键；该字段不会写入正式产物。 */
export function databaseSourceFieldId(source: DatabaseSourceIdentity): string {
  return `source:database:${databaseSourceFieldKey(source)}`
}

/** 为 Endpoint 字段生成稳定的语义键。 */
export function apiDesignFieldKey(field: Pick<WorkflowApiField, 'side' | 'location' | 'path'>): string {
  return `${field.side}:${field.location}:${field.path}`
}

/** 移除界面字段身份，只保留正式映射需要的 Endpoint 字段事实。 */
export function endpointFieldSnapshot(field: WorkflowApiField): WorkflowApiEndpointFieldSnapshot {
  return {
    side: field.side,
    location: field.location,
    path: field.path,
    type: field.type,
    required: Boolean(field.required),
    description: field.description || ''
  }
}

/** 创建一个可选字段未配置记录。 */
export function createUnconfiguredFieldMapping(field: WorkflowApiField): WorkflowApiFieldMapping {
  return { endpointField: endpointFieldSnapshot(field), mappingType: 'unconfigured' }
}

/** 将候选来源字段裁剪为正式映射允许的内嵌结构。 */
export function sourceFieldSnapshot(
  source: WorkflowApiDatabaseFieldNode | WorkflowApiExternalFieldNode,
  endpoint?: WorkflowApiField
): WorkflowApiSourceField {
  if (source.sourceType === 'database') {
    const usage = endpoint ? resolveDatabaseUsage(endpoint, source.usage) : source.usage
    return {
      sourceType: 'database',
      sourceId: source.sourceId,
      schema: source.schema,
      table: source.table,
      column: source.column,
      type: source.type,
      usage,
      description: source.description
    }
  }
  return {
    sourceType: 'external_api',
    sourceId: source.sourceId,
    directoryId: source.directoryId,
    operationId: source.operationId,
    section: source.section,
    path: source.path,
    type: source.type,
    description: source.description
  }
}

/** 将后端草稿归一化为每个 Endpoint 字段恰好一条映射记录。 */
export function normalizeApiDesignDraft(
  payload: WorkflowApiDesignPayload
): WorkflowApiDesignDraft {
  const currentMappings = Array.isArray(payload.draft?.fieldMappings)
    ? payload.draft.fieldMappings
    : []
  const existing = new Map(
    currentMappings.map((mapping) => [apiDesignFieldKey(mapping.endpointField), mapping])
  )
  const endpointFields = Array.isArray(payload.endpointFields) ? payload.endpointFields : []
  return {
    apiContractId: String(payload.draft?.apiContractId || payload.endpoint?.apiContractId || ''),
    endpointId: String(payload.draft?.endpointId || payload.endpoint?.id || ''),
    implementationDescription: typeof payload.draft?.implementationDescription === 'string'
      ? payload.draft.implementationDescription
      : '',
    databaseOperation: payload.draft?.databaseOperation || undefined,
    databaseWrites: Array.isArray(payload.draft?.databaseWrites) ? payload.draft.databaseWrites : [],
    sourceBinding: payload.draft?.sourceBinding,
    externalApiBindings: Array.isArray(payload.draft?.externalApiBindings) ? payload.draft.externalApiBindings : [],
    databaseQuery: payload.draft?.databaseQuery || undefined,
    fieldMappings: endpointFields.map((field) => {
      const mapping = existing.get(apiDesignFieldKey(field))
      return mapping
        ? { ...mapping, endpointField: endpointFieldSnapshot(field) }
        : createUnconfiguredFieldMapping(field)
    })
  }
}

/** 查找一个 Endpoint 字段对应的唯一映射记录。 */
export function findFieldMapping(
  draft: WorkflowApiDesignDraft,
  field: Pick<WorkflowApiField, 'side' | 'location' | 'path'>
): WorkflowApiFieldMapping | undefined {
  const key = apiDesignFieldKey(field)
  return draft.fieldMappings.find((mapping) => apiDesignFieldKey(mapping.endpointField) === key)
}

/** 原子替换一个 Endpoint 字段的映射记录。 */
export function replaceFieldMapping(
  draft: WorkflowApiDesignDraft,
  mapping: WorkflowApiFieldMapping
): WorkflowApiDesignDraft {
  const key = apiDesignFieldKey(mapping.endpointField)
  return {
    ...draft,
    fieldMappings: draft.fieldMappings.map((current) =>
      apiDesignFieldKey(current.endpointField) === key ? mapping : current
    )
  }
}

/** 创建一个字段的纯业务说明映射。 */
export function createBusinessDescriptionMapping(
  field: WorkflowApiField,
  description: string
): WorkflowApiFieldMapping {
  return {
    endpointField: endpointFieldSnapshot(field),
    mappingType: 'business_description',
    businessDescription: description.trim()
  }
}

/** 校验固定标量是否符合目标字段的基础类型。 */
function fixedScalarValueValid(value: unknown, fieldType: string): boolean {
  const family = typeFamily(fieldType)
  if (family === 'number') return typeof value === 'number' && Number.isFinite(value)
  if (family === 'boolean') return typeof value === 'boolean'
  if (family === 'string' || family === 'temporal') return typeof value === 'string' && value.trim().length > 0
  if (family === 'array') return Array.isArray(value) && (arrayElementFamily(fieldType) === 'unknown' || value.every((item) => fixedScalarValueValid(item, arrayElementFamily(fieldType))))
  if (family === 'object') return value !== null && typeof value === 'object' && !Array.isArray(value)
  return value !== undefined && value !== null && !Array.isArray(value) && typeof value !== 'object'
}

/** 统一校验直接取值与业务规则，转换依赖的类型可不同于目标类型。 */
export function valueRightValid(right: WorkflowApiValueRight | undefined, type: string, database = false, operator?: WorkflowApiDatabaseConditionOperator): boolean {
  if (!right) return false
  if (right.kind === 'endpoint') return Boolean(right.endpointField?.side === 'request' && apiDesignTypesCompatible(right.endpointField.type, type, database))
  if (right.kind === 'fixed') return fixedScalarValueValid(right.value, type)
  if (!right.businessDescription.trim() || right.businessDescription.length > 2000) return false
  if (right.origin === 'endpoint' && !right.endpointFields.length || right.origin === 'builtin' && !right.builtinFields.length) return false
  if (right.endpointFields.some((field) => field.side !== 'request')) return false
  if (new Set(right.endpointFields.map(apiDesignFieldKey)).size !== right.endpointFields.length || new Set(right.builtinFields).size !== right.builtinFields.length) return false
  if (right.missingBehavior === 'default') {
    if (operator) return databaseConditionValueValid({ kind: 'condition', sourceType: 'database', sourceId: '', schema: '', table: '', column: '', type, operator, right: { kind: 'fixed', value: right.defaultValue } })
    return fixedScalarValueValid(right.defaultValue, type)
  }
  return right.defaultValue === undefined
}

/** 校验自包含字段映射的完整覆盖、来源方向和类型。 */
export function validateApiDesignDraft(draft: WorkflowApiDesignDraft): ApiDesignValidationErrors {
  const errors: ApiDesignValidationErrors = {}
  const implementationDescription = draft.implementationDescription
  if (implementationDescription !== undefined && typeof implementationDescription !== 'string') {
    errors.__implementationDescription = 'API 实现描述必须是文本。'
  } else if (typeof implementationDescription === 'string' && implementationDescription.trim().length > 4000) {
    errors.__implementationDescription = 'API 实现描述不能超过 4000 个字符。'
  }
  const keys = new Set<string>()
  const externalApiBindings = draft.externalApiBindings || []
  const hasExternalApiReference = draft.sourceBinding?.sourceType === 'external_api' || externalApiBindings.length > 0 || draft.fieldMappings.some((mapping) =>
    mapping.mappingType === 'source_mapping' && mapping.sourceFields.some((source) => source.sourceType === 'external_api'))
  if (externalApiBindings.length > 3000) errors.__externalApiBindings = '外部 API 入参取值最多 3000 项。'
  const bindingTargets = new Set<string>()
  externalApiBindings.forEach((item) => {
    const field = item.externalField
    const identity = JSON.stringify([field.sourceId, field.directoryId, field.operationId, field.section, field.path])
    const key = `__externalApiBinding:${field.section}:${field.path}`
    if (field.section === 'response_body' || !field.sourceId || !field.directoryId || !field.operationId || !field.path) errors[key] = '请选择有效的外部请求目标。'
    else if (bindingTargets.has(identity)) errors[key] = '同一外部请求目标不能重复配置。'
    else if (!valueRightValid(item.right, field.type)) errors[key] = '请完成取值配置，或添加明确的业务转换规则。'
    bindingTargets.add(identity)
  })
  const query = draft.databaseQuery
  const conditions = query?.items.flatMap((item) => item.kind === 'group' ? item.items : [item]) || []
  if (query && (!['and', 'or'].includes(query.join) || !query.items.length || query.items.some((item) => item.kind === 'group' && (!['and', 'or'].includes(item.join) || !item.items.length)) || conditions.length > 300)) {
    errors.__databaseQuery = '查询条件分组不能为空，且最多只能包含 300 条条件。'
  } else if (conditions.some((condition) => !condition.column || !databaseConditionValueValid(condition))) {
    errors.__databaseQuery = '请为每条查询条件选择数据库列、有效运算符和右值。'
  }
  const databaseSources = draft.fieldMappings.flatMap((mapping) => mapping.mappingType === 'source_mapping'
    ? mapping.sourceFields.filter((source): source is Extract<WorkflowApiSourceField, { sourceType: 'database' }> => source.sourceType === 'database')
    : [])
  const hasDatabase = databaseSources.length > 0 || conditions.length > 0
  const databaseWrites = draft.databaseWrites || []
  const hasDatabaseMapping = hasDatabase || databaseWrites.length > 0
  if (hasDatabaseMapping && !draft.databaseOperation) errors.__databaseOperation = '请选择数据库操作类型。'
  // 数据库表已选定但字段尚未开始映射时，允许先选择 CRUD 操作；正式确认仍由后端校验来源与操作成对出现。
  const operation = draft.databaseOperation
  const filters = conditions.length
  const validWrites = databaseWrites.filter((write) => Boolean(write.column) && valueRightValid(write.right, write.type, true)).length
  if (operation === 'create' && validWrites === 0) errors.__databaseOperation = '新增操作至少需要一个完整的写入字段。'
  if (operation === 'update' && (filters === 0 || validWrites === 0)) errors.__databaseOperation = '修改操作至少需要查询条件和完整的写入字段。'
  if (operation === 'delete' && filters === 0) errors.__databaseOperation = '删除操作至少需要一个查询条件。'
  if (operation === 'create' && conditions.length) errors.__databaseOperation = '新增操作不能包含查询条件。'
  if (databaseWrites.length && operation !== 'create' && operation !== 'update') errors.__databaseWrites = '只有新增和修改操作可以配置写入字段。'
  if (databaseWrites.length > 3000) errors.__databaseWrites = '写入字段最多 3000 项。'
  const writeColumns = new Set<string>()
  databaseWrites.forEach((write, index) => {
    const rowKey = `__databaseWrite:${index}`
    if (!write.column || !write.sourceId || !write.schema || !write.table) {
      errors[rowKey] = '请先选择当前数据表中的目标字段。'
      return
    }
    const identity = JSON.stringify([write.sourceId, write.schema, write.table, write.column])
    if (writeColumns.has(identity)) errors[rowKey] = `目标字段 ${write.table}.${write.column} 重复。`
    writeColumns.add(identity)
    if (!write.right) {
      errors[rowKey] = '请选择写入值来源。'
      return
    }
    if (write.right.kind === 'business') {
      if (!valueRightValid(write.right, write.type, true)) errors[rowKey] = '请完成业务取值规则。'
      return
    }
    if (write.right.kind === 'endpoint') {
      const endpointField = write.right.endpointField
      if (!endpointField || endpointField.side !== 'request' || !draft.fieldMappings.some((mapping) =>
        mapping.endpointField.side === 'request' && apiDesignFieldKey(mapping.endpointField) === apiDesignFieldKey(endpointField))) {
        errors[rowKey] = '请选择当前接口的请求参数。'
      } else if (!apiDesignTypesCompatible(endpointField.type, write.type, true)) {
        errors[rowKey] = '请求参数与目标数据库字段类型不兼容。'
      }
      return
    }
    if (!fixedScalarValueValid(write.right.value, write.type)) errors[rowKey] = '固定值与目标数据库字段类型不兼容。'
  })
  draft.fieldMappings.forEach((mapping) => {
    const key = apiDesignFieldKey(mapping.endpointField)
    if (keys.has(key)) {
      errors[key] = `Endpoint 字段映射重复：${mapping.endpointField.path}。`
      return
    }
    keys.add(key)
    if (mapping.mappingType === 'unconfigured') {
      if ((draft.databaseOperation || hasExternalApiReference) && mapping.endpointField.side === 'request') return
      errors[key] = 'Endpoint 字段尚未配置映射。'
      return
    }
    if (mapping.mappingType === 'value_mapping') {
      if (mapping.endpointField.side === 'response' && mapping.endpointField.required && mapping.right.kind === 'business' && mapping.right.missingBehavior === 'omit') errors[key] = '必填返回字段不能省略。'
      if (!valueRightValid(mapping.right, mapping.endpointField.type)) errors[key] = '请完成业务取值规则。'
      return
    }
    if (mapping.mappingType === 'business_description') {
      if ('sourceFields' in mapping || 'processingType' in mapping) {
        errors[key] = '纯业务说明不能携带真实来源或处理类型。'
        return
      }
      if (!mapping.businessDescription.trim() || mapping.businessDescription.length > 2000) errors[key] = '请输入不超过 2000 字符的业务说明。'
      return
    }
    const sources = mapping.sourceFields
    if (mapping.endpointField.side === 'response' && mapping.endpointField.required && mapping.missingBehavior === 'omit') errors[key] = '必填返回字段不能省略。'
    if (mapping.processingType !== 'direct' && !valueRightValid({ kind: 'business', origin: 'business', endpointFields: mapping.endpointFields || [], builtinFields: mapping.builtinFields || [], businessDescription: mapping.businessDescription || '', missingBehavior: mapping.missingBehavior || 'error', ...(mapping.defaultValue !== undefined ? { defaultValue: mapping.defaultValue } : {}) }, mapping.endpointField.type)) errors[key] = '请检查处理依赖、业务说明和缺值策略。'
    if (mapping.missingBehavior === 'default' && !fixedScalarValueValid(mapping.defaultValue, mapping.endpointField.type)) errors[key] = '默认值与返回字段类型不兼容。'
    const kind = mapping.processingType
    if (!['direct', 'single_field_description', 'multi_field_description'].includes(kind)) {
      errors[key] = '请选择处理类型。'
      return
    }
    if (!Array.isArray(sources) || sources.length > 100 || (kind === 'multi_field_description' ? sources.length < 2 : sources.length !== 1)) {
      errors[key] = kind === 'multi_field_description' ? '请选择 2 至 100 个不同来源。' : '请选择一个来源；请明确删除不保留的来源。'
      return
    }
    if (kind === 'direct' ? 'businessDescription' in mapping : !mapping.businessDescription?.trim() || mapping.businessDescription.length > 2000) {
      errors[key] = kind === 'direct' ? '直接映射不能携带业务处理内容。' : '请输入不超过 2000 字符的业务处理内容。'
    }
    const identities = sources.map(apiDesignSourceIdentity)
    if (new Set(identities).size !== identities.length) errors[key] = '字段映射包含重复来源。'
    for (const source of sources) {
      if (kind === 'direct' && !apiDesignTypesCompatible(mapping.endpointField.type, source.type, source.sourceType === 'database')) errors[key] = 'Endpoint 与数据源字段类型不兼容。'
      validateSourceDirection(mapping, source, errors, key, operation)
    }
  })
  const knownRequests = new Set(draft.fieldMappings.filter((item) => item.endpointField.side === 'request').map((item) => apiDesignFieldKey(item.endpointField)))
  if (conditions.some((condition) => condition.right?.kind === 'endpoint' && (!condition.right.endpointField || !knownRequests.has(apiDesignFieldKey(condition.right.endpointField))))) errors.__databaseQuery = '查询条件引用的接口参数不属于当前 Endpoint。'
  const values = [...conditions.map((item) => item.right), ...databaseWrites.map((item) => item.right), ...externalApiBindings.map((item) => item.right), ...draft.fieldMappings.flatMap((item) => item.mappingType === 'value_mapping' ? [item.right] : item.mappingType === 'source_mapping' ? (item.endpointFields || []).map((endpointField) => ({ kind: 'endpoint' as const, endpointField })) : [])]
  for (const value of values) {
    const dependencies = value?.kind === 'business' ? value.endpointFields : value?.kind === 'endpoint' && value.endpointField ? [value.endpointField] : []
    if (dependencies.some((field) => field.side !== 'request' || !knownRequests.has(apiDesignFieldKey(field)))) errors.__valueRules = '取值规则含有失效的接口参数依赖。'
  }
  return errors
}

/** 校验单条映射中的外部字段方向和数据库用途。 */
function validateSourceDirection(
  mapping: WorkflowApiFieldMapping,
  source: WorkflowApiSourceField,
  errors: ApiDesignValidationErrors,
  key: string,
  operation?: WorkflowApiDatabaseOperation
): void {
  const endpoint = mapping.endpointField
  if (source.sourceType === 'external_api') {
    if (endpoint.side === 'request' && source.section === 'response_body') {
      errors[key] = 'Request 字段不能映射外部 API 响应字段。'
    } else if (endpoint.side === 'response' && source.section !== 'response_body') {
      errors[key] = 'Response 字段只能映射外部 API 响应字段。'
    }
    return
  }
  const usage = source.usage || 'read'
  if (endpoint.side === 'request' && source.sourceType === 'database') {
    errors[key] = '数据库写入请在“写入字段”中选择目标列和值来源。'
    return
  }
  const allowed = allowedDatabaseUsagesForOperation(endpoint, operation)
  if (!allowed.includes(usage)) {
    errors[key] = `${operation || '当前操作'}不允许字段 ${source.table}.${source.column} 用于${endpoint.side === 'request' ? '写入' : '读取'}。`
  } else if (endpoint.side === 'request' && usage !== 'write') {
    errors[key] = `请求映射中的数据库字段 ${source.table}.${source.column} 只能用于写入。`
  } else if (endpoint.side === 'response' && usage !== 'read') {
    errors[key] = `响应映射中的数据库字段 ${source.table}.${source.column} 必须使用 read。`
  }
}

/** 判断 API 与来源类型是否兼容；仅数据库时间列可对应 JSON 字符串。 */
export function apiDesignTypesCompatible(left: string, right: string, databaseSource = false): boolean {
  const leftFamily = typeFamily(left)
  const rightFamily = typeFamily(right)
  return leftFamily === 'unknown' || rightFamily === 'unknown' || leftFamily === rightFamily
    || (databaseSource && leftFamily === 'string' && rightFamily === 'temporal')
}

/** 创建提交给 AG-UI 工作流的当前完整确认动作。 */
export function createApiDesignAction(
  draft: WorkflowApiDesignDraft,
  action: WorkflowApiDesignAction['action']
): WorkflowApiDesignAction {
  const fieldMappings = draft.fieldMappings.filter((mapping) => !(draft.databaseOperation && mapping.mappingType === 'unconfigured' && mapping.endpointField.side === 'request'))
  return { action, apiContractId: draft.apiContractId, endpointId: draft.endpointId, draft: { ...draft, fieldMappings } }
}

/** 把内嵌来源字段转换为简洁预览标签。 */
export function apiDesignSourceFieldLabel(source?: WorkflowApiSourceField): string {
  if (!source) return ''
  return source.sourceType === 'database'
    ? `${source.sourceId}.${source.table}.${source.column}`
    : `${source.sourceId}.${source.operationId}.${source.section}.${source.path}`
}

/** 把一条自包含字段映射转换为可读数据流。 */
export function apiDesignMappingPreview(mapping: WorkflowApiFieldMapping): string {
  const endpoint = `${mapping.endpointField.side}.${mapping.endpointField.location}.${mapping.endpointField.path}`
  if (mapping.mappingType === 'unconfigured') return `${endpoint}（未配置）`
  if (mapping.mappingType === 'value_mapping') return `${valueSummary(mapping.right)} → ${endpoint}`
  if (mapping.mappingType === 'business_description') {
    return `${endpoint} ⇒ 业务说明：${mapping.businessDescription}`
  }
  const source = mapping.sourceFields.map(apiDesignSourceFieldLabel).join(' + ')
  const middle = [source, mapping.businessDescription].filter(Boolean)
  return mapping.endpointField.side === 'request'
    ? [endpoint, ...middle].join(' → ')
    : [...middle.reverse(), endpoint].join(' → ')
}

/** 以括号保留查询条件树的组合语义。 */
export function databaseQueryPreview(query: WorkflowApiDatabaseQuery): string {
  return query.items.map((item) => {
    if (item.kind === 'group') return `(${databaseQueryPreview(item)})`
    const right = item.right ? valueSummary(item.right) : ''
    return `${item.table}.${item.column} ${item.operator}${right ? ` ${right}` : ''}`
  }).join(` ${query.join.toUpperCase()} `)
}

/** 把数据库写入列和值来源转换为简短预览。 */
export function databaseWritePreview(write: WorkflowApiDatabaseWriteDraft): string {
  const right = valueSummary(write.right)
  return `${right} → ${write.schema}.${write.table}.${write.column || '待选字段'}`
}

/** 返回草稿中所有已配置字段的可读数据流。 */
export function apiDesignMappingPreviews(draft: WorkflowApiDesignDraft): string[] {
  const operation = draft.databaseOperation ? [`数据库操作：${draft.databaseOperation}`] : []
  const query = draft.databaseQuery
  const conditions = query ? [`查询条件：${databaseQueryPreview(query)}`] : []
  const writes = (draft.databaseWrites || []).map((write) => `写入字段：${databaseWritePreview(write)}`)
  return [...operation, ...conditions, ...writes, ...draft.fieldMappings
    .filter((mapping) => mapping.mappingType !== 'unconfigured')
    .map(apiDesignMappingPreview)]
}

/** 为真实来源生成稳定身份，用于多来源去重。 */
export function apiDesignSourceIdentity(source: WorkflowApiSourceField): string {
  return JSON.stringify(source.sourceType === 'database'
    ? [source.sourceType, source.sourceId, source.schema, source.table, source.column, source.usage || 'read']
    : [source.sourceType, source.sourceId, source.directoryId, source.operationId, source.section, source.path])
}
