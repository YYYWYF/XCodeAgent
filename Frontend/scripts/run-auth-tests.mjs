import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'authentication.test.ts')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-auth-tests-'))
const outputFile = path.join(outputDirectory, 'authentication.test.mjs')

try {
  // 将 TypeScript 测试临时打包为 Node 可执行模块，不在仓库中生成测试产物。
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
  // 测试结束后只清理系统临时目录中的打包文件。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
