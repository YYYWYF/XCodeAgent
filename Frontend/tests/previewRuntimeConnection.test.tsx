import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create } from 'react-test-renderer'
import { usePreviewRuntimeConnection } from '../src/renderer/src/components/AiChatPanel/hooks/usePreviewRuntimeConnection'

/** 返回真实 AG-UI 信封，连接和订阅逻辑使用生产 Hook 与官方客户端。 */
function reply(request: Record<string, any>, value: unknown = { status: 'completed' }): Response {
  const frames = [
    { type: 'RUN_STARTED', threadId: request.threadId, runId: request.runId },
    { type: 'CUSTOM', name: 'preview-runtime', value },
    { type: 'RUN_FINISHED', threadId: request.threadId, runId: request.runId }
  ]
  return new Response(frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join(''),
    { headers: { 'content-type': 'text/event-stream' } })
}

test('空闲预览只读一次，业务订阅断流后不定时重试，用户读取成功才恢复', async () => {
  const savedWindow = globalThis.window, savedFetch = globalThis.fetch
  Object.defineProperty(globalThis, 'window', { configurable: true,
    value: { devAgentStudio: { agentBaseUrl: 'http://agent.test' } } })
  let root: ReturnType<typeof create> | undefined
  let connection!: ReturnType<typeof usePreviewRuntimeConnection>
  let fail = false
  const actions: string[] = []
  /** 状态接收不反向修改订阅依赖，避免测试自身制造重复请求。 */
  const receive = (): void => {}
  /** 挂载生产 Hook；只有明确打开预览状态时才订阅 watch。 */
  function Probe({ watching = false }): ReturnType<typeof createElement> {
    connection = usePreviewRuntimeConnection('/preview-request-driven', undefined, false, receive, watching)
    return createElement('div')
  }
  globalThis.fetch = async (_url, init) => {
    const request = JSON.parse(String(init?.body))
    const action = request.forwardedProps.previewRuntime.action
    actions.push(action)
    if (fail || action === 'watch') throw new TypeError('Failed to fetch')
    return reply(request)
  }
  try {
    await act(async () => { root = create(createElement(Probe)) })
    assert.deepEqual(actions, ['get'])
    assert.equal(connection.connection.status, 'healthy')
    fail = true
    await act(async () => { await assert.rejects(connection.read()) })
    assert.equal(connection.connection.status, 'unavailable')
    const before = actions.length
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1100)) })
    assert.equal(actions.length, before, '失败后不能每秒探测')
    fail = false
    await act(async () => { await connection.read() })
    assert.equal(connection.connection.status, 'healthy')
    assert.equal(actions.length, before + 1, '用户正常读取只发起一次请求')
    await act(async () => { root!.update(createElement(Probe, { watching: true })) })
    assert.deepEqual(actions.slice(-2), ['get', 'watch'])
    assert.equal(connection.connection.status, 'unavailable')
    const afterWatch = actions.length
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1100)) })
    assert.equal(actions.length, afterWatch, '业务订阅断开后也不能自动重试')
  } finally {
    await act(async () => { root?.unmount() })
    globalThis.fetch = savedFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: savedWindow })
  }
})

test('预览业务拒绝、无有效结果和 HTTP 失败经真实 SDK 保留操作原因，不标记 Backend 断线', async () => {
  const savedWindow = globalThis.window, savedFetch = globalThis.fetch
  Object.defineProperty(globalThis, 'window', { configurable: true,
    value: { devAgentStudio: { agentBaseUrl: 'http://agent.test' } } })
  let root: ReturnType<typeof create> | undefined
  let connection!: ReturnType<typeof usePreviewRuntimeConnection>
  /** 隔离状态接收，使错误只来自真实请求及响应。 */
  const receive = (): void => {}
  /** 为每种错误挂载生产 Hook 的首次只读请求。 */
  function Probe({ scope }: { scope: string }): ReturnType<typeof createElement> {
    connection = usePreviewRuntimeConnection(scope, undefined, false, receive, false)
    return createElement('div')
  }
  try {
    for (const scenario of ['business', 'empty', 'http']) {
      globalThis.fetch = async (_url, init) => {
        const request = JSON.parse(String(init?.body))
        return scenario === 'http' ? new Response('预览状态文件读取失败', { status: 500 })
          : reply(request, scenario === 'empty' ? null : { status: 'failed', error: { message: '已有预览维护任务' } })
      }
      await act(async () => { root = create(createElement(Probe, { scope: `/preview-${scenario}` })) })
      assert.equal(connection.connection.status, 'healthy')
      assert.match(connection.businessError, scenario === 'business' ? /已有预览维护任务/ : scenario === 'empty' ? /没有返回有效状态/ : /HTTP 500.*预览状态文件读取失败/)
      assert.equal(connection.connection.lastError, undefined)
      globalThis.fetch = async (_url, init) => reply(JSON.parse(String(init?.body)))
      await act(async () => { await connection.read() })
      assert.equal(connection.businessError, '')
      await act(async () => { root?.unmount() })
      root = undefined
    }
  } finally {
    await act(async () => { root?.unmount() })
    globalThis.fetch = savedFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: savedWindow })
  }
})
