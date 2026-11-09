import assert from 'node:assert/strict'
import http from 'node:http'
import { test } from 'node:test'
import { spawn } from 'node:child_process'
import { controlBackendRuntime, retireBackendRuntime, waitBackendExit } from '../src/main/backendRuntimeClient'

test('后台升级忙碌不执行清理，清理失败撤回栅栏，成功等待真实原进程退出', async () => {
  const calls: string[] = []
  let busy = true
  let complete = true
  const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' })
  assert.ok(child.pid)
  const server = http.createServer(async (request, response) => {
    let body = ''
    for await (const part of request) body += part
    const payload = JSON.parse(body)
    const action = payload.forwardedProps.backendRuntime.action as string
    assert.equal(payload.forwardedProps.backendRuntime.controlToken, 't'.repeat(64))
    calls.push(action)
    const state = action === 'prepare_exit' ? (busy ? 'busy' : 'prepared') : action === 'commit_exit' ? 'exiting' : 'open'
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    for (const event of [
      { type: 'RUN_STARTED', threadId: payload.threadId, runId: payload.runId },
      { type: 'CUSTOM', name: 'backend-runtime', value: { schemaVersion: 1, status: 'completed', state, ticket: 'exit-ticket' } },
      ...(complete ? [{ type: 'RUN_FINISHED', threadId: payload.threadId, runId: payload.runId }] : [])
    ]) response.write(`data: ${JSON.stringify(event)}\n\n`)
    response.end()
    if (action === 'commit_exit') setTimeout(() => child.kill(), 100)
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  assert.ok(address && typeof address === 'object')
  const runtime = { baseUrl: `http://127.0.0.1:${address.port}`, backendInstanceId: 'backend', controlToken: 't'.repeat(64), pid: child.pid }
  try {
    let cleaned = false
    await assert.rejects(retireBackendRuntime(runtime, 'desktop', async () => { cleaned = true }), /后台仍有任务/)
    assert.equal(cleaned, false)
    assert.deepEqual(calls, ['prepare_exit'])
    busy = false
    await assert.rejects(retireBackendRuntime(runtime, 'desktop', async () => { throw new Error('cleanup failed') }), /cleanup failed/)
    assert.deepEqual(calls.slice(-2), ['prepare_exit', 'cancel_exit'])
    complete = false
    await assert.rejects(controlBackendRuntime(runtime, 'desktop', 'attach'), /未完成/)
    complete = true
    await retireBackendRuntime(runtime, 'desktop', async () => { cleaned = true })
    assert.equal(cleaned, true)
    assert.deepEqual(calls.slice(-2), ['prepare_exit', 'commit_exit'])
    await waitBackendExit(child.pid, 100)
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill()
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})
