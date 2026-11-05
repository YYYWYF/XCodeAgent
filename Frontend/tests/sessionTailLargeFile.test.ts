import assert from 'node:assert/strict'
import { test } from 'node:test'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { readSessionPage, readSessionSummary, saveSession } from '../src/main/sessionHistory'
import { SESSION_FILE_BLOCK_BYTES, SESSION_PAGE_BYTES } from '../src/main/sessionFileReader'

/** 生成真实大文件且不在测试自身构造完整字符串；返回大消息正文范围。 */
async function largeFixture(file: string): Promise<{ start: number; end: number }> {
  const handle = await fs.open(file, 'w')
  const header =
    '{"id":"large","title":"大历史","threadId":"thread","messages":[{"id":1,"role":"assistant","content":"'
  await handle.write(header)
  const start = Buffer.byteLength(header)
  const block = Buffer.alloc(1024 * 1024, 120)
  for (let index = 0; index < 210; index++) await handle.write(block)
  const end = start + block.length * 210
  await handle.write('","createdAt":1}')
  for (let id = 2; id <= 11; id++) {
    await handle.write(
      `,${JSON.stringify({ id, role: 'user', content: `尾部 ${id} 😀 \\" { [ ] }`, createdAt: id })}`
    )
  }
  await handle.write(']}\n')
  await handle.close()
  return { start, end }
}

/** 对指定范围流式求摘要，验证保存没有改写或截断未加载的大消息。 */
async function digest(file: string, start: number, end: number): Promise<string> {
  const handle = await fs.open(file, 'r')
  const bytes = Buffer.alloc(64 * 1024)
  const hash = createHash('sha256')
  try {
    for (let position = start; position < end; ) {
      const { bytesRead } = await handle.read(
        bytes,
        0,
        Math.min(bytes.length, end - position),
        position
      )
      assert.ok(bytesRead)
      hash.update(bytes.subarray(0, bytesRead))
      position += bytesRead
    }
    return hash.digest('hex')
  } finally {
    await handle.close()
  }
}

test('210 MiB 无索引：首次只读尾页，部分保存保留原文，上翻完整读取巨型消息', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'session-tail-large-'))
  const file = path.join(directory, 'session.json')
  try {
    const range = await largeFixture(file)
    const originalOpen = fs.open,
      originalReadFile = fs.readFile
    let readBytes = 0,
      largestBuffer = 0
    fs.readFile = ((...args: Parameters<typeof fs.readFile>) => {
      assert.notEqual(String(args[0]), file, '不得全量读取历史正文')
      return originalReadFile(...args)
    }) as typeof fs.readFile
    fs.open = (async (...args: Parameters<typeof fs.open>) => {
      const handle = await originalOpen(...args)
      if (String(args[0]) === file) {
        const read = handle.read.bind(handle)
        handle.read = (async (...readArgs: any[]) => {
          largestBuffer = Math.max(largestBuffer, readArgs[0].byteLength)
          const result = await (read as any)(...readArgs)
          readBytes += result.bytesRead
          return result
        }) as typeof handle.read
      }
      return handle
    }) as typeof fs.open
    try {
      assert.equal((await readSessionSummary(file)).messageCount, null)
      const page = await readSessionPage(file)
      assert.deepEqual(
        (page.session.messages as any[]).map((message) => message.id),
        [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
      )
      assert.ok(readBytes < 512 * 1024, `首次读取了 ${readBytes} 字节`)
      assert.equal(page.history.before, 2)
      assert.ok(largestBuffer <= SESSION_FILE_BLOCK_BYTES)
      // 先只保存尾页，确保尚未加载的巨型消息在多轮保存中逐字节保留。
      let session = { ...page.session }
      for (let index = 0; index < 2; index++) {
        const summary = await saveSession(file, session, page.history.loadedMessageIds)
        assert.equal(summary.messageCount, 11)
        session = { ...session, updatedAt: index + 1 }
      }
      const older = await readSessionPage(file, page.history.before)
      const large = (older.session.messages as any[])[0]
      assert.equal(large.id, 1)
      assert.equal(large.content.length, 210 * 1024 * 1024)
      assert.equal(large.content[0], 'x')
      assert.equal(large.content.at(-1), 'x')
      assert.deepEqual(older.history.loadedMessageIds, [1])
      assert.equal(older.history.before, undefined)
    } finally {
      fs.open = originalOpen
      fs.readFile = originalReadFile
    }
    const handle = await fs.open(file, 'r')
    const prefix = Buffer.alloc(4096)
    await handle.read(prefix, 0, prefix.length, 0)
    await handle.close()
    const marker = Buffer.from('"content":"')
    const savedStart = prefix.indexOf(marker) + marker.length
    const expected = createHash('sha256')
    const block = Buffer.alloc(1024 * 1024, 120)
    for (let index = 0; index < 210; index++) expected.update(block)
    assert.equal(
      await digest(file, savedStart, savedStart + range.end - range.start),
      expected.digest('hex')
    )
    assert.equal((await readSessionPage(file)).session.messages.length, 10)
  } finally {
    await fs.rm(directory, { recursive: true, force: true })
  }
})

test('单页字节预算不足时返回少于十条；字符串转义和括号跨块仍能完整上翻', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'session-tail-budget-'))
  const file = path.join(directory, 'session.json')
  try {
    const messages = Array.from({ length: 12 }, (_, index) => ({
      id: 100 - index,
      role: 'assistant',
      content: `${'中😀\\\\\\"{[]} '.repeat(40000)}${index}`,
      createdAt: index + 1
    }))
    await fs.writeFile(file, JSON.stringify({ title: '转义 "messages": [ 测试', messages }))
    const first = await readSessionPage(file)
    assert.ok(first.session.messages.length < 10)
    assert.ok(Buffer.byteLength(JSON.stringify(first.session.messages)) <= SESSION_PAGE_BYTES)
    const received = [...(first.session.messages as any[])]
    let before = first.history.before
    while (before !== undefined) {
      const page = await readSessionPage(file, before)
      received.unshift(...(page.session.messages as any[]))
      before = page.history.before
    }
    assert.deepEqual(received, messages)
  } finally {
    await fs.rm(directory, { recursive: true, force: true })
  }
})

test('单条超过单页预算也完整读取和保存，分页游标仍能继续向前', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'session-single-large-'))
  const file = path.join(directory, 'session.json')
  try {
    const content = `${'x'.repeat(SESSION_PAGE_BYTES + 1024 * 1024)}大消息结尾😀`
    await fs.writeFile(
      file,
      JSON.stringify({
        messages: [
          { id: 1, role: 'user', content: '较早消息' },
          { id: 2, role: 'assistant', content },
          { id: 3, role: 'user', content: '最新消息' }
        ]
      })
    )
    const tail = await readSessionPage(file)
    assert.deepEqual(tail.history.loadedMessageIds, [3])
    const large = await readSessionPage(file, tail.history.before)
    assert.equal(large.session.messages.length, 1)
    assert.equal(large.session.messages[0].content, content)
    assert.deepEqual(large.history.loadedMessageIds, [2])
    assert.equal(large.history.before, 2)
    large.session.messages[0].content = `${content}已编辑`
    await saveSession(file, large.session, large.history.loadedMessageIds)
    const reloaded = await readSessionPage(file, 3)
    assert.equal(reloaded.session.messages[0].content, `${content}已编辑`)
    const older = await readSessionPage(file, reloaded.history.before)
    assert.equal(older.session.messages[0].content, '较早消息')
    assert.equal(older.history.before, undefined)
    assert.equal((await readSessionPage(file)).session.messages[0].content, '最新消息')
  } finally {
    await fs.rm(directory, { recursive: true, force: true })
  }
})
