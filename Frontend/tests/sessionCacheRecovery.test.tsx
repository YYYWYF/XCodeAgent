import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, type ReactElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
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
import type { ChatSessionRecord } from '../src/renderer/src/service/chatSessions'
import type { ApplicationConfig } from '../src/renderer/src/typings'

test('真实会话链路：回收后重新读盘恢复原 Thread，失败保存和迟到保存不丢新消息，当前大会话不回收', async () => {
  const oldWindow = globalThis.window,
    oldStorage = globalThis.localStorage
  const storage = new Map<string, string>()
  const records: ChatSessionRecord[] = Array.from({ length: 7 }, (_, index) => ({
    id: `session-${index}`,
    title: `历史 ${index}`,
    threadId: `thread-${index}`,
    workflowId: 'cache-app',
    editorMode: 'frontend',
    workbenchPhase: 'development',
    stage: 'DEVELOPMENT',
    sequence: index + 1,
    entryKey: `entry-${index}`,
    workspaceRoot: '/cache-recovery',
    createdAt: index + 1,
    updatedAt: index + 1,
    messages: [{ id: 1, role: 'assistant', content: `已保存 ${index}`, createdAt: 1 }]
  }))
  const reads: string[] = []
  let failSave = false,
    holdSave: Promise<void> | undefined,
    resolveSave!: () => void
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key)
    }
  })
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      devAgentStudio: {
        agentBaseUrl: 'http://cache-recovery.test',
        sessions: {
          list: async () => ({
            sessions: records.map((record) => ({ ...record, messageCount: record.messages.length }))
          }),
          read: async ({ sessionId }: { sessionId: string }) => {
            reads.push(sessionId)
            return { session: records.find((record) => record.id === sessionId) }
          },
          save: async ({ session }: { session: ChatSessionRecord }) => {
            if (holdSave) await holdSave
            if (failSave) throw new Error('disk full')
            records[records.findIndex((record) => record.id === session.id)] = session
            return { ok: true, session: { ...session, messageCount: session.messages.length } }
          }
        }
      }
    }
  })
  let root: ReactTestRenderer | undefined,
    chat!: ReturnType<typeof useChatSessions>,
    runtime!: SessionRuntimeStore
  const phase = {
    phase: 'development',
    recordReachedPhase: () => undefined
  } as unknown as WorkbenchPhaseContextValue
  const application = { id: 'cache-app', workspaceRoot: '/cache-recovery' } as ApplicationConfig
  /** 使用生产加载、保存、选择与缓存 effect，IPC 仅替换为可控持久化边界。 */
  function Probe(): ReactElement {
    runtime = useSessionRuntimeStore()
    chat = useChatSessions({
      application,
      editorMode: 'frontend',
      workbenchPhase: 'development',
      onCloseRightPanel: () => undefined,
      onApplicationLifecycleChange: () => undefined
    })
    return createElement('output')
  }
  /** 沿正式会话选择入口切换，确保 pin 的更新顺序与产品一致。 */
  const open = async (index: number): Promise<void> => {
    await act(async () => chat.handleOpenSession(`session-${index}`))
  }
  /** 将当前正文提交到正式保存入口，不绕过保存版本追踪。 */
  const save = (): Promise<void> =>
    chat.persistSession({
      sessionId: chat.activeSession!.sessionId,
      threadId: chat.activeSession!.threadId,
      editorMode: 'frontend',
      messages: chat.messages
    })
  try {
    await act(async () => {
      root = create(
        createElement(
          SessionRuntimeProvider,
          null,
          createElement(WorkbenchPhaseContext.Provider, { value: phase }, createElement(Probe))
        )
      )
    })
    await open(0)
    const originalKey = chat.activeSession!.key
    await open(1)
    await open(2)
    await open(3)
    await open(4)
    assert.equal(runtime.hasSessionMessages(originalKey), false)
    const readBefore = reads.filter((id) => id === 'session-0').length
    await open(0)
    assert.equal(reads.filter((id) => id === 'session-0').length, readBefore + 1)
    assert.equal(chat.messages[0].content, '已保存 0')
    assert.equal(chat.activeSession!.threadId, 'thread-0')
    await act(async () =>
      runtime.setSessionMessages(originalKey, [{ ...chat.messages[0], content: '未保存的新内容' }])
    )
    failSave = true
    await act(async () => {
      await assert.rejects(save(), /disk full/)
    })
    await open(1)
    await open(2)
    await open(3)
    await open(4)
    await open(0)
    assert.equal(chat.messages[0].content, '未保存的新内容')
    failSave = false
    holdSave = new Promise<void>((resolve) => {
      resolveSave = resolve
    })
    let pending!: Promise<void>
    await act(async () => {
      pending = save()
    })
    await act(async () =>
      runtime.setSessionMessages(originalKey, [
        { ...chat.messages[0], content: '旧保存尚未完成时的新消息' }
      ])
    )
    await act(async () => {
      resolveSave()
      await pending
    })
    holdSave = undefined
    await open(1)
    await open(2)
    await open(3)
    await open(4)
    await open(0)
    assert.equal(chat.messages[0].content, '旧保存尚未完成时的新消息')
    await act(async () => save())
    await open(1)
    await open(2)
    await open(3)
    await open(4)
    assert.equal(runtime.hasSessionMessages(originalKey), false)
    // 仅使用临时测试记录验证超过 32MiB 估算预算的当前会话仍被保护。
    records[0].messages[0].content = '大'.repeat(17 * 1024 * 1024)
    await open(0)
    assert.equal(runtime.hasSessionMessages(originalKey), true)
    assert.equal(chat.messages[0].content.length, 17 * 1024 * 1024)
    assert.equal(chat.activeSession!.threadId, 'thread-0')
  } finally {
    await act(async () => root?.unmount())
    Object.defineProperty(globalThis, 'window', { configurable: true, value: oldWindow })
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: oldStorage })
  }
})
