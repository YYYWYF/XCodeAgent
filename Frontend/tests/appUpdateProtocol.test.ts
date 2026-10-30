import assert from 'node:assert/strict'
import { test } from 'node:test'
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { EventType } from '@ag-ui/core'
import { checkAppRelease } from '../src/main/appUpdate/checkRelease'
import { AppUpdateCoordinator } from '../src/main/appUpdate/coordinator'

/** 创建真实 AG-UI HTTP 测试服务，核对请求与 SDK 的标准事件消费。 */
async function withBackend(
  reply: (payload: Record<string, unknown>, response: http.ServerResponse) => void,
  run: (baseUrl: string) => Promise<void>
): Promise<void> {
  const server = http.createServer((request, response) => {
    assert.equal(request.url, '/prefix/app-update/run')
    let body = ''
    request.on('data', (chunk: Buffer) => {
      body += chunk.toString()
    })
    request.on('end', () => reply(JSON.parse(body) as Record<string, unknown>, response))
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  try {
    await run(`http://127.0.0.1:${(server.address() as AddressInfo).port}/prefix/`)
  } finally {
    server.closeAllConnections()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
}

/** 使用 AG-UI 事件类型构造上游成功或失败流，不在生产代码手写 SSE 解析。 */
function respond(
  payload: Record<string, unknown>,
  response: http.ServerResponse,
  failed = false,
  complete = true
): void {
  const value = failed
    ? { status: 'failed', error: { message: 'offline' } }
    : {
        status: 'completed',
        updateAvailable: true,
        latestAppVersion: '1.0.1',
        appDownloadUrl: 'http://cdn.example/'
      }
  const events: Record<string, unknown>[] = [
    { type: EventType.RUN_STARTED, threadId: payload.threadId, runId: payload.runId },
    { type: EventType.CUSTOM, name: 'app-update', value },
    { type: EventType.STATE_SNAPSHOT, snapshot: { appUpdate: value } }
  ]
  if (complete)
    events.push({ type: EventType.RUN_FINISHED, threadId: payload.threadId, runId: payload.runId })
  response.writeHead(200, { 'Content-Type': 'text/event-stream' })
  response.end(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''))
}

test('SDK 通过 Python 动态地址调用独立 AG-UI，客户端不提交 Java 地址', async () => {
  await withBackend(
    (payload, response) => {
      assert.deepEqual(payload.forwardedProps, {
        appUpdate: { action: 'check', currentVersion: '1.0.0' }
      })
      respond(payload, response)
    },
    async (base) => {
      assert.deepEqual(await checkAppRelease(base, '1.0.0', new AbortController().signal), {
        updateAvailable: true,
        latestAppVersion: '1.0.1',
        appDownloadUrl: 'http://cdn.example/'
      })
    }
  )
})

test('AG-UI 失败和缺少 RunFinished 均拒绝进入下载', async () => {
  for (const [failed, complete] of [
    [true, true],
    [false, false]
  ]) {
    await withBackend(
      (payload, response) => respond(payload, response, failed, complete),
      async (base) => {
        await assert.rejects(checkAppRelease(base, '1.0.0', new AbortController().signal))
      }
    )
  }
})

test('检测取消会停止 SDK 请求，而不是继续等待 SSE', async () => {
  await withBackend(
    (_payload, response) => {
      response.writeHead(200, { 'Content-Type': 'text/event-stream' })
      response.flushHeaders()
    },
    async (base) => {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 20)
      try {
        await assert.rejects(checkAppRelease(base, '1.0.0', controller.signal))
      } finally {
        clearTimeout(timer)
      }
    }
  )
})

test('Python 就绪等待不占用检测预算，准备失败不伪装为可登录', async () => {
  let prepared = false
  const make = (prepare: () => Promise<void>): AppUpdateCoordinator =>
    new AppUpdateCoordinator({
      currentVersion: '1.0.0',
      isPackaged: false,
      prepare,
      check: async () => {
        assert.equal(prepared, true)
        return { updateAvailable: false, latestAppVersion: '1.0.0', appDownloadUrl: null }
      },
      createDriver: () => {
        throw new Error('must not download')
      },
      publish: () => undefined,
      prepareInstall: async () => undefined,
      timings: { check: 2, notice: 1 }
    })
  const coordinator = make(async () => {
    await new Promise((resolve) => setTimeout(resolve, 15))
    prepared = true
  })
  await coordinator.start()
  assert.equal(await coordinator.result, true)
  const failed = make(async () => {
    throw new Error('Python failed to start')
  })
  await assert.rejects(failed.start(), /Python failed/)
  failed.quit()
  assert.equal(await failed.result, false)
})
