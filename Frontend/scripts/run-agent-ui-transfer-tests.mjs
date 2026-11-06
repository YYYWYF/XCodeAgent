import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const root = path.resolve(import.meta.dirname, '..')
const require = createRequire(path.join(root, 'package.json'))
const output = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-agent-ui-transfer-'))
const originalNodeEnv = process.env.NODE_ENV
process.env.NODE_ENV = 'development'
try {
  await build({
    configFile: false,
    define: { 'process.env.NODE_ENV': JSON.stringify('development') },
    esbuild: { jsx: 'automatic' },
    logLevel: 'error',
    root,
    ssr: { noExternal: true },
    plugins: [
      {
        name: 'transfer-panel-dom-boundary',
        enforce: 'pre',
        // 只隔离新面板的 Antd portal；服务与官方 SDK 均用实际实现。
        resolveId(source, importer) {
          // 保留 React 的 Node 实现，使异步 act 使用 setImmediate 并正常释放测试进程。
          if (['react', 'react/jsx-runtime', 'react-test-renderer'].includes(source)) {
            return { id: require.resolve(source), external: true }
          }
          if (source === 'antd' && (importer?.endsWith('/components/AgentUiTransfer/index.tsx') ||
              importer?.endsWith('/WorkflowRunCard/AgentUiPreparationReview.tsx'))) {
            return path.join(root, 'tests/agentUiTransferAntd.tsx')
          }
        }
      }
    ],
    css: { preprocessorOptions: { less: { additionalData: '@class-prefix: devagentstudio;' } } },
    build: {
      minify: false,
      outDir: output,
      ssr: true,
      rollupOptions: {
        input: {
          sdk: path.join(root, 'tests/agentUiDesignTransfer.test.ts'),
          panel: path.join(root, 'tests/agentUiTransferPanel.test.tsx'),
          preparation: path.join(root, 'tests/agentUiPreparationReview.test.tsx')
        },
        output: { entryFileNames: '[name].mjs' }
      }
    }
  })
  await import(pathToFileURL(path.join(output, 'sdk.mjs')).href)
  await import(pathToFileURL(path.join(output, 'panel.mjs')).href)
  await import(pathToFileURL(path.join(output, 'preparation.mjs')).href)
} finally {
  if (originalNodeEnv === undefined) delete process.env.NODE_ENV
  else process.env.NODE_ENV = originalNodeEnv
  // 只清理本次 runner 创建的唯一临时目录。
  await fs.rm(output, { recursive: true, force: true })
}
