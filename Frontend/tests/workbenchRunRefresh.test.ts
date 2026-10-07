import assert from 'node:assert/strict'
import { test } from 'node:test'
import { refreshWorkbenchRun, type WorkbenchRunRefreshConnection } from '../src/renderer/src/components/AiChatPanel/hooks/useWorkbenchRunRefresh'
import { completeConnectionRequest, failConnectionRequest, initialConnectionState } from '../src/renderer/src/service/connectionState'
import { globalFallbackState } from '../src/renderer/src/components/AiChatPanel/globalFallbackState'
import type { ApplicationLifecycle } from '../src/renderer/src/typings'

/** 隔离连接和生命周期，验证只读刷新不制造失败或恢复动作。 */
function harness() {
  let state = initialConnectionState(true)
  let generation = 0
  const lifecycle = { activeExecutions: { run: { status: 'running' } }, extensions: { executionRecovery: { candidates: [] } } } as unknown as ApplicationLifecycle
  let merged = lifecycle
  let merges = 0
  const observer: WorkbenchRunRefreshConnection = {
    /** 预留代次时保留当前连接展示。 */
    start: () => {
      state = { ...state, requestGeneration: ++generation }
      return generation
    },
    /** 使用实际连接 reducer 验证晚到结果不会倒退状态。 */
    settle: (requestGeneration, error) => {
      state = error === undefined ? completeConnectionRequest(state, requestGeneration)
        : failConnectionRequest(state, requestGeneration, error)
    }
  }
  return {
    observer,
    lifecycle,
    /** 读取当前隔离状态。 */
    snapshot: () => ({ state, merged, merges }),
    /** 只接受成功响应的生命周期。 */
    merge: (value: ApplicationLifecycle) => { merged = value; merges++ }
  }
}

test('后台刷新失败展示连接兜底，同时原执行与恢复候选保持不变', async () => {
  const current = harness()
  await refreshWorkbenchRun(async () => { throw new Error('Backend 不可达') }, current.merge, current.observer, () => true)
  const { state, merged, merges } = current.snapshot()
  assert.equal(state.status, 'unavailable')
  assert.equal(state.lastError, 'Backend 不可达')
  assert.equal(globalFallbackState({ connectionStatus: state.status, hasRecoveryIncident: false }).visible, true)
  assert.equal(merged, current.lifecycle)
  assert.equal(merged.activeExecutions.run.status, 'running')
  assert.equal(merges, 0)
})

test('下一轮成功恢复连接并合并后端结果，不自动提交业务动作', async () => {
  const current = harness()
  await refreshWorkbenchRun(async () => { throw new Error('断开') }, current.merge, current.observer, () => true)
  await refreshWorkbenchRun(async () => current.lifecycle, current.merge, current.observer, () => true)
  assert.equal(current.snapshot().state.status, 'healthy')
  assert.equal(current.snapshot().merges, 1)
  assert.equal(current.snapshot().merged, current.lifecycle)
})

test('切换应用或会话后丢弃迟到的成功及失败响应', async () => {
  for (const failure of [false, true]) {
    const current = harness()
    let reject!: (reason: Error) => void
    let resolve!: (value: ApplicationLifecycle) => void
    const pending = new Promise<ApplicationLifecycle>((success, failed) => { resolve = success; reject = failed })
    let active = true
    const request = refreshWorkbenchRun(() => pending, current.merge, current.observer, () => active)
    active = false
    if (failure) reject(new Error('旧请求失败'))
    else resolve(current.lifecycle)
    await request
    assert.equal(current.snapshot().state.status, 'healthy')
    assert.equal(current.snapshot().merges, 0)
  }
})

test('新的连接请求完成后，旧轮询失败不能再次显示不可用', async () => {
  const current = harness()
  let reject!: (reason: Error) => void
  const pending = new Promise<ApplicationLifecycle>((_resolve, failed) => { reject = failed })
  const request = refreshWorkbenchRun(() => pending, current.merge, current.observer, () => true)
  assert.equal(current.snapshot().state.status, 'healthy')
  const newer = current.observer.start()
  current.observer.settle(newer)
  reject(new Error('旧轮询超时'))
  await request
  assert.equal(current.snapshot().state.status, 'healthy')
  assert.equal(current.snapshot().state.requestGeneration, newer)
})
