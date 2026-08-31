// 浏览器原型的 Vite 配置：UI 层复用真实 renderer，数据来自 mock 数据层（mock-data/ 与 src/renderer/src/mock/）。
// dev:prototype = `vite --config vite.prototype.config.ts`，从 prototype/ 根运行，故用 process.cwd()。
import { readFileSync } from 'fs'
import { createServer } from 'http'
import { resolve } from 'path'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import styleConfig from './src/renderer/src/config/style.json'

const rootDir = process.cwd()
const MOCK_APPLICATION_PORT = 5190

// 在独立端口运行“被生成应用”，避免预览地址误指向 XCodeAgent 原型自身。
function mockPreviewAppPlugin(): Plugin {
  const previewHtmlPath = resolve(rootDir, 'mock-data/preview-app.html')
  return {
    name: 'mock-preview-app',
    configureServer(server): void {
      const previewServer = createServer((request, response): void => {
        const path = (request.url || '/').split('?')[0]
        if (path === '/' || path === '/recheck-introduction' || path === '/my-rechecks') {
          response.statusCode = 200
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.end(readFileSync(previewHtmlPath, 'utf-8'))
          return
        }
        response.statusCode = 404
        response.setHeader('Content-Type', 'text/plain; charset=utf-8')
        response.end('Mock application route not found')
      })
      // 热重载或异常退出后预览端口可能仍由上一进程提供服务；复用它，避免拖垮工作台本身。
      previewServer.on('error', (error: NodeJS.ErrnoException): void => {
        if (error.code === 'EADDRINUSE') {
          server.config.logger.info(
            `预览应用已运行在 0.0.0.0:${MOCK_APPLICATION_PORT}`
          )
          return
        }
        server.config.logger.error(`预览应用启动失败：${error.message}`)
      })
      previewServer.listen(MOCK_APPLICATION_PORT, '0.0.0.0')
      server.httpServer?.once('close', (): void => {
        if (previewServer.listening) previewServer.close()
      })
    }
  }
}

export default defineConfig({
  root: resolve(rootDir, 'src/renderer'),
  plugins: [react(), mockPreviewAppPlugin()],
  resolve: {
    alias: {
      '@renderer': resolve(rootDir, 'src/renderer/src'),
      '@mock-data': resolve(rootDir, 'mock-data')
    }
  },
  define: {
    'process.env.APP_ENV': JSON.stringify(process.env.APP_ENV ?? 'dev')
  },
  css: {
    preprocessorOptions: {
      // 逐字复制 electron.vite.config.ts 的 less 配置，保证 antd.less 与 @{class-prefix} 编译一致。
      less: {
        additionalData: `@class-prefix: ${styleConfig.classPrefix};`,
        javascriptEnabled: true,
        modifyVars: {
          '@root-entry-name': 'default',
          '@primary-color': '#6b3cf0'
        }
      }
    }
  },
  server: {
    // 监听全部网卡，允许同一局域网内的其它设备访问原型演示地址。
    host: true,
    open: '/',
    port: 5180,
    strictPort: false
  }
})
