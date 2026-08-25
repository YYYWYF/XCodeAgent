import type { JsonRecord } from './TechnicalPlanDocPanelData'
import { asRecord, recordItems, stringItems, textValue } from './TechnicalPlanDocPanelData'

export type { JsonRecord }

export { asRecord, recordItems, stringItems, textValue }

export type RequirementPageRow = {
  key: string
  pageId: string
  name: string
  path: string
  moduleId: string
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
  return recordItems(productPlan.pages).map((page, index) => {
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
      moduleId: textValue(page.module_id) || textValue(specPage.module_id),
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
