import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const frontendRoot = path.resolve(import.meta.dirname, '..')
const outputDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-ui-design-code-'))
const outputFile = path.join(outputDirectory, 'uiDesignPageCode.test.mjs')

try {
  // 主进程模块用 node: 内建模块，打包成 ESM 后直接跑。
  await build({
    configFile: false,
    logLevel: 'error',
    root: frontendRoot,
    ssr: { noExternal: true },
    build: {
      emptyOutDir: true,
      minify: false,
      outDir: outputDirectory,
      ssr: path.join(frontendRoot, 'tests/uiDesignPageCode.test.ts'),
      rollupOptions: {
        external: ['electron'],
        output: { entryFileNames: path.basename(outputFile) }
      }
    }
  })
  await import(pathToFileURL(outputFile).href)
} finally {
  // 无论结果如何都只清理此次测试创建的临时产物。
  await fs.rm(outputDirectory, { force: true, recursive: true })
}
