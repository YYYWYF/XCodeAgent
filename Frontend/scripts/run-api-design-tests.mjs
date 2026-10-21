import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'apiDesign.test.ts')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-api-design-tests-'))
const outputFile = path.join(outputDirectory, 'apiDesign.test.mjs')
// Hook 回归与生产代码共用 React 18 的 Node 实现，避免打包 act 的 MessagePort 不退出。
process.env.NODE_ENV = 'test'
const require = createRequire(import.meta.url)
const reactModules = ['react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'react-dom', 'react-dom/server', 'react-test-renderer']

try {
  // 将纯函数测试打包到系统临时目录，避免生成仓库内测试产物。
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    define: { 'process.env.NODE_ENV': JSON.stringify('test') },
    ssr: { noExternal: true },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: entryFile,
      rollupOptions: {
        external: reactModules,
        output: { entryFileNames: path.basename(outputFile), paths: (id) => reactModules.includes(id) ? require.resolve(id) : id }
      }
    }
  })
  await import(`${pathToFileURL(outputFile).href}?run=${Date.now()}`)
} finally {
  // 无论测试是否成功，都清理系统临时目录。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
