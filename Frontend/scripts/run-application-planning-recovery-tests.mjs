import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'applicationPlanningRecovery.test.ts')
const outputDirectory = await fs.mkdtemp(
  path.join(os.tmpdir(), 'xcodeagent-planning-recovery-tests-')
)
const outputFile = path.join(outputDirectory, 'applicationPlanningRecovery.test.mjs')

try {
  // 沿用 Runtime 测试的临时 Vite 打包方式，独立验证 Recovery Projection 解析。
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
  // 只清理由本测试入口创建的系统临时目录。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
