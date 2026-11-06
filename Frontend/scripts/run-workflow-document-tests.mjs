import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

// Hook 回归使用 Node 的 React 开发调度，不在生产 React 包中调用 act。
process.env.NODE_ENV = 'test'
const require = createRequire(import.meta.url)
const frontendRoot = path.resolve(import.meta.dirname, '..')
const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-workflow-documents-'))
const reactModules = ['react', 'react-test-renderer']

try {
  await build({
    configFile: false,
    root: frontendRoot,
    logLevel: 'error',
    define: { 'process.env.NODE_ENV': JSON.stringify('test') },
    ssr: { noExternal: true },
    build: {
      minify: false,
      outDir: directory,
      ssr: path.join(frontendRoot, 'tests/workflowDocuments.test.ts'),
      rollupOptions: {
        external: reactModules,
        output: {
          entryFileNames: 'workflowDocuments.mjs',
          paths: (id) => (reactModules.includes(id) ? pathToFileURL(require.resolve(id)).href : id)
        }
      }
    }
  })
  await import(pathToFileURL(path.join(directory, 'workflowDocuments.mjs')).href)
} finally {
  // 只清理本次回归创建的系统临时产物。
  await fs.rm(directory, { force: true, recursive: true })
}
