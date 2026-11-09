import assert from 'node:assert/strict'
import { test } from 'node:test'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createElement, type ReactElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { readSessionPage, readSessionSummary, saveSession } from '../src/main/sessionHistory'
import { useChatSessions } from '../src/renderer/src/components/AiChatPanel/hooks/useChatSessions'
import {
  SessionRuntimeProvider,
  useSessionRuntimeStore,
  type SessionRuntimeStore
} from '../src/renderer/src/components/AiChatPanel/hooks/useSessionRuntimeStore'
import {
  WorkbenchPhaseContext,
  type WorkbenchPhaseContextValue
} from '../src/renderer/src/context/workbenchPhaseState'
import type { ApplicationConfig } from '../src/renderer/src/typings'
import { HistoryMessageRow } from '../src/renderer/src/components/AiChatPanel/components/MessageList/HistoryMessageRow'
import { useHistoryScroll } from '../src/renderer/src/components/AiChatPanel/components/MessageList/useHistoryScroll'

test('生产 Hook + 真实文件：尾页恢复、上滚并发新回复、部分保存、失败重试和切换隔离', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'session-page-runtime-'))
  const oldWindow = globalThis.window,
    oldStorage = globalThis.localStorage
  const local = new Map<string, string>()
  const files = ['a', 'b'].map((id) => path.join(directory, `${id}.json`))
  const records = ['a', 'b'].map((id, index) => ({
    id,
    title: id,
    threadId: `thread-${id}`,
    workflowId: 'app',
    workspaceRoot: '/page-runtime',
    editorMode: 'frontend',
    workbenchPhase: 'development',
    stage: 'DEVELOPMENT',
    entryKey: id,
    sequence: index + 1,
    createdAt: index + 1,
    updatedAt: index + 1,
    messages: Array.from({ length: 65 }, (_, position) => ({
      id: position + 1,
      role: 'assistant',
      content: `${id} ${position + 1}`,
      createdAt: position + 1
    }))
  }))
  await Promise.all(
    records.map((record, index) => fs.writeFile(files[index], JSON.stringify(record)))
  )
  let release!: () => void,
    held: Promise<void> | undefined,
    failRead = false
  const native = {
    list: async () => ({ sessions: await Promise.all(files.map(readSessionSummary)) }),
    read: async ({
      sessionId,
      before,
      limit
    }: {
      sessionId: string
      before?: number
      limit?: number
    }) => {
      if (before !== undefined && held) await held
      if (failRead) throw new Error('history read failed')
      return structuredClone(await readSessionPage(files[sessionId === 'a' ? 0 : 1], before, limit))
    },
    save: async ({
      session,
      loadedMessageIds
    }: {
      session: Record<string, any>
      loadedMessageIds?: number[]
    }) => ({
      session: await saveSession(files[session.id === 'a' ? 0 : 1], session, loadedMessageIds)
    })
  }
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => local.get(key) ?? null,
      setItem: (key: string, value: string) => local.set(key, value)
    }
  })
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      devAgentStudio: { agentBaseUrl: 'http://history.test', sessions: native }
    }
  })
  let chat!: ReturnType<typeof useChatSessions>,
    store!: SessionRuntimeStore,
    root: ReactTestRenderer | undefined
  const application = { id: 'app', workspaceRoot: '/page-runtime' } as ApplicationConfig
  /** 运行生产加载和保存 effect；仅把 Electron IPC 接到临时文件存储。 */
  function Probe(): ReactElement {
    store = useSessionRuntimeStore()
    chat = useChatSessions({
      application,
      editorMode: 'frontend',
      workbenchPhase: 'development',
      onCloseRightPanel: () => undefined,
      onApplicationLifecycleChange: () => undefined
    })
    return createElement('output')
  }
  try {
    await act(async () => {
      root = create(
        createElement(
          SessionRuntimeProvider,
          null,
          createElement(
            WorkbenchPhaseContext.Provider,
            {
              value: {
                phase: 'development',
                recordReachedPhase: () => undefined
              } as unknown as WorkbenchPhaseContextValue
            },
            createElement(Probe)
          )
        )
      )
    })
    // 文件异步读取可能超过 React 首次 act，沿公开选择入口等待其完成。
    for (let index = 0; chat.loadingSessions && index < 100; index++) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 5))
      })
    }
    await act(async () => chat.handleOpenSession('a'))
    assert.equal(chat.messages.length, 10)
    assert.equal(chat.messages[0].id, 56)
    assert.equal(chat.activeSession!.threadId, 'thread-a')
    const key = chat.activeSession!.key
    held = new Promise((resolve) => {
      release = resolve
    })
    let pending!: Promise<void>
    await act(async () => {
      pending = chat.loadOlderMessages()
    })
    await act(async () => {
      store.setDraftByKey(key, '尚未发送的草稿')
      store.setSessionMessages(key, (messages) => [
        ...messages.map((message) =>
          message.id === 65 ? { ...message, content: '流式新回复' } : message
        ),
        { id: 66, role: 'assistant', content: '后台追加', createdAt: 66 }
      ])
    })
    await act(async () => {
      release()
      await pending
    })
    held = undefined
    assert.equal(chat.messages[0].id, 46)
    assert.equal(chat.messages.find((message) => message.id === 65)?.content, '流式新回复')
    assert.equal(chat.messages.at(-1)?.id, 66)
    assert.equal(chat.draft, '尚未发送的草稿')
    await act(async () =>
      chat.persistSession({
        sessionId: 'a',
        threadId: 'thread-a',
        editorMode: 'frontend',
        messages: chat.messages
      })
    )
    const saved = JSON.parse(await fs.readFile(files[0], 'utf8'))
    assert.equal(saved.messages.length, 66)
    assert.equal(saved.messages[0].content, 'a 1')
    failRead = true
    await act(async () => chat.loadOlderMessages())
    assert.match(chat.historyError || '', /history read failed/)
    assert.equal(chat.messages[0].id, 46)
    failRead = false
    held = new Promise((resolve) => {
      release = resolve
    })
    await act(async () => {
      pending = chat.loadOlderMessages()
    })
    await act(async () => chat.handleOpenSession('b'))
    await act(async () => {
      release()
      await pending
    })
    assert.equal(chat.activeSession!.threadId, 'thread-b')
    assert.equal(chat.messages.length, 10)
    assert.equal(chat.messages[0].content, 'b 56')
  } finally {
    await act(async () => root?.unmount())
    Object.defineProperty(globalThis, 'window', { configurable: true, value: oldWindow })
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: oldStorage })
    await fs.rm(directory, { recursive: true, force: true })
  }
})

test('视口外的历史不创建重卡片；用户交互后的内容不因滚走而卸载', async () => {
  const oldIntersection = globalThis.IntersectionObserver,
    oldResize = globalThis.ResizeObserver
  let notify!: (entries: Array<{ isIntersecting: boolean }>) => void
  let renders = 0
  /** 统计重组件的实际挂载；构造元素以判断空消息不等于挂载组件。 */
  function HeavyCard(): ReactElement {
    renders++
    return createElement('input')
  }
  Object.defineProperty(globalThis, 'IntersectionObserver', {
    configurable: true,
    value: class {
      constructor(callback: typeof notify) {
        notify = callback
      }
      observe(): void {}
      disconnect(): void {}
    }
  })
  Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: undefined })
  let root!: ReactTestRenderer
  try {
    await act(async () => {
      root = create(
        createElement(HistoryMessageRow, {
          id: 1,
          initiallyVisible: false,
          protectedRow: false,
          render: () => createElement(HeavyCard)
        }),
        {
          createNodeMock: () => ({ parentElement: { parentElement: {} } })
        }
      )
    })
    assert.equal(renders, 0)
    await act(async () => notify([{ isIntersecting: true }]))
    assert.equal(root.root.findAllByType('input').length, 1)
    await act(async () => root.root.findByType('div').props.onFocusCapture())
    await act(async () => notify([{ isIntersecting: false }]))
    assert.equal(root.root.findAllByType('input').length, 1)
  } finally {
    await act(async () => root.unmount())
    Object.defineProperty(globalThis, 'IntersectionObserver', {
      configurable: true,
      value: oldIntersection
    })
    Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: oldResize })
  }
})

test('前插历史与虚拟行实际高度变化均保持阅读锚点，跟随底部时不干预滚动', async () => {
  const oldResize = globalThis.ResizeObserver
  let resize!: () => void,
    offset = 100,
    remember!: () => void
  const following = { current: false }
  const container = {
    scrollTop: 80,
    firstElementChild: {},
    getBoundingClientRect: () => ({ top: 0 }),
    querySelector: () => element,
    querySelectorAll: () => [element],
    addEventListener: () => undefined,
    removeEventListener: () => undefined
  }
  const element = {
    dataset: { messageId: '42' },
    getBoundingClientRect: () => ({
      top: offset - container.scrollTop,
      bottom: offset - container.scrollTop + 80
    })
  }
  const ref = { current: container } as unknown as import('react').RefObject<HTMLDivElement>
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class {
      constructor(callback: () => void) {
        resize = callback
      }
      observe(): void {}
      disconnect(): void {}
    }
  })
  /** 在真实 React 提交间模拟异步历史前插与虚拟行高度收敛。 */
  function Probe({ firstId }: { firstId: number }): ReactElement {
    remember = useHistoryScroll(ref, firstId, following)
    return createElement('output')
  }
  let root!: ReactTestRenderer
  try {
    await act(async () => {
      root = create(createElement(Probe, { firstId: 42 }))
    })
    remember()
    offset += 500
    await act(async () => root.update(createElement(Probe, { firstId: 22 })))
    assert.equal(element.getBoundingClientRect().top, 20)
    offset += 150
    resize()
    assert.equal(element.getBoundingClientRect().top, 20)
    const top = container.scrollTop
    following.current = true
    offset += 100
    resize()
    assert.equal(container.scrollTop, top)
  } finally {
    await act(async () => root.unmount())
    Object.defineProperty(globalThis, 'ResizeObserver', { configurable: true, value: oldResize })
  }
})
