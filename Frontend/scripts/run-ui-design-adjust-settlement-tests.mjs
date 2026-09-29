import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-adjust-settle-'))
const outputFile = path.join(outputDirectory, 'uiDesignAdjustSettlement.test.mjs')

try {
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    ssr: { noExternal: true },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: path.join(frontendRoot, 'tests/uiDesignAdjustSettlement.test.ts'),
      rollupOptions: { output: { entryFileNames: path.basename(outputFile) } }
    }
  })
  await import(pathToFileURL(outputFile).href)
} finally {
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
