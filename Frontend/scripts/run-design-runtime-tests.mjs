import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'designRuntimePath.test.ts')
const projectPreviewTests = path.join(frontendRoot, 'tests', 'agentUiProjectPreview.test.ts')
const styleConfig = JSON.parse(
  await fs.readFile(path.join(frontendRoot, 'src/renderer/src/config/style.json'), 'utf8')
)
const outputDirectory = await fs.mkdtemp(
  path.join(os.tmpdir(), 'devagentstudio-design-runtime-tests-')
)

try {
  // 用项目现有的 Vite 临时运行模式执行主进程协议的定向测试。
  await build({
    configFile: false,
    define: { 'process.env.NODE_ENV': JSON.stringify('development') },
    logLevel: 'error',
    root: frontendRoot,
    ssr: { noExternal: true },
    css: {
      preprocessorOptions: {
        less: {
          additionalData: `@class-prefix: ${styleConfig.classPrefix};`,
          javascriptEnabled: true,
          modifyVars: { '@root-entry-name': 'default', '@primary-color': '#6b3cf0' }
        }
      }
    },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: true,
      rollupOptions: {
        input: { runtime: entryFile, projectPreview: projectPreviewTests },
        output: { entryFileNames: '[name].mjs' }
      }
    }
  })
  await import(pathToFileURL(path.join(outputDirectory, 'runtime.mjs')).href)
  await import(pathToFileURL(path.join(outputDirectory, 'projectPreview.mjs')).href)
} finally {
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
