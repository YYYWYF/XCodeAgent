import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'applicationPlanningRuntime.test.ts')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-planning-runtime-tests-'))
const outputFile = path.join(outputDirectory, 'applicationPlanningRuntime.test.mjs')

try {
  // 沿用项目 Vite 临时打包模式，使 Runtime 测试独立于 Modal 和 Electron。
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
  // 只清理由本测试入口创建的唯一系统临时目录。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
