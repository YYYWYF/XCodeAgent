import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-diagnostics-tests-'))
try {
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    ssr: { noExternal: true },
    build: {
      outDir: directory,
      minify: false,
      ssr: path.join(frontendRoot, 'tests/desktopDiagnostics.test.ts'),
      rollupOptions: { output: { entryFileNames: 'desktopDiagnostics.test.mjs' } }
    }
  })
  await import(pathToFileURL(path.join(directory, 'desktopDiagnostics.test.mjs')).href)
} finally {
  await fs.rm(directory, { recursive: true, force: true })
}
