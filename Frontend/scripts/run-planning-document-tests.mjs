import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { build } from 'vite'

// 保持 React 原生 Node 实现，确保 act 正确调度读取与取消 effect。
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
const root = path.resolve(import.meta.dirname, '..')
const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-planning-documents-'))
const output = path.join(directory, 'planningDocumentView.test.mjs')
try {
  await build({
    configFile: false,
    logLevel: 'error',
    root,
    define: { 'process.env.NODE_ENV': JSON.stringify('test') },
    ssr: { noExternal: true },
    css: { preprocessorOptions: { less: { additionalData: '@class-prefix: test;' } } },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: directory,
      ssr: path.join(root, 'tests/planningDocumentView.test.tsx'),
      rollupOptions: {
        external: reactModules,
        output: {
          entryFileNames: path.basename(output),
          paths: (id) => (reactModules.includes(id) ? require.resolve(id) : id)
        }
      }
    }
  })
  await import(pathToFileURL(output).href)
} finally {
  await fs.rm(directory, { force: true, recursive: true })
}
