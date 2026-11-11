import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { renderToStaticMarkup } from 'react-dom/server'
import { useBuiltinAuthorizationTask } from '../src/renderer/src/components/AiChatPanel/components/ProcessSteps/useBuiltinAuthorizationTask'
import BuiltinAuthorizationTaskCard from '../src/renderer/src/components/AiChatPanel/components/ProcessSteps/BuiltinAuthorizationTaskCard'
import { authorizationTaskWorkflowResult, runBuiltinPatch, type BuiltinPatchResult } from '../src/renderer/src/service/builtinAuthorization'

test('重试按钮以后端信号启用，UNKNOWN 与输入变化不能绕过保护', () => {
  const task: BuiltinPatchResult = { status: 'needs_attention', stage: 'seed', schemaReady: true,
    seedReady: false, message: '请人工核对', failureCategory: 'inputs_changed' }
  const blocked = renderToStaticMarkup(createElement(BuiltinAuthorizationTaskCard, { task, workspace: '/workspace' }))
  assert.match(blocked, /待处理|inputs_changed/)
  assert.match(blocked, />刷新状态</)
  const blockedRetry = blocked.match(/<button\b[^>]*title="当前状态不能安全重试[^>]*>/)?.[0]
  assert.ok(blockedRetry)
  assert.match(blockedRetry, /disabled=""/)
  const recoverable = renderToStaticMarkup(createElement(BuiltinAuthorizationTaskCard, {
    task: { ...task, failureCategory: 'timeout', nextAction: 'resume' }, workspace: '/workspace'
  }))
  assert.match(recoverable, />重\s*试</)
  const retryable = renderToStaticMarkup(createElement(BuiltinAuthorizationTaskCard, {
    task: { ...task, failureCategory: 'seed_invalid_response', nextAction: 'retry' }, workspace: '/workspace'
  }))
  assert.match(retryable, /title="恢复初始化，保留原操作并避免重复提交"/)
  assert.doesNotMatch(retryable, /disabled=""/)
})

test('显式重试通过官方 AG-UI 客户端发送 retry，并保留后端重试资格', async () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const originalFetch = globalThis.fetch
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { devAgentStudio: { agentBaseUrl: 'http://authorization.test' } } })
  globalThis.fetch = async (_url, init) => {
    const payload = JSON.parse(String(init?.body))
    assert.deepEqual(payload.forwardedProps.builtinAuthorization, { action: 'retry', workspaceRoot: '/workspace' })
    const value = { status: 'completed', patch: { status: 'needs_attention', stage: 'seed', schemaReady: true,
      seedReady: false, message: '查询失败，可重试', failureCategory: 'seed_invalid_response', nextAction: 'retry' } }
    return new Response([
      { type: 'RUN_STARTED', runId: payload.runId, threadId: payload.threadId },
      { type: 'CUSTOM', name: 'builtin-authorization', value },
      { type: 'STATE_SNAPSHOT', snapshot: { builtinAuthorization: value } },
      { type: 'RUN_FINISHED', runId: payload.runId, threadId: payload.threadId }
    ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } })
  }
  try {
    const result = await runBuiltinPatch('/workspace', 'retry')
    assert.equal(result?.nextAction, 'retry')
    assert.equal(authorizationTaskWorkflowResult(result!).nextAction, 'retry')
  } finally {
    globalThis.fetch = originalFetch
    if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})

test('只读轮询隔离工作区迟到响应，历史不查询，隐藏暂停且卸载清理', async () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
  const originalFetch = globalThis.fetch
  const originalSetTimeout = globalThis.setTimeout
  const originalClearTimeout = globalThis.clearTimeout
  const listeners = new Set<() => void>()
  const document = { visibilityState: 'visible',
    /** 记录可见性事件，使用真实 Hook 的生命周期。 */
    addEventListener: (_: string, callback: () => void) => { listeners.add(callback) },
    /** 卸载时必须释放监听器。 */
    removeEventListener: (_: string, callback: () => void) => { listeners.delete(callback) } }
  const timers = new Map<number, () => void>()
  const pending: Array<{ workspace: string; release: () => void }> = []
  let attachCalls = 0
  let backendInstanceId = `backend-${'1'.repeat(32)}`
  let nextTimer = 0
  let current: BuiltinPatchResult | undefined
  let renderer: ReactTestRenderer | undefined
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { devAgentStudio: { agentBaseUrl: 'http://authorization.test' } } })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: document })
  globalThis.setTimeout = ((callback: () => void, delay: number, ...args: unknown[]) => {
    if (delay !== 3000) return originalSetTimeout(callback, delay, ...args)
    timers.set(++nextTimer, callback)
    return nextTimer
  }) as typeof setTimeout
  globalThis.clearTimeout = ((id: number) => {
    if (!timers.delete(id)) originalClearTimeout(id)
  }) as typeof clearTimeout
  globalThis.fetch = async (_url, init) => {
    const payload = JSON.parse(String(init?.body))
    const input = payload.forwardedProps.builtinAuthorization
    if (!input) {
      assert.equal(payload.forwardedProps.applicationLifecycle.action, 'workspace_attach')
      attachCalls += 1
      const value = { schemaVersion: 1, runId: payload.runId, threadId: payload.threadId, status: 'completed', lifecycle: {} }
      return new Response([
        { type: 'RUN_STARTED', runId: payload.runId, threadId: payload.threadId },
        { type: 'CUSTOM', name: 'application-lifecycle', value },
        { type: 'RUN_FINISHED', runId: payload.runId, threadId: payload.threadId }
      ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } })
    }
    assert.equal(input.action, 'get')
    await new Promise<void>((release) => { pending.push({ workspace: input.workspaceRoot, release }) })
    const value = { status: 'completed', backendInstanceId, patch: { status: 'running', stage: 'schema', schemaReady: false,
      seedReady: false, message: input.workspaceRoot } }
    const events = [
      { type: 'RUN_STARTED', runId: payload.runId, threadId: payload.threadId },
      { type: 'CUSTOM', name: 'builtin-authorization', value },
      { type: 'RUN_FINISHED', runId: payload.runId, threadId: payload.threadId }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'Content-Type': 'text/event-stream' }
    })
  }
  /** 仅渲染 Hook，无宿主 DOM 或第二套轮询实现。 */
  function Harness({ workspace }: { workspace?: string }) {
    current = useBuiltinAuthorizationTask(workspace)
    return null
  }
  /** 等待 SDK 消费当前内存流，不使用真实计时等待。 */
  async function flush(): Promise<void> {
    await new Promise<void>((resolve) => setImmediate(resolve))
  }
  try {
    await act(async () => { renderer = create(createElement(Harness, {})); await flush() })
    assert.equal(pending.length, 0)
    await act(async () => { renderer!.update(createElement(Harness, { workspace: '/a' })); await flush() })
    await act(async () => { renderer!.update(createElement(Harness, { workspace: '/b' })); await flush() })
    assert.deepEqual(pending.map((item) => item.workspace), ['/a', '/b'])
    await act(async () => { pending[0].release(); await flush() })
    assert.equal(current, undefined)
    await act(async () => { pending[1].release(); await flush() })
    assert.equal(current?.message, '/b')
    assert.equal(timers.size, 1)
    backendInstanceId = `backend-${'2'.repeat(32)}`
    await act(async () => {
      const callbacks = [...timers.values()]
      timers.clear()
      callbacks.forEach((callback) => callback())
      await flush()
      pending[2].release()
      await flush()
    })
    assert.equal(attachCalls, 1)
    document.visibilityState = 'hidden'
    await act(async () => {
      const callbacks = [...timers.values()]
      timers.clear()
      callbacks.forEach((callback) => callback())
      await flush()
    })
    assert.equal(pending.length, 3)
    document.visibilityState = 'visible'
    await act(async () => { listeners.forEach((callback) => callback()); await flush() })
    assert.equal(pending.length, 4)
    await act(async () => { pending[3].release(); await flush() })
    assert.equal(attachCalls, 2)
    await act(async () => { renderer!.unmount(); await flush() })
    assert.equal(listeners.size, 0)
    assert.equal(timers.size, 0)
  } finally {
    renderer?.unmount()
    globalThis.fetch = originalFetch
    globalThis.setTimeout = originalSetTimeout
    globalThis.clearTimeout = originalClearTimeout
    if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
    else Reflect.deleteProperty(globalThis, 'window')
    if (originalDocument) Object.defineProperty(globalThis, 'document', originalDocument)
    else Reflect.deleteProperty(globalThis, 'document')
  }
})
