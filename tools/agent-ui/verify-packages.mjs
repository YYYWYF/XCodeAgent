import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = fileURLToPath(new URL('../../vendor/agent-ui/', import.meta.url))
const manifest = JSON.parse(await readFile(path.join(root, 'manifest.json'), 'utf8'))
const names = new Set(['core', 'renderer', 'runtime', 'plugins', 'web-component', 'creator']
  .map(name => `@za38.03/agent-ui-${name}`))
if (!Array.isArray(manifest.packages) || manifest.packages.length !== names.size
  || new Set(manifest.packages.map(item => item.name)).size !== names.size
  || manifest.packages.some(item => !names.has(item.name))) throw new Error('Invalid six-package manifest')
for (const item of manifest.packages) {
  if (path.basename(item.file) !== item.file || !item.file.endsWith('.tgz')) throw new Error('Invalid package path')
  const content = await readFile(path.join(root, item.file))
  const digest = createHash('sha256').update(content).digest('hex')
  if (content.length !== item.bytes || digest !== item.sha256) throw new Error(`Package integrity mismatch: ${item.name}`)
}
console.log(`Verified ${manifest.packages.length} local Agent UI tarballs from ${manifest.commit}`)
