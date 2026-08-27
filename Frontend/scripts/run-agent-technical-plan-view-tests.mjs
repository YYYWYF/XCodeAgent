import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const entryFile = path.join(frontendRoot, 'tests', 'agentTechnicalPlanView.test.ts')
const outputDirectory = await fs.mkdtemp(
  path.join(os.tmpdir(), 'xcodeagent-agent-technical-plan-view-tests-')
)
const outputFile = path.join(outputDirectory, 'agentTechnicalPlanView.test.mjs')
const styleConfig = JSON.parse(
  await fs.readFile(
    path.join(frontendRoot, 'src', 'renderer', 'src', 'config', 'style.json'),
    'utf8'
  )
)

try {
  // 将 TechnicalPlan 视图测试临时打包到系统目录，覆盖真实 React 与 Less 依赖。
  await build({
    configFile: false,
    css: {
      preprocessorOptions: {
        less: {
          additionalData: `@class-prefix: ${styleConfig.classPrefix};`,
          javascriptEnabled: true,
          modifyVars: { '@root-entry-name': 'default' }
        }
      }
    },
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
  // 无论测试是否成功，都清理临时打包目录。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
