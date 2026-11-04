import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

// 外部 React 也必须使用开发入口，才能执行 act 与组件状态测试。
process.env.NODE_ENV = 'development'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const outputDirectory = await fs.mkdtemp(
  path.join(os.tmpdir(), 'devagentstudio-database-delivery-')
)
const styleConfig = JSON.parse(
  await fs.readFile(path.join(frontendRoot, 'src/renderer/src/config/style.json'), 'utf8')
)

try {
  // React 由 Node 原生加载，保留 act 对 timers 的动态 require，避免打包后的 MessageChannel 阻止退出。
  await fs.symlink(
    path.join(frontendRoot, 'node_modules'),
    path.join(outputDirectory, 'node_modules'),
    'dir'
  )
  for (const entry of ['databaseDeliveryPreview']) {
    // 分别打包两种渲染器，避免测试渲染与静态渲染共享 React 上下文。
    await build({
      configFile: false,
      root: frontendRoot,
      logLevel: 'error',
      define: { 'process.env.NODE_ENV': JSON.stringify('development') },
      esbuild: { jsx: 'automatic' },
      resolve: {
        alias: [
          {
            find: /^antd$/,
            replacement: path.join(frontendRoot, 'tests/databaseDeliveryAntd.tsx')
          },
          {
            find: /^@ant-design\/icons$/,
            replacement: path.join(frontendRoot, 'tests/databaseDeliveryIcons.tsx')
          }
        ]
      },
      // 直接在本进程编译 LESS，避免测试打包的预处理 worker 保持 Node 进程存活。
      css: {
        preprocessorMaxWorkers: 0,
        preprocessorOptions: {
          less: { additionalData: `@class-prefix: ${styleConfig.classPrefix};` }
        }
      },
      ssr: { noExternal: true },
      build: {
        outDir: outputDirectory,
        minify: false,
        emptyOutDir: false,
        ssr: path.join(frontendRoot, `tests/${entry}.test.tsx`),
        rollupOptions: {
          external: [
            'react',
            'react/jsx-runtime',
            'react-dom',
            'react-dom/server.node',
            'react-test-renderer'
          ],
          output: { entryFileNames: `${entry}.mjs` }
        }
      }
    })
    await import(pathToFileURL(path.join(outputDirectory, `${entry}.mjs`)).href)
  }
} finally {
  // 仅回收此次测试创建的临时目录。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
