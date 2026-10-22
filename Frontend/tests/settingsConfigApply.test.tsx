import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { ApplicationConfig, ApplicationDraft } from '../src/renderer/src/typings'
import { initialApplicationDraft } from '../src/renderer/src/components/Welcome/constants'
import { buildApplicationSchema } from '../src/renderer/src/components/Welcome/utils'
import {
  applySettingsConfigChanges,
  configOperationEffectMessages,
  configOperationFailureMessage,
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

test('设置保存将工程同步提醒汇总为一个弹框内容', () => {
  assert.deepEqual(
    settingsSaveNoticeMessages({
      status: 'APPLIED',
      changedFiles: ['frontend/.env', 'backend/src/main/resources/application.yml']
    }),
    [
      '应用配置已保存。',
      '已更新 frontend/.env；请重新构建或重启前端服务以加载新配置。',
      '已更新 application.yml；请重启后端服务以加载新配置。'
    ]
  )
})

test('配置操作明确说明代码注入、任意工程文件写入与未确认的失败变更', () => {
  assert.deepEqual(
    configOperationEffectMessages({
      installedCapabilities: ['login', 'authorization'],
      changedFiles: ['frontend/.env', 'backend/src/main/resources/application.yml', 'backend/pom.xml']
    }),
    [
      '已向工程注入登录、权限能力相关代码。',
      '已更新 frontend/.env；请重新构建或重启前端服务以加载新配置。',
      '已更新 application.yml；请重启后端服务以加载新配置。',
      '已更新工程文件：backend/pom.xml。'
    ]
  )
  assert.match(
    configOperationFailureMessage(new Error('工程配置未完全应用'), {
      'auth.enable': true,
      'authorization.enabled': true
    }),
    /登录相关工程配置、权限能力代码/
  )
})
