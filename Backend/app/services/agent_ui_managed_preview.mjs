import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { createServer } from 'node:http'
import { createRequire } from 'node:module'
import { readFile, realpath } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

/** 标准预览拥有 Rsbuild；纯文本模式直连原 Runtime，模拟模式才创建官方 Mock。 */
async function main() {
  assert.equal(process.argv.length, 3)
  const options = JSON.parse(process.argv[2])
  assert.ok(['real-text', 'formal-text', 'official-mock'].includes(options.mode))
  const realText = options.mode !== 'official-mock'
  const formalText = options.mode === 'formal-text'
  if (realText) assert.equal(options.endpoint, `/agents/${options.agentId}/run`)
  const target = await realpath(options.target)
  const provider = path.join(target, 'src/extensions/AgentUiHostProvider.tsx')
  const providerCode = await readFile(provider, 'utf8')
  assert.equal(createHash('sha256').update(providerCode).digest('hex'), options.providerSha256)
  if (formalText) assert.ok(providerCode.includes(`endpoint=${JSON.stringify(options.endpoint)}`))
  const toolRequire = createRequire(path.join(options.installationRoot, 'package.json'))
  const metadata = await readFile(toolRequire.resolve('@za38.03/agent-ui-creator/package.json'))
  assert.equal(createHash('sha256').update(metadata).digest('hex'), options.packageJsonSha256)
  const identity = JSON.parse(metadata)
  assert.equal(identity.version, options.creatorVersion)
  // Mock 的公开 export 仅提供 import 条件，不能使用 require 条件解析该子路径。
  const mockExport = identity.exports?.['./mock-agent']?.import
  assert.equal(typeof mockExport, 'string', 'Creator 缺少公开 Mock import 入口')
  const packageRoot = path.dirname(toolRequire.resolve('@za38.03/agent-ui-creator/package.json'))
  const mockEntry = await realpath(path.resolve(packageRoot, mockExport))
  assert.ok(mockEntry.startsWith(packageRoot + path.sep), 'Mock export 越过成品包边界')
  const { createMockAgentHttpHandler } = await import(pathToFileURL(mockEntry).href)
  const handler = realText ? null : createMockAgentHttpHandler({ defaultScenarioId: 'simple-chat' })
  const mock = realText ? null : createServer((request, response) => {
    void handler(request, response).catch(() => {
      if (!response.headersSent) response.writeHead(500)
      response.end()
    })
  })
  let preview
  let closing = false
  /** 所有失败和停止共用同一资源收口，不触碰工作区的其他进程。 */
  async function close() {
    if (closing) return
    closing = true
    try { await preview?.server.close() } finally {
      mock?.closeAllConnections()
      if (mock?.listening) await new Promise(resolve => mock.close(resolve))
    }
  }
  /** 处理受管启动器的进程组停止信号。 */
  function stop() { void close().catch(() => { process.exitCode = 1 }) }
  process.once('SIGTERM', stop)
  process.once('SIGINT', stop)
  try {
    if (mock) await new Promise((resolve, reject) => {
      mock.once('error', reject)
      mock.listen(0, '127.0.0.1', resolve)
    })
    process.chdir(target)
    process.env.NODE_ENV = 'development'
    const targetRequire = createRequire(path.join(target, 'package.json'))
    const { createRsbuild, loadConfig, loadEnv } = await import(pathToFileURL(targetRequire.resolve('@rsbuild/core')).href)
    // 与 Rsbuild CLI 一样仅公开 PUBLIC_ 变量，不把服务端凭据写入浏览器。
    const { publicVars } = loadEnv({ cwd: target, mode: 'development' })
    const publicEnv = Object.fromEntries(Object.entries(publicVars)
      .filter(([key]) => key.startsWith('import.meta.env.'))
      .map(([key, value]) => [key.slice('import.meta.env.'.length), JSON.parse(value)]))
    const { content: config } = await loadConfig({ cwd: target })
    const hooks = config.tools?.bundlerChain
    const tags = config.html?.tags
    if (closing) return
    const rsbuild = await createRsbuild({ cwd: target, rsbuildConfig: {
      ...config, root: target, mode: 'development',
      source: { ...config.source, define: { ...publicVars, ...config.source?.define,
        'import.meta.env': JSON.stringify({ ...publicEnv, DEV: true, PROD: false, MODE: 'development', BASE_URL: '/' }),
      } },
      dev: { ...config.dev, writeToDisk: false },
      output: { ...config.output, cleanDistPath: false },
      performance: { ...config.performance, buildCache: false },
      server: { ...config.server, host: '127.0.0.1', strictPort: true, open: false,
        proxy: { ...config.server?.proxy, ...(mock ? { '/__agent_ui_mock': `http://127.0.0.1:${mock.address().port}` } : {}) } },
      html: { ...config.html, tags: [...(Array.isArray(tags) ? tags : tags ? [tags] : []), {
        tag: 'div', head: false, append: false,
        attrs: { role: 'status', 'data-agent-ui-preview-source': options.mode,
          title: realText ? '当前 Python 智能体生成真实回复；仅文本，不传页面业务数据，完整业务能力未验收' : '官方 Mock 提供模拟回复，尚未连接真实智能体',
          'aria-label': realText ? '真实对话预览，仅文本' : '开发预览，官方 Mock 模拟回复，非真实智能体',
          style: 'position:fixed;left:8px;bottom:8px;z-index:2147483647;max-width:calc(100vw - 16px);padding:2px 6px;background:Canvas;color:CanvasText;font:11px/1.5 system-ui,sans-serif;pointer-events:none' },
        children: formalText ? '真实文本对话 · 正式接线' : realText ? '真实对话预览 · 仅文本' : '开发预览 · 模拟回复',
      }] },
      tools: { ...config.tools, bundlerChain: [...(Array.isArray(hooks) ? hooks : hooks ? [hooks] : []),
        /** 保留目标原有编译规则，仅改写已核对的空连接 Provider。 */
        ...(!formalText ? [chain => chain.module.rule('devagentstudio-preview-mock').test(filename => filename === provider)
          .enforce('pre').use('devagentstudio-preview-mock')
          .loader(fileURLToPath(new URL('./agent_ui_preview_endpoint.cjs', import.meta.url)))
          .options({ sha256: options.providerSha256, agentId: options.agentId,
            mode: options.mode, endpoint: options.endpoint, sessionId: options.sessionId })] : []),
      ] },
    } })
    preview = await rsbuild.startDevServer()
    if (closing) { await preview.server.close(); return }
    console.log(realText ? 'Agent UI 真实对话预览：当前 Python 智能体，仅文本；完整 T27 仍待验收。'
      : 'Agent UI 开发预览：官方 Mock，真实智能体尚未连接。')
  } catch (error) {
    await close()
    throw error
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1 })
