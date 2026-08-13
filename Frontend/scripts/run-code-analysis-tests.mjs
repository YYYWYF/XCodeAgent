import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'codeAnalysis.test.ts')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-code-analysis-tests-'))
const outputFile = path.join(outputDirectory, 'codeAnalysis.test.mjs')

try {
  // 将 TypeScript 定向测试打包到临时目录，避免产生仓库构建文件。
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
  // 无论测试结果如何都清理临时目录。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
