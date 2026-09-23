import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'designRuntimePath.test.ts')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-design-runtime-tests-'))
const outputFile = path.join(outputDirectory, 'designRuntimePath.test.mjs')

try {
  // 用项目现有的 Vite 临时运行模式执行主进程协议的定向测试。
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
      rollupOptions: { output: { entryFileNames: path.basename(outputFile) } }
    }
  })
  await import(`${pathToFileURL(outputFile).href}?run=${Date.now()}`)
} finally {
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
