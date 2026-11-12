import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

// 卡片交互测试用原生 React 调度运行真实 effect。
process.env.NODE_ENV = 'test'
const require = createRequire(import.meta.url)
const reactModules = ['react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'react-dom', 'react-dom/server', 'react-test-renderer']

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entries = ['applicationConfigCard.test.tsx', 'settingsConfigApply.test.tsx', 'applicationPersistence.test.ts', 'applicationConfigRecovery.test.ts']
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-application-config-tests-'))

try {
  for (const entry of entries) {
    const outputFile = path.join(outputDirectory, entry.replace(/\.tsx?$/, '.mjs'))
    // 测试产物写入系统临时目录，避免污染工作区。
    await build({
      configFile: false,
      define: { 'process.env.NODE_ENV': JSON.stringify('test') },
      esbuild: { jsx: 'automatic' },
      logLevel: 'error',
      root: frontendRoot,
      ssr: { noExternal: true },
      build: {
        emptyOutDir: false,
        minify: false,
        outDir: outputDirectory,
        ssr: path.join(frontendRoot, 'tests', entry),
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
  }
} finally {
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
