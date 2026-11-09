import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create } from 'react-test-renderer'
import type { HttpAgent } from '@ag-ui/client'
import { cancelUiDesignGeneration, getUiDesignGeneration } from '../src/renderer/src/service/uiDesignCancellation'
import { useUiDesignCancellation } from '../src/renderer/src/hooks/useUiDesignCancellation'

test('停止 SDK 必须收到精确代次的成功回执，业务失败、缺少回执和错误代次均拒绝', async () => {
  const previous = globalThis.window
  globalThis.window = { devAgentStudio: { agentBaseUrl: 'http://localhost:8000' } } as never
  try {
    for (const value of [
      { status: 'completed', pageId: 'home', stopToken: 'token-a', settled: true },
      { status: 'failed', error: { message: '后台仍在退出' } },
      { status: 'completed', pageId: 'home', stopToken: 'token-b', settled: true },
      { status: 'in_progress' }
    ]) {
      const transport = { runAgent: async (input, callbacks) => {
        assert.equal(input.forwardedProps?.uiDesignGeneration?.action, 'cancel')
        callbacks?.onCustomEvent?.({ event: { name: 'ui-design-generation', value } } as never)
        return { result: {} } as never
      } } as Pick<HttpAgent, 'runAgent'>
      const result = cancelUiDesignGeneration({ workspaceRoot: '/app', pageId: 'home', stopToken: 'token-a' }, transport)
      if (value.status === 'completed' && value.stopToken === 'token-a') await result
      else await assert.rejects(result)
    }
  } finally { globalThis.window = previous }
})

test('状态查询通过官方 SDK 读取后台凭据，不依赖本地清单标识', async () => {
  const previous = globalThis.window
  globalThis.window = {} as never
  try {
    const transport = { runAgent: async (input, callbacks) => {
      assert.deepEqual(input.forwardedProps?.uiDesignGeneration, { action: 'get', workspaceRoot: '/app', pageId: 'home' })
      callbacks?.onStateSnapshotEvent?.({ event: { snapshot: { uiDesignGeneration: {
        status: 'completed', pageId: 'home', stopToken: 'server-token', taskState: 'orphaned', pageStatus: 'generating'
      } } } } as never)
      return { result: {} } as never
    } } as Pick<HttpAgent, 'runAgent'>
    assert.equal((await getUiDesignGeneration({ workspaceRoot: '/app', pageId: 'home' }, transport)).stopToken, 'server-token')
  } finally { globalThis.window = previous }
})

test('切换工作区后迟到的任务查询不能发起停止', async () => {
  let hook: ReturnType<typeof useUiDesignCancellation>
  let release!: (value: Awaited<ReturnType<typeof getUiDesignGeneration>>) => void
  let stops = 0
  /** 用延迟后台查询覆盖读取后、停止前的应用切换边界。 */
  function Harness({ workspace }: { workspace: string }) {
    hook = useUiDesignCancellation(workspace, () => {}, async () => { stops += 1 },
      () => new Promise((resolve) => { release = resolve }))
    return null
  }
  let view: ReturnType<typeof create>
  try {
    await act(async () => { view = create(createElement(Harness, { workspace: '/app' })) })
    let pending!: Promise<void>
    await act(async () => { pending = hook.cancel('home'); await Promise.resolve() })
    await act(async () => { view.update(createElement(Harness, { workspace: '/other' })) })
    await act(async () => {
      release({ pageId: 'home', stopToken: 'old-token', taskState: 'active', pageStatus: 'generating' })
      await pending
    })
    assert.equal(stops, 0)
  } finally { act(() => view?.unmount()) }
})

test('停止 Hook 等待退出时不解锁，网络失败不回调停止成功，切换应用隔离旧回执', async () => {
  const previous = globalThis.window
  globalThis.window = { devAgentStudio: { workspace: { readUiDesigns: async () => ({ uiDesigns: {
    pages: [{ pageId: 'home', generation_id: 'generation-a', status: 'generating' }]
  } }) } } } as never
  let hook: ReturnType<typeof useUiDesignCancellation>
  let release: () => void = () => {}
  let stopped = 0
  let fail = false
  /** 保留停止请求窗口或返回确定性网络失败。 */
  const stop = async (): Promise<void> => {
    if (fail) throw new Error('网络断开，停止尚未完成')
    await new Promise<void>((resolve) => { release = resolve })
  }
  /** 挂载生产 Hook，观察实际 pending 与回调时序。 */
  function Harness({ workspace }: { workspace: string }) {
    hook = useUiDesignCancellation(workspace, () => { stopped += 1 }, stop,
      async () => ({ pageId: 'home', stopToken: 'token-a', taskState: 'active', pageStatus: 'generating' }))
    return null
  }
  let view: ReturnType<typeof create>
  try {
    await act(async () => { view = create(createElement(Harness, { workspace: '/app' })) })
    let pending: Promise<void>
    await act(async () => { pending = hook.cancel('home'); await Promise.resolve() })
    assert.deepEqual(hook!.stopping, ['home'])
    assert.equal(stopped, 0)
    await act(async () => { release(); await pending! })
    assert.deepEqual(hook!.stopping, [])
    assert.equal(stopped, 1)
    fail = true
    await act(async () => { await hook.cancel('home') })
    assert.match(hook!.error, /网络断开/)
    assert.equal(stopped, 1)
    fail = false
    await act(async () => { pending = hook.cancel('home'); await Promise.resolve() })
    await act(async () => { view!.update(createElement(Harness, { workspace: '/other-app' })) })
    await act(async () => { release(); await pending! })
    assert.equal(stopped, 1)
    assert.deepEqual(hook!.stopping, [])
  } finally {
    act(() => view?.unmount())
    globalThis.window = previous
  }
})

test('停止等待后台确认提交完成，缺少磁盘标识也能停止孤立任务', async () => {
  const previous = globalThis.window
  let reads = 0
  const requests: string[] = []
  globalThis.window = {} as never
  let hook: ReturnType<typeof useUiDesignCancellation>
  let stopped = 0
  /** 用生产 Hook 覆盖本地已占用、后台尚未登记的停止窗口。 */
  function Harness() {
    hook = useUiDesignCancellation('/app', () => { stopped += 1 }, async (request) => {
      requests.push(request.stopToken)
    }, async () => ({ pageId: 'home', stopToken: 'new-token',
      taskState: ++reads < 3 ? 'preparing' : 'orphaned', pageStatus: 'generating' }))
    return null
  }
  let view: ReturnType<typeof create>
  try {
    await act(async () => { view = create(createElement(Harness)) })
    await act(async () => { await hook.cancel('home') })
    assert.equal(reads, 3)
    assert.deepEqual(requests, ['new-token'])
    assert.equal(stopped, 1)
    assert.equal(hook!.error, '')
  } finally {
    act(() => view?.unmount())
    globalThis.window = previous
  }
})
