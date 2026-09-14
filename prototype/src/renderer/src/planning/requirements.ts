import type { InitializationPlanningRecord } from './model'
import { invalidatePlanningArtifacts } from './state'
import { synchronizePlanningReferences } from './seed'
import { assertRequirementReady } from './requirementQuality'

export type RequirementFormDraft = { spec: Record<string, any>; productPlan: Record<string, any> }

/** 校验可编辑集合和稳定身份，错误草稿不能进入正式记录。 */
function validateItems(items: unknown, field: string, identity: string): Array<Record<string, any>> {
  if (!Array.isArray(items)) throw new Error(`${field}格式不正确。`)
  const ids = new Set<string>()
  for (const item of items) {
    if (!item || typeof item !== 'object' || !String(item[identity] || '').trim() || !String(item.name || '').trim()) throw new Error(`${field}需要填写名称。`)
    if (ids.has(item[identity])) throw new Error(`${field}存在重复标识。`)
    ids.add(item[identity])
  }
  return items
}

/** 保存原工程表单的业务事实，内部产品规划保持同一页面身份并同步 Markdown。 */
export function applyRequirementForm(current: InitializationPlanningRecord, draft: RequirementFormDraft): InitializationPlanningRecord {
  if (current.stage !== 'awaiting_requirement_document_confirmation') throw new Error('当前说明书已不在待确认状态，请重新打开最新草稿。')
  if (!draft?.spec || !draft.productPlan) throw new Error('缺少需求表单内容。')
  const spec = structuredClone(draft.spec)
  const appInfo = spec.app_info || {}
  if (!String(appInfo.name || '').trim() || !String(appInfo.target || appInfo.description || '').trim()) throw new Error('请填写应用名称和应用目标。')
  const pages = validateItems(spec.pages, '页面', 'pageId')
  if (!pages.length) throw new Error('请至少保留一个页面。')
  const paths = new Set<string>()
  for (const page of pages) {
    if (!/^\/(?!\/)[^\s?#]*$/.test(page.path || '')) throw new Error(`页面「${page.name}」的路由应以 / 开头，不能包含空格、查询参数或锚点。`)
    if (paths.has(page.path)) throw new Error(`页面路由 ${page.path} 重复。`)
    paths.add(page.path)
  }
  const roles = validateItems(spec.user_roles, '业务参与者', 'id')
  validateItems(spec.business_flows, '业务流程', 'id')
  const authorization = spec.authorization_requirements || { enabled: false }
  const authority = current.artifacts.requirementSpec.authorization_requirements || { enabled: false }
  authorization.enabled = authority.enabled === true
  if (authorization.enabled) {
    const roleIds = new Set(roles.map((role) => role.id))
    if (!roleIds.has(authorization.initialAdminRoleId)) throw new Error('请选择初始系统管理员角色。')
    for (const rule of [...(authorization.restrictedPages || []), ...(authorization.restrictedOperations || [])]) {
      if (!String(rule.name || '').trim() || !String(rule.description || '').trim()) throw new Error('请填写权限规则的名称和业务说明。')
      if (!Array.isArray(rule.defaultGrantedRoleIds) || !rule.defaultGrantedRoleIds.length || rule.defaultGrantedRoleIds.some((id: string) => !roleIds.has(id))) throw new Error('请为权限规则选择有效的首次默认授权角色。')
    }
    for (const rule of authorization.restrictedPages || []) {
      if (!pages.some((page) => page.pageId === rule.targetPageId)) throw new Error('请为受控页面选择对应页面。')
    }
  }
  spec.authorization_requirements = authorization
  spec.app_info = { ...appInfo, description: appInfo.target || appInfo.description, summary: appInfo.target || appInfo.description }
  const next = structuredClone(current)
  next.artifacts.requirementSpec = spec
  const product = structuredClone(draft.productPlan)
  const pageIds = new Set(pages.map((page) => page.pageId))
  product.pages = pages.map((page) => {
    const existing = (product.pages || []).find((item: any) => item.pageId === page.pageId) || {}
    return {
      information_items: [], actions: [], state_requirements: {}, acceptance_criteria: [],
      ...existing, pageId: page.pageId, name: page.name, path: page.path, description: page.description,
      goal: existing.goal || page.description, module_id: page.module_id || existing.module_id || '',
      navigation_targets: (existing.navigation_targets || []).filter((id: string) => pageIds.has(id))
    }
  })
  // 删除页面时拒绝悬空的业务跳转，提示用户一并修正操作，而不是悄悄改变业务。
  for (const page of product.pages) {
    for (const action of page.actions) {
      if (action.behavior?.targetPageId && !pageIds.has(action.behavior.targetPageId)) throw new Error(`「${page.name}」的「${action.name}」仍指向被删除的页面，请先调整该操作。`)
    }
  }
  product.app = { ...product.app, name: spec.app_info.name, summary: spec.app_info.description }
  product.business_flows = structuredClone(spec.business_flows)
  // 在保存时即执行真实 RequirementSpec 的最小完整性门禁，避免把缺口拖到下游阶段。
  assertRequirementReady(spec, product)
  product.confirmation_status = spec.confirmation_status = 'pending_user_confirmation'
  next.artifacts.productPlan = product
  invalidatePlanningArtifacts(next, ['ui-designs', 'technical-plan'])
  next.artifactStatus['requirement-spec'] = next.artifactStatus['product-plan'] = 'pending'
  next.revision += 1
  next.updatedAt = new Date().toISOString()
  next.documents = {}
  synchronizePlanningReferences(next.artifacts)
  return next
}
