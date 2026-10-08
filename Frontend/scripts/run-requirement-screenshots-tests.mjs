import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'requirementScreenshots.test.ts')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-screenshot-tests-'))
const outputFile = path.join(outputDirectory, 'requirementScreenshots.test.mjs')

try {
  // 把 Electron 主进程截图校验测试打包到临时目录后执行。
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
  // 无论测试是否成功，都清理本轮唯一临时目录。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
