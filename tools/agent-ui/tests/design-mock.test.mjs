import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { test } from 'node:test'
import * as api from '@za38.03/agent-ui-creator/mock-agent'
import { loadDesignMock } from '../../../Backend/app/services/agent_ui_design_mock.mjs'

const installationRoot = resolve(import.meta.dirname, '..')
const metadata = await readFile(resolve(installationRoot, 'node_modules/@za38.03/agent-ui-creator/package.json'))
const binding = { pageId: 'chat', revision: 'a'.repeat(64), sourceDigest: 'b'.repeat(64), buildDigest: 'c'.repeat(64) }
const options = { installationRoot, creatorVersion: JSON.parse(metadata).version,
  packageJsonSha256: createHash('sha256').update(metadata).digest('hex'), binding, scenarioId: 'simple-chat' }

/** 场景目录来自安装包；只记录公开能力，拒绝未知场景和陈旧工具身份。 */
test('official catalog and exact tool identity determine Mock evidence', async () => {
  const mock = await loadDesignMock(options, async () => api)
  assert.equal(mock.evidence.source, 'official-mock')
  assert.equal(mock.evidence.scenarioId, 'simple-chat')
  assert.deepEqual(mock.evidence.binding, binding)
  assert.ok(mock.evidence.catalog.some((item) => item.id === 'cancel-before-first-output'))
  assert.equal(mock.evidence.recordings, 'validated-offline-replay')
  await assert.rejects(loadDesignMock({ ...options, scenarioId: 'invented' }, async () => api))
  let imported = false
  await assert.rejects(loadDesignMock({ ...options, packageJsonSha256: '0'.repeat(64) }, async () => {
    imported = true
    return api
  }))
  assert.equal(imported, false, 'Stale metadata must fail before loading the tool')
})

/** 官方 JSONL 解析器先校验数据；本地来源绑定和 run/thread 校验再拒绝串稿录制。 */
test('recording import is bounded and rejects malformed or mismatched identities', async () => {
  const mock = await loadDesignMock(options, async () => api)
  const identity = { ...binding, id: 'local-fixture', threadId: 'recorded-thread', runId: 'recorded-run' }
  const text = [
    { type: 'RUN_STARTED', threadId: identity.threadId, runId: identity.runId },
    { type: 'TEXT_MESSAGE_START', messageId: 'message', role: 'assistant' },
    { type: 'TEXT_MESSAGE_CONTENT', messageId: 'message', delta: '录制的模拟文本' },
    { type: 'TEXT_MESSAGE_END', messageId: 'message' },
    { type: 'RUN_FINISHED', threadId: identity.threadId, runId: identity.runId },
  ].map((event) => JSON.stringify(event)).join('\n')
  const recording = mock.parseRecording(text, identity)
  assert.equal(recording.source, 'local-recording')
  const input = { threadId: 'current-thread', runId: 'current-run', messages: [], tools: [], context: [], state: {} }
  const events = []
  for await (const event of mock.runRecording(input, recording, { timingScale: 0 })) events.push(event)
  assert.equal(events[0].threadId, input.threadId)
  assert.equal(events.at(-1).runId, input.runId)
  for (const [value, provenance] of [
    ['{broken', identity], [text, { ...identity, revision: 'd'.repeat(64) }],
    [text, { ...identity, threadId: 'wrong-thread' }], [text, { ...identity, pageId: 'home' }],
    [text, { ...identity, runId: 'wrong-run' }], [text, { ...identity, sourceDigest: '0'.repeat(64) }],
    [text, { ...identity, buildDigest: '0'.repeat(64) }],
    [' '.repeat(2 * 1024 * 1024 + 1), identity],
    [text + '\n' + JSON.stringify({ type: 'RUN_STARTED', threadId: 'other', runId: 'other' }), identity],
  ]) assert.throws(() => mock.parseRecording(value, provenance))
})
