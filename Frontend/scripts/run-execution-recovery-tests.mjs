import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'
import { createRequire } from 'node:module'

// 使用同一份 React 测试运行时，确保真实剪贴板按钮的交互测试支持 act。
process.env.NODE_ENV = 'test'
const require = createRequire(import.meta.url)
const reactModules = [
  'react',
  'react/jsx-runtime',
  'react/jsx-dev-runtime',
  'react-dom',
  'react-dom/server',
  'react-test-renderer'
]

const frontendRoot = path.resolve(import.meta.dirname, '..')
const outputRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-execution-recovery-tests-'))
const entries = [
  'executionRecoveryState.test.ts',
  'executionRecoveryCard.test.tsx',
  'errorDiagnostics.test.tsx',
  'workbenchErrorNotification.test.tsx'
]

try {
  // 分别打包状态解析与实际卡片测试，避免测试依赖仓库里的生成文件。
  for (const entry of entries) {
    const outputDirectory = path.join(outputRoot, path.parse(entry).name)
    const outputFile = path.join(outputDirectory, `${path.parse(entry).name}.mjs`)
    await build({
      configFile: false,
      logLevel: 'error',
      root: frontendRoot,
      define: { 'process.env.NODE_ENV': JSON.stringify('test') },
      css: {
        preprocessorMaxWorkers: 0,
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
        rollupOptions: {
          external: reactModules,
          output: {
            entryFileNames: path.basename(outputFile),
            paths: (id) => (reactModules.includes(id) ? require.resolve(id) : id)
          }
        }
      }
    })
    await import(`${pathToFileURL(outputFile).href}?run=${Date.now()}`)
  }
} finally {
  // 仅清理由本测试入口创建的系统临时目录。
  await fs.rm(outputRoot, { force: true, recursive: true })
}
