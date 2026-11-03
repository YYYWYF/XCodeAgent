import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

process.env.NODE_ENV = 'test'
const require = createRequire(import.meta.url)
const reactModules = ['react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'react-test-renderer']
const frontendRoot = path.resolve(import.meta.dirname, '..')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-backend-logs-'))
const outputFile = path.join(outputDirectory, 'backendLogs.test.mjs')

try {
  await build({
    configFile: false,
    root: frontendRoot,
    logLevel: 'error',
    esbuild: { jsx: 'automatic' },
    define: { 'process.env.NODE_ENV': JSON.stringify('test') },
    ssr: { noExternal: true },
    build: {
      minify: false,
      outDir: outputDirectory,
      ssr: path.join(frontendRoot, 'tests/backendLogs.test.tsx'),
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
  await fs.rm(outputDirectory, { recursive: true, force: true })
}
