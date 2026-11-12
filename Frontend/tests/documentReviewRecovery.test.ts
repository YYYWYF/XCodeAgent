import assert from 'node:assert/strict'
import http from 'node:http'
import { test } from 'node:test'
import { getApplicationLifecycle, getApplicationLifecycleSummary, openApplicationWorkspace } from '../src/renderer/src/service/applicationLifecycle'
import type { ApplicationLifecycle } from '../src/renderer/src/typings'
import { ApplicationPlanningRuntime } from '../src/renderer/src/service/applicationPlanningRuntime'
import { loadActiveApplicationPlannings, reduceApplicationPlanningCurrentState, type ApplicationPlanningCurrentState } from '../src/renderer/src/service/activeApplicationPlanning'
import { initialConnectionState } from '../src/renderer/src/service/connectionState'

test('首页摘要未完成但 Attach 校准已就绪时，不登记多余的规划 Runtime', async () => {
  const previousWindow = globalThis.window
  const previousFetch = globalThis.fetch
  const application = { id: 'attach-completed', workspaceRoot: '/attach-completed', name: '校准完成', lastOpenedAt: 1 }
  const inputs: Array<{ action: string; includeRecovery?: boolean }> = []
  Object.defineProperty(globalThis, 'window', { configurable: true, writable: true, value: {
    localStorage: { setItem: () => undefined },
    devAgentStudio: { agentBaseUrl: 'http://attachment-completed.test',
      applications: { load: async () => ({ applications: [application] }) },
      workspace: { readApplication: async () => ({ application: { appName: application.name } }) } }
  } })
  /** 使用官方 AG-UI 客户端模拟摘要与正式校准之间发生的阶段变化。 */
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(String(init?.body))
    const input = request.forwardedProps.applicationLifecycle
    inputs.push(input)
    const lifecycle = { application: { id: application.id }, revision: 1,
      initialization: { threadId: 'original-thread', stage: input.includeRecovery === false
        ? 'awaiting_technical_plan_confirmation' : 'ready_for_workbench' }, activeExecutions: {}, extensions: {} }
    const payload = { schemaVersion: 1, runId: request.runId, threadId: request.threadId, status: 'completed', lifecycle,
      workspaceAttach: { workspaceReconciled: true, sessionDeferred: false } }
    const events = [{ type: 'RUN_STARTED', threadId: request.threadId, runId: request.runId },
      { type: 'STATE_SNAPSHOT', snapshot: { applicationLifecycle: payload } },
      { type: 'RUN_FINISHED', threadId: request.threadId, runId: request.runId }]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
      { headers: { 'Content-Type': 'text/event-stream' } })
  }
  try {
    assert.deepEqual(await loadActiveApplicationPlannings(), [])
    assert.deepEqual(inputs.map((input) => input.action), ['get', 'workspace_attach'])
    assert.equal(inputs[0].includeRecovery, false)
  } finally {
    globalThis.fetch = previousFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, writable: true, value: previousWindow })
  }
})

/** 使用官方 AG-UI 客户端验证读取、显式重建及单一恢复结果之间的完整请求链。 */
test('缺少 checkpoint 但有文档时只发起审阅重建，不发送生成或确认动作', async () => {
  const actions: string[] = []
  const inputs: Array<{ action: string; sessionId?: string; sessionThreadId?: string; reconcileWorkspace?: boolean; includeRecovery?: boolean }> = []
  let available: 'available' | 'blocked' | undefined = 'available'
  let prepared = false
  const lifecycle = {
    application: { id: 'document-review-client', name: '现有文档' }, revision: 1, updatedAt: '2026-11-09T00:00:00Z',
    initialization: { threadId: 'review-thread', stage: 'awaiting_technical_plan_confirmation', status: 'awaiting_user' },
    activeExecutions: {}, extensions: {}
  } as ApplicationLifecycle
  const server = http.createServer(async (request, response) => {
    // 假服务只提供规范 AG-UI 帧；产品调用与事件解析均使用官方客户端。
    assert.ok(['/application-lifecycle/run', '/application-page-planning/run'].includes(request.url || ''))
    let body = ''
    for await (const part of request) body += part
    const payload = JSON.parse(body)
    const input = payload.forwardedProps.applicationLifecycle || payload.forwardedProps.applicationPlanningRecovery
    if (request.url === '/application-page-planning/run') {
      const value = available === 'blocked' ? { schemaVersion: 1, status: 'failed', error: { message: '当前文档不完整' } }
        : prepared ? { schemaVersion: 1, status: 'completed', runId: 'new-review', threadId: 'review-thread', summary: { status: 'requires_user_input' }, state: {}, events: [] }
        : { schemaVersion: 1, status: 'completed', checkpointAvailable: false }
      const events = [{ type: 'RUN_STARTED', runId: payload.runId, threadId: payload.threadId },
        { type: 'CUSTOM', name: 'workflow-run', value },
        { type: 'RUN_FINISHED', runId: payload.runId, threadId: payload.threadId, result: { workflow: value } }]
      response.writeHead(200, { 'Content-Type': 'text/event-stream' })
      response.end(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''))
      return
    }
    actions.push(input.action)
    inputs.push(input)
    if (input.action === 'prepare_document_review') prepared = true
    const current = { ...lifecycle, revision: prepared ? 2 : 1,
      extensions: input.planningThreadId ? { documentReview: prepared || !available ? null
        : { status: available, kind: 'technical_plan', message: '当前文档不完整' } } : {} }
    const value = { schemaVersion: 1, runId: payload.runId, threadId: payload.threadId,
      status: 'completed', action: input.action, lifecycle: current,
      workspaceAttach: { workspaceReconciled: input.reconcileWorkspace !== false, sessionDeferred: false } }
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
    assert.equal(first.extensions.documentReview, null)
    assert.deepEqual(actions, ['workspace_attach', 'get', 'prepare_document_review'])
    actions.length = 0
    await getApplicationLifecycle(application, 'review-thread', { planningThreadId: 'review-thread' })
    assert.deepEqual(actions, ['get'])
    // 同工作区的两个本机会话均须携带自己的身份执行 Attach，不能共享一次无作用域校准。
    prepared = true
    inputs.length = 0
    actions.length = 0
    await Promise.all(['a', 'b'].map((id) => getApplicationLifecycle(application, `control-${id}`,
      { sessionId: `session-${id}`, threadId: `thread-${id}` })))
    const attaches = inputs.filter((input) => input.action === 'workspace_attach')
    assert.deepEqual(attaches.map((input) => [input.sessionId, input.sessionThreadId]).sort(),
      [['session-a', 'thread-a'], ['session-b', 'thread-b']])
    assert.ok(attaches.every((input) => input.reconcileWorkspace === false), '会话校准不能重复全工作区扫描')
    assert.deepEqual(actions, ['workspace_attach', 'workspace_attach'])
    assert.equal(actions.filter((action) => action === 'prepare_document_review').length, 0)
    actions.length = 0
    await getApplicationLifecycle(application, 'control-a', { sessionId: 'session-a', threadId: 'thread-a' })
    assert.deepEqual(actions, ['get'], '已校准会话后续查询仍读最新状态，但不再 Attach')
    actions.length = 0
    await getApplicationLifecycleSummary(application)
    assert.deepEqual(actions, ['get'])
    assert.equal(inputs.at(-1)?.includeRecovery, false)
    actions.length = 0
    await openApplicationWorkspace(application)
    assert.deepEqual(actions, ['workspace_attach'], '显式重入重新校准并直接消费完整回执')
    actions.length = 0
    await getApplicationLifecycle(application)
    assert.deepEqual(actions, ['get'], '普通读取不再自动接管后台实例')
    prepared = false
    available = 'blocked'
    actions.length = 0
    await assert.rejects(getApplicationLifecycle(application, 'review-thread', { planningThreadId: 'review-thread' }), /当前文档不完整/)
    assert.deepEqual(actions, ['get'])
    // 本机旧项目重入时使用生产 Runtime 与官方 AG-UI 读取：文档错误不能冒充后端失联。
    let state = { application, lifecycle, threadId: 'review-thread', restoreArtifactsFromDisk: true,
      transportState: 'idle', connection: initialConnectionState(true) } as ApplicationPlanningCurrentState
    let writes = 0
    const runtime = new ApplicationPlanningRuntime({ applicationId: application.id, threadId: state.threadId,
      getCurrentState: () => state,
      dispatchCurrentEvent: (event) => { state = reduceApplicationPlanningCurrentState(state, event) },
      publishContent: () => {}, publishReply: () => {}, publishWorkflow: () => {},
      onTechnicalPlanConfirmed: async () => true, onRevisionContinuation: async () => {},
      session: { hasActiveRun: () => false, stop: async () => {},
        sendMessage: async () => { writes++; throw new Error('恢复读取不能生成或确认') } } })
    try {
      await runtime.ensureStarted()
      assert.equal(state.connection.status, 'healthy', '后端明确返回文档问题，不能显示重连')
      assert.equal(state.syncError, '当前文档不完整')
      assert.equal(state.transportState, 'idle')
      await runtime.reconcileCurrentState()
      assert.equal(state.connection.status, 'healthy', '重复同步业务错误也不能进入重连循环')
      available = undefined
      assert.equal((await runtime.reconcileCurrentState()).status, 'checkpoint_missing')
      assert.equal(state.connection.status, 'healthy')
      assert.equal(state.syncError, undefined, '读取成功应清除旧业务读取错误')
      prepared = true
      assert.equal((await runtime.reconcileCurrentState()).status, 'uncertain')
      assert.equal(state.connection.status, 'healthy', '响应缺少有效恢复解释属于状态问题')
      assert.equal(state.syncError, '读取应用规划权威状态没有返回有效的 Recovery Projection。')
      assert.equal(writes, 0)
    } finally { runtime.dispose() }
    prepared = false
    available = undefined
    actions.length = 0
    const empty = await getApplicationLifecycle(application, 'review-thread', { planningThreadId: 'review-thread' })
    assert.equal(empty.extensions.documentReview, null)
    assert.deepEqual(actions, ['get'])
  } finally {
    Object.defineProperty(globalThis, 'window', { configurable: true, writable: true, value: previousWindow })
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})
