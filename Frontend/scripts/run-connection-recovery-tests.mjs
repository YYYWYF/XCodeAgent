import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'connectionRecoverySeparation.test.tsx')
const outputDirectory = await fs.mkdtemp(
  path.join(os.tmpdir(), 'xcodeagent-connection-recovery-tests-')
)
const outputFile = path.join(outputDirectory, 'connectionRecoverySeparation.test.mjs')

try {
  // 使用独立临时构建验证 Connection 与 durable Recovery 的二维 UI 契约。
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    css: {
      preprocessorOptions: {
        less: { additionalData: '@class-prefix: xa;', javascriptEnabled: true }
      }
    },
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
