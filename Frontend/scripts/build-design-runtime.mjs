/**
 * 打包 antd5 runtime bundle。
 *
 * 把 src/renderer/design-runtime/antd5-runtime.ts 打成一个 IIFE bundle，
 * 产物输出到 src/renderer/public/design-runtime/antd5-runtime.js，随主应用
 * 发版，供同源空白 iframe 注入。这是方案 B 里"预打包 antd5 runtime"那一步——
 * 构建期一次性打包，运行时不再 pnpm install / 起 dev server。
 *
 * 用法：node scripts/build-design-runtime.mjs
 * 也可在 package.json 加 "build:design-runtime": "node scripts/build-design-runtime.mjs"
 */

import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'vite'
import react from '@vitejs/plugin-react'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = resolve(__dirname, '..')

await build({
  // 用独立 config，不碰主工程的 electron-vite 配置，避免 antd4/antd5 串味。
  configFile: false,
  root,
  logLevel: 'info',
  plugins: [react()],
  define: {
    // antd5 / pro-components 的部分依赖会引用 process.env.NODE_ENV 做分支，
    // 浏览器没有 process 全局会抛 ReferenceError。这里在编译期替换为字面量，
    // 既消除 process 引用，又让产物走 production 分支（去掉 dev-only 警告）。
    'process.env.NODE_ENV': JSON.stringify('production'),
    'process.env': JSON.stringify({ NODE_ENV: 'production' })
  },
  build: {
    // IIFE：产物自执行，把导出挂到 window（入口里已挂 __DESIGN_RUNTIME__）。
    lib: {
      entry: resolve(root, 'src/renderer/design-runtime/antd5-runtime.ts'),
      formats: ['iife'],
      name: '__DESIGN_RUNTIME_BOOTSTRAP__',
      fileName: () => 'antd5-runtime.js'
    },
    outDir: resolve(root, 'src/renderer/public/design-runtime'),
    emptyOutDir: true,
    // 设计稿只用浏览器端，不需要 node polyfill。
    minify: 'esbuild',
    sourcemap: false,
    // antd5 / pro-components / icons / react 全部打进 bundle，运行时通过 window 取。
    rollupOptions: {
      external: []
    }
  },
  resolve: {
    alias: {
      // 入口里 `from 'antd5'` 指向 npm alias 装的 antd@5。
      antd5: resolve(root, 'node_modules/antd5'),
      // 关键：pro-components 的 peerDeps 同时支持 antd4/antd5，但 pnpm 默认
      // 会把它解析到主工程已装的 antd4，从而把 antd4 的 .less 拉进 bundle
      // 并触发 "Inline JavaScript is not enabled" 的 less 报错。这里把 `antd`
      // 强制指向 antd5，让 pro-components 走 antd5（纯 css-in-js，无 less）。
      antd: resolve(root, 'node_modules/antd5')
    }
  }
})

console.log('✓ antd5 runtime bundle 已生成: src/renderer/public/design-runtime/antd5-runtime.js')
