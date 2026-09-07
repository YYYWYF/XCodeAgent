import fs from 'node:fs/promises'
import path from 'node:path'
import { createHash } from 'node:crypto'

export const PRODUCT_PLAN_SCHEMA_VERSION = 'product-plan.v5'
export const ENDPOINT_API_DESIGN_SCHEMA_VERSION = 'endpoint-field-mapping.v1'

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

/** 校验正式产物中的场景实体是否完整匹配当前 Contract 的 TechnicalPlan 模板。 */
function endpointSceneEntitiesMatchTechnicalPlan(
  design: Record<string, unknown>,
  technicalPlan: Record<string, unknown>,
  apiContractId: string
): boolean {
  const entities = Array.isArray(design.sceneEntities) ? design.sceneEntities : []
  if (entities.length === 0) return true
  const contract = (Array.isArray(technicalPlan.api_contracts) ? technicalPlan.api_contracts : [])
    .find((item): item is Record<string, unknown> =>
      Boolean(item && typeof item === 'object' && String(item.id || '') === apiContractId)
    )
  if (!contract) return false
  const templateIds = new Set(
    (Array.isArray(contract.entity_ids) ? contract.entity_ids : []).map(String)
  )
  const templates = new Map(
    (Array.isArray(technicalPlan.entities) ? technicalPlan.entities : [])
      .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
      .map((item) => [String(item.id || ''), item])
  )
  const usedTemplateIds = new Set<string>()
  return entities.every((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return false
    const entity = item as Record<string, unknown>
    const templateId = String(entity.templateEntityId || '')
    const template = templates.get(templateId)
    if (!template || !templateIds.has(templateId) || usedTemplateIds.has(templateId)) return false
    usedTemplateIds.add(templateId)
    const normalizeFields = (value: unknown): Array<Record<string, unknown>> =>
      (Array.isArray(value) ? value : []).map((field) => {
        const source = field && typeof field === 'object' && !Array.isArray(field)
          ? field as Record<string, unknown>
          : {}
        const name = String(source.name || source.path || '')
        return {
          name,
          label: String(source.label || name),
          type: String(source.type || 'unknown'),
          required: Boolean(source.required),
          description: String(source.description || '')
        }
      })
    return String(entity.name || '') === String(template.name || templateId) &&
      String(entity.description || '') === String(template.description || '') &&
      JSON.stringify(normalizeFields(entity.fields)) === JSON.stringify(normalizeFields(template.fields))
  })
}

/** 校验当前版映射中的一句话业务说明，避免手工残缺产物误判为已确认。 */
function endpointFieldMappingsMatchCurrentContract(design: Record<string, unknown>): boolean {
  /** 把未知输入收敛为普通对象。 */
  const record = (value: unknown): Record<string, unknown> | null =>
    Boolean(value && typeof value === 'object' && !Array.isArray(value))
      ? value as Record<string, unknown>
      : null

  const entities = new Map(
    (Array.isArray(design.sceneEntities) ? design.sceneEntities : [])
      .map(record)
      .filter((item): item is Record<string, unknown> => Boolean(item))
      .map((entity) => [String(entity.id || ''), entity])
  )
  const mappings = Array.isArray(design.fieldMappings) ? design.fieldMappings : []
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
    if (mappingType === 'unconfigured') {
      if (endpoint.required === true) return false
      continue
    }
    if (mappingType === 'business_description') {
      if (!String(mapping.businessDescription || '').trim()) return false
      continue
    }
    const sourceField = record(mapping.sourceField)
    if (mappingType === 'direct_source' && !sourceField) return false
    if (mappingType === 'through_entity') {
      const entityField = record(mapping.entityField)
      const entity = entities.get(String(entityField?.entityId || ''))
      const fields = Array.isArray(entity?.fields) ? entity.fields.map(record) : []
      if (!entityField || !fields.some((field) =>
        field &&
        String(field.id || '') === String(entityField.fieldId || '') &&
        String(field.name || '') === String(entityField.path || '') &&
        String(field.type || 'unknown') === String(entityField.type || 'unknown')
      )) return false
    } else if (mappingType !== 'direct_source') {
      return false
    }
    if (sourceField && !['database', 'external_api'].includes(String(sourceField.sourceType || ''))) {
      return false
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
    const technicalPlanObject = JSON.parse(technicalPlan.toString('utf8')) as Record<string, unknown>
    const basedOn = Array.isArray(design.basedOn)
      ? design.basedOn.filter((item): item is Record<string, unknown> =>
          Boolean(item && typeof item === 'object')
        )
      : []
    const technicalReference = basedOn.find(
      (item) => item.artifactKey === 'technical-plan'
    )
    const technicalSha256 = createHash('sha256').update(technicalPlan).digest('hex')
    const sceneEntitiesValid = endpointSceneEntitiesMatchTechnicalPlan(
      design,
      technicalPlanObject,
      apiContractId
    )
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
      Array.isArray(design.sceneEntities) &&
      Array.isArray(design.fieldMappings) &&
      implementationDescriptionValid &&
      sceneEntitiesValid &&
      mappingsValid &&
      technicalReference?.sha256 === technicalSha256
    if (!sceneEntitiesValid) {
      return {
        designed: false,
        status: 'stale',
        reason: '场景实体已不符合当前 TechnicalPlan 模板，请重新设计 API。'
      }
    }
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
