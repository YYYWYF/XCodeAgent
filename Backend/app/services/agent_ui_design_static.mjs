import { createServer } from 'node:http'
import { readdir, readFile, lstat } from 'node:fs/promises'
import { join, extname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'
import { loadDesignMock } from './agent_ui_design_mock.mjs'

/** 同一受管进程同时持有静态快照和可选官方 Mock；生产构建没有工具代码。 */
export async function startDesignStatic(importMockApi) {
  const [directoryUrl, launchId, requestedPort, expectedDigest, rawOptions = 'null'] = process.argv.slice(2)
  if (process.versions.electron || !/^[a-f0-9]{32}$/.test(launchId ?? '')) process.exit(1)
  const root = fileURLToPath(directoryUrl)
  const files = new Map()
  const mime = new Map(Object.entries({
    '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
    '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2',
  }))
  let bytes = 0

  /** 启动时只装载构建文件快照，后续请求不再访问磁盘或跟随符号链接。 */
  async function load(directory, prefix = '') {
    if ((await lstat(directory)).isSymbolicLink()) throw new Error('symlink')
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      if (entry.isSymbolicLink() || entry.name.startsWith('.')) throw new Error('invalid build')
      const path = join(directory, entry.name)
      const key = prefix + entry.name
      if (entry.isDirectory()) {
        await load(path, key + '/')
      } else {
        const type = mime.get(extname(entry.name))
        const size = (await lstat(path)).size
        if (!type || size > 16 * 1024 * 1024 || files.size >= 512) throw new Error('invalid build')
        const body = await readFile(path)
        bytes += body.length
        if (bytes > 32 * 1024 * 1024) throw new Error('build too large')
        files.set(key, { type, body })
      }
    }
  }

  try {
    await load(root)
    if (!files.has('index.html')) throw new Error('missing entry')
    const digest = createHash('sha256')
    for (const key of [...files.keys()].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)))) {
      digest.update(key).update('\0').update(createHash('sha256').update(files.get(key).body).digest())
    }
    if (digest.digest('hex') !== expectedDigest) throw new Error('build changed')
    const options = JSON.parse(rawOptions)
    const mock = options ? await loadDesignMock(options, importMockApi) : null
    const prefix = `/${launchId}/`
    if (mock) {
      // 配置只注入当前 HTTP 快照；磁盘 dist、应用源码和正式交付中均没有测试地址。
      const config = { pageId: options.binding.pageId, endpoint: `${prefix}agent`, source: 'official-mock',
        launchId, revision: options.binding.revision }
      const escaped = JSON.stringify(config).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;')
      const file = files.get('index.html')
      const meta = `<meta name="agent-ui-design-preview" content="${escaped}">`
      const html = file.body.toString('utf8')
      file.body = Buffer.from(html.includes('</head>') ? html.replace('</head>', () => meta + '</head>') : meta + html)
    }
    let authority = ''
    const csp = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
      "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; " +
      // 允许官方组件收到 submit 事件；form-action 仍禁止任何未被组件处理的表单导航。
      "base-uri 'none'; form-action 'none'; object-src 'none'; sandbox allow-scripts allow-same-origin allow-forms; " +
      "frame-ancestors 'self' file: devagentstudio-design: http://localhost:* http://127.0.0.1:*"
    /** 仅在本次随机路径、实际 Host 和同源请求下返回白名单构建资产。 */
    const server = createServer((request, response) => {
      response.setHeader('Content-Security-Policy', csp)
      response.setHeader('X-Content-Type-Options', 'nosniff')
      response.setHeader('Cache-Control', 'no-store')
      response.setHeader('Referrer-Policy', 'no-referrer')
      response.setHeader('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
      if (request.headers.host !== authority || (request.headers.origin && request.headers.origin !== `http://${authority}`)) {
        response.writeHead(403).end()
        return
      }
      if (mock && request.url === `${prefix}agent` && request.method === 'POST') {
        // 场景由开发 owner 固定；不暴露官方管理/历史路由，不读取或重编码 AG-UI 事件。
        request.url = `/agent?scenario=${encodeURIComponent(options.scenarioId)}`
        void mock.handler(request, response).catch(() => {
          if (!response.headersSent) response.writeHead(500)
          response.end()
        })
        return
      }
      const path = request.url ?? ''
      if (!['GET', 'HEAD'].includes(request.method) || !path.startsWith(prefix)) {
        response.writeHead(404).end()
        return
      }
      // 不做 URL 解码或 SPA 任意路径回退；编码穿越、源码和 .env 均不在快照中。
      const key = path.slice(prefix.length) || 'index.html'
      const file = files.get(key)
      if (!file) {
        response.writeHead(404).end()
        return
      }
      response.setHeader('Content-Type', file.type)
      response.writeHead(200).end(request.method === 'HEAD' ? undefined : file.body)
    })
    server.requestTimeout = 5000
    server.headersTimeout = 5000
    server.on('error', () => process.exit(1))
    server.listen({ host: '127.0.0.1', port: Number(requestedPort), exclusive: true }, () => {
      authority = `127.0.0.1:${server.address().port}`
      process.stdout.write(JSON.stringify({ launchId, processId: process.pid, url: `http://${authority}${prefix}`,
        mockEvidence: mock?.evidence ?? null }) + '\n')
    })
    /** 正常退出由受管进程组回收；关闭监听后不留下存活连接。 */
    function stop() {
      server.closeAllConnections()
      server.close(() => process.exit(0))
    }
    process.once('SIGTERM', stop)
    process.once('SIGINT', stop)
  } catch {
    process.exit(1)
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await startDesignStatic()
