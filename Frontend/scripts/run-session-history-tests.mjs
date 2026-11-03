import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { build } from 'vite'

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
const outputDirectory = await fs.mkdtemp(
  path.join(os.tmpdir(), 'devagentstudio-session-history-tests-')
)
const outputFile = path.join(outputDirectory, 'sessionHistory.test.mjs')
try {
  await build({
    configFile: false,
    logLevel: 'error',
    root,
    define: { 'process.env.NODE_ENV': JSON.stringify('test') },
    css: {
      preprocessorOptions: {
        less: { additionalData: '@class-prefix: test;', javascriptEnabled: true }
      }
    },
    ssr: { noExternal: true },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: path.join(root, 'tests/sessionHistory.test.tsx'),
      rollupOptions: {
        external: reactModules,
        output: {
          entryFileNames: path.basename(outputFile),
          paths: (id) => (reactModules.includes(id) ? require.resolve(id) : id)
        }
      }
    }
  })
  await import(pathToFileURL(outputFile).href)
} finally {
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
