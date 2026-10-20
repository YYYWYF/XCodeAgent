import type { JsonRecord } from './TechnicalPlanDocPanelData'
import {
  asRecord,
  parameterSummary,
  recordItems,
  stringItems,
  textValue
} from './TechnicalPlanDocPanelData'

export type { JsonRecord }

export { asRecord, recordItems, stringItems, textValue }

export type RequirementPageRow = {
  key: string
  pageId: string
  name: string
  path: string
  description: string
  goal: string
  informationItems: Array<{ key: string; label: string; description: string }>
  actions: Array<{
    key: string
    name: string
    description: string
    behaviorType: string
    expectedResult: string
    targetPageId: string
    requiresConfirmation: boolean
  }>
  navigationTargets: string[]
  allowedRoles: string[]
  stateRequirements: Array<{ key: string; label: string; description: string }>
  acceptanceCriteria: string[]
}

const STATE_REQUIREMENT_LABELS: Record<string, string> = {
  loading: '加载中',
  empty: '空状态',
  error: '异常',
  disabled: '禁用',
  success: '成功',
  validation: '校验'
}

/** 把产品规划页面拍平为右侧面板可直接渲染的行结构，缺失字段一律降级为空态文案。 */
export function requirementPageRows(
  productPlan: JsonRecord,
  requirementPages: JsonRecord[]
): RequirementPageRow[] {
  const specPageMap = new Map<string, JsonRecord>()
  requirementPages.forEach((page, index) => {
    const pageId = textValue(page.pageId, `page-${index + 1}`)
    if (pageId) specPageMap.set(pageId, page)
  })
  // 需求草稿阶段（澄清中/待确认）还没有 ProductPlan：退回用 spec 的页面清单渲染，
  // 避免结构化视图在只有需求数据时显示 0 个页面。
  const planPages = recordItems(productPlan.pages)
  const sourcePages = planPages.length ? planPages : requirementPages
  return sourcePages.map((page, index) => {
    const pageId = textValue(page.pageId, `page-${index + 1}`)
    const specPage = specPageMap.get(pageId) || {}
    const informationItems = recordItems(page.information_items).map((item, itemIndex) => ({
      key: textValue(item.itemId, `${pageId}-item-${itemIndex + 1}`),
      label: textValue(item.label, `信息 ${itemIndex + 1}`),
      description: textValue(item.description)
    }))
    const actions = recordItems(page.actions).map((action, actionIndex) => {
      const behavior = asRecord(action.behavior)
      return {
        key: textValue(action.actionId, `${pageId}-action-${actionIndex + 1}`),
        name: textValue(action.name, `操作 ${actionIndex + 1}`),
        description: textValue(action.description),
        behaviorType: textValue(behavior.type, 'operation'),
        expectedResult: textValue(behavior.expectedResult),
        targetPageId: textValue(behavior.targetPageId),
        requiresConfirmation: Boolean(action.requiresConfirmation)
      }
    })
    const stateRequirements = Object.entries(asRecord(page.state_requirements)).map(
      ([stateKey, description]) => ({
        key: stateKey,
        label: STATE_REQUIREMENT_LABELS[stateKey] || stateKey,
        description: textValue(description)
      })
    )
    return {
      key: pageId,
      pageId,
      name: textValue(page.name) || textValue(specPage.name, pageId),
      path: textValue(page.path) || textValue(specPage.path),
      description: textValue(page.description) || textValue(specPage.description),
      goal: textValue(page.goal),
      informationItems,
      actions,
      navigationTargets: stringItems(page.navigation_targets),
      allowedRoles: stringItems(page.allowed_roles),
      stateRequirements,
      acceptanceCriteria: stringItems(page.acceptance_criteria)
    }
  })
}

export type RequirementEntityRow = {
  key: string
  name: string
  id: string
  description: string
  fields: Array<{ key: string; label: string; description: string }>
}

/** 把需求实体拍平为卡片行，字段以中文说明为主。 */
export function requirementEntityRows(spec: JsonRecord): RequirementEntityRow[] {
  return recordItems(spec.entities).map((entity, index) => ({
    key: textValue(entity.id, `entity-${index + 1}`),
    name: textValue(entity.name, `实体 ${index + 1}`),
    id: textValue(entity.id),
    description: textValue(entity.description),
    fields: recordItems(entity.fields).map((field, fieldIndex) => ({
      key: `${textValue(entity.id, `entity-${index + 1}`)}-field-${fieldIndex + 1}`,
      label: textValue(field.label) || textValue(field.name, `字段 ${fieldIndex + 1}`),
      description: textValue(field.description)
    }))
  }))
}

export type RequirementFlowRow = {
  key: string
  name: string
  description: string
  steps: string[]
}

/** 把业务流程拍平为步骤列表行。 */
export function requirementFlowRows(spec: JsonRecord): RequirementFlowRow[] {
  return recordItems(spec.business_flows).map((flow, index) => ({
    key: textValue(flow.id, `flow-${index + 1}`),
    name: textValue(flow.name, `流程 ${index + 1}`),
    description: textValue(flow.description),
    steps: stringItems(flow.steps)
  }))
}

const MODULE_PRIORITY_LABELS: Record<string, string> = {
  must: '必须',
  should: '应有',
  could: '可有',
  wont: '不做'
}

/** 读取功能模块优先级的中文标签。 */
export function modulePriorityLabel(value: unknown): string {
  const priority = textValue(value).toLowerCase()
  return MODULE_PRIORITY_LABELS[priority] || priority || '必须'
}

/** 操作行为类型的中文标签，未知类型原样展示。 */
export function behaviorTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    navigation: '页面跳转',
    submit: '提交',
    mutation: '数据变更',
    operation: '操作'
  }
  return labels[value] || value || '操作'
}

// ===== 概览 tab 扩展：页面交互 / 页面布局 / 页面风格 / 接口请求与响应 =====
// 这四块信息在 RequirementSpec 里没有结构化字段，只在后续产物（ProductPlan / TechnicalPlan）
// 或 spec 自由文本里存在。以下拍平函数把已有后续产物里的数据聚合成概览可直接渲染的行结构；
// 首次需求草稿阶段后续产物尚未生成时返回空数组，由概览组件渲染空态文案。

export type RequirementInteractionActionRow = {
  key: string
  name: string
  behaviorType: string
  behaviorTypeLabel: string
  expectedResult: string
}

export type RequirementInteractionRow = {
  key: string
  pageId: string
  pageName: string
  actions: RequirementInteractionActionRow[]
  navigationTargets: string[]
}

/**
 * 按页面聚合 ProductPlan 各页的操作交互：每个页面一张卡，列出该页面有哪些操作、
 * 操作类型（页面跳转/提交/数据变更/操作）以及预期结果，让用户一眼看清每个页面能做什么。
 * navigation_targets 存的是 pageId，这里映射成页面中文名展示给用户。
 */
export function requirementInteractionRows(productPlan: JsonRecord): RequirementInteractionRow[] {
  const pages = recordItems(productPlan.pages)
  if (!pages.length) return []
  // pageId → 页面中文名，用于把 navigation_targets 里的 pageId 翻译成可读名称。
  const pageNameMap = new Map<string, string>()
  pages.forEach((page, index) => {
    const pageId = textValue(page.pageId, `page-${index + 1}`)
    pageNameMap.set(pageId, textValue(page.name, pageId))
  })
  return pages.map((page, index) => {
    const pageId = textValue(page.pageId, `page-${index + 1}`)
    const actions = recordItems(page.actions).map((action, actionIndex) => {
      const behavior = asRecord(action.behavior)
      const behaviorType = textValue(behavior.type, 'operation')
      return {
        key: textValue(action.actionId, `${pageId}-action-${actionIndex + 1}`),
        name: textValue(action.name, `操作 ${actionIndex + 1}`),
        behaviorType,
        behaviorTypeLabel: behaviorTypeLabel(behaviorType),
        expectedResult: textValue(behavior.expectedResult)
      }
    })
    return {
      key: pageId,
      pageId,
      pageName: textValue(page.name, pageId),
      actions,
      navigationTargets: stringItems(page.navigation_targets).map(
        (target) => pageNameMap.get(target) || target
      )
    }
  })
}

export type RequirementLayoutRow = {
  key: string
  pageId: string
  pageName: string
  goal: string
  informationItems: Array<{ key: string; label: string; description: string }>
}

/**
 * 按页面聚合 ProductPlan 各页的布局：每个页面一张卡，展示页面目标与实际包含的业务信息项
 * （如"订单号、客户、金额、状态"），让用户看清每个页面要呈现什么内容。
 */
export function requirementLayoutRows(productPlan: JsonRecord): RequirementLayoutRow[] {
  const pages = recordItems(productPlan.pages)
  if (!pages.length) return []
  return pages.map((page, index) => {
    const pageId = textValue(page.pageId, `page-${index + 1}`)
    const informationItems = recordItems(page.information_items).map((item, itemIndex) => ({
      key: textValue(item.itemId, `${pageId}-item-${itemIndex + 1}`),
      label: textValue(item.label, `信息 ${itemIndex + 1}`),
      description: textValue(item.description)
    }))
    return {
      key: pageId,
      pageId,
      pageName: textValue(page.name, pageId),
      goal: textValue(page.goal),
      informationItems
    }
  })
}

// 页面风格维度：从 spec 自由文本里轻量提取用户提及的风格描述。
// UiDesign 产物无独立风格字段（在 TSX code 里），只能从需求文本兜底。
// 按维度分组，每个维度配关键词列表与可读标签，避免把整句原文塞进 Tag 造成混乱。
const STYLE_DIMENSIONS: Array<{ key: string; label: string; keywords: string[] }> = [
  {
    key: 'tone',
    label: '色调',
    keywords: ['暗色', '深色', '亮色', '浅色', '夜间', '深色模式', '亮色模式']
  },
  {
    key: 'aesthetic',
    label: '视觉风格',
    keywords: ['简洁', '简约', '扁平', '圆角', '留白', '现代', '商务', '科技感']
  },
  {
    key: 'layout',
    label: '布局形态',
    keywords: ['卡片式', '列表式', '表单式', '表格', '看板', '仪表盘', 'dashboard', '左右分栏', '上下分栏']
  },
  {
    key: 'navigation',
    label: '导航结构',
    keywords: ['侧边栏导航', '顶部导航', '顶栏导航', '底部导航', '标签栏']
  },
  {
    key: 'device',
    label: '适配端',
    keywords: ['响应式', '移动端', '桌面端', '自适应']
  }
]

export type RequirementStyleHintRow = {
  key: string
  dimension: string
  dimensionLabel: string
  // 该维度下命中的关键词（去重），作为可读标签展示。
  labels: string[]
  // 用户原文中提及该维度的句子，作为来源说明（可空）。
  source: string
}

/**
 * 从 spec 的 source_request / app_info.summary / pages[].description 里按风格维度
 * 提取用户提及的视觉/布局/导航/端描述。每个命中的维度返回一行，labels 是简短标签，
 * source 是用户原文中第一句相关描述。避免把整句原文当 Tag 造成混乱，也避免把
 * "应用场景"等与风格无关的句子误匹配进来。
 */
export function requirementStyleHints(spec: JsonRecord): RequirementStyleHintRow[] {
  const sentences: string[] = []
  const pushText = (text: unknown) => {
    const value = textValue(text)
    if (!value) return
    value
      .split(/[。；;\n]/)
      .map((s) => s.trim())
      .filter(Boolean)
      .forEach((s) => sentences.push(s))
  }
  pushText(spec.source_request)
  pushText(asRecord(spec.app_info).summary)
  recordItems(spec.pages).forEach((page) => pushText(page.description))
  if (!sentences.length) return []
  const rows: RequirementStyleHintRow[] = []
  STYLE_DIMENSIONS.forEach((dimension) => {
    const matchedLabels = new Set<string>()
    const matchedSources: string[] = []
    sentences.forEach((sentence) => {
      const lower = sentence.toLowerCase()
      dimension.keywords.forEach((keyword) => {
        if (lower.includes(keyword.toLowerCase())) {
          matchedLabels.add(keyword)
          matchedSources.push(sentence)
        }
      })
    })
    if (matchedLabels.size) {
      rows.push({
        key: dimension.key,
        dimension: dimension.key,
        dimensionLabel: dimension.label,
        labels: Array.from(matchedLabels),
        source: matchedSources[0] || ''
      })
    }
  })
  return rows
}

export type RequirementApiContractRow = {
  key: string
  contractId: string
  contractName: string
  endpointId: string
  method: string
  path: string
  summary: string
  requestSchemaRef: string
  responseSchemaRef: string
  parameters: string
}

/** 遍历 TechnicalPlan.api_contracts，把每个 endpoint 拍平为接口请求/响应摘要行。 */
export function requirementApiContractRows(technicalPlan: JsonRecord): RequirementApiContractRow[] {
  const contracts = recordItems(technicalPlan.api_contracts)
  const rows: RequirementApiContractRow[] = []
  contracts.forEach((contract) => {
    const contractId = textValue(contract.id)
    const contractName = textValue(contract.name, contractId)
    recordItems(contract.endpoints).forEach((endpoint, endpointIndex) => {
      const endpointId = textValue(endpoint.id, `${contractId}-ep-${endpointIndex + 1}`)
      rows.push({
        key: endpointId,
        contractId,
        contractName,
        endpointId,
        method: textValue(endpoint.method, 'GET'),
        path: textValue(endpoint.path),
        summary: textValue(endpoint.summary),
        requestSchemaRef: textValue(endpoint.request_schema_ref),
        responseSchemaRef: textValue(endpoint.response_schema_ref),
        parameters: parameterSummary(endpoint)
      })
    })
  })
  return rows
}
