import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'stageSessions.test.ts')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-stage-session-tests-'))
const outputFile = path.join(outputDirectory, 'stageSessions.test.mjs')

try {
  // 将 TypeScript 定向测试临时打包到系统目录，避免在仓库中留下测试产物。
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    ssr: { noExternal: true },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: entryFile,
      rollupOptions: {
        output: { entryFileNames: path.basename(outputFile) }
      }
    }
  })
  await import(`${pathToFileURL(outputFile).href}?run=${Date.now()}`)
} finally {
  // 无论测试是否成功，都清理临时打包目录。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
