import assert from 'node:assert/strict'
import { test } from 'node:test'
import { WorkspaceRecoveryAttachments } from '../src/renderer/src/service/workspaceRecoveryAttachments'
import type { ApplicationLifecycle } from '../src/renderer/src/typings'

const lifecycle = { revision: 1 } as ApplicationLifecycle

test('校准只记录凭据，不复用快照；会话及克隆目录彼此隔离', async () => {
  const store = new WorkspaceRecoveryAttachments()
  let reads = 0
  /** 模拟校准已完成的标准结果，不让测试依赖业务生命周期内容。 */
  const read = async () => { reads++; return { lifecycle, calibrated: true } }
  const first = store.ensure('/original', 'workspace', read)
  const second = store.ensure('/original', 'workspace', read)
  assert.equal(await first, await second)
  assert.equal(reads, 1)
  assert.equal(await store.ensure('/original', 'workspace', read), undefined)
  await store.ensure('/original', 'session-a', read)
  await store.ensure('/original', 'session-b', read)
  await store.ensure('/clone', 'workspace', read)
  assert.equal(reads, 4)
})

test('失败及推迟不签发凭据，重新进入拒绝迟到回执', async () => {
  const store = new WorkspaceRecoveryAttachments()
  let reads = 0
  /** 前两次分别模拟校准失败与活动任务推迟，第三次才完成。 */
  const read = async () => {
    reads++
    if (reads === 1) throw new Error('读取失败')
    return { lifecycle, calibrated: reads > 2 }
  }
  await assert.rejects(store.ensure('/workspace', 'owner', read), /读取失败/)
  await store.ensure('/workspace', 'owner', read)
  await store.ensure('/workspace', 'owner', read)
  assert.equal(reads, 3)
  let release!: (value: { lifecycle: ApplicationLifecycle; calibrated: boolean }) => void
  const pending = store.ensure('/workspace', 'other', () => new Promise((resolve) => { release = resolve }))
  await Promise.resolve()
  store.invalidate('/workspace')
  release({ lifecycle, calibrated: true })
  await assert.rejects(pending, /恢复范围已变化/)
  await store.ensure('/workspace', 'owner', read)
  store.invalidate()
  await store.ensure('/workspace', 'owner', read)
  assert.equal(reads, 5)
})
