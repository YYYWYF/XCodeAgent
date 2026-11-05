import { build } from 'esbuild'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const temporary = await mkdtemp(resolve(tmpdir(), 'runtime-debug-'))
try {
  const output = resolve(temporary, 'test.cjs')
  await build({ entryPoints: [resolve(root, 'tests/runtimeDebugConversation.test.ts')], outfile: output,
    bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', loader: { '.less': 'empty' }, logLevel: 'silent' })
  // 禁止动态代码生成，复现 Electron 不允许 unsafe-eval 的关键约束。
  execFileSync(process.execPath, ['--disallow-code-generation-from-strings', output], { stdio: 'inherit', timeout: 30000 })
} finally { await rm(temporary, { recursive: true, force: true }) }
