import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { build } from 'vite'

// 停止时序回归使用 Node 原生 React act，避免生产构建禁用 act 或残留 MessagePort。
process.env.NODE_ENV = 'test'
const require = createRequire(import.meta.url)
const reactModules = ['react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'react-test-renderer']
const frontendRoot = path.resolve(import.meta.dirname, '..')
const outputDirectory = await fs.mkdtemp(
  path.join(os.tmpdir(), 'devagentstudio-ui-design-progress-')
)
const outputFile = path.join(outputDirectory, 'uiDesignGenerationProgress.test.mjs')

try {
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
      ssr: path.join(frontendRoot, 'tests/uiDesignGenerationProgress.test.ts'),
      rollupOptions: { external: reactModules, output: {
        entryFileNames: path.basename(outputFile),
        paths: (id) => reactModules.includes(id) ? require.resolve(id) : id
      } }
    }
  })
  await import(pathToFileURL(outputFile).href)
} finally {
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
