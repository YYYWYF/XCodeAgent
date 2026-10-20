import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { ApplicationConfig, ApplicationDraft } from '../src/renderer/src/typings'
import { initialApplicationDraft } from '../src/renderer/src/components/Welcome/constants'
import { buildApplicationSchema } from '../src/renderer/src/components/Welcome/utils'
import {
  configPrerequisiteChanges,
  type ConfigPrerequisite
} from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/ApplicationConfigPrerequisiteCard'
import { applyConfigChanges } from '../src/renderer/src/service/applicationConfigOperation'

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
    'authorization.enabled': true
  })
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
  const storageKey = `application-config-operation:${workspace}:7:${requestId}`
  storage.setItem(storageKey, 'operation-0001')
  const actions: string[] = []
  Object.assign(globalThis, {
    window: { devAgentStudio: { agentBaseUrl: 'https://config.test' }, localStorage: storage },
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
  const storageKey = `application-config-operation:${workspace}:7:${requestId}`
  storage.setItem(storageKey, 'operation-0002')
  const actions: string[] = []
  Object.assign(globalThis, {
    window: { devAgentStudio: { agentBaseUrl: 'https://config.test' }, localStorage: storage },
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
