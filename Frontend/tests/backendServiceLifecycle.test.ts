import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import {
  getBackendBaseUrl,
  startBackendService,
  stopBackendService,
  prepareBackendServiceUpdate
} from '../src/main/backendService'
import { launches, waitForLaunch } from './backendServiceChildProcess'
import { APP_ENV } from '../src/main/env'
import { backendEnvFilename } from '../src/main/backendEnvironment'

/** 只提供健康响应和进程身份的临时 Backend；没有租约、旧实例接管或空闲退场接口。 */
const backendFixture = `
const http = require('node:http');
const server = http.createServer((request, response) => {
  if (request.url !== '/health') { response.writeHead(404); response.end(); return; }
  response.writeHead(200, {'Content-Type': 'application/json'});
  response.end(JSON.stringify({status:'ok', app_env:process.env.APP_ENV,
    backendPid:process.pid, backendInstanceId:'fixture-' + process.pid}));
});
setTimeout(() => server.listen(Number(process.env.DEVAGENTSTUDIO_BACKEND_PORT), '127.0.0.1'),
  Number(process.env.TEST_BACKEND_READY_DELAY || 0));
`

test('退出客户端结束内置 Backend，重开新建进程，升级与在途启动也不会留下后台', async () => {
  const originalResources = Object.getOwnPropertyDescriptor(process, 'resourcesPath')
  const originalDelay = process.env.TEST_BACKEND_READY_DELAY
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'backend-owned-lifecycle-'))
  const backend = path.join(directory, 'backend')
  await fs.mkdir(path.join(backend, '_internal/app/builtin_skills'), { recursive: true })
  await fs.writeFile(path.join(backend, backendEnvFilename(APP_ENV)), '')
  await fs.writeFile(
    path.join(
      backend,
      process.platform === 'win32' ? 'devagentstudio-backend.exe' : 'devagentstudio-backend'
    ),
    backendFixture
  )
  Object.defineProperty(process, 'resourcesPath', { configurable: true, value: directory })
  try {
    const firstIndex = launches.length
    const urls = await Promise.all([startBackendService(), startBackendService()])
    assert.equal(urls[0], urls[1])
    assert.equal(launches.length, firstIndex + 1)
    const first = launches[firstIndex]
    assert.deepEqual(first.options.stdio, ['ignore', 'pipe', 'pipe'])
    assert.equal(first.options.windowsHide, true)
    assert.equal(first.options.detached, process.platform !== 'win32')
    assert.equal(first.options.env?.DEVAGENTSTUDIO_BACKEND_CONTROL_TOKEN, undefined)
    assert.ok(first.child.pid)
    const health = await (await fetch(`${getBackendBaseUrl()}/health`)).json()
    assert.equal(health.backendPid, first.child.pid)

    await stopBackendService()
    assert.ok(first.child.exitCode !== null || first.child.signalCode !== null)
    await assert.rejects(fetch(`${urls[0]}/health`))
    await startBackendService()
    assert.equal(launches.length, firstIndex + 2)
    const second = launches[firstIndex + 1].child
    assert.notEqual(second.pid, first.child.pid)

    await assert.rejects(
      prepareBackendServiceUpdate(async () => {
        throw new Error('preview cleanup failed')
      }),
      /preview cleanup failed/
    )
    assert.equal(second.exitCode, null)
    assert.equal(second.signalCode, null)
    let cleaned = false
    await prepareBackendServiceUpdate(async () => {
      cleaned = true
    })
    assert.equal(cleaned, true)
    assert.ok(second.exitCode !== null || second.signalCode !== null)

    // 即使在 Backend 尚未就绪时退出，启动回调也不能补建或发布新的后台。
    process.env.TEST_BACKEND_READY_DELAY = '1000'
    const nextIndex = launches.length
    const pending = startBackendService()
    const rejected = assert.rejects(pending, /exited|已取消/)
    await waitForLaunch(nextIndex)
    await stopBackendService()
    await rejected
    const last = launches[nextIndex].child
    assert.ok(last.exitCode !== null || last.signalCode !== null)
    await new Promise<void>((resolve) => setImmediate(resolve))
    assert.equal(launches.length, nextIndex + 1)
  } finally {
    await stopBackendService()
    if (originalResources) Object.defineProperty(process, 'resourcesPath', originalResources)
    else Reflect.deleteProperty(process, 'resourcesPath')
    if (originalDelay === undefined) delete process.env.TEST_BACKEND_READY_DELAY
    else process.env.TEST_BACKEND_READY_DELAY = originalDelay
  }
})
