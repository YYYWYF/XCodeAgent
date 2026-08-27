import assert from 'node:assert/strict'
import { test } from 'node:test'
import { authorizationDesignView } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanAuthorizationData'

/** 构造包含角色、资源和 Endpoint ANY-OF 的最小 TechnicalPlan。 */
function enabledPlan(): Record<string, unknown> {
  return {
    authorization_manifest: {
      enabled: true,
      resources: [
        { resourceKey: 'system_authorization_management', name: '权限管理', type: 'system', targetResourceRef: 'system:authorization_management' },
        { resourceKey: 'people', name: '人员', type: 'page', targetResourceRef: 'page:people', sourceRuleIds: ['people_page'] },
        { resourceKey: 'people_edit', name: '编辑人员', type: 'operation', targetResourceRef: 'action:people:edit', sourceRuleIds: ['people_edit'] },
        { resourceKey: 'people_approve', name: '审批人员', type: 'operation', targetResourceRef: 'action:people:approve', sourceRuleIds: ['people_approve'] }
      ],
      bindings: {
        endpoints: [
          { endpointId: 'people_api.update', operationResourceKeys: ['people_edit', 'people_approve'] },
          { endpointId: 'people_api.list', operationResourceKeys: [] }
        ]
      },
      defaultRoleAuthorization: {
        roles: [
          { roleSeedKey: 'administrator', name: '系统管理员', isSystemRole: true, isInitialAdminRole: true },
          { roleSeedKey: 'editor', name: '编辑者', description: '维护人员资料。' },
          { roleSeedKey: 'viewer', name: '查看者' }
        ],
        roleResourceGrants: [
          { roleSeedKey: 'administrator', resourceKeys: ['system_authorization_management'] },
          { roleSeedKey: 'editor', resourceKeys: ['people', 'people_edit'] }
        ]
      }
    }
  }
}

/** 验证角色视图仅投影 manifest 中已有的默认资源和 Endpoint ANY-OF 关联。 */
test('权限设计按角色汇总资源与 Endpoint ANY-OF', () => {
  const view = authorizationDesignView(enabledPlan())

  assert.ok(view)
  assert.equal(view.roles.length, 3)
  const editor = view.roles.find((role) => role.seedKey === 'editor')
  assert.ok(editor)
  assert.deepEqual(editor.groups.map((group) => group.key), ['page', 'operation'])
  const operation = editor.groups.find((group) => group.key === 'operation')?.resources[0]
  assert.deepEqual(operation?.endpointBindings, [
    { endpointId: 'people_api.update', ownedResourceKeys: ['people_edit'] }
  ])
  assert.equal(view.roles.find((role) => role.seedKey === 'viewer')?.resourceCount, 0)
})

/** 验证权限关闭时不产生权限 Tab 的可展示数据。 */
test('权限关闭时不生成权限设计视图', () => {
  assert.equal(authorizationDesignView({ authorization_manifest: { enabled: false } }), undefined)
})

/** 验证未知资源引用只显示为未解析项，不会令草稿阅读失败。 */
test('未知资源引用保持可读的未解析状态', () => {
  const plan = enabledPlan() as { authorization_manifest: Record<string, unknown> }
  const authorization = plan.authorization_manifest.defaultRoleAuthorization as Record<string, unknown>
  const grants = authorization.roleResourceGrants as Array<Record<string, unknown>>
  grants[1].resourceKeys = ['missing_resource']

  const resource = authorizationDesignView(plan)?.roles.find((role) => role.seedKey === 'editor')?.groups[0]?.resources[0]
  assert.equal(resource?.name, '引用未解析')
  assert.equal(resource?.type, 'unknown')
})
