import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-released-version-'))
const outputFile = path.join(outputDirectory, 'releasedVersionPanel.test.mjs')

try {
  // 将聚焦测试及真实 React 组件临时打包，沿用应用 LESS 的前缀配置。
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    define: {
      // 测试需要读回实现里的常量；打包后 import.meta.url 指向临时目录，无法据此定位源码。
      __FRONTEND_ROOT__: JSON.stringify(frontendRoot)
    },
    esbuild: { jsx: 'automatic' },
    css: { preprocessorOptions: { less: { additionalData: '@class-prefix: test;' } } },
    ssr: { noExternal: true },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: path.join(frontendRoot, 'tests/releasedVersionPanel.test.tsx'),
      rollupOptions: { output: { entryFileNames: path.basename(outputFile) } }
    }
  })
  await import(pathToFileURL(outputFile).href)
} finally {
  // 无论结果如何都只清理此次测试创建的临时产物。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
