import { asRecord, recordItems, stringItems, textValue, type JsonRecord } from './TechnicalPlanDocPanelData'

export type RoleResourceView = {
  key: string
  name: string
  description: string
  permissionDescription: string
}

export type RequirementRoleView = {
  key: string
  name: string
  description: string
  resources: RoleResourceView[]
}

/** 按正式绑定引用说明权限允许做什么，不从资源标识猜测业务含义。 */
function resourcePermissionDescriptions(productPlan: JsonRecord, manifest?: JsonRecord): Map<string, string> {
  const targets = new Map<string, string>()
  const pages = new Map(recordItems(productPlan.pages).map((page) => [textValue(page.pageId), page]))
  const bindings = asRecord(manifest?.bindings)
  for (const binding of recordItems(bindings.pages)) {
    const name = textValue(pages.get(textValue(binding.pageId))?.name)
    if (name) targets.set(textValue(binding.resourceKey), `允许访问「${name}」页面。`)
  }
  for (const binding of recordItems(bindings.actions)) {
    const page = pages.get(textValue(binding.pageId))
    const action = recordItems(page?.actions).find((item) => item.actionId === binding.actionId)
    const pageName = textValue(page?.name)
    const actionName = textValue(action?.name)
    if (pageName && actionName) targets.set(textValue(binding.resourceKey), `允许在「${pageName}」页面执行「${actionName}」操作。`)
  }
  const systemKey = textValue(asRecord(manifest?.systemAuthorization).managementResourceKey)
  if (systemKey) targets.set(systemKey, '允许管理角色、成员角色和权限分配。')
  return targets
}

/** 只投影服务端编译的业务授权；未启用权限时仍展示需求中的业务角色。 */
export function requirementAuthorizationRoles(spec: JsonRecord, productPlan: JsonRecord, manifest?: JsonRecord): RequirementRoleView[] {
  const authorization = asRecord(manifest?.defaultRoleAuthorization)
  const compiledRoles = recordItems(authorization.roles)
  const roles = compiledRoles.length ? compiledRoles : recordItems(spec.user_roles).map((role) => ({
    ...role, roleSeedKey: role.id
  }))
  const resources = new Map(recordItems(manifest?.resources).map((item) => [textValue(item.resourceKey), item]))
  const permissionDescriptions = resourcePermissionDescriptions(productPlan, manifest)
  const grants = new Map(recordItems(authorization.roleResourceGrants).map((item) => [
    textValue(item.roleSeedKey), stringItems(item.resourceKeys)
  ]))
  const result = roles.map((role) => ({
    key: textValue(role.roleSeedKey), name: textValue(role.name), description: textValue(role.description),
    resources: (grants.get(textValue(role.roleSeedKey)) || []).map((key) => {
      const item = resources.get(key)
      const description = textValue(item?.description)
      return {
        key, name: textValue(item?.name, '引用未解析'),
        // 通用占位说明由具体权限含义替代，业务补充说明仍然保留。
        description: /^受控(?:页面|操作)[。.]?$/.test(description) ? '' : description,
        permissionDescription: permissionDescriptions.get(key) || '暂时无法确定这项权限允许访问哪个页面或执行什么操作。'
      }
    })
  }))
  const system = asRecord(manifest?.systemAuthorization)
  const key = textValue(system.managementResourceKey)
  if (system.adminRoleSeedKey && key) result.unshift({
    key: textValue(system.adminRoleSeedKey), name: '系统权限管理员',
    description: '系统固定角色，仅负责权限管理。',
    resources: [{ key, name: textValue(resources.get(key)?.name, '权限管理'), description: '', permissionDescription: permissionDescriptions.get(key)! }]
  })
  return result
}
