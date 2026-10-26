import assert from 'node:assert/strict'
import { test } from 'node:test'
import { applyConfigChanges, configChangesDigest, resumePendingConfigOperation } from '../src/renderer/src/service/applicationConfigOperation'

/** 提供独立本地身份存储，不持久化测试提案原值。 */
function memoryStorage(): Storage {
  const data = new Map<string, string>()
  return { get length() { return data.size }, clear: () => data.clear(), getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null, removeItem: (key) => { data.delete(key) }, setItem: (key, value) => { data.set(key, value) } }
}

/** 通过真实 AG-UI 客户端消费最小完整测试事件流。 */
function response(operation: Record<string, unknown>): Response {
  const value = { schemaVersion: 1, status: 'completed', operation }
  const events = [{ type: 'RUN_STARTED', threadId: 'test', runId: 'test' },
    { type: 'CUSTOM', name: 'application-config', value }, { type: 'STATE_SNAPSHOT', snapshot: { applicationConfigOperation: value } },
    { type: 'RUN_FINISHED', threadId: 'test', runId: 'test' }]
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } })
}

test('用户变更提案后只 get 原操作，阻断 apply 且保留无密钥的 pending', async () => {
  const originalWindow = globalThis.window, originalFetch = globalThis.fetch
  const storage = memoryStorage(), actions: string[] = []
  const workspace = '/tmp/changed-proposal', key = `application-config-operation:${workspace}`
  const pending = { version: 1, operationId: 'pending-operation', expectedConfigRevision: 7, digest: await configChangesDigest({ 'auth.yht.privateKey': 'original-secret' }) }
  storage.setItem(key, JSON.stringify(pending))
  Object.assign(globalThis, { window: { localStorage: storage, devAgentStudio: { agentBaseUrl: 'https://config.test' } },
    fetch: async (_url: string, init?: RequestInit) => { actions.push(JSON.parse(String(init?.body)).forwardedProps.applicationConfig.action); return response({ operationId: pending.operationId, status: 'FAILED' }) } })
  try {
    await assert.rejects(applyConfigChanges(workspace, 8, { 'auth.yht.privateKey': 'changed-secret' }), /CONFIG_OPERATION_CONFLICT/)
    assert.deepEqual(actions, ['get'])
    assert.deepEqual(JSON.parse(storage.getItem(key)!), pending)
    assert.doesNotMatch(storage.getItem(key)!, /original-secret|changed-secret/)
  } finally { Object.assign(globalThis, { window: originalWindow, fetch: originalFetch }) }
})

test('APPLIED 后读取失败保留 ID，刷新 revision 后仍只重读原操作', async () => {
  const originalWindow = globalThis.window, originalFetch = globalThis.fetch
  const storage = memoryStorage(), actions: string[] = []
  const workspace = '/tmp/read-failed', key = `application-config-operation:${workspace}`
  const pending = { version: 1, operationId: 'read-operation', expectedConfigRevision: 7, digest: await configChangesDigest({ appName: '更新' }) }
  storage.setItem(key, JSON.stringify(pending))
  let reads = 0
  Object.assign(globalThis, { window: { dispatchEvent: () => true, localStorage: storage, devAgentStudio: { agentBaseUrl: 'https://config.test', workspace: {
    readApplication: async () => { if (++reads === 1) throw new Error('read failed'); return { application: { configRevision: 8 } } }
  } } }, fetch: async (_url: string, init?: RequestInit) => { actions.push(JSON.parse(String(init?.body)).forwardedProps.applicationConfig.action); return response({ operationId: pending.operationId, status: 'APPLIED' }) } })
  try {
    await assert.rejects(applyConfigChanges(workspace, 7, { appName: '更新' }), /application.json 读取失败/)
    assert.ok(storage.getItem(key))
    await applyConfigChanges(workspace, 8, { appName: '更新' })
    assert.deepEqual(actions, ['get', 'get'])
    assert.equal(storage.getItem(key), null)
  } finally { Object.assign(globalThis, { window: originalWindow, fetch: originalFetch }) }
})

test('并发相同提案只发起一次 preview 和 apply', async () => {
  const originalWindow = globalThis.window, originalFetch = globalThis.fetch
  const storage = memoryStorage(), actions: string[] = []
  Object.assign(globalThis, { window: { dispatchEvent: () => true, localStorage: storage, devAgentStudio: { agentBaseUrl: 'https://config.test', workspace: { readApplication: async () => ({ application: { configRevision: 8 } }) } } },
    fetch: async (_url: string, init?: RequestInit) => { const input = JSON.parse(String(init?.body)).forwardedProps.applicationConfig; actions.push(input.action); return response({ operationId: input.operationId, status: input.action === 'preview' ? undefined : 'APPLIED' }) } })
  try {
    const [first, second] = await Promise.all([applyConfigChanges('/tmp/concurrent', 7, { appName: 'Changed' }), applyConfigChanges('/tmp/concurrent', 7, { appName: 'Changed' })])
    assert.deepEqual(actions, ['preview', 'apply'])
    assert.equal(first.operationId, second.operationId)
    assert.equal(storage.length, 0)
  } finally { Object.assign(globalThis, { window: originalWindow, fetch: originalFetch }) }
})


test('显式恢复仅发送原操作身份，不传变化提案和密钥', async () => {
  const originalWindow = globalThis.window, originalFetch = globalThis.fetch
  const storage = memoryStorage(), workspace = '/tmp/explicit-resume', key = `application-config-operation:${workspace}`
  storage.setItem(key, JSON.stringify({ version: 1, operationId: 'resume-operation', expectedConfigRevision: 7, digest: await configChangesDigest({ 'auth.yht.privateKey': 'synthetic-secret' }) }))
  let input: Record<string, unknown> | undefined
  Object.assign(globalThis, { window: { dispatchEvent: () => true, localStorage: storage, devAgentStudio: { agentBaseUrl: 'https://config.test', workspace: { readApplication: async () => ({ application: { configRevision: 8 } }) } } },
    fetch: async (_url: string, init?: RequestInit) => { input = JSON.parse(String(init?.body)).forwardedProps.applicationConfig; return response({ operationId: 'resume-operation', status: 'APPLIED' }) } })
  try {
    await resumePendingConfigOperation(workspace)
    assert.deepEqual(input, { action: 'resume', workspaceRoot: workspace, operationId: 'resume-operation' })
    assert.equal(storage.getItem(key), null)
  } finally { Object.assign(globalThis, { window: originalWindow, fetch: originalFetch }) }
})
