import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { requestCloudJson } from '../src/renderer/src/service/cloudApi'
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

test('Java JSON 请求从 Electron 内存读取 token 并强制使用 Bearer 头', async () => {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  let authorization: string | null = null
  Object.assign(globalThis, {
    window: {
      xcodeAgent: {
        auth: {
          getAccessToken: async () => ({ accessToken: 'memory-token' })
        }
      }
    },
    fetch: async (_url: string, init?: RequestInit) => {
      authorization = new Headers(init?.headers).get('Authorization')
      return jsonResponse(200, { ok: true })
    }
  })

  try {
    const result = await requestCloudJson<{ ok: boolean }>('https://cloud.example/api', {
      headers: { Authorization: 'Bearer caller-token' }
    })
    assert.deepEqual(result, { ok: true })
    assert.equal(authorization, 'Bearer memory-token')
  } finally {
    Object.assign(globalThis, { window: originalWindow, fetch: originalFetch })
  }
})

test('Java 401 与缺少内存 token 都发布认证失败', async () => {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  const statuses: number[] = []
  const unsubscribe = subscribeAuthenticationFailure((failure) => {
    statuses.push(failure.status)
  })

  try {
    Object.assign(globalThis, {
      window: {
        xcodeAgent: {
          auth: {
            getAccessToken: async () => ({ accessToken: 'memory-token' })
          }
        }
      },
      fetch: async () => jsonResponse(401)
    })
    await assert.rejects(requestCloudJson('https://cloud.example/api'))
    assert.deepEqual(statuses, [401])

    resetAuthenticationFailure()
    Object.assign(globalThis, {
      window: {
        xcodeAgent: {
          auth: {
            getAccessToken: async () => ({ accessToken: null })
          }
        }
      }
    })
    await assert.rejects(requestCloudJson('https://cloud.example/api'))
    assert.deepEqual(statuses, [401, 401])
  } finally {
    unsubscribe()
    Object.assign(globalThis, { window: originalWindow, fetch: originalFetch })
  }
})
