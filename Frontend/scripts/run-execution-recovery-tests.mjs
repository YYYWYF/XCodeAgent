import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const outputRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-execution-recovery-tests-'))
const entries = ['executionRecoveryState.test.ts', 'executionRecoveryCard.test.tsx']

try {
  // 分别打包状态解析与实际卡片测试，避免测试依赖仓库里的生成文件。
  for (const entry of entries) {
    const outputDirectory = path.join(outputRoot, path.parse(entry).name)
    const outputFile = path.join(outputDirectory, `${path.parse(entry).name}.mjs`)
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
        ssr: path.join(frontendRoot, 'tests', entry),
        rollupOptions: { output: { entryFileNames: path.basename(outputFile) } }
      }
    })
    await import(`${pathToFileURL(outputFile).href}?run=${Date.now()}`)
  }
} finally {
  // 仅清理由本测试入口创建的系统临时目录。
  await fs.rm(outputRoot, { force: true, recursive: true })
}
