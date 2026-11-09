import assert from 'node:assert/strict'
import http from 'node:http'
import { test } from 'node:test'
import { getApplicationLifecycle } from '../src/renderer/src/service/applicationLifecycle'
import type { ApplicationLifecycle } from '../src/renderer/src/typings'

/** 使用官方 AG-UI 客户端验证读取、显式重建及单一恢复结果之间的完整请求链。 */
test('缺少 checkpoint 但有文档时只发起审阅重建，不发送生成或确认动作', async () => {
  const actions: string[] = []
  const inputs: Array<{ action: string; sessionId?: string; sessionThreadId?: string }> = []
  let available: 'available' | 'blocked' | undefined = 'available'
  let prepared = false
  const lifecycle = {
    application: { id: 'document-review-client', name: '现有文档' }, revision: 1, updatedAt: '2026-11-09T00:00:00Z',
    initialization: { threadId: 'review-thread', stage: 'awaiting_technical_plan_confirmation', status: 'awaiting_user' },
    activeExecutions: {}, extensions: {}
  } as ApplicationLifecycle
  const server = http.createServer(async (request, response) => {
    // 假服务只提供规范 AG-UI 帧；产品调用与事件解析均使用官方客户端。
    assert.equal(request.url, '/application-lifecycle/run')
    let body = ''
    for await (const part of request) body += part
    const payload = JSON.parse(body)
    const input = payload.forwardedProps.applicationLifecycle
    actions.push(input.action)
    inputs.push(input)
    if (input.action === 'prepare_document_review') prepared = true
    const current = { ...lifecycle, revision: prepared ? 2 : 1,
      extensions: input.planningThreadId ? { recoverySnapshot: {
        applicationId: lifecycle.application.id, threadId: input.planningThreadId, kind: 'planning',
        lifecycleRevision: prepared ? 2 : 1, checkpointAvailable: prepared, observedAt: '2026-11-09T00:00:00Z',
        workflow: prepared ? { runId: 'new-review', threadId: 'review-thread', summary: { status: 'requires_user_input' }, state: {}, events: [] } : null,
        output: null, documentReview: prepared || !available ? null : { status: available, kind: 'technical_plan', message: '当前文档不完整' }
      } } : {} }
    const value = { schemaVersion: 1, runId: payload.runId, threadId: payload.threadId,
      status: 'completed', action: input.action, lifecycle: current }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    for (const event of [
      { type: 'RUN_STARTED', threadId: payload.threadId, runId: payload.runId },
      { type: 'CUSTOM', name: 'application-lifecycle', value },
      { type: 'STATE_SNAPSHOT', snapshot: { applicationLifecycle: value } },
      { type: 'RUN_FINISHED', threadId: payload.threadId, runId: payload.runId }
    ]) response.write(`data: ${JSON.stringify(event)}\n\n`)
    response.end()
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  assert.ok(address && typeof address === 'object')
  const previousWindow = globalThis.window
  Object.defineProperty(globalThis, 'window', { configurable: true, writable: true,
    value: { devAgentStudio: { agentBaseUrl: `http://127.0.0.1:${address.port}` } } })
  const application = { id: lifecycle.application.id, appName: '现有文档', workspaceRoot: '/document-review-client' }
  try {
    const [first, second] = await Promise.all([getApplicationLifecycle(application, 'review-thread', { planningThreadId: 'review-thread' }),
      getApplicationLifecycle(application, 'review-thread', { planningThreadId: 'review-thread' })])
    assert.equal(first, second)
    assert.equal(first.extensions.recoverySnapshot?.checkpointAvailable, true)
    assert.deepEqual(actions, ['workspace_attach', 'get', 'prepare_document_review'])
    actions.length = 0
    await getApplicationLifecycle(application, 'review-thread', { planningThreadId: 'review-thread' })
    assert.deepEqual(actions, ['workspace_attach', 'get'])
    // 同工作区的两个本机会话均须携带自己的身份执行 Attach，不能共享一次无作用域校准。
    prepared = true
    inputs.length = 0
    actions.length = 0
    await Promise.all(['a', 'b'].map((id) => getApplicationLifecycle(application, `control-${id}`,
      { sessionId: `session-${id}`, threadId: `thread-${id}` })))
    const attaches = inputs.filter((input) => input.action === 'workspace_attach')
    assert.deepEqual(attaches.map((input) => [input.sessionId, input.sessionThreadId]).sort(),
      [['session-a', 'thread-a'], ['session-b', 'thread-b']])
    assert.equal(actions.filter((action) => action === 'prepare_document_review').length, 0)
    prepared = false
    available = 'blocked'
    actions.length = 0
    await assert.rejects(getApplicationLifecycle(application, 'review-thread', { planningThreadId: 'review-thread' }), /当前文档不完整/)
    assert.deepEqual(actions, ['workspace_attach', 'get'])
    available = undefined
    actions.length = 0
    const empty = await getApplicationLifecycle(application, 'review-thread', { planningThreadId: 'review-thread' })
    assert.equal(empty.extensions.recoverySnapshot?.checkpointAvailable, false)
    assert.deepEqual(actions, ['workspace_attach', 'get'])
  } finally {
    Object.defineProperty(globalThis, 'window', { configurable: true, writable: true, value: previousWindow })
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})
