import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { build } from 'vite'

// React 的 Node 实现使用原生 timers，避免打包后的 act 回退创建常驻 MessagePort。
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
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-report-tests-'))
const outputFile = path.join(outputDirectory, 'testReportPanel.test.mjs')

try {
  // Hook 回归单独使用真实 React act/effect，不改变现有静态渲染测试的构建环境。
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    define: { 'process.env.NODE_ENV': JSON.stringify('test') },
    ssr: { noExternal: true },
    css: { preprocessorOptions: { less: { additionalData: '@class-prefix: test;' } } },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: path.join(frontendRoot, 'tests', 'testReportPanel.test.ts'),
      rollupOptions: {
        external: reactModules,
        output: {
          entryFileNames: path.basename(outputFile),
          paths: (id) => (reactModules.includes(id) ? require.resolve(id) : id)
        }
      }
    }
  })
  await import(`${pathToFileURL(outputFile).href}?run=${Date.now()}`)
} finally {
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
