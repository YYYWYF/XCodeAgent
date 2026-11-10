import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { build } from 'vite'

// Hook 回归通过 Node 原生开发版 React 执行 act，避免打包其调度器后无法退出。
process.env.NODE_ENV = 'test'
const require = createRequire(import.meta.url)
const reactModules = [
  'react',
  'react/jsx-runtime',
  'react/jsx-dev-runtime',
  'react-dom',
  'react-dom/server',
  'react-test-renderer'
]
const frontendRoot = path.resolve(import.meta.dirname, '..')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-uncommitted-'))
const outputFile = path.join(outputDirectory, 'uncommittedChanges.test.mjs')

try {
  // 将聚焦测试及真实 React 组件临时打包，沿用应用 LESS 的前缀配置。
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    define: {
      'process.env.NODE_ENV': JSON.stringify('test'),
      // 测试需要读回实现里的常量；打包后 import.meta.url 指向临时目录，无法据此定位源码。
      __FRONTEND_ROOT__: JSON.stringify(frontendRoot)
    },
    esbuild: { jsx: 'automatic' },
    css: {
      preprocessorMaxWorkers: 0,
      preprocessorOptions: { less: { additionalData: '@class-prefix: test;' } }
    },
    ssr: { noExternal: true },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: path.join(frontendRoot, 'tests/uncommittedChanges.test.ts'),
      rollupOptions: {
        external: reactModules,
        output: {
          entryFileNames: path.basename(outputFile),
          paths: (id) => (reactModules.includes(id) ? pathToFileURL(require.resolve(id)).href : id)
        }
      }
    }
  })
  await import(pathToFileURL(outputFile).href)
} finally {
  // 无论结果如何都只清理此次测试创建的临时产物。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
