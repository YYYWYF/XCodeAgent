import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { ApplicationConfig, ApplicationDraft } from '../src/renderer/src/typings'
import { initialApplicationDraft } from '../src/renderer/src/components/Welcome/constants'
import { buildApplicationSchema } from '../src/renderer/src/components/Welcome/utils'
import { LOGIN_AUTHORIZATION_DEFAULTS } from '../src/renderer/src/components/Welcome/constants'
import { createSettingsDraft } from '../src/renderer/src/components/SettingsPage/settingsDraft'
import {
  applySettingsConfigChanges,
  configOperationEffectMessages,
  configOperationFailureMessage,
  settingsConfigChanges,
  settingsSaveNoticeMessages
} from '../src/renderer/src/components/SettingsPage/settingsConfigChanges'

/** 构造设置页提交所需的当前应用配置。 */
function application(): ApplicationConfig {
  const schema = buildApplicationSchema(structuredClone(initialApplicationDraft) as ApplicationDraft)
  return {
    ...schema,
    configRevision: 7,
    id: 'settings-config-apply-test',
    name: schema.appName,
    workspaceRoot: '/tmp/settings-config-apply-test',
    pages: ['工作台'],
    defaultPage: '工作台'
  }
}

test('设置页仅将发生变化的白名单字段交给同一配置事务', async () => {
  const current = application()
  const next = structuredClone(current)
  next.theme.primaryColor = '#e60012'
  const calls: unknown[][] = []

  const result = await applySettingsConfigChanges(current, next, async (...args) => {
    calls.push(args)
    return { status: 'APPLIED', changedFiles: ['frontend/.env'] }
  })

  assert.deepEqual(calls, [[
    '/tmp/settings-config-apply-test',
    7,
    { 'theme.primaryColor': '#e60012' }
  ]])
  assert.deepEqual(result, { status: 'APPLIED', changedFiles: ['frontend/.env'] })
})

test('旧应用设置补齐缺失地址，保留用户主动留空和自定义值', () => {
  const current = application()
  delete (current.auth.yht as Partial<typeof current.auth.yht>).centerUri
  delete (current.auth.yht as Partial<typeof current.auth.yht>).authorizeUri
  current.auth.yht.redirectUri = ''
  current.auth.yht.endUrl = 'https://custom.example/logout'
  delete current.authorization.api

  const draft = createSettingsDraft(current)

  assert.equal(draft.auth.yht.centerUri, LOGIN_AUTHORIZATION_DEFAULTS.centerUri)
  assert.equal(draft.auth.yht.authorizeUri, LOGIN_AUTHORIZATION_DEFAULTS.authorizeUri)
  assert.equal(draft.auth.yht.redirectUri, '')
  assert.equal(draft.auth.yht.endUrl, 'https://custom.example/logout')
  assert.equal(draft.authorization.api?.baseUrl, LOGIN_AUTHORIZATION_DEFAULTS.baseUrl)
  assert.equal(draft.authorization.api?.viewId, LOGIN_AUTHORIZATION_DEFAULTS.viewId)
})

test('旧快照展示默认地址但未编辑时不产生登录或权限字段更新', () => {
  const current = application()
  delete (current.auth.yht as Partial<typeof current.auth.yht>).centerUri
  delete (current.auth.yht as Partial<typeof current.auth.yht>).authorizeUri
  delete (current.auth.yht as Partial<typeof current.auth.yht>).redirectUri
  delete (current.auth.yht as Partial<typeof current.auth.yht>).endUrl
  delete (current.auth.yht as Partial<typeof current.auth.yht>).logoutShareJs
  current.authorization.enabled = true
  current.authorization.initialAdministratorSubjects = ['subject-001']
  delete current.authorization.api

  const displayed = createSettingsDraft(current)

  assert.deepEqual(settingsConfigChanges(current, displayed), {})
})

test('设置页配置无变化时不创建重复操作，事务失败会原样交给界面处理', async () => {
  const current = application()
  let calls = 0
  const unchanged = await applySettingsConfigChanges(current, structuredClone(current), async () => {
    calls += 1
    return { status: 'APPLIED' }
  })
  assert.equal(unchanged, undefined)
  assert.equal(calls, 0)

  const changed = structuredClone(current)
  changed.appName = '失败演练'
  await assert.rejects(
    applySettingsConfigChanges(current, changed, async () => {
      throw new Error('CONFIG_REVISION_CONFLICT')
    }),
    /CONFIG_REVISION_CONFLICT/
  )
})

test('未启用登录时仅修改图标不会读取未注册的一号通字段', async () => {
  const current = application()
  current.auth.enable = false
  const next = structuredClone(current)
  // 登录关闭后，一号通表单字段不会挂载，validateFields 的结果没有 yht。
  delete (next.auth as Partial<typeof next.auth>).yht
  next.appIcon = 'DesktopOutlined'
  const calls: unknown[][] = []

  await applySettingsConfigChanges(current, next, async (...args) => {
    calls.push(args)
    return { status: 'APPLIED' }
  })

  assert.deepEqual(calls, [[
    '/tmp/settings-config-apply-test',
    7,
    { appIcon: 'DesktopOutlined' }
  ]])
})

test('旧应用首次启用登录和权限时补齐缺失的嵌套配置', async () => {
  const current = application()
  current.auth.enable = false
  delete (current.auth as Partial<typeof current.auth>).yht
  delete current.authorization.api
  const next = createSettingsDraft(current)
  next.auth.enable = true
  next.auth.yht.clientId = 'client-001'
  next.auth.yht.privateKey = 'private-key'
  next.auth.yht.publicKey = 'public-key'
  next.auth.yht.centerPublicKey = 'center-public-key'
  next.authorization.enabled = true
  next.authorization.initialAdministratorSubjects = ['subject-001']
  const calls: unknown[][] = []

  await applySettingsConfigChanges(current, next, async (...args) => {
    calls.push(args)
    return { status: 'APPLIED' }
  })

  assert.deepEqual(calls, [[
    '/tmp/settings-config-apply-test',
    7,
    {
      'auth.enable': true,
      'authorization.enabled': true,
      'auth.yht.clientId': 'client-001',
      'auth.yht.privateKey': 'private-key',
      'auth.yht.publicKey': 'public-key',
      'auth.yht.centerPublicKey': 'center-public-key',
      'authorization.initialAdministratorSubjects': ['subject-001'],
      'authorization.api.baseUrl': LOGIN_AUTHORIZATION_DEFAULTS.baseUrl,
      'authorization.api.viewId': LOGIN_AUTHORIZATION_DEFAULTS.viewId
    }
  ]])
})

test('已启用登录的旧应用首次开启权限时始终提交默认权限 API', () => {
  const current = application()
  current.auth.enable = true
  current.auth.yht.clientId = 'client-001'
  current.auth.yht.privateKey = 'private-key'
  current.auth.yht.publicKey = 'public-key'
  current.auth.yht.centerPublicKey = 'center-public-key'
  current.authorization.enabled = false
  current.authorization.initialAdministratorSubjects = []
  delete current.authorization.api
  const next = createSettingsDraft(current)
  next.authorization.enabled = true
  next.authorization.initialAdministratorSubjects = ['subject-001']

  assert.deepEqual(settingsConfigChanges(current, next), {
    'authorization.enabled': true,
    'authorization.initialAdministratorSubjects': ['subject-001'],
    'authorization.api.baseUrl': LOGIN_AUTHORIZATION_DEFAULTS.baseUrl,
    'authorization.api.viewId': LOGIN_AUTHORIZATION_DEFAULTS.viewId
  })
})

test('设置保存将工程同步提醒汇总为一个弹框内容', () => {
  assert.deepEqual(
    settingsSaveNoticeMessages({
      status: 'APPLIED',
      changedFiles: ['frontend/.env', 'backend/src/main/resources/application-dev.yml']
    }),
    [
      '应用配置已保存。',
      '已更新 frontend/.env；请重新构建或重启前端服务以加载新配置。',
      '已更新 application-dev.yml；请重启后端服务以加载新配置。'
    ]
  )
})

test('配置操作明确说明代码注入、任意工程文件写入与未确认的失败变更', () => {
  assert.deepEqual(
    configOperationEffectMessages({
      installedCapabilities: ['login', 'authorization'],
      changedFiles: ['frontend/.env', 'backend/src/main/resources/application-dev.yml', 'backend/pom.xml']
    }),
    [
      '已向工程注入登录、权限能力相关代码。',
      '已更新 frontend/.env；请重新构建或重启前端服务以加载新配置。',
      '已更新 application-dev.yml；请重启后端服务以加载新配置。',
      '已更新工程文件：backend/pom.xml。'
    ]
  )
  assert.match(
    configOperationFailureMessage(new Error('工程配置未完全应用'), {
      'auth.enable': true,
      'authorization.enabled': true
    }),
    /工程配置未完全应用/
  )
  assert.match(
    configOperationFailureMessage(new Error('CONFIG_APPLY_FAILED：模板能力代码安装失败：模板引擎不可用（内部错误码：TEMPLATE_ENGINE_UNAVAILABLE）'), {
      'authorization.enabled': true
    }),
    /模板能力代码安装失败.*TEMPLATE_ENGINE_UNAVAILABLE/
  )
})

test('版本冲突仅提示刷新，不伪报登录或权限工程变更失败', () => {
  const message = configOperationFailureMessage(new Error('CONFIG_REVISION_CONFLICT：配置已变化，请刷新后重试。'), {
    'auth.enable': true,
    'authorization.enabled': true
  })

  assert.match(message, /CONFIG_REVISION_CONFLICT/)
  assert.doesNotMatch(message, /登录相关工程配置|权限能力代码/)
})


test('实际参数失败阶段不被登录权限提案改写，且只显示安全结构化信息', () => {
  const error = Object.assign(new Error('untrusted secret value'), {
    failureStage: 'application_parameter_update', failureCode: 'CONFIG_STRUCTURE_MISSING',
    failureTarget: 'yht.api.base-url'
  })
  const text = configOperationFailureMessage(error, { 'authorization.enabled': true })
  assert.match(text, /工程参数写入失败.*CONFIG_STRUCTURE_MISSING.*yht.api.base-url/)
  assert.doesNotMatch(text, /能力安装|能力代码|secret/)
})
