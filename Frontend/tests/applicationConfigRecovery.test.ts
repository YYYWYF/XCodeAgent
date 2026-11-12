import assert from 'node:assert/strict'
import { test } from 'node:test'
import { applyConfigChanges, configChangesDigest, inspectPendingConfigIdentity, readConfigContinuation, resumePendingConfigOperation } from '../src/renderer/src/service/applicationConfigOperation'
import { configActionPhase, configCheckpointStatus, configDecisionAccepted, resolveConfigPrerequisiteMode, runConfigPrerequisiteAction } from '../src/renderer/src/service/applicationConfigContinuation'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

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

/** 为对话恢复提供可控的配置服务、磁盘刷新与身份记录。 */
async function withConversationOperation(run: (fixture: {
  workspace: string
  requestId: string
  operationId: string
  storage: Storage
  key: string
  actions: string[]
  setStatus: (status: 'FAILED' | 'APPLIED') => void
  setOwner: (owner: string) => void
  failDisk: () => void
}) => Promise<void>): Promise<void> {
  const originalWindow = globalThis.window, originalFetch = globalThis.fetch
  const workspace = '/tmp/config-conversation', requestId = 'request-0001', operationId = 'operation-0001'
  const storage = memoryStorage(), key = `application-config-operation:${workspace}`
  const changesDigest = await configChangesDigest({ 'auth.yht.privateKey': 'synthetic-secret' })
  storage.setItem(key, JSON.stringify({ version: 1, requestId, operationId, expectedConfigRevision: 1, digest: changesDigest }))
  const actions: string[] = []
  let status = 'FAILED', owner = requestId, readFails = false
  Object.assign(globalThis, {
    window: { dispatchEvent: () => true, localStorage: storage, devAgentStudio: {
      agentBaseUrl: 'https://config.test',
      workspace: { readApplication: async () => {
        if (readFails) throw new Error('read failed')
        return { application: { configRevision: 2 } }
      } }
    } },
    fetch: async (_url: string, init?: RequestInit) => {
      const input = JSON.parse(String(init?.body)).forwardedProps.applicationConfig
      actions.push(input.action)
      if (input.action === 'resume') {
        assert.deepEqual(input, { action: 'resume', workspaceRoot: workspace, requestId, operationId })
        status = 'APPLIED'
      }
      if (input.action === 'apply') return response({ operationId: input.operationId, requestId: input.requestId, status: 'APPLIED' })
      return response({ operationId, requestId: owner, status, changesDigest })
    }
  })
  try {
    await run({ workspace, requestId, operationId, storage, key, actions,
      setStatus: (next) => { status = next },
      setOwner: (next) => { owner = next },
      failDisk: () => { readFails = true }
    })
  } finally {
    Object.assign(globalThis, { window: originalWindow, fetch: originalFetch })
  }
}

test('同一请求只恢复一次；续跑失败及重新读取后只重试原会话', async () => {
  await withConversationOperation(async ({ workspace, requestId, operationId, storage, key, actions }) => {
    assert.equal(await resolveConfigPrerequisiteMode(workspace, requestId), 'resume')
    let continuations = 0
    /** 配置落盘不能代表业务已继续，并发只能调用一次续跑。 */
    const continueRequest = async (id?: string): Promise<boolean> => {
      assert.equal(id, operationId)
      assert.equal(configActionPhase(workspace, requestId), 'continuing')
      assert.equal(storage.getItem(key), null)
      assert.equal(storage.length, 1)
      assert.equal(storage.getItem(storage.key(0)!), operationId)
      return ++continuations === 2
    }
    const first = runConfigPrerequisiteAction(workspace, requestId, 1, { action: 'resume' }, continueRequest)
    const duplicate = runConfigPrerequisiteAction(workspace, requestId, 1, { action: 'resume' }, continueRequest)
    assert.equal(first, duplicate)
    assert.equal(configActionPhase(workspace, requestId), 'configuring')
    assert.equal(await first, false)
    assert.equal(await resolveConfigPrerequisiteMode(workspace, requestId), 'continue')
    assert.equal(await runConfigPrerequisiteAction(workspace, requestId, 1, { action: 'continue' }, continueRequest), true)
    assert.deepEqual(actions, ['get', 'resume'])
    assert.equal(continuations, 2)
    assert.equal(storage.length, 0)
  })
})

test('新配置仍按 preview、apply、续跑的顺序提交', async () => {
  await withConversationOperation(async ({ workspace, requestId, storage, key, actions }) => {
    storage.removeItem(key)
    assert.equal(await resolveConfigPrerequisiteMode(workspace, requestId), 'apply')
    assert.equal(await runConfigPrerequisiteAction(workspace, requestId, 1, { action: 'apply', changes: { appName: '新名称' } },
      async (operationId, operation) => {
        assert.deepEqual(actions, ['preview', 'apply'])
        assert.ok(operationId)
        assert.equal(operation?.operationId, operationId)
        assert.equal(readConfigContinuation(workspace, requestId), operationId)
        return true
      }), true)
    assert.equal(storage.length, 0)
  })
})

test('读到 APPLIED 时直接保留续跑身份；读盘失败仍保留原操作', async () => {
  await withConversationOperation(async ({ workspace, requestId, operationId, storage, key, actions, setStatus }) => {
    setStatus('APPLIED')
    assert.equal(await resolveConfigPrerequisiteMode(workspace, requestId), 'continue')
    assert.equal(readConfigContinuation(workspace, requestId), operationId)
    assert.equal(storage.getItem(key), null)
    assert.deepEqual(actions, ['get'])
  })
  await withConversationOperation(async ({ workspace, requestId, storage, key, actions, setStatus, failDisk }) => {
    setStatus('APPLIED')
    failDisk()
    await assert.rejects(resolveConfigPrerequisiteMode(workspace, requestId), /application.json 读取失败/)
    assert.ok(storage.getItem(key))
    assert.equal(readConfigContinuation(workspace, requestId), undefined)
    assert.deepEqual(actions, ['get'])
  })
})

test('其他请求、设置页和损坏身份不会发送配置变更或覆盖本地记录', async () => {
  await withConversationOperation(async ({ workspace, requestId, storage, key, actions, setOwner }) => {
    const stored = storage.getItem(key)
    assert.deepEqual(inspectPendingConfigIdentity(workspace), { kind: 'other', requestId })
    await assert.rejects(resolveConfigPrerequisiteMode(workspace, ''), /缺少原请求身份/)
    await assert.rejects(resolveConfigPrerequisiteMode(workspace, 'other-request'), /回到原会话/)
    await assert.rejects(applyConfigChanges(workspace, 1, { appName: '更新' }), /CONFIG_OPERATION_CONFLICT/)
    await assert.rejects(resumePendingConfigOperation(workspace), /回到原配置请求/)
    assert.equal(storage.getItem(key), stored)
    assert.deepEqual(actions, [])
    setOwner('other-request')
    await assert.rejects(resolveConfigPrerequisiteMode(workspace, requestId), /不匹配/)
    assert.equal(storage.getItem(key), stored)
    storage.setItem(key, '{}')
    await assert.rejects(resolveConfigPrerequisiteMode(workspace, requestId), /无法确认/)
    assert.equal(storage.getItem(key), '{}')
    assert.deepEqual(actions, ['get'])
  })
})

test('续跑接收只采信同一线程、同一请求的 checkpoint 事实', () => {
  const threadId = 'thread-0001', requestId = 'request-0001'
  const checkpoint: WorkflowRunPayload = { threadId, runId: 'run-0001', summary: { status: 'completed' }, events: [],
    state: { application_config_prerequisite: { requestId, sourceThreadId: threadId, status: 'RESUMED' } } }
  assert.equal(configDecisionAccepted(configCheckpointStatus(checkpoint, threadId, requestId), 'applied'), true)
  assert.equal(configDecisionAccepted(configCheckpointStatus(checkpoint, 'other-thread', requestId), 'applied'), false)
  assert.equal(configDecisionAccepted(configCheckpointStatus(checkpoint, threadId, 'other-request'), 'applied'), false)
  assert.equal(configDecisionAccepted('AWAITING_CONFIG', 'applied'), false)
  assert.equal(configDecisionAccepted('APPLIED', 'applied'), false)
})
