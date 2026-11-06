import { defineConfig, type Plugin } from 'vite'
import tailwindcss from '@tailwindcss/vite'

/** 检查真实生产模块图，拒绝开发工具、源码 catalog 和 Node 内建模块进入页面。 */
function productionBoundary(): Plugin {
  return {
    name: 'agent-ui-production-boundary',
    generateBundle() {
      for (const id of this.getModuleIds()) {
        if (/agent-ui-creator|source-registry|node:(fs|crypto)|__vite-browser-external/.test(id)) {
          this.error(`Development-only module in production graph: ${id}`)
        }
      }
    },
  }
}

export default defineConfig({
  plugins: [tailwindcss(), productionBoundary()],
  build: {
    rollupOptions: {
      output: {
        // 同一运行包保持同一分块，避免公开 facade 循环引用跨 chunk 执行。
        manualChunks(id) {
          if (id.includes('node_modules')) return 'agent-ui-vendor'
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5188,
    strictPort: true,
    proxy: { '/agent': 'http://127.0.0.1:47835' },
  },
})
