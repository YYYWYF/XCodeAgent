import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { ApplicationConfig, ApplicationDraft } from '../src/renderer/src/typings'
import { DatasourceEnum } from '../src/renderer/src/typings'
import { buildApplicationPlanningRequest } from '../src/renderer/src/service/applicationPagePlanning'
import { initialApplicationDraft } from '../src/renderer/src/components/Welcome/constants'
import { buildApplicationSchema } from '../src/renderer/src/components/Welcome/utils'

/** 验证新建表单只生成当前 v5 的内置权限种子，并清理重复成员标识。 */
test('生成 schemaVersion 5 权限配置', () => {
  const draft = structuredClone(initialApplicationDraft) as ApplicationDraft
  draft.appName = '权限验收应用'
  draft.senario = '验证新建应用权限配置。'
  draft.auth.enable = true
  draft.authorization = {
    enabled: true,
    initialAdministratorSubjects: [' ops@example.com ', 'ops@example.com', 'admin@example.com']
  }

  const schema = buildApplicationSchema(draft)

  assert.equal(schema.schemaVersion, 5)
  assert.deepEqual(schema.authorization, {
    enabled: true,
    initialAdministratorSubjects: ['ops@example.com', 'admin@example.com']
  })
})

/** 验证首轮规划请求只携带当前权限开关和管理员种子，不携带 provider 或页面开关。 */
test('规划请求携带当前权限事实', () => {
  const draft = structuredClone(initialApplicationDraft) as ApplicationDraft
  draft.appName = '规划请求验收应用'
  draft.senario = '验证首轮规划请求。'
  const schema = buildApplicationSchema(draft)
  const application = {
    ...schema,
    id: 'application-test',
    name: schema.appName,
    auth: { ...schema.auth, enable: true },
    authorization: {
      ...schema.authorization,
      enabled: true,
      initialAdministratorSubjects: ['ops@example.com']
    }
  } as ApplicationConfig

  const request = buildApplicationPlanningRequest(application)

  assert.match(request, /涉及权限控制：是。/)
  assert.match(request, /初始管理员成员标识：ops@example.com。/)
  assert.doesNotMatch(request, /运行态权限管理页面/)
  assert.doesNotMatch(request, /权限提供器模式/)
})

/** 验证权限开启时配置构造器拒绝缺少认证、数据库或管理员种子的非法组合。 */
test('拒绝权限配置前置条件冲突', () => {
  const base = structuredClone(initialApplicationDraft) as ApplicationDraft
  base.authorization.enabled = true

  assert.throws(() => buildApplicationSchema(base), /同时启用认证/)

  const staticDatasource = structuredClone(initialApplicationDraft) as ApplicationDraft
  staticDatasource.auth.enable = true
  staticDatasource.authorization = {
    enabled: true,
    initialAdministratorSubjects: ['ops@example.com']
  }
  staticDatasource.datasource = { type: DatasourceEnum.STATIC }
  assert.throws(() => buildApplicationSchema(staticDatasource), /数据库数据源/)

  const missingAdministrator = structuredClone(initialApplicationDraft) as ApplicationDraft
  missingAdministrator.auth.enable = true
  missingAdministrator.authorization.enabled = true
  assert.throws(() => buildApplicationSchema(missingAdministrator), /管理员 subjectId/)

  const placeholderAdministrator = structuredClone(initialApplicationDraft) as ApplicationDraft
  placeholderAdministrator.auth.enable = true
  placeholderAdministrator.authorization = {
    enabled: true,
    initialAdministratorSubjects: ['current-user']
  }
  assert.throws(() => buildApplicationSchema(placeholderAdministrator), /current-user/)
})
