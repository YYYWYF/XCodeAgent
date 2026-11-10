import assert from 'node:assert/strict'
import http from 'node:http'
import {
  backendEnvFilename,
  assertBackendEnvironment,
  checkBackendHealth
} from '../src/main/backendEnvironment'
import type { AppEnv } from '../src/main/env'
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
  let delay = 0
  const server = http.createServer((request, response) => {
    // 恢复期间后端响应略慢于一秒仍应视为可达；保持真实 HTTP 分片读取覆盖。
    setTimeout(() => {
      response.writeHead(status, { 'Content-Type': 'application/json' })
      response.write(body.slice(0, 5))
      response.end(body.slice(5))
    }, delay)
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  assert.ok(address && typeof address === 'object')
  const url = `http://127.0.0.1:${address.port}`
  try {
    assert.equal(await checkBackendHealth(url, 'st'), true)
    delay = 1200
    assert.equal(await checkBackendHealth(url, 'st'), true, '短暂响应延迟不能误报后台不可用')
    delay = 0
    await assert.rejects(checkBackendHealth(url, 'prd'), /后端环境为 st，客户端环境为 prd/)
    body = '{}'
    await assert.rejects(checkBackendHealth(url, 'st'), /后端环境为 未知/)
    body = 'invalid-json'
    assert.equal(await checkBackendHealth(url, 'st'), false)
    status = 503
    assert.equal(await checkBackendHealth(url, 'st'), false)
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve()))
    )
  }
  assert.equal(await checkBackendHealth(url, 'st'), false)
  console.log('Backend environment HTTP checks passed.')
}

await run()
