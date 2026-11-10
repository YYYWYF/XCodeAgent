import assert from 'node:assert/strict'
import { test } from 'node:test'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { readSessionPage, readSessionSummary, saveSession } from '../src/main/sessionHistory'
import { SessionMessageCache } from '../src/renderer/src/components/AiChatPanel/hooks/sessionMessageCache'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import './sessionHistoryRuntime.test'
import './sessionTailLargeFile.test'
import './sessionFileReplacement.test'
import './workflowEventProjection.test'
import './sessionWorkflowArtifacts.test'

/** 用临时当前格式记录覆盖 Unicode、转义符及嵌套工作流，不访问真实用户历史。 */
async function fixture(
  run: (file: string, record: Record<string, any>) => Promise<void>
): Promise<void> {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'session-history-'))
  const file = path.join(directory, 'session.json')
  const record = {
    id: 'history',
    title: '历史 {"messages": []}',
    threadId: 'original-thread',
    workflowId: 'app',
    workbenchPhase: 'development',
    stage: 'DEVELOPMENT',
    sequence: 1,
    entryKey: 'history',
    editorMode: 'frontend',
    workspaceRoot: '/test',
    createdAt: 1,
    updatedAt: 2,
    messages: Array.from({ length: 65 }, (_, index) => ({
      id: index + 1,
      role: index % 2 ? 'assistant' : 'user',
      createdAt: index + 1,
      content: `消息 ${index + 1} 中文 😀 引号 \\" \\n { [ ] }`
    }))
  }
  await fs.writeFile(file, JSON.stringify(record, null, 2))
  try {
    await run(file, record)
  } finally {
    await fs.rm(directory, { recursive: true, force: true })
  }
}

test('首次仅返回最近 10 条，按稳定消息 ID 上翻到完整历史，不重复遗漏', async () => {
  await fixture(async (file, record) => {
    const received: number[] = []
    let before: number | undefined
    do {
      const page = await readSessionPage(file, before)
      assert.ok(page.session.messages.length <= 10)
      received.unshift(...page.session.messages.map((message: AgentChatMessage) => message.id))
      assert.equal(page.session.threadId, 'original-thread')
      assert.equal(page.history.total, page.history.before === undefined ? 65 : null)
      before = page.history.before
    } while (before !== undefined)
    assert.deepEqual(
      received,
      record.messages.map((message: AgentChatMessage) => message.id)
    )
  })
})

test('摘要不含正文；无索引时列表和历史页也不全量读取原 JSON', async () => {
  await fixture(async (file) => {
    const summary = await readSessionSummary(file)
    assert.equal(summary.messages, undefined)
    assert.equal(summary.messageCount, null)
    const original = fs.readFile
    let fullReads = 0
    fs.readFile = ((...args: Parameters<typeof fs.readFile>) => {
      if (String(args[0]) === file) fullReads++
      return original(...args)
    }) as typeof fs.readFile
    try {
      await readSessionSummary(file)
      await readSessionPage(file)
      await readSessionPage(file, 56)
      assert.equal(fullReads, 0)
    } finally {
      fs.readFile = original
    }
  })
})

test('展示分页只返回最近十条，不为较早控制消息扫描或回填正文', async () => {
  await fixture(async (file, record) => {
    record.messages[1].workflow = {
      runId: 'pending-run',
      threadId: 'original-thread',
      events: [],
      summary: {
        status: 'requires_user_input',
        buildExecutionScope: { type: 'page', targetId: 'home' }
      }
    }
    await fs.writeFile(file, JSON.stringify(record))
    const page = await readSessionPage(file)
    assert.deepEqual(page.history.contextMessageIds, [])
    assert.equal(
      page.session.messages.some((message: AgentChatMessage) => message.workflow),
      false
    )
    assert.equal(
      page.session.messages.filter(
        (message: AgentChatMessage) => !page.history.contextMessageIds.includes(message.id)
      ).length,
      10
    )
  })
})

test('分页保存保留未加载前缀、更新已加载消息并追加新消息，显式删除仅限已加载范围', async () => {
  await fixture(async (file, record) => {
    const page = await readSessionPage(file)
    const messages = page.session.messages.slice(1)
    messages[0] = { ...messages[0], content: '已编辑' }
    messages.push({ id: 66, role: 'assistant', createdAt: 66, content: '后台新回复' })
    const summary = await saveSession(
      file,
      { ...page.session, messages },
      page.history.loadedMessageIds
    )
    const saved = JSON.parse(await fs.readFile(file, 'utf8'))
    assert.deepEqual(saved.messages.slice(0, 55), record.messages.slice(0, 55))
    assert.equal(
      saved.messages.some((message: AgentChatMessage) => message.id === 56),
      false
    )
    assert.equal(
      saved.messages.find((message: AgentChatMessage) => message.id === 57).content,
      '已编辑'
    )
    assert.equal(saved.messages.at(-1).content, '后台新回复')
    assert.equal(summary.messageCount, 65)
  })
})

test('派生索引缺失或损坏不影响尾读，原文件保持逐字节不变', async () => {
  await fixture(async (file) => {
    const original = await fs.readFile(file)
    await readSessionSummary(file)
    await fs.writeFile(`${file}.index`, 'broken cache')
    const page = await readSessionPage(file)
    assert.equal(page.session.messages.length, 10)
    assert.deepEqual(await fs.readFile(file), original)
  })
})

test('正文变化后读取当前尾页；非法游标和超限页请求明确失败', async () => {
  await fixture(async (file, record) => {
    await readSessionSummary(file)
    record.messages.push({ id: 66, role: 'user', createdAt: 66, content: '新增' })
    await fs.writeFile(file, JSON.stringify(record))
    assert.equal((await readSessionSummary(file)).messageCount, null)
    await assert.rejects(readSessionPage(file, 999), /历史位置/)
    await assert.rejects(readSessionPage(file, undefined, 11), /limit/)
  })
})

test('翻历史与流式更新交错时保留新内容，旧保存回执不清除脏状态', () => {
  const cache = new SessionMessageCache(0)
  const message = { id: 3, role: 'assistant', content: '旧内容', createdAt: 3 } as AgentChatMessage
  cache.restore('session', [message], {
    before: 3,
    total: 3,
    loadedMessageIds: [3],
    messagePositions: { 3: 2 },
    contextMessageIds: []
  })
  const save = cache.beginSave('session', [message])
  cache.update('session', [{ ...message, content: '流式新内容' }])
  cache.prepend('session', [{ id: 2, role: 'user', content: '更早历史', createdAt: 2 }], {
    before: 2,
    total: 3,
    loadedMessageIds: [2],
    messagePositions: { 2: 1 },
    contextMessageIds: []
  })
  cache.finishSave(save, true)
  assert.equal(cache.read('session').at(-1)?.content, '流式新内容')
  assert.deepEqual(
    cache.evict(() => false),
    []
  )
  assert.deepEqual(new Set(cache.history('session')!.loadedMessageIds), new Set([2, 3]))
})

test('完整加载的大消息正常参与多轮保存回执', () => {
  const cache = new SessionMessageCache()
  const large: AgentChatMessage = {
    id: 1,
    role: 'assistant',
    content: 'x'.repeat(2 * 1024 * 1024),
    createdAt: 1
  }
  cache.restore('large', [large], {
    total: 1,
    contextMessageIds: [],
    loadedMessageIds: [1],
    messagePositions: { 1: 0 }
  })
  for (let index = 0; index < 2; index++) {
    const token = cache.beginSave('large', cache.read('large'))
    assert.deepEqual(token.loadedMessageIds, [1])
    cache.finishSave(token, true)
    assert.deepEqual(cache.history('large')?.loadedMessageIds, [1])
  }
})

test('读取期间源文件被原子替换时拒绝混合快照，下一次读取使用新文件', async () => {
  await fixture(async (file, record) => {
    const originalOpen = fs.open
    let replaced = false
    fs.open = (async (...args: Parameters<typeof fs.open>) => {
      const handle = await originalOpen(...args)
      if (String(args[0]) === file && !replaced) {
        const read = handle.read.bind(handle)
        handle.read = (async (...readArgs: any[]) => {
          const result = await (read as any)(...readArgs)
          if (!replaced) {
            replaced = true
            await fs.writeFile(
              `${file}.replacement`,
              JSON.stringify({ ...record, title: '新的原子版本' })
            )
            await fs.rename(`${file}.replacement`, file)
          }
          return result
        }) as typeof handle.read
      }
      return handle
    }) as typeof fs.open
    try {
      await assert.rejects(readSessionPage(file), /读取期间已变化/)
    } finally {
      fs.open = originalOpen
    }
    assert.equal((await readSessionPage(file)).session.title, '新的原子版本')
  })
})

test('保存处理短写；保存失败保留原文件并清理临时文件', async () => {
  await fixture(async (file) => {
    const page = await readSessionPage(file)
    const originalOpen = fs.open
    let failWrite = false
    fs.open = (async (...args: Parameters<typeof fs.open>) => {
      const handle = await originalOpen(...args)
      if (String(args[0]).endsWith('.tmp')) {
        const write = handle.write.bind(handle)
        handle.write = (async (bytes: Buffer, offset: number, length: number) => {
          if (failWrite) throw new Error('模拟磁盘写入失败')
          return write(bytes, offset, Math.min(length, 13))
        }) as typeof handle.write
      }
      return handle
    }) as typeof fs.open
    try {
      await saveSession(file, page.session, page.history.loadedMessageIds)
      assert.equal(JSON.parse(await fs.readFile(file, 'utf8')).messages.length, 65)
      const original = await fs.readFile(file)
      failWrite = true
      await assert.rejects(
        saveSession(file, page.session, page.history.loadedMessageIds),
        /磁盘写入失败/
      )
      assert.deepEqual(await fs.readFile(file), original)
      assert.deepEqual(await fs.readdir(path.dirname(file)), ['session.json'])
    } finally {
      fs.open = originalOpen
    }
  })
})

test('历史依磁盘位置合并，消息 ID 不要求单调；迟到页不能复活本地已移除消息', () => {
  const cache = new SessionMessageCache()
  const messages = [50, 2, 90].map((id) => ({
    id,
    role: 'assistant' as const,
    content: String(id),
    createdAt: id
  }))
  cache.restore('session', messages.slice(1), {
    before: 2,
    total: 3,
    contextMessageIds: [],
    loadedMessageIds: [2, 90],
    messagePositions: { 2: 1, 90: 2 }
  })
  cache.update('session', messages.slice(2))
  assert.equal(cache.history('session')?.before, 90)
  cache.prepend('session', messages.slice(0, 2), {
    total: 3,
    contextMessageIds: [],
    loadedMessageIds: [50, 2],
    messagePositions: { 50: 0, 2: 1 }
  })
  assert.deepEqual(
    cache.read('session').map((message) => message.id),
    [50, 90]
  )
})
