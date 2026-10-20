import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-source-tree-'))
const outputFile = path.join(outputDirectory, 'sourceTree.test.mjs')

try {
  // 打包真实目录组件与纯合并逻辑，沿用前端已有的临时测试执行方式。
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    esbuild: { jsx: 'automatic' },
    ssr: { noExternal: true },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: path.join(frontendRoot, 'tests/sourceTree.test.tsx'),
      rollupOptions: { output: { entryFileNames: path.basename(outputFile) } }
    }
  })
  await import(pathToFileURL(outputFile).href)
} finally {
  // 仅清理本次测试创建的临时产物。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
