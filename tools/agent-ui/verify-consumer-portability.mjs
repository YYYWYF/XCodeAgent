import assert from 'node:assert/strict'
import { cp, mkdtemp, readFile, mkdir, writeFile, access } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import { initializeConsumer } from './prepare-consumer.mjs'

const repository = fileURLToPath(new URL('../..', import.meta.url))
const pnpmCli = process.env.npm_execpath ?? process.argv[2]
assert.ok(pnpmCli && /\.[cm]?js$/.test(pnpmCli), 'Run through pnpm check:consumer, or pass the pnpm JavaScript CLI path.')
const root = await mkdtemp(join(tmpdir(), 'Agent UI 消费验证 '))
const target = join(root, 'tools/agent-ui-consumer')
await mkdir(target, { recursive: true })
await cp(join(repository, 'vendor/agent-ui'), join(root, 'vendor/agent-ui'), { recursive: true })
for (const file of ['package.json', 'pnpm-lock.yaml', '.pnpmfile.cjs', 'tsconfig.json', 'vite.config.ts', 'index.html']) {
  await cp(join(repository, 'tools/agent-ui-consumer', file), join(target, file))
}
await mkdir(join(target, 'src'))
for (const file of ['App.tsx', 'main.tsx', 'styles.css']) {
  await cp(join(repository, 'tools/agent-ui-consumer/src', file), join(target, 'src', file))
}

/** 使用消费者自己的 packageManager 执行检查，完整输出留在隔离目录供审阅。 */
function run(args, name) {
  const result = spawnSync(process.execPath, [pnpmCli, ...args], {
    cwd: target, encoding: 'utf8', timeout: 180_000, shell: false,
    env: { ...process.env, CI: 'true' },
  })
  if (result.error) throw result.error
  return writeFile(join(root, `${name}.log`), result.stdout + result.stderr).then(() => {
    assert.equal(result.status, 0, `${name} failed; see ${join(root, `${name}.log`)}`)
  })
}

await run(['install', '--frozen-lockfile', '--offline', '--ignore-scripts', '--registry=https://registry.npmjs.org'], 'install')
const initialized = await initializeConsumer(target)
// SDK 从 X 工具环境调用一次；隔离消费者内从未安装 Creator 或复制工具 node_modules。
await assert.rejects(access(join(root, 'tools/agent-ui')))
await assert.rejects(access(join(target, 'node_modules/@za38.03/agent-ui-creator')))
await run(['typecheck'], 'typecheck')
await run(['build'], 'build')
const lock = await readFile(join(target, 'pnpm-lock.yaml'), 'utf8')
assert.doesNotMatch(lock, /AgentUICreator|workspace:|link:|\/Users\//)
const manifest = JSON.parse(await readFile(join(target, 'package.json'), 'utf8'))
assert.ok(!Object.keys(manifest.dependencies).some((name) => name.includes('creator')))
console.log(JSON.stringify({ root: resolve(root), initializedFiles: initialized.createdPaths.length, install: 'passed', typecheck: 'passed', build: 'passed', creatorInstalled: false }, null, 2))
