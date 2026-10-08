import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import {
  lstatIfPresent,
  movePathToTrashIfPresent,
  removeDirectoryIfPresent
} from '../src/main/filesystem'
import { prepareSessionDeletion } from '../src/renderer/src/service/applicationLifecycle'
import {
  sessionRuntimeKey,
  sessionRuntimeKeyBelongsToWorkspace
} from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import type { ApplicationLifecycle } from '../src/renderer/src/typings'
import { removeStoredApplication } from '../src/renderer/src/service/applicationStorage'

/** 验证已不存在的路径可以重复执行删除而不产生错误。 */
test('删除不存在的目录按幂等成功处理', async () => {
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-delete-'))
  const missingDirectory = path.join(temporaryRoot, 'missing-project')

  try {
    assert.equal(await lstatIfPresent(missingDirectory), undefined)
    await removeDirectoryIfPresent(missingDirectory)
    assert.equal(await lstatIfPresent(missingDirectory), undefined)
  } finally {
    await fs.rm(temporaryRoot, { force: true, recursive: true })
  }
})

/** 验证实际存在的目录仍然由删除工具移除。 */
test('删除存在的目录仍然执行递归删除', async () => {
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-delete-'))
  const projectDirectory = path.join(temporaryRoot, 'project')

  try {
    await fs.mkdir(path.join(projectDirectory, 'nested'), { recursive: true })
    await fs.writeFile(path.join(projectDirectory, 'nested', 'file.txt'), 'content', 'utf8')
    await removeDirectoryIfPresent(projectDirectory)
    assert.equal(await lstatIfPresent(projectDirectory), undefined)
  } finally {
    await fs.rm(temporaryRoot, { force: true, recursive: true })
  }
})

/** 验证项目路径通过系统回收站适配器转移，不再执行永久删除。 */
test('存在的项目路径会移入系统回收站', async () => {
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-trash-'))
  const projectDirectory = path.join(temporaryRoot, 'project')
  const trashedPaths: string[] = []

  try {
    await fs.mkdir(projectDirectory)
    await movePathToTrashIfPresent(projectDirectory, async (targetPath) => {
      trashedPaths.push(targetPath)
    })
    assert.deepEqual(trashedPaths, [projectDirectory])
  } finally {
    await fs.rm(temporaryRoot, { force: true, recursive: true })
  }
})

/** 验证不存在的路径不会调用系统回收站，也不会产生错误。 */
test('不存在的项目路径跳过系统回收站调用', async () => {
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-trash-'))
  const missingDirectory = path.join(temporaryRoot, 'missing-project')
  let trashCallCount = 0

  try {
    await movePathToTrashIfPresent(missingDirectory, async () => {
      trashCallCount += 1
    })
    assert.equal(trashCallCount, 0)
  } finally {
    await fs.rm(temporaryRoot, { force: true, recursive: true })
  }
})

/** 验证项目删除时只匹配同一绝对工作区的会话运行态，不误删同名目录记录。 */
test('会话运行态按完整工作区路径隔离清理', () => {
  const targetWorkspace = '/Users/example/projects/travels'
  const targetKey = sessionRuntimeKey(targetWorkspace, 'frontend', 'session-1')
  const sameNameElsewhereKey = sessionRuntimeKey(
    '/Users/example/archive/travels',
    'frontend',
    'session-2'
  )

  assert.equal(sessionRuntimeKeyBelongsToWorkspace(targetKey, targetWorkspace), true)
  assert.equal(sessionRuntimeKeyBelongsToWorkspace(sameNameElsewhereKey, targetWorkspace), false)
  assert.equal(sessionRuntimeKeyBelongsToWorkspace('malformed-key', targetWorkspace), false)
})

/** 删除索引只移除目标，不读取缺失配置、不丢失其他项目或改写最近打开时间。 */
test('手动删除目录后移除索引仍保留其他缺失目录的项目', async () => {
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window
  const indexes = [
    { id: 'deleted', workspaceRoot: '/missing/xc50', name: '目标项目', lastOpenedAt: 1 },
    { id: 'keep', workspaceRoot: '/missing/other', name: '保留项目', lastOpenedAt: 2 }
  ]
  let saved: unknown
  let configReads = 0
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      localStorage: { setItem: (): void => {} },
      dispatchEvent: (): boolean => true,
      devAgentStudio: {
        applications: {
          load: async () => ({ applications: indexes }),
          save: async (applications: unknown): Promise<void> => {
            saved = applications
          }
        },
        workspace: {
          readApplication: async (): Promise<never> => {
            configReads += 1
            throw new Error('ENOENT')
          }
        }
      }
    }
  })
  try {
    await removeStoredApplication('deleted')
    assert.deepEqual(saved, [indexes[1]])
    assert.equal(configReads, 0)
    await removeStoredApplication('unknown')
    assert.deepEqual(saved, indexes)
  } finally {
    if (originalWindow === undefined) Reflect.deleteProperty(globalThis, 'window')
    else Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
  }
})

/** 删除准备必须把本地 Session 和 Graph thread 身份一并发送给服务端。 */
test('生命周期 service 通过 AG-UI 请求停止会话并清理 checkpoint', async () => {
  const originalFetch = globalThis.fetch
  const originalWindow = (globalThis as typeof globalThis & { window?: unknown }).window
  let forwarded: Record<string, unknown> | undefined
  const lifecycle = {
    application: { id: 'app-1', name: '测试应用' },
    revision: 4,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {},
    extensions: {}
  } as ApplicationLifecycle
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { devAgentStudio: { agentBaseUrl: 'http://agent.test' } }
  })
  globalThis.fetch = async (_input, init) => {
    const body = JSON.parse(String(init?.body))
    forwarded = body.forwardedProps.applicationLifecycle
    const value = {
      schemaVersion: 1,
      runId: 'prepare-run',
      threadId: body.threadId,
      status: 'completed',
      action: 'prepare_session_deletion',
      lifecycle
    }
    const events = [
      { type: 'RUN_STARTED', threadId: body.threadId, runId: body.runId },
      { type: 'CUSTOM', name: 'application-lifecycle', value },
      { type: 'STATE_SNAPSHOT', snapshot: { applicationLifecycle: value } },
      { type: 'RUN_FINISHED', threadId: body.threadId, runId: body.runId, result: value }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'Content-Type': 'text/event-stream' }
    })
  }
  try {
    const received = await prepareSessionDeletion('/workspace', 'session-a', 'graph-thread-a')
    assert.deepEqual(forwarded, {
      action: 'prepare_session_deletion',
      workspaceRoot: '/workspace',
      sessionId: 'session-a',
      sessionThreadId: 'graph-thread-a'
    })
    assert.deepEqual(received, lifecycle)
  } finally {
    globalThis.fetch = originalFetch
    if (originalWindow === undefined) Reflect.deleteProperty(globalThis, 'window')
    else Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
  }
})
