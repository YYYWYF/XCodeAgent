import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create } from 'react-test-renderer'
import type { HttpAgent } from '@ag-ui/client'
import { cancelUiDesignGeneration } from '../src/renderer/src/service/uiDesignCancellation'
import { useUiDesignCancellation } from '../src/renderer/src/hooks/useUiDesignCancellation'

test('停止 SDK 必须收到精确代次的成功回执，业务失败、缺少回执和错误代次均拒绝', async () => {
  const previous = globalThis.window
  globalThis.window = { devAgentStudio: { agentBaseUrl: 'http://localhost:8000' } } as never
  try {
    for (const value of [
      { status: 'completed', pageId: 'home', generationId: 'generation-a', cancelled: true },
      { status: 'failed', error: { message: '后台仍在退出' } },
      { status: 'completed', pageId: 'home', generationId: 'generation-b', cancelled: true },
      { status: 'in_progress' }
    ]) {
      const transport = { runAgent: async (input, callbacks) => {
        assert.equal(input.forwardedProps?.uiDesignGeneration?.action, 'cancel')
        callbacks?.onCustomEvent?.({ event: { name: 'ui-design-generation', value } } as never)
        return { result: {} } as never
      } } as Pick<HttpAgent, 'runAgent'>
      const result = cancelUiDesignGeneration({ workspaceRoot: '/app', pageId: 'home', generationId: 'generation-a' }, transport)
      if (value.status === 'completed' && value.generationId === 'generation-a') await result
      else await assert.rejects(result)
    }
  } finally { globalThis.window = previous }
})

test('停止 Hook 等待退出时不解锁，网络失败不回调停止成功，切换应用隔离旧回执', async () => {
  const previous = globalThis.window
  globalThis.window = { devAgentStudio: { workspace: { readUiDesigns: async () => ({ uiDesigns: {
    pages: [{ pageId: 'home', generation_id: 'generation-a' }]
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
    hook = useUiDesignCancellation(workspace, () => { stopped += 1 }, stop)
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
