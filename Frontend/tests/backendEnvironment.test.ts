import assert from 'node:assert/strict'
import http from 'node:http'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { readReusableBackend, writeBackendRuntime } from '../src/main/backendRuntime'
import {
  backendEnvFilename,
  assertBackendEnvironment,
  checkBackendHealth
} from '../src/main/backendEnvironment'
import type { AppEnv } from '../src/main/env'
import './backendRuntimeClient.test'
import './backendConnectionMonitor.test'
import './backendConnectionRecovery.test'

/** 通过真实 HTTP 服务验证身份解析、分片响应和连接错误。 */
async function run(): Promise<void> {
  for (const env of ['dev', 'st', 'uat', 'prd'] as AppEnv[]) {
    assert.equal(backendEnvFilename(env), env === 'dev' ? '.env' : `.env.${env}`)
    assert.doesNotThrow(() => assertBackendEnvironment({ app_env: env }, env))
    assert.throws(() => assertBackendEnvironment({}, env), /start-backend/)
  }
  let body = '{"app_env":"st"}'
  let status = 200
  const server = http.createServer((request, response) => {
    if (request.url === '/backend-runtime/run') {
      request.resume()
      response.writeHead(200, { 'Content-Type': 'text/event-stream' })
      for (const event of [
        { type: 'RUN_STARTED', threadId: 'runtime', runId: 'lease' },
        { type: 'CUSTOM', name: 'backend-runtime', value: { schemaVersion: 1, status: 'completed', state: 'open' } },
        { type: 'RUN_FINISHED', threadId: 'runtime', runId: 'lease' }
      ]) response.write(`data: ${JSON.stringify(event)}\n\n`)
      response.end()
      return
    }
    response.writeHead(status, { 'Content-Type': 'application/json' })
    response.write(body.slice(0, 5))
    response.end(body.slice(5))
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  assert.ok(address && typeof address === 'object')
  const url = `http://127.0.0.1:${address.port}`
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'backend-reconnect-'))
  const executable = path.join(directory, 'backend')
  const runtimeFile = path.join(directory, 'runtime.json')
  try {
    assert.equal(await checkBackendHealth(url, 'st'), true)
    await assert.rejects(checkBackendHealth(url, 'prd'), /后端环境为 st，客户端环境为 prd/)
    body = '{}'
    await assert.rejects(checkBackendHealth(url, 'st'), /后端环境为 未知/)
    body = 'invalid-json'
    assert.equal(await checkBackendHealth(url, 'st'), false)
    await fs.writeFile(executable, 'test executable identity')
    const runtime = { pid: process.pid, baseUrl: url, backendInstanceId: 'original-backend',
      appEnv: 'st' as const, executablePath: executable, executableMtimeMs: (await fs.stat(executable)).mtimeMs, controlToken: 't'.repeat(64) }
    assert.equal(await readReusableBackend(runtimeFile, 'st', executable, 'desktop'), undefined)
    await writeBackendRuntime(runtimeFile, runtime)
    body = JSON.stringify({ app_env: 'st', backendInstanceId: 'original-backend', backendPid: process.pid, managedRuntime: { enabled: true } })
    assert.deepEqual(await readReusableBackend(runtimeFile, 'st', executable, 'desktop'), runtime)
    assert.equal((await fs.stat(runtimeFile)).mode & 0o777, 0o600)
    body = '{"app_env":"st","backendInstanceId":"another-backend"}'
    await assert.rejects(readReusableBackend(runtimeFile, 'st', executable, 'desktop'), /原后台服务尚未响应/)
    body = JSON.stringify({ app_env: 'st', backendInstanceId: 'original-backend', backendPid: process.pid, managedRuntime: { enabled: true } })
    await writeBackendRuntime(runtimeFile, { ...runtime, executableMtimeMs: runtime.executableMtimeMs - 1 })
    await assert.rejects(readReusableBackend(runtimeFile, 'st', executable, 'desktop'), /后台仍有任务或其它客户端/)
    await assert.rejects(readReusableBackend(runtimeFile, 'prd', executable, 'desktop'), /后台服务登记无效/)
    status = 503
    assert.equal(await checkBackendHealth(url, 'st'), false)
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve()))
    )
    // 临时目录保留给本次测试诊断，不接触实际用户后端登记文件。
  }
  assert.equal(await checkBackendHealth(url, 'st'), false)
  console.log('Backend environment HTTP checks passed.')
}

await run()
