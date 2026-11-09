import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setTimeout as delay } from 'node:timers/promises'
import { createElement, type ReactElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import {
  UiDesignPageSource,
  subscribeDesignSource,
  type DesignPageSource
} from '../src/renderer/src/hooks/uiDesignPageSource'
import { useUiDesignPagesWithCode } from '../src/renderer/src/hooks/useUiDesignPagesWithCode'
import { UiReadVisibility } from '../src/renderer/src/hooks/uiReadVisibility'
import { SessionMessageCache } from '../src/renderer/src/components/AiChatPanel/hooks/sessionMessageCache'
import { enqueueSessionSave } from '../src/renderer/src/components/AiChatPanel/hooks/sessionSaveQueue'
import {
  SessionRuntimeProvider,
  useSessionRuntimeStore,
  type SessionRuntimeStore
} from '../src/renderer/src/components/AiChatPanel/hooks/useSessionRuntimeStore'
import { createSessionIdentity } from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import './sessionCacheRecovery.test'

/** 构造可从磁盘完整恢复的终态消息；不使用真实用户历史。 */
function messages(content = '已保存正文'): AgentChatMessage[] {
  return [{ id: 1, role: 'assistant', content, createdAt: 1 }]
}

/** 为异步读取/保存提供可控收口点，覆盖旧回执迟到。 */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

test('生成期间串行轮询，终态后停止，无变化不发布新快照', async () => {
  let reads = 0,
    publications = 0
  let pages: DesignPageSource[] = [{ pageId: 'home', status: 'generating' }]
  const source = new UiDesignPageSource(
    async () => {
      reads++
      return pages
    },
    () => true,
    5
  )
  source.subscribe(() => {
    publications++
  })
  source.updateSource('first-run', true)
  try {
    await delay(18)
    assert.ok(reads >= 2)
    assert.equal(publications, 2)
    pages = [{ pageId: 'home', status: 'confirmed', code: 'tsx' }]
    await delay(12)
    const stoppedAt = reads
    await delay(18)
    assert.equal(reads, stoppedAt)
    assert.equal(source.snapshot.codes.home, 'tsx')
    source.updateSource('regenerate', true)
    await delay(1)
    assert.equal(reads, stoppedAt + 1)
  } finally {
    source.dispose()
  }
})

test('慢读取不会重叠，期间多次刷新合并，旧回执不能中止新生成', async () => {
  const held = deferred<DesignPageSource[]>()
  let reads = 0,
    concurrent = 0,
    peak = 0
  const source = new UiDesignPageSource(
    async () => {
      concurrent++
      peak = Math.max(peak, concurrent)
      const result = ++reads === 1 ? await held.promise : [{ pageId: 'home', status: 'generating' }]
      concurrent--
      return result
    },
    () => true,
    100
  )
  source.updateSource('old', false)
  source.updateSource('new', true)
  source.refresh()
  source.refresh()
  try {
    await delay(5)
    assert.equal(reads, 1)
    held.resolve([{ pageId: 'home', status: 'confirmed' }])
    await delay(2)
    assert.equal(reads, 2)
    assert.equal(peak, 1)
    assert.equal(source.snapshot.statuses.home, 'generating')
  } finally {
    source.dispose()
  }
})

test('隐藏期间不再读取，重新可见补读，销毁后隔离迟到结果', async () => {
  let visible = true,
    reads = 0
  const held = deferred<DesignPageSource[]>()
  const source = new UiDesignPageSource(
    async () => {
      reads++
      return held.promise
    },
    () => visible,
    5
  )
  source.updateSource('run', true)
  visible = false
  source.pause()
  held.resolve([{ pageId: 'home', status: 'generating' }])
  await delay(12)
  assert.equal(reads, 1)
  visible = true
  source.refresh()
  await delay(1)
  assert.equal(reads, 2)
  source.dispose()
  await delay(10)
  assert.equal(reads, 2)
  assert.deepEqual(source.snapshot.codes, {})
})

test('同工作区真实 hook 共用读取，隐藏工作台释放展示订阅，重返与切换工作区不串数据', async () => {
  const oldWindow = globalThis.window,
    oldDocument = globalThis.document
  const documentStub = Object.assign(new EventTarget(), { visibilityState: 'visible' })
  const calls: string[] = []
  const windowStub = Object.assign(new EventTarget(), {
    devAgentStudio: {
      workspace: {
        readUiDesigns: async ({ workspaceRoot }: { workspaceRoot: string }) => {
          calls.push(workspaceRoot)
          return {
            uiDesigns: { pages: [{ pageId: 'home', status: 'confirmed', code: workspaceRoot }] }
          }
        }
      }
    }
  })
  Object.defineProperty(globalThis, 'window', { configurable: true, value: windowStub })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: documentStub })
  const sourcePages = [{ pageId: 'home', status: 'confirmed' }]
  let shown: DesignPageSource[][] = []
  let root: ReactTestRenderer | undefined
  /** 两个真实组件订阅相同工作区，模拟左右两处设计稿展示。 */
  function Probe({ workspace }: { workspace: string }): ReactElement {
    shown.push(useUiDesignPagesWithCode(workspace, sourcePages).pages)
    return createElement('output')
  }
  /** 保持组件挂载，仅改变工作台可见性，确保不会影响后台运行对象。 */
  const tree = (visible: boolean, workspace = 'A'): ReactElement =>
    createElement(
      UiReadVisibility.Provider,
      { value: visible },
      createElement(Probe, { workspace }),
      createElement(Probe, { workspace })
    )
  try {
    await act(async () => {
      root = create(tree(true))
    })
    assert.deepEqual(calls, ['A'])
    assert.equal(shown.at(-1)?.[0].code, 'A')
    const renders = shown.length
    await act(async () => {
      windowStub.dispatchEvent(new Event('focus'))
    })
    assert.equal(shown.length, renders)
    await act(async () => {
      root!.update(tree(false))
    })
    const hiddenAt = calls.length
    await act(async () => {
      windowStub.dispatchEvent(new Event('focus'))
    })
    assert.equal(calls.length, hiddenAt)
    await act(async () => {
      root!.update(tree(true, 'B'))
    })
    assert.equal(shown.at(-1)?.[0].code, 'B')
    assert.equal(calls.at(-1), 'B')
  } finally {
    await act(async () => root?.unmount())
    Object.defineProperty(globalThis, 'window', { configurable: true, value: oldWindow })
    Object.defineProperty(globalThis, 'document', { configurable: true, value: oldDocument })
  }
})

test('隐藏后马上重新订阅也不重叠 IPC，旧读取可供新视图使用', async () => {
  const oldWindow = globalThis.window,
    oldDocument = globalThis.document
  const held = deferred<{ uiDesigns: { pages: DesignPageSource[] } }>()
  let reads = 0
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: Object.assign(new EventTarget(), {
      devAgentStudio: {
        workspace: {
          readUiDesigns: async () => {
            reads++
            return held.promise
          }
        }
      }
    })
  })
  Object.defineProperty(globalThis, 'document', {
    configurable: true,
    value: Object.assign(new EventTarget(), { visibilityState: 'visible' })
  })
  let second: ReturnType<typeof subscribeDesignSource> | undefined
  try {
    const first = subscribeDesignSource('/slow-reentry', () => undefined)
    first.source.updateSource('initial', false)
    await delay(1)
    first.release()
    second = subscribeDesignSource('/slow-reentry', () => undefined)
    second.source.updateSource('reenter', false)
    await delay(1)
    assert.equal(reads, 1)
    held.resolve({
      uiDesigns: { pages: [{ pageId: 'home', status: 'confirmed', code: 'after-reentry' }] }
    })
    await delay(1)
    assert.equal(second.source.snapshot.codes.home, 'after-reentry')
    assert.equal(reads, 1)
  } finally {
    second?.release()
    Object.defineProperty(globalThis, 'window', { configurable: true, value: oldWindow })
    Object.defineProperty(globalThis, 'document', { configurable: true, value: oldDocument })
  }
})

test('已保存历史按最近使用和体积回收，大记录不会挤掉全部小记录', () => {
  const cache = new SessionMessageCache(2, 1500)
  cache.restore('old', messages())
  cache.restore('recent', messages())
  cache.read('old')
  cache.restore('new', messages())
  assert.deepEqual(
    cache.evict(() => false),
    ['recent']
  )
  cache.restore('large', messages('x'.repeat(2000)))
  assert.deepEqual(
    cache.evict(() => false),
    ['large']
  )
  assert.equal(cache.has('old'), true)
  assert.equal(cache.has('new'), true)
})

test('未保存、保存失败、后台运行和当前视图在零预算下仍保留', () => {
  const cache = new SessionMessageCache(0, 0)
  cache.update('dirty', messages())
  cache.restore('view', messages())
  const release = cache.retain('view')
  cache.restore('running', messages())
  cache.restore('saving', messages())
  const token = cache.beginSave('saving', cache.read('saving'))
  assert.deepEqual(
    cache.evict((key) => key === 'running'),
    []
  )
  cache.finishSave(token, false)
  assert.deepEqual(
    cache.evict((key) => key === 'running'),
    []
  )
  release()
  assert.deepEqual(
    cache.evict((key) => key === 'running'),
    ['view']
  )
})

test('旧版本保存成功不清除新消息的脏标记，最新版本成功后才允许回收', () => {
  const cache = new SessionMessageCache(0)
  cache.update('session', messages('first'))
  const old = cache.beginSave('session', cache.read('session'))
  cache.update('session', messages('new-content'))
  cache.finishSave(old, true)
  assert.deepEqual(
    cache.evict(() => false),
    []
  )
  const token = cache.beginSave(
    'session',
    cache.read('session').map((message) => ({ ...message }))
  )
  cache.finishSave(token, true)
  assert.deepEqual(
    cache.evict(() => false),
    ['session']
  )
  cache.restore('session', messages('new-content'))
  assert.equal(cache.read('session')[0].content, 'new-content')
})

test('删除后同名重建拒绝旧保存回执，迟到磁盘读取不覆盖未保存消息', () => {
  const cache = new SessionMessageCache(0)
  cache.update('session', messages('old'))
  const old = cache.beginSave('session', cache.read('session'))
  cache.remove('session')
  cache.update('session', messages('new'))
  const current = cache.beginSave('session', cache.read('session'))
  cache.finishSave(old, true)
  cache.restore('session', messages('stale disk'))
  assert.equal(cache.read('session')[0].content, 'new')
  assert.deepEqual(
    cache.evict(() => false),
    []
  )
  cache.finishSave(current, true)
  assert.deepEqual(
    cache.evict(() => false),
    ['session']
  )
})

test('待确认和暂停恢复的完整快照保留，缓存缺失与空正文分开', () => {
  const cache = new SessionMessageCache(0)
  for (const status of ['requires_user_input', 'paused', 'running']) {
    const value = messages()
    value[0].workflow = {
      runId: status,
      threadId: 'original-thread',
      summary: { status },
      events: []
    }
    cache.restore(status, value)
  }
  cache.restore('empty', [])
  assert.equal(cache.has('empty'), true)
  assert.deepEqual(
    cache.evict(() => false),
    ['empty']
  )
  assert.equal(cache.has('empty'), false)
  assert.equal(cache.read('paused')[0].workflow?.threadId, 'original-thread')
})

test('同会话慢旧保存先完成，后保存才开始，失败后仍可继续保存', async () => {
  const held = deferred<void>()
  const order: string[] = []
  const first = enqueueSessionSave('same', async () => {
    order.push('first-start')
    await held.promise
    order.push('first-end')
  })
  const second = enqueueSessionSave('same', async () => {
    order.push('second')
  })
  await delay(1)
  assert.deepEqual(order, ['first-start'])
  held.resolve()
  await Promise.all([first, second])
  assert.deepEqual(order, ['first-start', 'first-end', 'second'])
  const failed = enqueueSessionSave('same', async () => {
    throw new Error('disk full')
  })
  const recovered = enqueueSessionSave('same', async () => 'saved')
  await assert.rejects(failed, /disk full/)
  assert.equal(await recovered, 'saved')
})

test('真实 Provider 回收闲置客户端，后台任务、草稿和技能保留；重新注册原身份恢复历史', async () => {
  let runtime!: SessionRuntimeStore, root: ReactTestRenderer | undefined
  const oldWindow = globalThis.window
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { devAgentStudio: { agentBaseUrl: 'http://cache.test' } }
  })
  /** 挂载生产 Provider，读取真实状态和回收 effect。 */
  function Probe(): ReactElement {
    runtime = useSessionRuntimeStore()
    return createElement('output')
  }
  /** 为每份记录固定会话与原 thread，验证回收不改变恢复身份。 */
  const identity = (index: number) =>
    createSessionIdentity({
      workspaceRoot: '/cache',
      editorMode: 'frontend',
      sessionId: `s${index}`,
      threadId: `t${index}`,
      workflowId: 'app',
      workbenchPhase: 'development'
    })
  try {
    await act(async () => {
      root = create(createElement(SessionRuntimeProvider, null, createElement(Probe)))
    })
    await act(async () => {
      runtime.registerSession(identity(0), messages('background'))
      runtime.acquireSessionExecution(identity(0), true)
      runtime.setDraftByKey(identity(1).key, '未发送草稿')
      runtime.setSelectedSkillsByKey(identity(1).key, [{ name: 'test-skill' }] as never)
      for (let i = 1; i <= 7; i++) runtime.registerSession(identity(i), messages(`history-${i}`))
    })
    assert.equal(runtime.hasSessionMessages(identity(0).key), true)
    assert.equal(runtime.sessionExecutions.length, 1)
    assert.equal(runtime.hasSessionMessages(identity(1).key), false)
    assert.equal(runtime.agUiSessionsRef.current[identity(1).key], undefined)
    assert.equal(runtime.draftForKey(identity(1).key), '未发送草稿')
    assert.equal(runtime.selectedSkillsForKey(identity(1).key).length, 1)
    assert.equal(runtime.getIdentity(identity(1).key)?.threadId, 't1')
    assert.equal(Object.keys(runtime.agUiSessionsRef.current).length, 4)
    await act(async () => {
      runtime.registerSession(identity(1), messages('history-1'))
    })
    assert.equal(runtime.getSessionMessages(identity(1).key)[0].content, 'history-1')
    assert.equal(runtime.agUiSessionsRef.current[identity(1).key].threadId, 't1')
    await act(async () => {
      runtime.releaseSessionExecution(identity(0).key)
    })
    assert.equal(runtime.sessionExecutions.length, 0)
  } finally {
    await act(async () => root?.unmount())
    Object.defineProperty(globalThis, 'window', { configurable: true, value: oldWindow })
  }
})
