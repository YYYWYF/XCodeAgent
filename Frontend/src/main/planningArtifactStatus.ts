import fs from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'

export const PRODUCT_PLAN_SCHEMA_VERSION = 'product-plan.v5'
export const ENDPOINT_API_DESIGN_SCHEMA_VERSION = 'endpoint-field-mapping.v3'

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
    '.xcodeagent',
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
    '.xcodeagent',
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

/** 校验当前版映射中的来源和多行业务处理，避免手工残缺产物误判为已确认。 */
function endpointFieldMappingsMatchCurrentContract(design: Record<string, unknown>): boolean {
  /** 把未知输入收敛为普通对象。 */
  const record = (value: unknown): Record<string, unknown> | null =>
    Boolean(value && typeof value === 'object' && !Array.isArray(value))
      ? value as Record<string, unknown>
      : null

  const mappings = Array.isArray(design.fieldMappings) ? design.fieldMappings : []
  if (mappings.length === 0) return false
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
        if (!String(sourceField.sourceId || '') || !String(sourceField.schema || '') ||
          !String(sourceField.table || '') || !String(sourceField.column || '')) return false
        const usage = String(sourceField.usage || '')
        if (endpoint.side === 'request' && !['filter', 'write'].includes(usage)) return false
        if (endpoint.side === 'response' && usage !== 'read') return false
      } else {
        if (!String(sourceField.sourceId || '') || !String(sourceField.directoryId || '') ||
          !String(sourceField.operationId || '') || !String(sourceField.section || '') ||
          !String(sourceField.path || '')) return false
        if (endpoint.side === 'request' && sourceField.section === 'response_body') return false
        if (endpoint.side === 'response' && sourceField.section !== 'response_body') return false
      }
    }
  }
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
      fs.readFile(path.join(workspaceRoot, '.xcodeagent', 'plans', 'technical-plan.json'))
    ])
    if (!markdown.trim()) {
      return { designed: false, status: 'stale', reason: 'API 设计 Markdown 为空。' }
    }
    const markdownRevision = markdown.match(/xcodeagent-artifact-revision:\s*([0-9a-f]{32})/)?.[1] || ''
    const design = JSON.parse(jsonText) as Record<string, unknown>
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
