import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'vite'

const root = path.resolve(import.meta.dirname, '..')
const output = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-agent-ui-dependencies-'))
try {
  await build({
    configFile: false,
    logLevel: 'error',
    root,
    ssr: { noExternal: true },
    build: {
      minify: false,
      outDir: output,
      ssr: path.join(root, 'tests/agentUiDependencies.test.ts'),
      rollupOptions: { output: { entryFileNames: 'test.mjs' } }
    }
  })
  await import(pathToFileURL(path.join(output, 'test.mjs')).href)
} finally {
  // 只清理本次 runner 创建的唯一临时目录。
  await fs.rm(output, { recursive: true, force: true })
}
