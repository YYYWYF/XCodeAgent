import { runAuthentication } from '../src/main/authClient'
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { requestCloudJson } from '../src/renderer/src/service/cloudApi'
import { AgUiChatSession } from '../src/renderer/src/service/agUiAgent'
import {
  createAgUiHttpAgent,
  createHttpError,
  getAuthenticationFailureStatus,
  isAuthenticationFailure,
  reportAuthenticationFailure,
  resetAuthenticationFailure,
  subscribeAuthenticationFailure
} from '../src/renderer/src/service/authentication'

afterEach(() => {
  resetAuthenticationFailure()
})

/** 创建指定状态的 JSON HTTP 响应。 */
function jsonResponse(status: number, payload: unknown = { detail: 'request failed' }): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' }
  })
}

/** 创建包含 AG-UI 事件的 SSE 响应。 */
function eventStreamResponse(events: unknown[]): Response {
  const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('')
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' }
  })
}

for (const status of [401, 403] as const) {
  test(`HttpAgent ${status} 同时通知全局和本地 subscriber`, async () => {
    const authenticationStatuses: number[] = []
    const localCalls: string[] = []
    const unsubscribe = subscribeAuthenticationFailure((failure) => {
      authenticationStatuses.push(failure.status)
    })
    const agent = createAgUiHttpAgent({
      url: 'https://example.invalid/workflow/run',
      fetch: async () => jsonResponse(status)
    })

    await assert.rejects(
      agent.runAgent(
        {},
        {
          onRunFailed: () => {
            localCalls.push('failed')
          },
          onRunFinalized: () => {
            localCalls.push('finalized')
          }
        }
      ),
      (error) => getAuthenticationFailureStatus(error) === status
    )

    unsubscribe()
    assert.deepEqual(authenticationStatuses, [status])
    assert.deepEqual(localCalls, ['failed', 'finalized'])
  })
}

test('认证错误识别优先读取 status 并兼容 HTTP 消息', () => {
  assert.equal(isAuthenticationFailure(createHttpError(401)), true)
  assert.equal(isAuthenticationFailure(new Error('HTTP 403: forbidden')), true)
  assert.equal(isAuthenticationFailure(createHttpError(500)), false)
  assert.equal(isAuthenticationFailure(new Error('network unavailable')), false)
})

test('并发认证失败只发布一次，重置后允许再次发布', () => {
  const statuses: number[] = []
  const unsubscribe = subscribeAuthenticationFailure((failure) => {
    statuses.push(failure.status)
  })

  reportAuthenticationFailure(createHttpError(401))
  reportAuthenticationFailure(createHttpError(403))
  assert.deepEqual(statuses, [401])

  resetAuthenticationFailure()
  reportAuthenticationFailure(createHttpError(403))
  assert.deepEqual(statuses, [401, 403])
  unsubscribe()
})

test('HTTP 500 和网络错误不会发布认证失败', async () => {
  const statuses: number[] = []
  const unsubscribe = subscribeAuthenticationFailure((failure) => {
    statuses.push(failure.status)
  })

  const serverErrorAgent = createAgUiHttpAgent({
    url: 'https://example.invalid/workflow/run',
    fetch: async () => jsonResponse(500)
  })
  await assert.rejects(serverErrorAgent.runAgent())

  const networkErrorAgent = createAgUiHttpAgent({
    url: 'https://example.invalid/workflow/run',
    fetch: async () => {
      throw new Error('network unavailable')
    }
  })
  await assert.rejects(networkErrorAgent.runAgent())

  unsubscribe()
  assert.deepEqual(statuses, [])
})

test('AG-UI RUN_ERROR 只交给业务 subscriber', async () => {
  const statuses: number[] = []
  const runErrors: string[] = []
  const unsubscribe = subscribeAuthenticationFailure((failure) => {
    statuses.push(failure.status)
  })
  const agent = createAgUiHttpAgent({
    url: 'https://example.invalid/workflow/run',
    fetch: async () =>
      eventStreamResponse([
        { type: 'RUN_STARTED', threadId: 'thread-1', runId: 'run-1' },
        { type: 'RUN_ERROR', message: 'business failure', code: 'BUSINESS_ERROR' }
      ])
  })

  await agent.runAgent(
    {},
    {
      onRunErrorEvent: ({ event }) => {
        runErrors.push(event.message)
      }
    }
  )

  unsubscribe()
  assert.deepEqual(statuses, [])
  assert.deepEqual(runErrors, ['business failure'])
})

test('Workflow 会话把 RUN_ERROR 提升为可供初始化界面展示的异常', async () => {
  const originalFetch = globalThis.fetch
  const streamedContent: string[] = []
  Object.assign(globalThis, {
    fetch: async () =>
      eventStreamResponse([
        { type: 'RUN_STARTED', threadId: 'thread-init', runId: 'run-init' },
        { type: 'TEXT_MESSAGE_START', messageId: 'message-init', role: 'assistant' },
        { type: 'TEXT_MESSAGE_END', messageId: 'message-init' },
        {
          type: 'CUSTOM',
          name: 'workflow-run',
          value: {
            runId: 'run-init',
            threadId: 'thread-init',
            summary: {
              status: 'failed',
              phase: 'build',
              buildSummary: { retry_available: true, retryable_failures: 1 }
            },
            events: []
          }
        },
        { type: 'RUN_ERROR', message: '模型请求超时，请稍后重试。', code: 'MODEL_TIMEOUT' }
      ])
  })

  try {
    const session = new AgUiChatSession(
      'thread-init',
      'https://example.invalid/application-page-planning/run'
    )
    const run = session.sendMessage('初始化应用', {
      editorMode: 'frontend',
      onContent: (content) => streamedContent.push(content)
    })
    const error = await run.catch((caughtError) => caughtError)
    assert.match(String(error), /模型请求超时，请稍后重试/)
    assert.equal((error as { name?: string }).name, 'AgUiRunError')
    assert.equal(
      (error as { workflow?: { summary?: { phase?: string } } }).workflow?.summary?.phase,
      'build'
    )
    assert.equal(
      streamedContent.every((content) => content === ''),
      true
    )
  } finally {
    Object.assign(globalThis, { fetch: originalFetch })
  }
})

test('AG-UI Python 请求不会携带 access_token', async () => {
  let authorization: string | null = 'unexpected'
  const agent = createAgUiHttpAgent({
    url: 'https://example.invalid/workflow/run',
    fetch: async (_url, init) => {
      authorization = new Headers(init.headers).get('Authorization')
      return jsonResponse(500)
    }
  })

  await assert.rejects(agent.runAgent())
  assert.equal(authorization, null)
})

test('云端请求通过 IPC 交给 Python，渲染器不读取 token 或发送 fetch', async () => {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  let sent: unknown
  Object.assign(globalThis, {
    window: { devAgentStudio: { auth: { requestCloudJson: async (input: unknown) => {
      sent = input
      return { httpStatus: 200, body: { ok: true } }
    } } } },
    fetch: async () => { throw new Error('渲染器不得直接调用云端') }
  })
  try {
    const result = await requestCloudJson('https://cloud.example/api', {
      method: 'POST', body: '{}', headers: { Authorization: 'Bearer should-not-forward' }
    })
    assert.deepEqual(result, { ok: true })
    assert.deepEqual(sent, { url: 'https://cloud.example/api', method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } })
  } finally { Object.assign(globalThis, { window: originalWindow, fetch: originalFetch }) }
})

for (const status of [401, 403] as const) {
  test(`Python 云端代理返回 ${status} 仍触发全局认证提示`, async () => {
    const originalWindow = globalThis.window
    const statuses: number[] = []
    const unsubscribe = subscribeAuthenticationFailure((failure) => statuses.push(failure.status))
    Object.assign(globalThis, { window: { devAgentStudio: { auth: {
      requestCloudJson: async () => ({ httpStatus: status, body: { detail: '认证拒绝' } })
    } } } })
    try {
      await assert.rejects(requestCloudJson('https://cloud.example/api'))
      assert.deepEqual(statuses, [status])
    } finally { unsubscribe(); Object.assign(globalThis, { window: originalWindow }) }
  })
}

test('主进程消费 Python AG-UI 进度和最终文件登录状态', async () => {
  const originalFetch = globalThis.fetch
  const progress: string[] = []
  let sent: unknown
  Object.assign(globalThis, { fetch: async (_url: unknown, init: RequestInit) => {
    sent = JSON.parse(String(init.body)).forwardedProps.authentication
    return eventStreamResponse([
      { type: 'RUN_STARTED', threadId: 'auth-thread', runId: 'auth-run' },
      { type: 'CUSTOM', name: 'authentication', value: { status: 'in_progress', progress: { stage: 'awaiting_authorization', message: '等待授权', percent: 25 } } },
      { type: 'RUN_FINISHED', threadId: 'auth-thread', runId: 'auth-run', result: { authentication: { status: 'completed', authenticated: true, user: { id: 'u1', name: '用户' } } } }
    ])
  } })
  try {
    const result = await runAuthentication('http://127.0.0.1:8000', { action: 'status' }, (value) => { progress.push(value.progress!.stage) })
    assert.equal(result.authenticated, true)
    assert.deepEqual(progress, ['awaiting_authorization'])
    assert.deepEqual(sent, { action: 'status' })
  } finally { Object.assign(globalThis, { fetch: originalFetch }) }
})

test('主进程拒绝非本机后端及不完整的认证结果', async () => {
  await assert.rejects(runAuthentication('https://remote.example', { action: 'status' }), /本机/)
  const originalFetch = globalThis.fetch
  Object.assign(globalThis, { fetch: async () => eventStreamResponse([
    { type: 'RUN_STARTED', threadId: 't', runId: 'r' },
    { type: 'RUN_FINISHED', threadId: 't', runId: 'r', result: {} }
  ]) })
  try { await assert.rejects(runAuthentication('http://127.0.0.1:8000', { action: 'status' }), /完整结果/) }
  finally { Object.assign(globalThis, { fetch: originalFetch }) }
})

test('浏览器打开失败不会被 SDK subscriber 吞掉', async () => {
  const originalFetch = globalThis.fetch
  Object.assign(globalThis, { fetch: async () => eventStreamResponse([
    { type: 'RUN_STARTED', threadId: 't', runId: 'r' },
    { type: 'CUSTOM', name: 'authentication', value: { status: 'in_progress', progress: { stage: 'awaiting_authorization', message: '等待授权', percent: 25 } } },
    { type: 'RUN_FINISHED', threadId: 't', runId: 'r', result: { authentication: { status: 'completed' } } }
  ]) })
  try {
    await assert.rejects(runAuthentication('http://127.0.0.1:8000', { action: 'login' }, () => { throw new Error('浏览器打开失败') }), /浏览器打开失败/)
  } finally { Object.assign(globalThis, { fetch: originalFetch }) }
})
