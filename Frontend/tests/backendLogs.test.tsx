import assert from 'node:assert/strict'
import { test } from 'node:test'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { runBackendLogs, type BackendLogsSnapshot } from '../src/renderer/src/service/backendLogs'
import { useBackendLogs } from '../src/renderer/src/components/BackendLogDrawer/useBackendLogs'
import {
  formatLogEntry,
  visibleLogEntries
} from '../src/renderer/src/components/BackendLogDrawer/backendLogPresentation'

/** 创建包含多行异常的真实日志投影，便于核对排序和筛选。 */
function snapshot(revision = 1): BackendLogsSnapshot {
  return {
    revision,
    filePath: '/tmp/backend.log',
    updatedAt: '2026-11-03T08:00:00Z',
    storageError: '',
    truncated: false,
    entries: [
      {
        id: 'a',
        timestamp: '2026-11-03T08:00:00Z',
        level: 'ERROR',
        source: 'business',
        text: 'Traceback\n  第一行\nValueError: failed\n'
      },
      {
        id: 'b',
        timestamp: '2026-11-03T08:00:01Z',
        level: 'INFO',
        source: 'model',
        text: '[model-tool-calls] [{"args":{"code":"a\\n  b"}}]\n'
      }
    ]
  }
}

test('按条目倒序而不颠倒堆栈，搜索覆盖来源与完整字面量，复制保留原文', () => {
  const logs = snapshot().entries
  const reversed = visibleLogEntries(logs, '', true)
  assert.deepEqual(
    reversed.map((entry) => entry.id),
    ['b', 'a']
  )
  assert.deepEqual(
    logs.map((entry) => entry.id),
    ['a', 'b']
  )
  assert.match(formatLogEntry(reversed[1]), /Traceback\n {2}第一行\nValueError: failed/)
  assert.deepEqual(
    visibleLogEntries(logs, 'BUSINESS', false).map((entry) => entry.id),
    ['a']
  )
  assert.deepEqual(
    visibleLogEntries(logs, '[{"args"', false).map((entry) => entry.id),
    ['b']
  )
  assert.equal(visibleLogEntries(logs, 'missing', true).length, 0)
})

type Feed = { action: string; aborted: boolean; emit: (value: unknown) => void; finish: () => void }

/** 用标准 SSE Response 驱动真实 AG-UI SDK，不替换生产客户端或 Hook。 */
function installTransport(): {
  feeds: Feed[]
  restore: () => void
  fail: (value: boolean) => void
} {
  const originalFetch = globalThis.fetch
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const feeds: Feed[] = []
  let failure = false
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      devAgentStudio: { agentBaseUrl: 'http://logs.test' },
      setTimeout,
      clearTimeout
    }
  })
  globalThis.fetch = async (_input, init): Promise<Response> => {
    if (failure) throw new Error('连接中断')
    const body = JSON.parse(String(init?.body))
    const action = body.forwardedProps.backendLogs.action
    const encoder = new TextEncoder()
    let controller: ReadableStreamDefaultController<Uint8Array>
    let ended = false
    const stream = new ReadableStream<Uint8Array>({
      start(value) {
        controller = value
      },
      cancel() {
        ended = true
      }
    })
    /** 将真实 AG-UI 事件编码到 SDK 消费的 SSE 传输。 */
    const event = (value: unknown): void => {
      if (!ended) controller.enqueue(encoder.encode(`data: ${JSON.stringify(value)}\n\n`))
    }
    /** 完整结束测试服务响应，并释放 abort 监听。 */
    const finish = (): void => {
      if (ended) return
      ended = true
      init?.signal?.removeEventListener('abort', abort)
      controller.close()
    }
    /** 记录 SDK 是否实际中止了旧请求。 */
    const abort = (): void => {
      feed.aborted = true
      finish()
    }
    const feed: Feed = {
      action,
      aborted: false,
      finish,
      emit(value) {
        event({ type: 'CUSTOM', name: 'backend-logs', value })
        event({ type: 'STATE_SNAPSHOT', snapshot: { backendLogs: value } })
      }
    }
    feeds.push(feed)
    init?.signal?.addEventListener('abort', abort, { once: true })
    event({ type: 'RUN_STARTED', threadId: body.threadId, runId: body.runId })
    const payload = { status: action === 'get' ? 'completed' : 'in_progress', logs: snapshot() }
    feed.emit(payload)
    if (action === 'get') {
      event({
        type: 'RUN_FINISHED',
        threadId: body.threadId,
        runId: body.runId,
        result: { backendLogs: payload }
      })
      finish()
    }
    return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })
  }
  return {
    feeds,
    fail(value) {
      failure = value
    },
    restore() {
      feeds.forEach((feed) => feed.finish())
      globalThis.fetch = originalFetch
      if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
      else Reflect.deleteProperty(globalThis, 'window')
    }
  }
}

/** 等待真实 SDK 的流式分发进入 React effect。 */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 25))
  })
}

test('真实 SDK 读取日志并去重 Custom 与 StateSnapshot 双重投影', async () => {
  const transport = installTransport()
  try {
    const updates: BackendLogsSnapshot[] = []
    const logs = await runBackendLogs('get', { onUpdate: (value) => updates.push(value) })
    assert.equal(logs.filePath, '/tmp/backend.log')
    assert.equal(updates.length, 1)
    assert.deepEqual(
      transport.feeds.map((feed) => feed.action),
      ['get']
    )
    const controller = new AbortController()
    controller.abort()
    await assert.rejects(runBackendLogs('watch', { signal: controller.signal }), /已关闭/)
    assert.equal(transport.feeds.length, 1)
  } finally {
    transport.restore()
  }
})

test('刷新、关闭自动刷新和卸载均取消旧订阅，断线保留最后快照', async () => {
  const transport = installTransport()
  let renderer: ReactTestRenderer | undefined
  let state: ReturnType<typeof useBackendLogs> | undefined
  /** 在真实 React 生命周期中观察日志读取状态。 */
  function Probe({ auto = true }: { auto?: boolean }): null {
    state = useBackendLogs(auto)
    return null
  }
  try {
    await act(async () => {
      renderer = create(<Probe />)
    })
    await settle()
    assert.deepEqual(
      transport.feeds.map((feed) => feed.action),
      ['get', 'watch']
    )
    const watch = transport.feeds[1]
    await act(async () => {
      watch.emit({ status: 'in_progress', logs: snapshot(2) })
    })
    await settle()
    assert.equal(state!.snapshot?.revision, 2)
    await act(async () => {
      state!.refresh()
    })
    await settle()
    assert.equal(watch.aborted, true)
    assert.deepEqual(
      transport.feeds.map((feed) => feed.action),
      ['get', 'watch', 'get', 'watch']
    )
    transport.fail(true)
    await act(async () => {
      state!.refresh()
    })
    await settle()
    assert.match(state!.error, /连接中断/)
    assert.ok(state!.snapshot?.entries.length)
    assert.equal(transport.feeds[3].aborted, true)
    transport.fail(false)
    await act(async () => {
      renderer!.update(<Probe auto={false} />)
    })
    await settle()
    assert.equal(transport.feeds.at(-1)?.action, 'get')
    assert.equal(state!.error, '')
    const count = transport.feeds.length
    await settle()
    assert.equal(transport.feeds.length, count)
    await act(async () => {
      renderer!.update(<Probe />)
    })
    await settle()
    const last = transport.feeds.at(-1)!
    assert.equal(last.action, 'watch')
    await act(async () => {
      renderer!.unmount()
      renderer = undefined
    })
    assert.equal(last.aborted, true)
  } finally {
    await act(async () => renderer?.unmount())
    transport.restore()
  }
})
