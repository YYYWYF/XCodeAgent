import {
  AGENT_UI_TEMPLATE_MODULE,
  AGENT_UI_TEMPLATE_VERSION,
  type AgentUiCapability,
  type AgentUiContextItem,
  type AgentUiFeatureFlags,
  type AgentUiMockContent,
  type AgentUiTemplateConfig,
  type AgentSurfaceEvidence,
  type AgentSurfaceEvidenceInput,
  type FloatingPoint,
  type FloatingSize
} from './types'

/** 判断未知值是否为可读取的普通对象。 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** 读取配置中的必填非空字符串。 */
function requiredString(source: Record<string, unknown>, key: string): string {
  const value = source[key]
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`Agent UI 配置缺少字符串字段：${key}`)
  }
  return value.trim()
}

/** 拒绝配置对象中的缺失或越界字段，保持固定模板输入可审计。 */
function exactKeys(
  source: Record<string, unknown>,
  expected: readonly string[],
  key: string
): void {
  const actual = Object.keys(source).sort()
  const required = [...expected].sort()
  if (actual.length !== required.length || actual.some((item, index) => item !== required[index])) {
    throw new Error(`Agent UI 配置字段集合无效：${key}`)
  }
}

/** 读取由稳定 ID 和展示标签组成的配置数组。 */
function idLabelItems<T extends AgentUiCapability | AgentUiContextItem>(
  value: unknown,
  key: string,
  includeValue: boolean
): T[] {
  if (!Array.isArray(value)) throw new Error(`Agent UI 配置字段必须为数组：${key}`)
  const items = value.map((item, index) => {
    if (!isRecord(item)) throw new Error(`Agent UI 配置项必须为对象：${key}[${index}]`)
    exactKeys(item, includeValue ? ['id', 'label', 'value'] : ['id', 'label'], `${key}[${index}]`)
    const normalized: Record<string, string> = {
      id: requiredString(item, 'id'),
      label: requiredString(item, 'label')
    }
    if (includeValue) normalized.value = requiredString(item, 'value')
    return normalized as T
  })
  if (new Set(items.map((item) => item.id)).size !== items.length) {
    throw new Error(`Agent UI 配置字段含重复 id：${key}`)
  }
  return items
}

/** 读取字符串数组并拒绝空值和重复项。 */
function stringItems(value: unknown, key: string): string[] {
  if (!Array.isArray(value)) throw new Error(`Agent UI 配置字段必须为数组：${key}`)
  const items = value.map((item, index) => {
    if (typeof item !== 'string' || !item.trim()) {
      throw new Error(`Agent UI 配置项必须为非空字符串：${key}[${index}]`)
    }
    return item.trim()
  })
  if (new Set(items).size !== items.length) throw new Error(`Agent UI 配置字段含重复项：${key}`)
  return items
}

/** 读取固定功能开关，禁止缺失或非布尔值。 */
function featureFlags(value: unknown): AgentUiFeatureFlags {
  if (!isRecord(value)) throw new Error('Agent UI 配置字段必须为对象：features')
  exactKeys(value, ['attachments', 'approvals', 'tools', 'maximize'], 'features')
  const result = {} as AgentUiFeatureFlags
  for (const key of ['attachments', 'approvals', 'tools', 'maximize'] as const) {
    if (typeof value[key] !== 'boolean') throw new Error(`Agent UI 功能开关必须为布尔值：${key}`)
    result[key] = value[key]
  }
  return result
}

/** 读取固定 Mock 文案集合。 */
function mockContent(value: unknown): AgentUiMockContent {
  if (!isRecord(value)) throw new Error('Agent UI 配置字段必须为对象：mock')
  exactKeys(
    value,
    [
      'userMessage',
      'assistantMessage',
      'toolTitle',
      'toolDetail',
      'approvalTitle',
      'approvalDetail',
      'successMessage',
      'errorMessage'
    ],
    'mock'
  )
  return {
    userMessage: requiredString(value, 'userMessage'),
    assistantMessage: requiredString(value, 'assistantMessage'),
    toolTitle: requiredString(value, 'toolTitle'),
    toolDetail: requiredString(value, 'toolDetail'),
    approvalTitle: requiredString(value, 'approvalTitle'),
    approvalDetail: requiredString(value, 'approvalDetail'),
    successMessage: requiredString(value, 'successMessage'),
    errorMessage: requiredString(value, 'errorMessage')
  }
}

/** 解析并严格验证设计稿传入固定 Agent UI 的 JSON 配置。 */
export function parseAgentUiTemplateConfig(configJson: string): AgentUiTemplateConfig {
  let parsed: unknown
  try {
    parsed = JSON.parse(configJson)
  } catch {
    throw new Error('Agent UI 配置不是有效 JSON。')
  }
  if (!isRecord(parsed)) throw new Error('Agent UI 配置根节点必须为对象。')
  exactKeys(
    parsed,
    [
      'templateVersion',
      'agentId',
      'surface',
      'name',
      'responsibility',
      'actionId',
      'contextItems',
      'capabilities',
      'suggestedQuestions',
      'features',
      'mock'
    ],
    'root'
  )
  const templateVersion = requiredString(parsed, 'templateVersion')
  if (templateVersion !== AGENT_UI_TEMPLATE_VERSION) {
    throw new Error(`Agent UI 模板版本必须为 ${AGENT_UI_TEMPLATE_VERSION}。`)
  }
  const surface = requiredString(parsed, 'surface')
  if (surface !== 'standalone_page' && surface !== 'floating_panel') {
    throw new Error('Agent UI Surface 无效。')
  }
  return {
    templateVersion: AGENT_UI_TEMPLATE_VERSION,
    agentId: requiredString(parsed, 'agentId'),
    surface,
    name: requiredString(parsed, 'name'),
    responsibility: requiredString(parsed, 'responsibility'),
    actionId: requiredString(parsed, 'actionId'),
    contextItems: idLabelItems<AgentUiContextItem>(parsed.contextItems, 'contextItems', true),
    capabilities: idLabelItems<AgentUiCapability>(parsed.capabilities, 'capabilities', false),
    suggestedQuestions: stringItems(parsed.suggestedQuestions, 'suggestedQuestions'),
    features: featureFlags(parsed.features),
    mock: mockContent(parsed.mock)
  }
}

/** 构造固定 Agent UI 组件需要暴露的稳定模板证据。 */
export function buildAgentSurfaceEvidence(input: AgentSurfaceEvidenceInput): AgentSurfaceEvidence {
  return {
    ...input,
    templateModule: AGENT_UI_TEMPLATE_MODULE,
    templateVersion: AGENT_UI_TEMPLATE_VERSION
  }
}

/** 把浮动入口限制在视口安全区内，并吸附到最近的水平边缘。 */
export function clampAndSnapFloatingPosition(
  point: FloatingPoint,
  viewport: FloatingSize,
  element: FloatingSize,
  margin: number
): FloatingPoint {
  const minX = margin
  const maxX = Math.max(margin, viewport.width - element.width - margin)
  const minY = margin
  const maxY = Math.max(margin, viewport.height - element.height - margin)
  const clampedX = Math.min(Math.max(point.x, minX), maxX)
  const clampedY = Math.min(Math.max(point.y, minY), maxY)
  const leftDistance = Math.abs(clampedX - minX)
  const rightDistance = Math.abs(maxX - clampedX)
  return {
    x: leftDistance <= rightDistance ? minX : maxX,
    y: clampedY
  }
}
