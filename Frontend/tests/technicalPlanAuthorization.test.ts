import assert from 'node:assert/strict'
import { test } from 'node:test'
import { endpointAuthorizationView } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanAuthorizationData'
import { requirementAuthorizationRoles } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementAuthorizationData'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { AuthorizationSection } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanAuthorizationSection'

/** 构造包含角色、资源和 Endpoint ANY-OF 的最小 TechnicalPlan。 */
function enabledPlan(): Record<string, unknown> {
  return {
    api_contracts: [{ endpoints: [
      { id: 'people_api.update', name: '更新人员', method: 'PATCH', path: '/api/people/{id}' },
      { id: 'people_api.list', name: '查询人员', method: 'GET', path: '/api/people' }
    ] }],
    pages: [{ pageId: 'people', references: { action_implementations: [
      { actionId: 'edit', endpointId: 'people_api.update' },
      { actionId: 'approve', stepBindings: [{ stepId: 'save', endpointId: 'people_api.update' }] }
    ] } }],
    authorization_manifest: {
      schema_version: 'authorization-manifest.v3',
      resources: [
        { resourceKey: 'system_authorization_management', name: '权限管理', type: 'system', targetResourceRef: 'system:authorization_management' },
        { resourceKey: 'people', name: '人员', description: '受控页面', type: 'page', targetResourceRef: 'page:people', sourceRuleIds: ['people_page'] },
        { resourceKey: 'people_edit', name: '编辑人员', description: '维护人员的联系方式。', type: 'operation', targetResourceRef: 'action:people:edit', sourceRuleIds: ['people_edit'] },
        { resourceKey: 'people_approve', name: '审批人员', type: 'operation', targetResourceRef: 'action:people:approve', sourceRuleIds: ['people_approve'] }
      ],
      bindings: {
        pages: [{ pageId: 'people', resourceKey: 'people' }],
        actions: [
          { pageId: 'people', actionId: 'edit', resourceKey: 'people_edit', mode: 'hidden' },
          { pageId: 'people', actionId: 'approve', resourceKey: 'people_approve', mode: 'disabled' }
        ],
        endpoints: [
          { endpointId: 'people_api.update', operationResourceKeys: ['people_edit', 'people_approve'] },
          { endpointId: 'people_api.list', operationResourceKeys: [] }
        ]
      },
      systemAuthorization: {
        adminRoleSeedKey: 'system_admin',
        managementResourceKey: 'system_authorization_management'
      },
      defaultRoleAuthorization: {
        roles: [
          { roleSeedKey: 'editor', name: '编辑者', description: '维护人员资料。' },
          { roleSeedKey: 'viewer', name: '查看者' }
        ],
        roleResourceGrants: [
          { roleSeedKey: 'editor', resourceKeys: ['people', 'people_edit'] }
        ]
      }
    }
  }
}

/** 验证上游只投影角色与默认资源，不混入技术规划的接口绑定。 */
test('第一阶段按角色展示资源，系统管理员不继承业务资源', () => {
  const view = requirementAuthorizationRoles({}, product, enabledPlan().authorization_manifest as Record<string, unknown>)
  assert.equal(view.length, 3)
  assert.equal(view.find((role) => role.key === 'system_admin')?.resources.length, 1)
  assert.equal(view.find((role) => role.key === 'system_admin')?.resources[0].permissionDescription, '允许管理角色、成员角色和权限分配。')
  const editor = view.find((role) => role.key === 'editor')
  assert.ok(editor)
  assert.deepEqual(editor.resources.map((item) => item.key), ['people', 'people_edit'])
  assert.deepEqual(editor.resources.map((item) => item.permissionDescription), ['允许访问「人员列表」页面。', '允许在「人员列表」页面执行「编辑人员」操作。'])
  assert.deepEqual(editor.resources.map((item) => item.description), ['', '维护人员的联系方式。'])
  assert.equal(view.find((role) => role.key === 'viewer')?.resources.length, 0)
  assert.ok(!JSON.stringify(view).includes('people_api'))
})

/** 验证权限关闭时不产生权限 Tab 的可展示数据。 */
test('权限未启用或产品尚未完成时仍展示业务角色，不伪造授权', () => {
  const roles = requirementAuthorizationRoles({ user_roles: [{ id: 'visitor', name: '访客' }] }, {})
  assert.equal(roles[0].name, '访客')
  assert.deepEqual(roles[0].resources, [])
})

/** 验证未知资源引用只显示为未解析项，不会令草稿阅读失败。 */
test('未知资源引用保持可读的未解析状态', () => {
  const plan = enabledPlan() as { authorization_manifest: Record<string, unknown> }
  const authorization = plan.authorization_manifest.defaultRoleAuthorization as Record<string, unknown>
  const grants = authorization.roleResourceGrants as Array<Record<string, unknown>>
  grants[0].resourceKeys = ['missing_resource']

  const resource = requirementAuthorizationRoles({}, product, plan.authorization_manifest).find((role) => role.key === 'editor')?.resources[0]
  assert.equal(resource?.name, '引用未解析')
  assert.equal(resource?.permissionDescription, '暂时无法确定这项权限允许访问哪个页面或执行什么操作。')
})

/** 验证展示使用完整绑定引用，标识含下划线时也不猜测页面或操作。 */
test('资源中文绑定按实际引用解析，缺失目标保留未解析状态', () => {
  const plan = enabledPlan() as { authorization_manifest: Record<string, unknown> }
  const bindings = plan.authorization_manifest.bindings as Record<string, unknown>
  const authorization = plan.authorization_manifest.defaultRoleAuthorization as Record<string, unknown>
  const grants = authorization.roleResourceGrants as Array<Record<string, unknown>>
  grants[0].resourceKeys = ['employee_list', 'employee_list_edit_person']
  plan.authorization_manifest.resources = [
    { resourceKey: 'employee_list', name: '人员', type: 'page', targetResourceRef: 'page:employee_list' },
    { resourceKey: 'employee_list_edit_person', name: '编辑人员', type: 'operation', targetResourceRef: 'action:employee_list:edit_person' }
  ]
  bindings.pages = [{ pageId: 'employee_list', resourceKey: 'employee_list' }]
  bindings.actions = [{ pageId: 'employee_list', actionId: 'edit_person', resourceKey: 'employee_list_edit_person', mode: 'hidden' }]
  const productPlan = { pages: [{ pageId: 'employee_list', name: '员工档案', actions: [{ actionId: 'edit_person', name: '维护档案' }] }] }
  const resources = requirementAuthorizationRoles({}, productPlan, plan.authorization_manifest)
    .find((role) => role.key === 'editor')?.resources
  assert.deepEqual(resources?.map((item) => item.permissionDescription), ['允许访问「员工档案」页面。', '允许在「员工档案」页面执行「维护档案」操作。'])
  assert.deepEqual(resources?.map((item) => item.key), ['employee_list', 'employee_list_edit_person'])
  const unresolved = requirementAuthorizationRoles({}, {}, plan.authorization_manifest)
    .find((role) => role.key === 'editor')?.resources
  assert.ok(unresolved?.every((item) => item.permissionDescription === '暂时无法确定这项权限允许访问哪个页面或执行什么操作。'))
})

const product = { pages: [{ pageId: 'people', name: '人员列表', actions: [
  { actionId: 'edit', name: '编辑人员' }, { actionId: 'approve', name: '审批人员' }
] }] }

/** 验证业务步骤和直接操作都能呈现接口绑定，未受控接口也有明确状态。 */
test('技术规划只展示操作、接口和 ANY-OF 资源绑定', () => {
  const rows = endpointAuthorizationView(enabledPlan(), product)
  assert.deepEqual(rows[0].operations, ['人员列表 / 编辑人员', '人员列表 / 审批人员'])
  assert.deepEqual(rows[0].resources.map((item) => item.key), ['people_edit', 'people_approve'])
  assert.deepEqual(rows[1].resources, [])
  assert.ok(!JSON.stringify(rows).includes('system_admin'))
  const html = renderToStaticMarkup(createElement(AuthorizationSection, {
    plan: enabledPlan(), productPlan: product, sectionKey: 'authorization', onReviseAuthorization: () => undefined
  }))
  for (const label of ['接口权限绑定', 'PATCH', '人员列表 / 编辑人员', 'ANY-OF', '未设置资源权限控制', '修改角色与权限'])
    assert.ok(html.includes(label), label)
  for (const label of ['SYSTEM_ADMIN', '编辑者', '项默认资源']) assert.ok(!html.includes(label), label)
})

/** 缺少正式权限目录必须保持未知，不能把读取失败解释成没有权限控制。 */
test('缺少权限目录时不显示接口默认可访问', () => {
  const plan = enabledPlan()
  delete plan.authorization_manifest
  const html = renderToStaticMarkup(createElement(AuthorizationSection, { plan, productPlan: product, sectionKey: 'authorization' }))
  assert.ok(html.includes('权限绑定尚未就绪'))
  assert.ok(!html.includes('未设置资源权限控制'))
})
