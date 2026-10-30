import assert from 'node:assert/strict'
import { test } from 'node:test'
import { requestEndpointPreparation, requestEndpointDesignPreparation } from '../src/renderer/src/service/endpointDesigns'
import { isRuntimeEndpointPreparation } from '../src/renderer/src/typings/endpointDesign'

/** 通过真实官方 SDK 读取 Runtime 判别联合，业务读取不得继续进入来源映射。 */
test('formal runtime preparation is routed independently of the directory snapshot', async () => {
  const oldFetch = globalThis.fetch
  const oldWindow = globalThis.window
  const requests: Array<{ url: string; input: Record<string, unknown> }> = []
  Object.defineProperty(globalThis, 'window', { configurable: true,
    value: { devAgentStudio: { agentBaseUrl: 'http://routing.test' } } })
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(String(init?.body))
    requests.push({ url: String(url), input: body.forwardedProps.endpointDesigns })
    const preparation = { apiContractId: 'agent.assistant', endpointId: 'agent.assistant.run',
      technicalPlanHash: 'a'.repeat(64), payload: { implementationKind: 'agent_runtime',
      endpoint: { kind: 'agent_runtime', agent_id: 'assistant' },
      draft: { apiContractId: 'agent.assistant', endpointId: 'agent.assistant.run', implementationKind: 'agent_runtime',
        sourceBinding: { sourceType: 'agent', agentId: 'assistant' }, inputBindings: [],
        invocation: { method: 'POST', path: '/agents/assistant/run' }, interactionContract: {}, capabilitySha256: 'b'.repeat(64) } } }
    const value = { schemaVersion: 1, runId: body.runId, threadId: body.threadId,
      action: 'prepare', status: 'completed', preparation }
    const events = [{ type: 'RUN_STARTED', runId: body.runId, threadId: body.threadId },
      { type: 'TEXT_MESSAGE_START', messageId: body.runId, role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: body.runId, delta: '已读取接入设计。' },
      { type: 'TEXT_MESSAGE_END', messageId: body.runId },
      { type: 'CUSTOM', name: 'endpoint-designs', value },
      { type: 'STATE_SNAPSHOT', snapshot: { endpointDesigns: value } },
      { type: 'RUN_FINISHED', runId: body.runId, threadId: body.threadId, result: { endpointDesigns: value } }]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
      { headers: { 'Content-Type': 'text/event-stream' } })
  }
  try {
    const preparation = await requestEndpointPreparation('/workspace', 'agent.assistant', 'agent.assistant.run')
    assert.equal(isRuntimeEndpointPreparation(preparation), true)
    await assert.rejects(requestEndpointDesignPreparation('/workspace', 'agent.assistant', 'agent.assistant.run'), /不能配置业务字段映射/)
    assert.equal(requests.length, 2)
    assert.ok(requests.every((request) => request.url.endsWith('/endpoint-designs/run') && request.input.action === 'prepare'))
  } finally {
    globalThis.fetch = oldFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: oldWindow })
  }
})
