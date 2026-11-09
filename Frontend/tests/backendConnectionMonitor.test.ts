import assert from 'node:assert/strict'
import { test } from 'node:test'
import { BackendConnectionMonitor } from '../src/main/backendConnectionMonitor'

test('后台异常时并发监督合并为一次恢复，健康探测不反复通知，失败可再试', async () => {
  let complete!: (value: { baseUrl: string; backendInstanceId: string }) => void
  let healthy = false
  let recoveries = 0
  let fail = false
  const restored = { baseUrl: 'http://127.0.0.1:19002', backendInstanceId: 'new-backend' }
  const monitor = new BackendConnectionMonitor({ baseUrl: 'http://127.0.0.1:19001',
    probe: async () => healthy ? restored : undefined,
    recover: async () => { recoveries++; if (fail) throw new Error('原进程还活着'); return new Promise((resolve) => { complete = resolve }) } })
  const states: string[] = []
  monitor.subscribe((state) => states.push(state.status))
  monitor.start()
  try {
    const first = monitor.checkNow()
    assert.equal(monitor.checkNow(), first)
    await new Promise((resolve) => setImmediate(resolve))
    assert.equal(recoveries, 1)
    complete(restored)
    await first
    assert.equal(monitor.getState().baseUrl, restored.baseUrl)
    healthy = true
    const revision = monitor.getState().revision
    await monitor.checkNow()
    assert.equal(monitor.getState().revision, revision)
    healthy = false; fail = true
    await monitor.checkNow()
    assert.equal(monitor.getState().status, 'unavailable', '显式事件恢复失败后等待下一次明确请求')
    await monitor.checkNow()
    assert.equal(monitor.getState().status, 'unavailable')
    const unavailableRevision = monitor.getState().revision
    await monitor.checkNow()
    assert.equal(monitor.getState().revision, unavailableRevision, '持续失败不能每轮撤销前端订阅')
    healthy = true
    await monitor.checkNow()
    assert.equal(monitor.getState().status, 'ready')
    assert.deepEqual(states, ['ready', 'reconnecting', 'unavailable', 'ready'])
  } finally { monitor.stop() }
})

test('启动和空闲不探测后端，明确事件才检查一次，失败后不自动重试', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const identity = { baseUrl: 'http://127.0.0.1:19001', backendInstanceId: 'same-backend' }
  let healthy = true, calls = 0, recoveries = 0
  const states: string[] = []
  const monitor = new BackendConnectionMonitor({ baseUrl: identity.baseUrl,
    probe: async () => { calls++; return healthy ? identity : undefined },
    recover: async () => { recoveries++; throw new Error('原后台仍活着') } })
  monitor.ready(identity)
  monitor.subscribe((state) => states.push(state.status))
  monitor.start()
  /** 推进足够长的空闲时间，证明不存在健康检测或失败重试定时器。 */
  const idle = async (): Promise<void> => {
    context.mock.timers.tick(3_600_000)
    await new Promise((resolve) => setImmediate(resolve))
  }
  try {
    await idle()
    assert.equal(calls, 0)
    assert.equal(recoveries, 0)
    assert.deepEqual(states, [])
    healthy = false
    await monitor.checkNow()
    assert.equal(calls, 1)
    assert.equal(recoveries, 1)
    assert.deepEqual(states, ['reconnecting', 'unavailable'])
    await idle()
    assert.equal(calls, 1)
    assert.equal(recoveries, 1)
    healthy = true
    await monitor.checkNow()
    assert.equal(monitor.getState().status, 'ready')
    await idle()
    assert.equal(calls, 2)
  } finally { monitor.stop() }
})

test('已确认进程退出时沿原安全接管逻辑处理一次', async () => {
  const identity = { baseUrl: 'http://127.0.0.1:19001', backendInstanceId: 'original' }
  let recoveries = 0
  const monitor = new BackendConnectionMonitor({ baseUrl: identity.baseUrl,
    probe: async () => undefined,
    recover: async () => { recoveries++; return { ...identity, backendInstanceId: 'replacement' } } })
  monitor.ready(identity)
  monitor.start()
  try {
    await monitor.checkNow()
    assert.equal(recoveries, 1)
    assert.equal(monitor.getState().backendInstanceId, 'replacement')
  } finally { monitor.stop() }
})

test('退出或更新暂停监督后，迟到探测不能启动后台，迟到启动不能发布新连接', async () => {
  let probe!: (value: undefined) => void
  let recoveries = 0
  const monitor = new BackendConnectionMonitor({ baseUrl: 'http://127.0.0.1:19001',
    probe: () => new Promise((resolve) => { probe = resolve }),
    recover: async () => { recoveries++; return { baseUrl: 'http://127.0.0.1:19002' } } })
  monitor.start()
  const pending = monitor.checkNow()
  await new Promise((resolve) => setImmediate(resolve))
  monitor.stop(); probe(undefined)
  await pending
  assert.equal(recoveries, 0)
  assert.equal(monitor.getState().status, 'stopped')

  let finish!: (value: { baseUrl: string }) => void
  const starting = new BackendConnectionMonitor({ baseUrl: 'http://127.0.0.1:19001',
    probe: async () => undefined, recover: () => new Promise((resolve) => { finish = resolve }) })
  starting.start()
  const startup = starting.checkNow()
  await new Promise((resolve) => setImmediate(resolve))
  starting.stop(); finish({ baseUrl: 'http://127.0.0.1:19002' })
  await startup
  assert.equal(starting.getState().status, 'stopped')
  assert.equal(starting.getState().baseUrl, 'http://127.0.0.1:19001')
})
