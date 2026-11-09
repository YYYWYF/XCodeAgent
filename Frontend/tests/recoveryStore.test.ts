import assert from 'node:assert/strict'
import { test } from 'node:test'
import { RecoveryStore, type RecoverySnapshot } from '../src/renderer/src/service/recoveryStore'
import type { ApplicationLifecycle } from '../src/renderer/src/typings'

/** 构造同一 Run 的真实业务版本，观测时间可以晚于业务版本，用于模拟迟到读取。 */
function fixture(revision = 1, checkpointAt = '2026-11-08T01:00:01Z', status = 'requires_user_input'): ApplicationLifecycle {
  const snapshot: RecoverySnapshot = { applicationId: 'app', threadId: 'thread', sessionId: 'owner', kind: 'session',
    lifecycleRevision: revision, checkpointAvailable: true, checkpointId: checkpointAt, checkpointAt,
    observedAt: '2026-11-08T01:00:05Z', output: null,
    workflow: { runId: 'run', threadId: 'thread', events: [], summary: { status }, state: {} } }
  return { application: { id: 'app' }, revision, extensions: { recoverySnapshot: snapshot } } as ApplicationLifecycle
}

test('恢复 store 合并同一在途读取，连接失败保留业务快照并退避', async () => {
  const store = new RecoveryStore(), key = store.key('/workspace', 'app', 'thread', 'owner')
  const original = fixture(); store.adopt(key, original)
  let calls = 0, complete!: (value: ApplicationLifecycle) => void
  /** 模拟真正尚未响应的共享读取。 */
  const read = (): Promise<ApplicationLifecycle> => { calls++; return new Promise((resolve) => { complete = resolve }) }
  const first = store.read(key, read), duplicate = store.read(key, read)
  await Promise.resolve(); complete(original)
  assert.equal(await first, await duplicate)
  assert.equal(calls, 1)
  await assert.rejects(store.read(key, async () => { throw new Error('连接断开') }))
  assert.equal(store.get(key).lifecycle, original)
  assert.equal(store.get(key).connection.status, 'unavailable')
  assert.equal(store.canRead(key), false)
  assert.equal(await store.read(key, async () => original, true), original)
  assert.equal(store.get(key).connection.status, 'healthy')
})

test('同生命周期版本的旧 checkpoint 即使更晚返回，也不能覆盖新确认', () => {
  const store = new RecoveryStore(), key = store.key('/workspace', 'app', 'thread', 'owner')
  const confirmed = fixture(1, '2026-11-08T01:00:03Z', 'completed')
  store.adopt(key, confirmed)
  const old = fixture(); old.extensions.recoverySnapshot!.observedAt = '2026-11-08T02:00:00Z'
  assert.equal(store.adopt(key, old).extensions.recoverySnapshot?.workflow?.summary.status, 'completed')
  const ended = fixture(2, '2026-11-08T01:00:03Z', 'completed')
  store.invalidate(key); store.adopt(key, ended)
  assert.equal(store.adopt(key, old).revision, 2)
})

test('结束写入和 fresh 同步使旧查询失效，旧响应不能复活确认卡', async () => {
  const store = new RecoveryStore(), key = store.key('/workspace', 'app', 'thread', 'owner')
  let complete!: (value: ApplicationLifecycle) => void
  const oldRequest = store.read(key, () => new Promise((resolve) => { complete = resolve }))
  await Promise.resolve()
  const ended = fixture(2, '2026-11-08T01:00:03Z', 'completed')
  store.invalidate(key); store.adopt(key, ended)
  complete(fixture())
  assert.equal((await oldRequest).revision, 2)
  assert.equal(store.get(key).lifecycle, ended)
  assert.notEqual(store.key('/workspace', 'app', 'thread', 'other'), key)
})
