import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { build } from 'vite'

// 复用项目已有 Hook 测试方式，保持原生 React 调度并避免打包后残留 MessagePort。
process.env.NODE_ENV = 'test'
const require = createRequire(import.meta.url)
const reactModules = ['react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'react-dom', 'react-dom/server', 'react-test-renderer']

const frontendRoot = path.resolve(import.meta.dirname, '..')
const outputDirectory = await fs.mkdtemp(
  path.join(os.tmpdir(), 'devagentstudio-development-artifacts-')
)
const outputFile = path.join(outputDirectory, 'developmentArtifacts.test.mjs')

try {
  // 将聚焦测试及真实 React 组件临时打包，沿用应用 LESS 的前缀配置。
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    define: { 'process.env.NODE_ENV': JSON.stringify('test') },
    esbuild: { jsx: 'automatic' },
    css: { preprocessorOptions: { less: { additionalData: '@class-prefix: test;' } } },
    ssr: { noExternal: true },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: path.join(frontendRoot, 'tests/developmentArtifacts.test.tsx'),
      rollupOptions: {
        external: reactModules,
        output: {
          entryFileNames: path.basename(outputFile),
          paths: (id) => reactModules.includes(id) ? require.resolve(id) : id
        }
      }
    }
  })
  await import(pathToFileURL(outputFile).href)
} finally {
  // 无论结果如何都只清理此次测试创建的临时产物。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
