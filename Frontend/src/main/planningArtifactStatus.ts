import fs from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { WORKSPACE_ARTIFACT_DIR_NAME } from './branding'

export const PRODUCT_PLAN_SCHEMA_VERSION = 'product-plan.v5'
export const ENDPOINT_API_DESIGN_SCHEMA_VERSION = 'endpoint-field-mapping.v6'

/** 把 endpoint 业务标识转换为与规划产物约定一致的安全文件名。 */
function endpointDocumentStem(apiContractId: string, endpointId: string): string {
  const normalized = `${apiContractId}--${endpointId}`
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '')
  return `endpoint--${normalized || 'unknown'}`
}

/** 返回 endpoint 用户可读设计文档的当前约定路径。 */
export function endpointDesignDocumentPath(
  workspaceRoot: string,
  apiContractId: string,
  endpointId: string
): string {
  return path.join(
    workspaceRoot,
    WORKSPACE_ARTIFACT_DIR_NAME,
    'plans',
    'endpoints',
    `${endpointDocumentStem(apiContractId, endpointId)}.md`
  )
}

/** 返回 endpoint 内部当前版设计 JSON 的规范路径。 */
export function endpointDesignJsonPath(
  workspaceRoot: string,
  apiContractId: string,
  endpointId: string
): string {
  return path.join(
    workspaceRoot,
    WORKSPACE_ARTIFACT_DIR_NAME,
    'plans',
    'endpoints',
    `${endpointDocumentStem(apiContractId, endpointId)}.json`
  )
}

export type EndpointDesignDocumentStatus = {
  designed: boolean
  status: 'pending' | 'confirmed' | 'stale'
  reason: string
}

/** 将当前 Endpoint 与数据库字段类型归一为运算符校验所需的类型族。 */
function statusTypeFamily(value: unknown): string {
  const normalized = String(value || '').toLowerCase().trim().split('(', 1)[0]
  if (['array', 'list', '[]'].some((token) => normalized.includes(token))) return 'array'
  if (['int', 'decimal', 'numeric', 'float', 'double', 'number'].some((token) => normalized.includes(token))) return 'number'
  if (['bool', 'bit'].some((token) => normalized.includes(token))) return 'boolean'
  if (['timestamp', 'datetime', 'date', 'time'].some((token) => normalized.includes(token))) return 'temporal'
  if (['char', 'text', 'string', 'uuid', 'enum'].some((token) => normalized.includes(token))) return 'string'
  return normalized || 'unknown'
}

/** 严格判断查询条件右值是否符合当前列类型。 */
function statusDatabaseConditionCompatible(item: Record<string, unknown>): boolean {
  const operator = String(item.operator || '')
  const family = statusTypeFamily(item.type)
  const allowed = new Set(['eq', 'ne', 'is_null', 'is_not_null'])
  if (family === 'number' || family === 'temporal') ['gt', 'gte', 'lt', 'lte', 'between', 'not_between', 'in', 'not_in'].forEach((value) => allowed.add(value))
  if (family === 'string') ['contains', 'not_contains', 'starts_with', 'ends_with', 'in', 'not_in'].forEach((value) => allowed.add(value))
  if (!allowed.has(operator)) return false
  const right = item.right && typeof item.right === 'object' && !Array.isArray(item.right) ? item.right as Record<string, unknown> : null
  if (operator === 'is_null' || operator === 'is_not_null') return !right
  if (!right) return false
  if (right.kind === 'endpoint') {
    const field = right.endpointField && typeof right.endpointField === 'object' ? right.endpointField as Record<string, unknown> : null
    return Boolean(field?.path && field.side === 'request' && statusOperatorCompatible(operator, field.type, item.type))
  }
  if (right.kind !== 'fixed') return false
  const value = right.value
  if (value === null || value === undefined) return false
  const scalarValid = (value: unknown): boolean => {
    if (family === 'number') return typeof value === 'number' && Number.isFinite(value)
    if (family === 'boolean') return typeof value === 'boolean'
    if (family === 'string' || family === 'temporal') return typeof value === 'string' && value.trim().length > 0
    return !Array.isArray(value) && typeof value !== 'object'
  }
  if (operator === 'in' || operator === 'not_in') return Array.isArray(value) && value.length > 0 && value.every(scalarValid)
  if (operator === 'between' || operator === 'not_between') {
    if (!Array.isArray(value) || value.length !== 2 || !value.every(scalarValid)) return false
    return family === 'number' ? Number(value[0]) <= Number(value[1]) : String(value[0]) <= String(value[1])
  }
  return scalarValid(value)
}

/** 判断接口参数与数据库查询运算符是否匹配。 */
function statusOperatorCompatible(operator: string, endpointType: unknown, sourceType: unknown): boolean {
  const endpointFamily = statusTypeFamily(endpointType)
  const sourceFamily = statusTypeFamily(sourceType)
  const valueFamily = endpointFamily === 'array'
    ? statusTypeFamily(String(endpointType).replace(/^\s*(?:array|list)<|>\s*$/gi, '').replace(/\[\]$/, ''))
    : endpointFamily
  if (['contains', 'not_contains', 'starts_with', 'ends_with'].includes(operator)) return valueFamily === 'string'
  if (['gt', 'gte', 'lt', 'lte'].includes(operator)) return valueFamily === 'number' || valueFamily === 'temporal'
  if (['between', 'not_between'].includes(operator)) return endpointFamily === 'array' && (valueFamily === 'number' || valueFamily === 'temporal') && sourceFamily !== 'array' && (sourceFamily === 'unknown' || sourceFamily === valueFamily)
  if (['in', 'not_in'].includes(operator)) return endpointFamily === 'array' && sourceFamily !== 'array' && (sourceFamily === 'unknown' || sourceFamily === valueFamily)
  return operator === 'eq' || operator === 'ne'
}

/** 校验当前版映射中的来源和多行业务处理，避免手工残缺产物误判为已确认。 */
function endpointFieldMappingsMatchCurrentContract(design: Record<string, unknown>): boolean {
  /** 把未知输入收敛为普通对象。 */
  const record = (value: unknown): Record<string, unknown> | null =>
    Boolean(value && typeof value === 'object' && !Array.isArray(value))
      ? value as Record<string, unknown>
      : null

  const mappings = Array.isArray(design.fieldMappings) ? design.fieldMappings : []
  const writes = Array.isArray(design.databaseWrites) ? design.databaseWrites : null
  if (!writes) return false
  if ('databaseConditions' in design) return false
  const operation = String(design.databaseOperation || '')
  const query = record(design.databaseQuery)
  const queryItems = query?.items
  if (design.databaseQuery !== undefined && (!query || !['and', 'or'].includes(String(query.join)) || !Array.isArray(queryItems) || !queryItems.length)) return false
  const conditions: Record<string, unknown>[] = []
  if (Array.isArray(queryItems)) for (const rawItem of queryItems) {
    const item = record(rawItem)
    if (!item) return false
    if (item.kind === 'condition') conditions.push(item)
    else if (item.kind === 'group' && ['and', 'or'].includes(String(item.join)) && Array.isArray(item.items) && item.items.length) {
      for (const rawChild of item.items) {
        const child = record(rawChild)
        if (!child || child.kind !== 'condition') return false
        conditions.push(child)
      }
    } else return false
  }
  let hasDatabaseSource = false
  const writeTargets = new Set<string>()
  const writeTables = new Set<string>()
  for (const rawWrite of writes) {
    const write = record(rawWrite)
    const right = record(write?.right)
    if (!write || write.sourceType !== 'database' || !write.sourceId || !write.schema || !write.table || !write.column || !write.type || !right) return false
    const target = JSON.stringify([write.sourceId, write.schema, write.table, write.column])
    if (writeTargets.has(target)) return false
    writeTargets.add(target)
    writeTables.add(JSON.stringify([write.sourceId, write.schema, write.table]))
    if (right.kind === 'endpoint') {
      const field = record(right.endpointField)
      if (!field || field.side !== 'request' || !field.location || !field.path || !field.type ||
        !['path', 'query', 'header', 'request_body'].includes(String(field.location))) return false
      const endpointType = statusTypeFamily(field.type)
      const columnType = statusTypeFamily(write.type)
      if (endpointType !== 'unknown' && columnType !== 'unknown' && endpointType !== columnType && !(endpointType === 'string' && columnType === 'temporal')) return false
    } else if (right.kind === 'fixed') {
      const value = right.value
      if (value === null || value === undefined || Array.isArray(value) || typeof value === 'object') return false
      const family = statusTypeFamily(write.type)
      if (family === 'number' && (typeof value !== 'number' || !Number.isFinite(value))) return false
      if (family === 'boolean' && typeof value !== 'boolean') return false
      if ((family === 'string' || family === 'temporal') && (typeof value !== 'string' || !value.trim())) return false
    } else return false
  }
  if (writeTables.size > 1) return false
  for (const condition of conditions) {
    const item = record(condition)
    if (item.sourceType !== 'database' || !item.sourceId || !item.schema || !item.table || !item.column || !statusDatabaseConditionCompatible(item)) return false
  }
  const keys = new Set<string>()
  for (const item of mappings) {
    const mapping = record(item)
    const endpoint = record(mapping?.endpointField)
    if (!mapping || !endpoint) return false
    const side = String(endpoint.side || '')
    const location = String(endpoint.location || '')
    const pathValue = String(endpoint.path || '')
    if (!['request', 'response'].includes(side) || !pathValue) return false
    if (!['path', 'query', 'header', 'request_body', 'response_body'].includes(location)) return false
    const key = `${side}:${location}:${pathValue}`
    if (keys.has(key)) return false
    keys.add(key)
    const mappingType = String(mapping.mappingType || '')
    if (mappingType === 'unconfigured') return false
    if (mappingType === 'business_description') {
      if (typeof mapping.businessDescription !== 'string' || !mapping.businessDescription.trim() || mapping.businessDescription.length > 2000) return false
      if ('sourceFields' in mapping || 'processingType' in mapping) return false
      continue
    }
    if (mappingType !== 'source_mapping') return false
    if ('sourceField' in mapping) return false
    const sources = Array.isArray(mapping.sourceFields) ? mapping.sourceFields : []
    const processing = mapping.processingType
    if (!['direct', 'single_field_description', 'multi_field_description'].includes(String(processing))) return false
    if (sources.length > 100 || (processing === 'multi_field_description' ? sources.length < 2 : sources.length !== 1)) return false
    if (processing === 'direct') {
      if ('businessDescription' in mapping) return false
    } else if (typeof mapping.businessDescription !== 'string' || !mapping.businessDescription.trim() || mapping.businessDescription.length > 2000) return false
    const sourceKeys = new Set<string>()
    for (const rawSource of sources) {
      const sourceField = record(rawSource)
      if (!sourceField || !['database', 'external_api'].includes(String(sourceField.sourceType || ''))) return false
      const fields = sourceField.sourceType === 'database' ? ['sourceType', 'sourceId', 'schema', 'table', 'column', 'usage'] : ['sourceType', 'sourceId', 'directoryId', 'operationId', 'section', 'path']
      const sourceKey = JSON.stringify(fields.map((field) => sourceField[field]))
      if (sourceKeys.has(sourceKey)) return false
      sourceKeys.add(sourceKey)
      if (sourceField.sourceType === 'database') {
        if ('filterOperator' in sourceField) return false
        if (!String(sourceField.sourceId || '') || !String(sourceField.schema || '') ||
          !String(sourceField.table || '') || !String(sourceField.column || '')) return false
        const usage = String(sourceField.usage || '')
        if (endpoint.side === 'request') return false
        if (endpoint.side === 'response' && usage !== 'read') return false
        hasDatabaseSource = true
      } else {
        if (!String(sourceField.sourceId || '') || !String(sourceField.directoryId || '') ||
          !String(sourceField.operationId || '') || !String(sourceField.section || '') ||
          !String(sourceField.path || '')) return false
        if (endpoint.side === 'request' && sourceField.section === 'response_body') return false
        if (endpoint.side === 'response' && sourceField.section !== 'response_body') return false
      }
    }
  }
  if (writes.length && !['create', 'update'].includes(operation)) return false
  if ((operation === 'create' || operation === 'update') && writes.length === 0) return false
  if (operation === 'update' && writeTables.size && conditions.length) {
    const queryTables = new Set(conditions.map((item) => JSON.stringify([item.sourceId, item.schema, item.table])))
    if (queryTables.size !== 1 || !queryTables.has([...writeTables][0])) return false
  }
  if ((conditions.length > 0 || hasDatabaseSource) && !['create', 'read', 'update', 'delete'].includes(operation)) return false
  if (operation === 'create' && conditions.length > 0) return false
  if (operation === 'update' && (conditions.length === 0 || writes.length === 0)) return false
  if (operation === 'delete' && conditions.length === 0) return false
  if (!conditions.length && !hasDatabaseSource && !writes.length && operation) return false
  return true
}


/** 判断一个正式产物文件是否存在，用于区分初始 pending 与残缺 stale。 */
async function endpointArtifactFileExists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath)
    return true
  } catch {
    return false
  }
}

/** 严格校验 API 设计双文件、确认状态、目标标识和当前 TechnicalPlan 指纹。 */
export async function endpointDesignDocumentStatus(
  workspaceRoot: string,
  apiContractId: string,
  endpointId: string
): Promise<EndpointDesignDocumentStatus> {
  const markdownPath = endpointDesignDocumentPath(workspaceRoot, apiContractId, endpointId)
  const jsonPath = endpointDesignJsonPath(workspaceRoot, apiContractId, endpointId)
  const [markdownExists, jsonExists] = await Promise.all([
    endpointArtifactFileExists(markdownPath),
    endpointArtifactFileExists(jsonPath)
  ])
  if (!markdownExists && !jsonExists) {
    return { designed: false, status: 'pending', reason: '缺少当前版 API 设计产物。' }
  }
  if (!markdownExists || !jsonExists) {
    return { designed: false, status: 'stale', reason: 'API 设计双文件不完整。' }
  }
  try {
    const [markdown, jsonText, technicalPlan] = await Promise.all([
      fs.readFile(markdownPath, 'utf8'),
      fs.readFile(jsonPath, 'utf8'),
      fs.readFile(path.join(workspaceRoot, WORKSPACE_ARTIFACT_DIR_NAME, 'plans', 'technical-plan.json'))
    ])
    if (!markdown.trim()) {
      return { designed: false, status: 'stale', reason: 'API 设计 Markdown 为空。' }
    }
    const markdownRevision = markdown.match(/devagentstudio-artifact-revision:\s*([0-9a-f]{32})/)?.[1] || ''
    const design = JSON.parse(jsonText) as Record<string, unknown>
    if (design.schemaVersion !== ENDPOINT_API_DESIGN_SCHEMA_VERSION) {
      return { designed: false, status: 'stale', reason: 'API 设计需重新配置。' }
    }
    const basedOn = Array.isArray(design.basedOn)
      ? design.basedOn.filter((item): item is Record<string, unknown> =>
          Boolean(item && typeof item === 'object')
        )
      : []
    const technicalReference = basedOn.find(
      (item) => item.artifactKey === 'technical-plan'
    )
    const technicalSha256 = createHash('sha256').update(technicalPlan).digest('hex')
    const mappingsValid = endpointFieldMappingsMatchCurrentContract(design)
    // Endpoint 实现描述是可选指导；只有存在时才校验文本类型和长度。
    const implementationDescription = design.implementationDescription
    const implementationDescriptionValid =
      implementationDescription === undefined ||
      implementationDescription === null ||
      (typeof implementationDescription === 'string' && implementationDescription.length <= 4000)
    const valid =
      design.schemaVersion === ENDPOINT_API_DESIGN_SCHEMA_VERSION &&
      design.artifactType === 'endpoint-field-mapping' &&
      design.status === 'confirmed' &&
      design.confirmationStatus === 'confirmed' &&
      design.apiContractId === apiContractId &&
      design.endpointId === endpointId &&
      typeof design.artifactRevision === 'string' &&
      /^[0-9a-f]{32}$/.test(design.artifactRevision) &&
      markdownRevision === design.artifactRevision &&
      typeof design.confirmedAt === 'string' &&
      !('nodes' in design) &&
      !('mappings' in design) &&
      !('sceneEntities' in design) &&
      Array.isArray(design.fieldMappings) &&
      implementationDescriptionValid &&
      mappingsValid &&
      technicalReference?.sha256 === technicalSha256
    return valid
      ? { designed: true, status: 'confirmed', reason: '' }
      : {
          designed: false,
          status: 'stale',
          reason: 'API 设计格式无效或 TechnicalPlan 已变化。'
        }
  } catch {
    return {
      designed: false,
      status: 'stale',
      reason: 'API 设计产物无法读取。'
    }
  }
}

/** 返回严格的当前版 Endpoint 设计有效性，供原有布尔调用点复用。 */
export async function endpointDesignDocumentExists(
  workspaceRoot: string,
  apiContractId: string,
  endpointId: string
): Promise<boolean> {
  return (await endpointDesignDocumentStatus(workspaceRoot, apiContractId, endpointId)).designed
}
