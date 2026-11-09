import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'authorizationBootstrap.test.ts')
const outputDirectory = await fs.mkdtemp(
  path.join(os.tmpdir(), 'devagentstudio-authorization-bootstrap-tests-')
)
const outputFile = path.join(outputDirectory, 'authorizationBootstrap.test.mjs')
process.env.NODE_ENV = 'test'
// 复用 Node 原生 React，保留 act 的计时器加载路径，避免 MessagePort 阻止测试退出。
const require = createRequire(import.meta.url)
const reactModules = ['react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'react-dom', 'react-dom/server', 'react-test-renderer']

try {
  // 将 TypeScript 定向测试临时打包到系统目录，避免生成仓库内测试产物。
  await build({
    configFile: false,
    define: { 'process.env.NODE_ENV': JSON.stringify('test') },
    logLevel: 'error',
    root: frontendRoot,
    ssr: { noExternal: true },
    css: {
      preprocessorMaxWorkers: 0,
      preprocessorOptions: {
        less: { additionalData: '@class-prefix: test;', javascriptEnabled: true }
      }
    },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: entryFile,
      rollupOptions: {
        external: reactModules,
        output: { entryFileNames: path.basename(outputFile), paths: (id) => reactModules.includes(id) ? pathToFileURL(require.resolve(id)).href : id }
      }
    }
  })
  await import(`${pathToFileURL(outputFile).href}?run=${Date.now()}`)
} finally {
  // 无论测试是否成功，都清理系统临时目录。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
