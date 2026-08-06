// 浏览器原型的 Vite 配置：UI 层复用真实 renderer，数据来自 mock 数据层（mock-data/ 与 src/renderer/src/mock/）。
// dev:prototype = `vite --config vite.prototype.config.ts`，从 prototype/ 根运行，故用 process.cwd()。
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import styleConfig from './src/renderer/src/config/style.json'

const rootDir = process.cwd()

// 拦截“被生成应用”的页面路由，返回 mock 应用页（模拟已启动的开发服务器）。
function mockPreviewAppPlugin() {
  const previewHtml = readFileSync(resolve(rootDir, 'mock-data/preview-app.html'), 'utf-8')
  const appPages = ['/my-projects', '/my-rechecks', '/recheck-review']
  return {
    name: 'mock-preview-app',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const path = (req.url || '').split('?')[0]
        if (appPages.includes(path)) {
          res.setHeader('Content-Type', 'text/html; charset=utf-8')
          res.end(previewHtml)
          return
        }
        next()
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
    open: '/',
    port: 5180,
    strictPort: false
  }
})
