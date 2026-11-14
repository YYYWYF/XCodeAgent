import { build } from 'vite'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const temporary = await mkdtemp(resolve(tmpdir(), 'runtime-debug-'))
try {
  const output = resolve(temporary, 'test.mjs')
  // 复用项目已声明的 Vite，避免依赖 pnpm 未向工程暴露的传递依赖 esbuild。
  await build({ configFile: false, root, logLevel: 'error', ssr: { noExternal: true },
    esbuild: { jsx: 'automatic' },
    css: { preprocessorOptions: { less: { additionalData: '@class-prefix: devagentstudio;',
      javascriptEnabled: true, modifyVars: { '@root-entry-name': 'default' } } } },
    build: { outDir: temporary, minify: false, ssr: resolve(root, 'tests/runtimeDebugConversation.test.ts'),
      rollupOptions: { output: { entryFileNames: 'test.mjs' } } } })
  // 禁止动态代码生成，复现 Electron 不允许 unsafe-eval 的关键约束。
  execFileSync(process.execPath, ['--disallow-code-generation-from-strings', output], { stdio: 'inherit', timeout: 30000 })
} finally { await rm(temporary, { recursive: true, force: true }) }
