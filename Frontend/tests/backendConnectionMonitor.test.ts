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
    assert.equal(monitor.getState().status, 'unavailable')
    healthy = true
    await monitor.checkNow()
    assert.equal(monitor.getState().status, 'ready')
    assert.deepEqual(states, ['ready', 'reconnecting', 'unavailable', 'ready'])
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
