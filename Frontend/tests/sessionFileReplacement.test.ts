import assert from 'node:assert/strict'
import { test } from 'node:test'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { saveSession } from '../src/main/sessionHistory'
import { writeSessionAtomically } from '../src/main/sessionPersistence'

/** 用隔离会话和模拟平台验证真实文件替换，始终恢复全局文件操作。 */
async function fixture(
  run: (file: string, original: string) => Promise<void>,
  platform = 'win32'
): Promise<void> {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'session-replacement-'))
  const file = path.join(directory, 'session.json')
  const original = JSON.stringify({
    id: 'session',
    messages: [{ id: 1, role: 'user', content: '未加载历史', createdAt: 1 }]
  })
  const descriptor = Object.getOwnPropertyDescriptor(process, 'platform')!
  const rename = fs.rename,
    open = fs.open
  await fs.writeFile(file, original)
  Object.defineProperty(process, 'platform', { ...descriptor, value: platform })
  try {
    await run(file, original)
    assert.deepEqual(await fs.readdir(directory), ['session.json'], '不得残留临时文件')
  } finally {
    fs.rename = rename
    fs.open = open
    Object.defineProperty(process, 'platform', descriptor)
    await fs.rm(directory, { recursive: true, force: true })
  }
}

/** 创建与截图一致的文件系统错误，断言最终仍向调用方报告原始原因。 */
function failure(code: string): NodeJS.ErrnoException {
  return Object.assign(new Error(`模拟替换失败: ${code}`), { code })
}

/** 部分保存仅追加新消息，旧消息必须由真实流式复制保留。 */
function append(file: string): Promise<Record<string, unknown>> {
  return saveSession(
    file,
    {
      id: 'session',
      messages: [{ id: 2, role: 'assistant', content: '新消息', createdAt: 2 }]
    },
    []
  )
}

test('Windows 流式保存先关闭旧文件，短暂拒绝后重试且保留未加载历史', async () => {
  await fixture(async (file, original) => {
    const open = fs.open,
      rename = fs.rename
    const sources: Awaited<ReturnType<typeof fs.open>>[] = []
    const errors = ['EPERM', 'EACCES', 'EBUSY']
    let attempts = 0
    fs.open = (async (...args: Parameters<typeof fs.open>) => {
      const handle = await open(...args)
      if (String(args[0]) === file) sources.push(handle)
      return handle
    }) as typeof fs.open
    fs.rename = async (source, target) => {
      assert.equal(String(target), file)
      assert.equal(sources.length, 1)
      assert.equal(sources[0].fd, -1, '替换前必须关闭旧文件')
      assert.equal(await fs.readFile(file, 'utf8'), original, '重试不能先删除或截断旧文件')
      const code = errors[attempts++]
      if (code) throw failure(code)
      await rename(source, target)
    }
    const result = await append(file)
    assert.equal(attempts, 4)
    assert.equal(result.messageCount, 2)
    assert.deepEqual(JSON.parse(await fs.readFile(file, 'utf8')).messages, [
      { id: 1, role: 'user', content: '未加载历史', createdAt: 1 },
      { id: 2, role: 'assistant', content: '新消息', createdAt: 2 }
    ])
  })
})

test('Windows 持续拒绝替换时有限退出，保留原文件并报告原始错误', async () => {
  await fixture(async (file, original) => {
    const error = failure('EPERM')
    let attempts = 0
    fs.rename = async () => {
      attempts++
      throw error
    }
    await assert.rejects(append(file), (actual) => actual === error)
    assert.equal(attempts, 6)
    assert.equal(await fs.readFile(file, 'utf8'), original)
  })
})

test('Windows 重试等待期间文件被外部修改时拒绝覆盖', async () => {
  await fixture(async (file) => {
    const external = JSON.stringify({
      id: 'session',
      messages: [{ id: 3, role: 'user', content: '外部新版本' }]
    })
    let attempts = 0
    fs.rename = async () => {
      attempts++
      await fs.writeFile(file, external)
      throw failure('EPERM')
    }
    await assert.rejects(append(file), /会话在保存期间已变化/)
    assert.equal(attempts, 1)
    assert.equal(await fs.readFile(file, 'utf8'), external)
  })
})

test('不可重试错误立即失败并保留旧文件', async () => {
  await fixture(async (file, original) => {
    const error = failure('ENOSPC')
    let attempts = 0
    fs.rename = async () => {
      attempts++
      throw error
    }
    await assert.rejects(append(file), (actual) => actual === error)
    assert.equal(attempts, 1)
    assert.equal(await fs.readFile(file, 'utf8'), original)
  })
})

test('非 Windows 不重试访问拒绝', async () => {
  await fixture(async (file, original) => {
    const error = failure('EPERM')
    let attempts = 0
    fs.rename = async () => {
      attempts++
      throw error
    }
    await assert.rejects(append(file), (actual) => actual === error)
    assert.equal(attempts, 1)
    assert.equal(await fs.readFile(file, 'utf8'), original)
  }, 'darwin')
})

test('完整会话发布同样处理 Windows 短暂占用', async () => {
  await fixture(async (file) => {
    const rename = fs.rename
    let attempts = 0
    fs.rename = async (source, target) => {
      if (++attempts === 1) throw failure('EPERM')
      await rename(source, target)
    }
    await writeSessionAtomically(file, { id: 'created', messages: [] })
    assert.equal(attempts, 2)
    assert.equal(JSON.parse(await fs.readFile(file, 'utf8')).id, 'created')
  })
})
