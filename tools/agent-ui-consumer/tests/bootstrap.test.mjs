import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mkdtemp, mkdir, writeFile, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { initializeConsumer } from '../../agent-ui/prepare-consumer.mjs'

/** 确认初始化失败不会覆盖目标工程中已经存在的应用层。 */
test('refuses an existing application layer without overwriting it', async () => {
  const root = await mkdtemp(join(tmpdir(), 'agent-ui-preserve-'))
  await mkdir(join(root, 'src/agent-ui'), { recursive: true })
  await writeFile(join(root, 'src/agent-ui/Agent.tsx'), 'user-owned')
  await assert.rejects(initializeConsumer(root), /already exists/)
  assert.equal(await readFile(join(root, 'src/agent-ui/Agent.tsx'), 'utf8'), 'user-owned')
})
