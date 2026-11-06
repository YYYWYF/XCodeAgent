import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { createServer } from 'vite'
import { createConsumerMockServer } from '../../agent-ui/mock-consumer.mjs'

const target = path.resolve(process.argv[2])
const enabled = process.argv.includes('--mock')
const dark = process.env.AGENT_UI_G4_THEME === 'dark'
assert.ok(!dark || enabled, 'Dark presentation injection requires explicit Mock test mode')
const provider = path.join(target, 'src/extensions/AgentUiHostProvider.tsx')
assert.match(await readFile(provider, 'utf8'), /endpoint=""/)
const mock = enabled ? createConsumerMockServer(process.env.AGENT_UI_G4_SCENARIO ?? 'simple-chat') : null
let requests = 0
if (mock) {
  await new Promise((resolve, reject) => {
    mock.once('error', reject)
    mock.listen(0, '127.0.0.1', resolve)
  })
  mock.on('request', () => console.log(JSON.stringify({ officialMockRequest: ++requests })))
}

const server = await createServer({
  root: target,
  plugins: enabled ? [{
    name: 'g4-test-only-mock-endpoint',
    enforce: 'pre',
    apply: 'serve',
    /** 只在显式测试进程中替换 endpoint，磁盘、正式构建和转入摘要保持原样。 */
    transform(code, id) {
      const file = id.split('?')[0]
      if (file === provider) {
        assert.equal(code.split('endpoint=""').length, 2)
        return { code: code.replace('endpoint=""', 'endpoint="/agent"'), map: null }
      }
      if (dark && file === path.join(target, 'src/正式助手/design/SharedHost.tsx')) {
        assert.equal(code.split('endpoint={endpoint} getRunContext=').length, 2)
        return { code: code.replace('endpoint={endpoint} getRunContext=', 'theme="dark" endpoint={endpoint} getRunContext='), map: null }
      }
      if (dark && file === path.join(target, 'src/g4.css')) {
        return { code: code + '\n:root { color-scheme: dark; }', map: null }
      }
    },
  }] : [],
  server: { host: '127.0.0.1', port: 5194, strictPort: true,
    proxy: mock ? { '/agent': `http://127.0.0.1:${mock.address().port}` } : {} },
})
await server.listen()
console.log(JSON.stringify({ url: 'http://127.0.0.1:5194/page/home', target,
  connection: enabled ? 'official_mock_test_only' : 'blocked_T27', theme: dark ? 'public_dark_prop_test_only' : 'official_default' }))

/** 只释放本进程启动的验收资源。 */
async function close() {
  await server.close()
  if (mock) {
    mock.closeAllConnections()
    await new Promise((resolve) => mock.close(resolve))
  }
}
process.once('SIGINT', () => { void close() })
process.once('SIGTERM', () => { void close() })
