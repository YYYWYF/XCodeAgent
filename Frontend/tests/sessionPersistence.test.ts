import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { writeSessionAtomically } from '../src/main/sessionPersistence'

const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'session-handoff-'))
const sessionFile = path.join(directory, 'original.json')
const content = '原会话交接消息'.repeat(100_000)
let saving = true
let reads = 0

try {
  await writeSessionAtomically(sessionFile, { id: 'original', revision: 0, content })
  // 模拟同一会话落交接消息时，阶段切换及会话列表同时读取；不启动第二个业务执行。
  await Promise.all([
    (async () => {
      try {
        for (let revision = 1; revision <= 20; revision += 1) {
          await writeSessionAtomically(sessionFile, { id: 'original', revision, content })
        }
      } finally { saving = false }
    })(),
    (async () => {
      do {
        const entries = (await fs.readdir(directory)).filter((entry) => entry.endsWith('.json'))
        assert.deepEqual(entries, ['original.json'])
        const record = JSON.parse(await fs.readFile(sessionFile, 'utf8'))
        assert.equal(record.id, 'original')
        assert.equal(record.content, content)
        reads += 1
      } while (saving)
    })()
  ])
  assert.ok(reads > 1)
  assert.equal(JSON.parse(await fs.readFile(sessionFile, 'utf8')).revision, 20)
  assert.deepEqual(await fs.readdir(directory), ['original.json'])
} finally {
  await fs.rm(directory, { recursive: true, force: true })
}
console.log(`session handoff atomic persistence passed (${reads} complete reads)`)
