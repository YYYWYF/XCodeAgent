import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'
import { createRequire } from 'node:module'

// Hook 回归复用 Node 的 React 调度，确保 act 可执行且不遗留 MessagePort。
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
const entryFile = path.join(frontendRoot, 'tests', 'previewRuntime.test.tsx')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-preview-runtime-tests-'))
const outputFile = path.join(outputDirectory, 'previewRuntime.test.mjs')
const styleConfig = JSON.parse(await fs.readFile(path.join(frontendRoot, 'src/renderer/src/config/style.json'), 'utf8'))

try {
  // 将协议定向测试打包到系统临时目录，避免生成仓库内测试产物。
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    define: { 'process.env.NODE_ENV': JSON.stringify('test') },
    ssr: { noExternal: true },
    // 表详情测试复用生产样式变量，避免独立 SSR 测试缺失 Less 品牌前缀。
    css: {
      preprocessorOptions: {
        less: {
          additionalData: `@class-prefix: ${styleConfig.classPrefix};`,
          javascriptEnabled: true,
          modifyVars: { '@root-entry-name': 'default', '@primary-color': '#6b3cf0' }
        }
      }
    },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: entryFile,
      rollupOptions: {
        external: reactModules,
        output: {
          entryFileNames: path.basename(outputFile),
          paths: (id) => (reactModules.includes(id) ? pathToFileURL(require.resolve(id)).href : id)
        }
      }
    }
  })
  await import(`${pathToFileURL(outputFile).href}?run=${Date.now()}`)
} finally {
  // 无论测试是否成功，都清理系统临时目录。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
