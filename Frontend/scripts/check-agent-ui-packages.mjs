import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = fileURLToPath(new URL('../', import.meta.url))
const manifest = JSON.parse(readFileSync(path.join(frontendRoot, 'package.json'), 'utf8'))
const packageNames = ['core', 'creator', 'plugins', 'renderer', 'runtime', 'web-component']
const packages = new Map()

// 只读取归档目录及包元数据，确认本地发布包完整且所有导出文件确实存在。
function readPackage(archive) {
  const entries = new Set(execFileSync('tar', ['-tzf', archive], { encoding: 'utf8' }).trim().split('\n'))
  const metadata = JSON.parse(execFileSync('tar', ['-xOf', archive, 'package/package.json'], { encoding: 'utf8' }))
  checkExports(metadata.exports, entries, metadata.name)
  return metadata
}

// 递归检查条件导出，通配符导出由归档内匹配的文件集合验证。
function checkExports(exports, entries, name) {
  if (typeof exports === 'string') {
    const target = `package/${exports.replace(/^\.\//, '')}`
    const [prefix, suffix] = target.split('*')
    const exists = target.includes('*')
      ? [...entries].some((entry) => entry.startsWith(prefix) && entry.endsWith(suffix))
      : entries.has(target)
    if (!exists) throw new Error(`${name} 缺少导出文件 ${exports}`)
    return
  }
  for (const value of Object.values(exports || {})) checkExports(value, entries, name)
}

for (const suffix of packageNames) {
  const name = `@za38.03/agent-ui-${suffix}`
  const reference = manifest.dependencies[name]
  if (!reference?.startsWith('file:../vendor/agent-ui/')) throw new Error(`${name} 必须引用本地 tgz`)
  if (manifest.pnpm?.overrides?.[name] !== reference) throw new Error(`${name} 缺少一致的本地依赖覆盖`)
  const metadata = readPackage(path.resolve(frontendRoot, reference.slice(5)))
  if (metadata.name !== name) throw new Error(`${name} 与归档包名 ${metadata.name} 不一致`)
  packages.set(name, metadata)
}

for (const [name, metadata] of packages) {
  for (const [dependency, version] of Object.entries({ ...metadata.dependencies, ...metadata.peerDependencies })) {
    if (dependency.startsWith('@za38.03/agent-ui-') && packages.get(dependency)?.version !== version) {
      throw new Error(`${name} 要求 ${dependency}@${version}，本地包版本不匹配`)
    }
  }
  console.log(`通过 ${name}@${metadata.version}`)
}
console.log('六个 Agent UI 本地包及导出文件检查通过。')
