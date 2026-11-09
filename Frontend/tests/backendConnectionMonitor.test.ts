import assert from 'node:assert/strict'
import { test } from 'node:test'
import { BackendConnectionMonitor } from '../src/main/backendConnectionMonitor'

test('后台异常时并发监督合并为一次恢复，健康探测不反复通知，失败可再试', async () => {
  let complete!: (value: { baseUrl: string; backendInstanceId: string }) => void
  let healthy = false
  let recoveries = 0
  let fail = false
  const restored = { baseUrl: 'http://127.0.0.1:19002', backendInstanceId: 'new-backend' }
  const monitor = new BackendConnectionMonitor({ baseUrl: 'http://127.0.0.1:19001', intervalMs: 60000,
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
    assert.equal(monitor.getState().status, 'ready', '单次失败不应中断仍可用的业务订阅')
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

test('健康十五秒巡检，锁屏恢复的单次超时三秒复查且不发布断连，持续失败逐步退避', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] })
  const identity = { baseUrl: 'http://127.0.0.1:19001', backendInstanceId: 'same-backend' }
  let healthy = true, calls = 0, recoveries = 0
  const states: string[] = []
  const monitor = new BackendConnectionMonitor({ baseUrl: identity.baseUrl,
    probe: async () => { calls++; if (!healthy) throw new Error('timeout'); return identity },
    recover: async () => { recoveries++; throw new Error('原后台仍活着') } })
  monitor.ready(identity)
  monitor.subscribe((state) => states.push(state.status))
  monitor.start()
  /** 推进主进程时间并等本轮异步探测和恢复收口，不真实等待巡检间隔。 */
  const advance = async (milliseconds: number): Promise<void> => {
    context.mock.timers.tick(milliseconds)
    await new Promise((resolve) => setImmediate(resolve))
  }
  try {
    await advance(14999)
    assert.equal(calls, 0)
    healthy = false
    await advance(1)
    assert.equal(calls, 1)
    assert.equal(monitor.getState().status, 'ready')
    healthy = true
    await advance(2999)
    assert.equal(calls, 1)
    await advance(1)
    assert.equal(calls, 2)
    assert.deepEqual(states, [], '单次超时恢复不能发布新传输代次')
    assert.equal(recoveries, 0)

    healthy = false
    await advance(15000)
    await advance(3000)
    assert.equal(recoveries, 1)
    assert.deepEqual(states, ['reconnecting', 'unavailable'])
    for (const delay of [6000, 12000, 24000, 30000, 30000]) {
      const before = calls
      await advance(delay - 1)
      assert.equal(calls, before)
      await advance(1)
      assert.equal(calls, before + 1)
    }
    assert.deepEqual(states, ['reconnecting', 'unavailable'], '同次断连的后台重试不反复广播')
    healthy = true
    await advance(30000)
    assert.equal(monitor.getState().status, 'ready')
    const restoredCalls = calls
    await advance(15000)
    assert.equal(calls, restoredCalls + 1, '恢复后回到正常巡检频率')
  } finally { monitor.stop() }
})

test('已确认进程退出时跳过连续失败等待，立即沿原安全接管逻辑恢复', async () => {
  const identity = { baseUrl: 'http://127.0.0.1:19001', backendInstanceId: 'original' }
  let recoveries = 0
  const monitor = new BackendConnectionMonitor({ baseUrl: identity.baseUrl, intervalMs: 60000,
    probe: async () => undefined,
    recover: async () => { recoveries++; return { ...identity, backendInstanceId: 'replacement' } } })
  monitor.ready(identity)
  monitor.start()
  try {
    await monitor.checkNow(true)
    assert.equal(recoveries, 1)
    assert.equal(monitor.getState().backendInstanceId, 'replacement')
  } finally { monitor.stop() }
})

test('退出或更新暂停监督后，迟到探测不能启动后台，迟到启动不能发布新连接', async () => {
  let probe!: (value: undefined) => void
  let recoveries = 0
  const monitor = new BackendConnectionMonitor({ baseUrl: 'http://127.0.0.1:19001', intervalMs: 60000,
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
  const starting = new BackendConnectionMonitor({ baseUrl: 'http://127.0.0.1:19001', intervalMs: 60000,
    probe: async () => undefined, recover: () => new Promise((resolve) => { finish = resolve }) })
  starting.start()
  const startup = starting.checkNow()
  await new Promise((resolve) => setImmediate(resolve))
  starting.stop(); finish({ baseUrl: 'http://127.0.0.1:19002' })
  await startup
  assert.equal(starting.getState().status, 'stopped')
  assert.equal(starting.getState().baseUrl, 'http://127.0.0.1:19001')
})
