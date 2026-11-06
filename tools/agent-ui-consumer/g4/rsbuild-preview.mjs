import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { createRequire } from 'node:module'
import { lstat, readdir, readFile, realpath } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createConsumerMockServer } from '../../agent-ui/mock-consumer.mjs'

/** 核对源码及交接相关输入；不读取环境文件，不跟随源码中的链接。 */
async function snapshot(target) {
  const hashes = {}
  /** 对实际目标源码计算摘要，供启动失败和正常退出时复核。 */
  async function visit(relative) {
    const filename = path.join(target, relative)
    assert.ok(!path.basename(filename).startsWith('.env'), 'Environment files are outside T24 source snapshots')
    const stat = await lstat(filename)
    assert.ok(!stat.isSymbolicLink(), `T24 source contains a symbolic link: ${relative}`)
    if (stat.isDirectory()) {
      for (const name of (await readdir(filename)).sort()) await visit(path.join(relative, name))
    } else if (stat.isFile()) {
      hashes[relative] = createHash('sha256').update(await readFile(filename)).digest('hex')
    }
  }
  for (const relative of ['src', 'scripts', 'rsbuild.config.ts', 'package.json', 'pnpm-lock.yaml', '.agent-ui']) {
    await visit(relative)
  }
  return hashes
}

/** 使用目标实际安装的 Rsbuild 和配置启动官方 Mock 验收，不接入真实业务 Runtime。 */
async function main() {
  assert.equal(process.argv.length, 4, 'Usage: node rsbuild-preview.mjs <frontend-root> --mock')
  assert.equal(process.argv[3], '--mock', 'Official Mock must be explicitly enabled')
  const target = await realpath(path.resolve(process.argv[2]))
  const provider = path.join(target, 'src/extensions/AgentUiHostProvider.tsx')
  assert.equal((await readFile(provider, 'utf8')).split('endpoint=""').length, 2)
  const before = await snapshot(target)
  const requireTarget = createRequire(path.join(target, 'package.json'))
  const { createRsbuild, loadConfig } = await import(pathToFileURL(requireTarget.resolve('@rsbuild/core')).href)
  const mock = createConsumerMockServer()
  let preview
  let closing = false
  /** 只关闭本次启动的验收进程资源，并核对正式源码没有被改写。 */
  async function close() {
    if (closing) return
    closing = true
    try {
      await preview?.server.close()
    } finally {
      mock.closeAllConnections()
      if (mock.listening) await new Promise((resolve, reject) => mock.close(error => error ? reject(error) : resolve()))
      assert.deepEqual(await snapshot(target), before, 'Target source changed during T24 preview; inspect concurrent edits')
      console.log(JSON.stringify({ t24SourceUnchanged: true, files: Object.keys(before).length }))
    }
  }
  /** 信号终止只清理本进程，不停止原受管服务。 */
  async function stop() {
    try { await close() } catch (error) { console.error(error.message); process.exitCode = 1 }
  }
  process.once('SIGINT', stop)
  process.once('SIGTERM', stop)
  try {
    await new Promise((resolve, reject) => {
      mock.once('error', reject)
      mock.listen(0, '127.0.0.1', resolve)
    })
    let requests = 0
    mock.on('request', () => console.log(JSON.stringify({ officialMockRequest: ++requests })))
    // PostCSS 等目标工具按当前工程解析；不加载 .env，不继承真实 Runtime 代理。
    process.chdir(target)
    const { content: config } = await loadConfig({ cwd: target, path: 'rsbuild.config.ts' })
    const hooks = config.tools?.bundlerChain
    const tags = config.html?.tags
    const rsbuild = await createRsbuild({ cwd: target, rsbuildConfig: {
      ...config,
      root: target,
      mode: 'development',
      // API 启动不调用 CLI 的 loadEnv；仅注入非敏感开发标记，业务展示使用源码默认值。
      source: { ...config.source, define: { ...config.source?.define,
        'import.meta.env': JSON.stringify({ DEV: true, PROD: false, MODE: 'development', BASE_URL: '/' }),
      } },
      dev: { ...config.dev, writeToDisk: false },
      output: { ...config.output, cleanDistPath: false },
      performance: { ...config.performance, buildCache: false },
      server: { ...config.server, host: '127.0.0.1', port: 5194, strictPort: true, open: false,
        proxy: { '/agent': `http://127.0.0.1:${mock.address().port}` } },
      html: { ...config.html, tags: [...(Array.isArray(tags) ? tags : tags ? [tags] : []), {
        tag: 'div', head: false, append: false,
        attrs: { role: 'status', 'data-agent-ui-validation-source': 'official-mock',
          style: 'position:fixed;right:12px;top:8px;z-index:2147483647;padding:6px 12px;border-radius:8px;background:#4c1d95;color:#fff;font:13px sans-serif;pointer-events:none' },
        children: '官方 Mock · T24 展示验收（非真实智能体）',
      }] },
      tools: { ...config.tools, bundlerChain: [...(Array.isArray(hooks) ? hooks : hooks ? [hooks] : []),
        /** 复用目标所有原钩子；仅替换已知 Provider 的编译输入。 */
        chain => chain.module.rule('t24-official-mock-host').test(filename => filename === provider)
          .enforce('pre').use('t24-official-mock-host')
          .loader(fileURLToPath(new URL('./mock-endpoint-loader.cjs', import.meta.url))),
      ] },
    } })
    preview = await rsbuild.startDevServer()
    console.log(JSON.stringify({ url: 'http://127.0.0.1:5194/', target,
      connection: 'official_mock_test_only', realAgentEnabled: false,
      sourceFiles: Object.keys(before).length, sourceDigest: createHash('sha256').update(JSON.stringify(before)).digest('hex') }))
  } catch (error) {
    await close()
    throw error
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1 })
