import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { loadPreviewMockApi, sanitizeRecording, createReplayHandler } from '../../../Backend/app/services/agent_ui_preview_replay.mjs'

const api = await loadPreviewMockApi({ installationRoot: new URL('..', import.meta.url).pathname })
const { HttpAgent } = api
const recording = [
  { type: 'RUN_STARTED', threadId: 'recorded', runId: 'recorded-run' },
  { type: 'TEXT_MESSAGE_START', messageId: 'message', role: 'assistant' },
  { type: 'TEXT_MESSAGE_CONTENT', messageId: 'message', delta: 'Bearer private-token' },
  { type: 'TEXT_MESSAGE_END', messageId: 'message' },
  { type: 'RUN_FINISHED', threadId: 'recorded', runId: 'recorded-run' }
].map(event => JSON.stringify(event)).join('\n')

test('official recording validation rejects malformed data and sanitizes before storage', () => {
  const safe = sanitizeRecording(api, recording)
  assert.doesNotMatch(safe.text, /private-token|Bearer|recorded-run/)
  assert.equal(safe.eventCount, 5)
  assert.throws(() => sanitizeRecording(api, '{broken'))
  assert.throws(() => sanitizeRecording(api, recording + '\n' + JSON.stringify({ type: 'RUN_STARTED', threadId: 'other', runId: 'other' })))
})

test('manual replay fixture uses the public timed JSONL format and excludes arbitrary application events', async () => {
  const fixture = await readFile(new URL('../../agent-ui-consumer/fixtures/t28-replay.jsonl', import.meta.url), 'utf8')
  const safe = sanitizeRecording(api, fixture)
  assert.equal(api.parseMockRecording(safe.text, { id: 'manual' }).events.at(-1).atMs, 5200)
  const injected = recording.replace('"TEXT_MESSAGE_END"', '"CUSTOM","name":"credentials","value":{"token":"private-token"}')
  assert.throws(() => sanitizeRecording(api, injected))
})

test('replay uses official runner and encoder with the canonical HTTP client', async () => {
  const safe = sanitizeRecording(api, recording)
  const handler = await createReplayHandler(api, safe.text)
  const server = createServer((request, response) => { void handler(request, response) })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const agent = new HttpAgent({ url: `http://127.0.0.1:${server.address().port}/`, threadId: 'fresh-thread' })
    const types = []
    await agent.runAgent({}, { onRunStartedEvent: ({ event }) => types.push(event.type),
      onRunFinishedEvent: ({ event }) => types.push(event.type) })
    assert.deepEqual(types, ['RUN_STARTED', 'RUN_FINISHED'])
    assert.equal(agent.messages[0].content, '［录制正文已隐藏］')
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)) }
})
