import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'
import { createRequire } from 'node:module'

// Hook 回归使用原生 React 调度，确保真实 effect 执行且 act 不遗留 MessagePort。
process.env.NODE_ENV = 'test'
const require = createRequire(import.meta.url)
const reactModules = ['react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'react-dom', 'react-dom/server', 'react-test-renderer']

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'connectionRecoverySeparation.test.tsx')
const outputDirectory = await fs.mkdtemp(
  path.join(os.tmpdir(), 'xcodeagent-connection-recovery-tests-')
)
const outputFile = path.join(outputDirectory, 'connectionRecoverySeparation.test.mjs')

try {
  // 使用独立临时构建验证 Connection 与 durable Recovery 的二维 UI 契约。
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    define: { 'process.env.NODE_ENV': JSON.stringify('test') },
    css: {
      preprocessorOptions: {
        less: { additionalData: '@class-prefix: xa;', javascriptEnabled: true }
      }
    },
    ssr: { noExternal: true },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: entryFile,
      rollupOptions: {
        external: reactModules,
        output: {
          entryFileNames: path.basename(outputFile),
          paths: (id) => reactModules.includes(id) ? require.resolve(id) : id
        }
      }
    }
  })
  await import(`${pathToFileURL(outputFile).href}?run=${Date.now()}`)
} finally {
  // 只清理由本测试入口创建的系统临时目录。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
