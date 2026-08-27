import {
  asRecord,
  recordItems,
  stringItems,
  textValue,
  type JsonRecord
} from './TechnicalPlanDocPanelData'

export type AuthorizationResourceView = {
  description: string
  endpointBindings: Array<{ endpointId: string; ownedResourceKeys: string[] }>
  key: string
  name: string
  sourceRuleIds: string[]
  target: string
  type: 'system' | 'page' | 'operation' | 'unknown'
}

export type AuthorizationRoleView = {
  description: string
  groups: Array<{ key: AuthorizationResourceView['type']; label: string; resources: AuthorizationResourceView[] }>
  isInitialAdminRole: boolean
  isSystemRole: boolean
  name: string
  resourceCount: number
  seedKey: string
}

export type AuthorizationDesignView = {
  roles: AuthorizationRoleView[]
}

const GROUPS: Array<{ key: AuthorizationResourceView['type']; label: string }> = [
  { key: 'system', label: '系统资源' },
  { key: 'page', label: '页面资源' },
  { key: 'operation', label: '操作资源' },
  { key: 'unknown', label: '未解析资源' }
]

/** 将 manifest 资源类型限制为权限设计视图支持的固定分组。 */
function resourceType(value: unknown): AuthorizationResourceView['type'] {
  const type = textValue(value)
  return type === 'system' || type === 'page' || type === 'operation' ? type : 'unknown'
}

/** 从权限 manifest 生成只读角色视角，绝不补写或推断权限事实。 */
export function authorizationDesignView(plan: JsonRecord): AuthorizationDesignView | undefined {
  const manifest = asRecord(plan.authorization_manifest)
  if (manifest.enabled !== true) return undefined

  const resources = new Map(
    recordItems(manifest.resources)
      .map((resource) => [textValue(resource.resourceKey), resource] as const)
      .filter(([key]) => Boolean(key))
  )
  const authorization = asRecord(manifest.defaultRoleAuthorization)
  const grants = new Map(
    recordItems(authorization.roleResourceGrants)
      .map((grant) => [textValue(grant.roleSeedKey), stringItems(grant.resourceKeys)] as const)
      .filter(([seedKey]) => Boolean(seedKey))
  )
  const allEndpointBindings = recordItems(asRecord(manifest.bindings).endpoints).map((binding) => ({
    endpointId: textValue(binding.endpointId),
    resourceKeys: stringItems(binding.operationResourceKeys)
  }))

  return {
    roles: recordItems(authorization.roles).map((role) => {
      const seedKey = textValue(role.roleSeedKey)
      const grantedKeys = grants.get(seedKey) || []
      const resourcesByGroup = new Map<AuthorizationResourceView['type'], AuthorizationResourceView[]>()
      for (const resourceKey of grantedKeys) {
        const resource = resources.get(resourceKey)
        const type = resourceType(resource?.type)
        const relatedEndpoints = allEndpointBindings
          .filter((binding) => binding.resourceKeys.includes(resourceKey) && binding.endpointId)
          .map((binding) => ({
            endpointId: binding.endpointId,
            ownedResourceKeys: binding.resourceKeys.filter((key) => grantedKeys.includes(key))
          }))
        const item: AuthorizationResourceView = {
          description: textValue(resource?.description),
          endpointBindings: relatedEndpoints,
          key: resourceKey,
          name: resource ? textValue(resource.name, resourceKey) : '引用未解析',
          sourceRuleIds: resource ? stringItems(resource.sourceRuleIds) : [],
          target: resource ? textValue(resource.targetResourceRef) : '',
          type
        }
        resourcesByGroup.set(type, [...(resourcesByGroup.get(type) || []), item])
      }
      return {
        description: textValue(role.description),
        groups: GROUPS.map((group) => ({
          ...group,
          resources: (resourcesByGroup.get(group.key) || []).sort((left, right) => left.key.localeCompare(right.key))
        })).filter((group) => group.resources.length),
        isInitialAdminRole: role.isInitialAdminRole === true,
        isSystemRole: role.isSystemRole === true,
        name: textValue(role.name, seedKey || '未命名角色'),
        resourceCount: grantedKeys.length,
        seedKey
      }
    })
  }
}
