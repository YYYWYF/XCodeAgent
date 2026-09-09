import type {
  WorkflowApiDatabaseFieldNode,
  WorkflowApiDesignAction,
  WorkflowApiDesignDraft,
  WorkflowApiDesignPayload,
  WorkflowApiEndpointFieldSnapshot,
  WorkflowApiExternalFieldNode,
  WorkflowApiField,
  WorkflowApiFieldMapping,
  WorkflowApiSourceField
} from '../../../../typings'

export type ApiDesignValidationErrors = Record<string, string>
export type ApiDatabaseUsage = NonNullable<WorkflowApiDatabaseFieldNode['usage']>

type DatabaseSourceIdentity = Pick<
  WorkflowApiDatabaseFieldNode,
  'sourceType' | 'sourceId' | 'schema' | 'table' | 'column' | 'type' | 'usage'
>

/** 根据 Endpoint 字段位置给数据库来源选择默认用途。 */
export function defaultDatabaseUsage(field: WorkflowApiField): ApiDatabaseUsage {
  if (field.side === 'response') return 'read'
  return field.location === 'request_body' ? 'write' : 'filter'
}

/** 返回当前 Endpoint 方向允许选择的数据库用途。 */
export function allowedDatabaseUsages(field: WorkflowApiField): ApiDatabaseUsage[] {
  return field.side === 'response' ? ['read'] : ['filter', 'write']
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
    return {
      sourceType: 'database',
      sourceId: source.sourceId,
      schema: source.schema,
      table: source.table,
      column: source.column,
      type: source.type,
      usage: endpoint ? resolveDatabaseUsage(endpoint, source.usage) : source.usage,
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
  draft.fieldMappings.forEach((mapping) => {
    const key = apiDesignFieldKey(mapping.endpointField)
    if (keys.has(key)) {
      errors[key] = `Endpoint 字段映射重复：${mapping.endpointField.path}。`
      return
    }
    keys.add(key)
    if (mapping.mappingType === 'unconfigured') {
      errors[key] = 'Endpoint 字段尚未配置映射。'
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
      if (kind === 'direct' && !apiDesignTypesCompatible(mapping.endpointField.type, source.type)) errors[key] = 'Endpoint 与数据源字段类型不兼容。'
      validateSourceDirection(mapping, source, errors, key)
    }
  })
  return errors
}

/** 校验单条映射中的外部字段方向和数据库用途。 */
function validateSourceDirection(
  mapping: WorkflowApiFieldMapping,
  source: WorkflowApiSourceField,
  errors: ApiDesignValidationErrors,
  key: string
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
  if (endpoint.side === 'request' && !['filter', 'write'].includes(usage)) {
    errors[key] = `请求映射中的数据库字段 ${source.table}.${source.column} 只能使用 filter 或 write。`
  } else if (endpoint.side === 'response' && usage !== 'read') {
    errors[key] = `响应映射中的数据库字段 ${source.table}.${source.column} 必须使用 read。`
  }
}

/** 判断 API、SQL 和外部 Schema 的字段类型是否兼容。 */
export function apiDesignTypesCompatible(left: string, right: string): boolean {
  const family = (value: string): string => {
    const normalized = String(value || '').trim().toLowerCase().split('(', 1)[0]
    if (['int', 'decimal', 'numeric', 'float', 'double', 'number'].some((token) => normalized.includes(token))) return 'number'
    if (['bool', 'bit'].some((token) => normalized.includes(token))) return 'boolean'
    if (['array', 'list', '[]'].some((token) => normalized.includes(token))) return 'array'
    if (['object', 'json', 'map', 'record'].some((token) => normalized.includes(token))) return 'object'
    // 日期、时间和 timestamp 在当前契约中作为字符串传输，需与后端类型族保持一致。
    if (['char', 'text', 'string', 'date', 'time', 'uuid', 'enum'].some((token) => normalized.includes(token))) return 'string'
    return normalized || 'unknown'
  }
  const leftFamily = family(left)
  const rightFamily = family(right)
  return leftFamily === 'unknown' || rightFamily === 'unknown' || leftFamily === rightFamily
}

/** 创建提交给 AG-UI 工作流的当前完整确认动作。 */
export function createApiDesignAction(
  draft: WorkflowApiDesignDraft,
  action: WorkflowApiDesignAction['action']
): WorkflowApiDesignAction {
  return { action, apiContractId: draft.apiContractId, endpointId: draft.endpointId, draft }
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
  if (mapping.mappingType === 'business_description') {
    return `${endpoint} ⇒ 业务说明：${mapping.businessDescription}`
  }
  const source = mapping.sourceFields.map(apiDesignSourceFieldLabel).join(" + ")
  const middle = [source, mapping.businessDescription].filter(Boolean)
  return mapping.endpointField.side === 'request'
    ? [endpoint, ...middle].join(' → ')
    : [...middle.reverse(), endpoint].join(' → ')
}

/** 返回草稿中所有已配置字段的可读数据流。 */
export function apiDesignMappingPreviews(draft: WorkflowApiDesignDraft): string[] {
  return draft.fieldMappings
    .filter((mapping) => mapping.mappingType !== 'unconfigured')
    .map(apiDesignMappingPreview)
}

/** 为真实来源生成稳定身份，用于多来源去重。 */
export function apiDesignSourceIdentity(source: WorkflowApiSourceField): string {
  return JSON.stringify(source.sourceType === 'database'
    ? [source.sourceType, source.sourceId, source.schema, source.table, source.column, source.usage || 'read']
    : [source.sourceType, source.sourceId, source.directoryId, source.operationId, source.section, source.path])
}
