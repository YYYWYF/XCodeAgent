import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { ApplicationConfig, ApplicationDraft } from '../src/renderer/src/typings'
import { initialApplicationDraft } from '../src/renderer/src/components/Welcome/constants'
import { buildApplicationSchema } from '../src/renderer/src/components/Welcome/utils'
import {
  configPrerequisiteChanges,
  initializeConversationFields,
  mergeConversationConfigValues,
  mergeSuggested,
  resolveConversationInitialChanges,
  resolveConversationEditablePaths,
  type ConfigPrerequisite
} from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/ApplicationConfigPrerequisiteCard'
import { renderToStaticMarkup } from 'react-dom/server'
import ApplicationConfigPrerequisiteCard from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/ApplicationConfigPrerequisiteCard'
import { applyConfigChanges, configChangesDigest } from '../src/renderer/src/service/applicationConfigOperation'

/** 构造与聊天卡一致的已生成应用配置。 */
function application(): ApplicationConfig {
  const schema = buildApplicationSchema(structuredClone(initialApplicationDraft) as ApplicationDraft)
  return {
    ...schema,
    id: 'application-config-card-test',
    name: schema.appName,
    workspaceRoot: '/tmp/application-config-card-test',
    pages: ['工作台'],
    defaultPage: '工作台'
  }
}

test('配置卡片在用户确认前不提交推断出的能力依赖', () => {
  const current = application()
  const values = structuredClone(current)
  values.appName = '员工中心'
  const inferred: ConfigPrerequisite[] = [
    { path: 'auth.enable', desiredValue: true, source: 'inferred', reason: '权限能力依赖登录。' },
    { path: 'authorization.enabled', desiredValue: true, source: 'inferred', reason: '管理员可见性需要权限能力。' }
  ]

  assert.deepEqual(configPrerequisiteChanges(current, values, inferred, false), {
    appName: '员工中心'
  })
})

test('配置卡片只在用户明确确认后追加推断依赖，且保留用户显式字段', () => {
  const current = application()
  const values = structuredClone(current)
  values.auth.yht.privateKey = 'user-submitted-secret'
  const inferred: ConfigPrerequisite[] = [
    { path: 'auth.enable', desiredValue: true, source: 'inferred' },
    { path: 'authorization.enabled', desiredValue: true, source: 'inferred' }
  ]

  assert.deepEqual(configPrerequisiteChanges(current, values, inferred, true), {
    'auth.yht.privateKey': 'user-submitted-secret',
    'auth.enable': true,
    'authorization.enabled': true,
    'authorization.api.baseUrl': initialApplicationDraft.authorization.api?.baseUrl,
    'authorization.api.viewId': initialApplicationDraft.authorization.api?.viewId
  })
})

test('配置卡片用完整应用配置补齐仅注册登录与权限字段的局部表单值', () => {
  const current = application()
  const partial = {
    auth: {
      enable: true,
      yht: { clientId: 'client', privateKey: 'private', publicKey: 'public', centerPublicKey: 'center' }
    },
    authorization: { enabled: true, initialAdministratorSubjects: ['subject-001'] }
  } as ApplicationDraft

  const merged = mergeConversationConfigValues(current, partial)

  assert.equal(merged.theme.primaryColor, current.theme.primaryColor)
  assert.deepEqual(configPrerequisiteChanges(current, merged, [], false), {
    'auth.enable': true,
    'authorization.enabled': true,
    'auth.yht.clientId': 'client',
    'auth.yht.privateKey': 'private',
    'auth.yht.publicKey': 'public',
    'auth.yht.centerPublicKey': 'center',
    'authorization.initialAdministratorSubjects': ['subject-001'],
    'authorization.api.baseUrl': initialApplicationDraft.authorization.api?.baseUrl,
    'authorization.api.viewId': initialApplicationDraft.authorization.api?.viewId
  })
})

test('配置卡片将名称、图标和主题色的局部表单值合并为受管配置提案', () => {
  const current = application()
  const partial = {
    appName: '客户中心',
    appIcon: 'DashboardOutlined',
    theme: { primaryColor: '#e60012' }
  } as ApplicationDraft

  const merged = mergeConversationConfigValues(current, partial)

  assert.deepEqual(configPrerequisiteChanges(current, merged, [], false), {
    appName: '客户中心',
    appIcon: 'DashboardOutlined',
    'theme.primaryColor': '#e60012'
  })
})

test('配置卡片可为旧配置补齐登录和权限 API 的缺失对象层级', () => {
  const current = application()
  delete (current.auth as Partial<typeof current.auth>).yht
  delete current.authorization.api

  const merged = mergeSuggested(current, {
    'auth.yht.centerUri': 'https://auth.example/center',
    'authorization.api.baseUrl': 'https://authorization.example/api'
  })

  assert.equal(merged.auth.yht.centerUri, 'https://auth.example/center')
  assert.equal(merged.authorization.api?.baseUrl, 'https://authorization.example/api')
})

test('配置卡片启用权限时展示完整登录和权限表单字段', () => {
  const paths = resolveConversationEditablePaths(
    [],
    [{ path: 'authorization.enabled', desiredValue: true, source: 'inferred' }],
    { 'authorization.enabled': true }
  )

  for (const path of [
    'auth.enable', 'auth.yht.clientId', 'auth.yht.privateKey', 'auth.yht.publicKey', 'auth.yht.centerPublicKey',
    'auth.yht.centerUri', 'auth.yht.authorizeUri', 'auth.yht.redirectUri', 'auth.yht.endUrl', 'auth.yht.logoutShareJs',
    'authorization.enabled', 'authorization.initialAdministratorSubjects', 'authorization.api.baseUrl', 'authorization.api.viewId'
  ]) {
    assert.ok(paths.includes(path as typeof paths[number]))
  }
})

test('配置卡片启用权限时同步开启登录初始值以显示凭据字段', () => {
  assert.deepEqual(
    resolveConversationInitialChanges(
      [],
      [{ path: 'authorization.enabled', desiredValue: true, source: 'inferred' }],
      { 'authorization.enabled': true }
    ),
    { 'authorization.enabled': true, 'auth.enable': true }
  )
})

/** 生成配置独立 AG-UI 流的最小成功响应。 */
function configOperationResponse(operation: Record<string, unknown>): Response {
  const payload = { schemaVersion: 1, status: 'completed', operation }
  const events = [
    { type: 'RUN_STARTED', threadId: 'config-test-thread', runId: 'config-test-run' },
    { type: 'CUSTOM', name: 'application-config', value: payload },
    { type: 'STATE_SNAPSHOT', snapshot: { applicationConfigOperation: payload } },
    { type: 'RUN_FINISHED', threadId: 'config-test-thread', runId: 'config-test-run' }
  ]
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'Content-Type': 'text/event-stream' }
  })
}

/** 创建只供配置卡恢复测试使用的内存存储。 */
function memoryStorage(): Storage {
  const data = new Map<string, string>()
  return {
    get length(): number { return data.size },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => { data.delete(key) },
    setItem: (key, value) => { data.set(key, value) }
  }
}

test('刷新后的配置卡先读取原 operationId，已完成时不重复提交', async () => {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  const storage = memoryStorage()
  const workspace = '/tmp/application-config-card-test'
  const requestId = 'request-0001'
  const storageKey = `application-config-operation:${workspace}`
  storage.setItem(storageKey, JSON.stringify({ version: 1, operationId: 'operation-0001', expectedConfigRevision: 7, requestId, digest: await configChangesDigest({ 'auth.enable': true }) }))
  const actions: string[] = []
  Object.assign(globalThis, {
    window: { dispatchEvent: () => true, devAgentStudio: { agentBaseUrl: 'https://config.test', workspace: { readApplication: async () => ({ application: application() }) } }, localStorage: storage },
    fetch: async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body))
      const action = body.forwardedProps.applicationConfig.action
      actions.push(action)
      return configOperationResponse({ operationId: 'operation-0001', status: 'APPLIED' })
    }
  })
  try {
    const result = await applyConfigChanges(workspace, 7, { 'auth.enable': true }, undefined, requestId)
    assert.equal(result.status, 'APPLIED')
    assert.deepEqual(actions, ['get'])
    assert.equal(storage.getItem(storageKey), null)
  } finally {
    Object.assign(globalThis, { window: originalWindow, fetch: originalFetch })
  }
})

test('刷新后失败的配置卡复用 operationId 重试，不重新创建预览操作', async () => {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  const storage = memoryStorage()
  const workspace = '/tmp/application-config-card-test'
  const requestId = 'request-0002'
  const storageKey = `application-config-operation:${workspace}`
  storage.setItem(storageKey, JSON.stringify({ version: 1, operationId: 'operation-0002', expectedConfigRevision: 7, requestId, digest: await configChangesDigest({ 'auth.enable': true }) }))
  const actions: string[] = []
  Object.assign(globalThis, {
    window: { dispatchEvent: () => true, devAgentStudio: { agentBaseUrl: 'https://config.test', workspace: { readApplication: async () => ({ application: application() }) } }, localStorage: storage },
    fetch: async (_url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body))
      const action = body.forwardedProps.applicationConfig.action
      actions.push(action)
      return configOperationResponse({ operationId: 'operation-0002', status: action === 'get' ? 'FAILED' : 'APPLIED' })
    }
  })
  try {
    const result = await applyConfigChanges(workspace, 7, { 'auth.enable': true }, undefined, requestId)
    assert.equal(result.status, 'APPLIED')
    assert.deepEqual(actions, ['get', 'apply'])
    assert.equal(storage.getItem(storageKey), null)
  } finally {
    Object.assign(globalThis, { window: originalWindow, fetch: originalFetch })
  }
})

for (const section of ['auth', 'authorization'] as const) {
  for (const invalid of [undefined, null, [], 'invalid']) {
    test(`配置卡拒绝非法 ${section} 根并保持错误可见：${String(invalid)}`, () => {
      const current = application()
      Object.assign(current, { [section]: invalid })
      const html = renderToStaticMarkup(<ApplicationConfigPrerequisiteCard application={current} pending={{}} onCancel={() => {}} onSubmit={async () => {}} />)
      assert.match(html, /配置无法读取/)
      assert.match(html, new RegExp(section))
    })
  }
}

test('确认推断依赖以完整配置合并局部表单且保留未提交凭据', () => {
  const current = application()
  const merged = mergeSuggested(mergeConversationConfigValues(current, {
    auth: { yht: { privateKey: 'typed-secret' } }
  } as ApplicationDraft), { 'auth.enable': true })
  assert.equal(merged.auth.yht.privateKey, 'typed-secret')
  assert.equal(merged.theme.primaryColor, current.theme.primaryColor)
  assert.equal(current.auth.enable, false)
})


test('重复刷新与新增字段初始化保留已编辑值', () => {
  const initialized = new Set<string>()
  const store = new Map<string, unknown>([['auth.yht.privateKey', 'typed-secret']])
  const form = {
    isFieldTouched: (parts: unknown) => (parts as string[]).join('.') === 'auth.yht.privateKey',
    setFieldValue: (parts: unknown, value: unknown) => { store.set((parts as string[]).join('.'), value) }
  }
  const current = mergeSuggested(application(), { 'auth.enable': true })
  initializeConversationFields(form, current, ['auth.enable', 'auth.yht.privateKey'], initialized)
  assert.equal(store.get('auth.enable'), true)
  assert.equal(store.get('auth.yht.privateKey'), 'typed-secret')
  store.set('auth.enable', false)
  initializeConversationFields(form, current, ['auth.enable', 'auth.yht.privateKey', 'auth.yht.clientId'], initialized)
  assert.equal(store.get('auth.enable'), false)
  assert.equal(store.get('auth.yht.privateKey'), 'typed-secret')
})
