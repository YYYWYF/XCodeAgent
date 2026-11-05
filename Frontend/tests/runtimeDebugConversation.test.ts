import assert from 'node:assert/strict'
import './runtimeDebugEndpoints.test'
import './debugJsonValidation.test'
import './debugKeyValue.test'
import { RuntimeDebugConversation } from '../src/renderer/src/service/runtimeDebugConversation'
import type { DebugConversation } from '../src/renderer/src/service/runtimeDebugConversation'

/** 使用真实 AG-UI SDK 和内存 SSE 响应验证交互身份、工具事件及断流不重放。 */
async function main(): Promise<void> {
  const original = globalThis.fetch
  let calls = 0
  let mode: 'pending' | 'completed' | 'broken' | 'oversized' | 'many-tools' = 'pending'
  let lastInput: Record<string, any> = {}
  let lastUrl = ''
  globalThis.fetch = async (_url, init) => {
    lastUrl = String(_url)
    calls += 1
    assert.equal(new Headers(init?.headers).get('authorization'), 'Bearer transient')
    const input = JSON.parse(String(init?.body)); lastInput = input
    if (mode === 'oversized') return new Response('data: '+ 'x'.repeat(600 * 1024),
      { headers: { 'Content-Type': 'text/event-stream' } })
    const result = { schemaVersion: 1, agentId: 'assistant', threadId: input.threadId, runId: input.runId,
      status: mode === 'pending' ? 'awaiting_user' : 'completed',
      ...(mode === 'pending' ? { pendingInteraction: { schemaVersion: 1, interactionId: 'approval-1',
        kind: 'confirmation', message: '确认写入？', payload: {} } } : {}) }
    const events = [
      { type: 'RUN_STARTED', threadId: input.threadId, runId: input.runId },
      { type: 'TEXT_MESSAGE_START', messageId: input.runId, role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: input.runId, delta: '测试回复' },
      { type: 'TEXT_MESSAGE_END', messageId: input.runId },
      { type: 'TOOL_CALL_START', toolCallId: input.runId, toolCallName: 'query' },
      { type: 'TOOL_CALL_ARGS', toolCallId: input.runId, delta: '{"limit":1}' },
      { type: 'TOOL_CALL_END', toolCallId: input.runId },
      { type: 'TOOL_CALL_RESULT', messageId: 'result-'+input.runId, toolCallId: input.runId, content: 'ok', role: 'tool' },
      ...(mode === 'broken' ? [] : [
        { type: 'CUSTOM', name: 'agent_runtime_result.v1', value: result },
        { type: 'STATE_SNAPSHOT', snapshot: { agentRuntime: result } },
        { type: 'RUN_FINISHED', threadId: input.threadId, runId: input.runId, result: { agentRuntime: result } }
      ])
    ]
    if (mode === 'many-tools') {
      const finishedIndex = events.findIndex((event) => event.type === 'CUSTOM')
      events.splice(finishedIndex, 0, ...Array.from({ length: 41 }, (_, index) => [
        { type: 'TOOL_CALL_START', toolCallId: `extra-${index}`, toolCallName: 'query' },
        { type: 'TOOL_CALL_ARGS', toolCallId: `extra-${index}`, delta: '{}' },
        { type: 'TOOL_CALL_END', toolCallId: `extra-${index}` },
        { type: 'TOOL_CALL_RESULT', toolCallId: `extra-${index}`, messageId: `result-${index}`, content: 'ok', role: 'tool' }
      ]).flat())
    }
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
      { headers: { 'Content-Type': 'text/event-stream' } })
  }
  let view: DebugConversation | undefined
  const client = new RuntimeDebugConversation({ url: 'http://127.0.0.1:32100', token: 'transient',
    launchId: 'launch', expiresAt: Date.now()/1000+600 }, 'assistant', (next) => { view = next })
  try {
    await client.send('查询')
    assert.equal('pageId' in lastInput.forwardedProps, false)
    assert.equal(view?.status, 'awaiting_user')
    assert.equal(view?.tools[0].result, 'ok')
    assert.equal(view?.pending?.interactionId, 'approval-1')
    await assert.rejects(client.send('不能绕过确认'), /处理当前交互/)
    assert.equal(calls, 1)
    mode = 'completed'
    await client.send('', {}, false)
    assert.deepEqual(lastInput.forwardedProps.interactionResponse, { interactionId: 'approval-1', value: false })
    assert.equal(view?.status, 'completed', view?.error)
    mode = 'broken'
    await client.send('断流测试')
    assert.equal(view?.status, 'unknown')
    await assert.rejects(client.send('禁止重试'), /不可继续/)
    assert.equal(calls, 3)
    // 切换或新建会话必须使用独立工具列表，不能继承旧会话执行详情。
    mode = 'completed'
    const fresh = new RuntimeDebugConversation({ url: 'http://127.0.0.1:32100', token: 'transient',
      launchId: 'launch', expiresAt: Date.now()/1000+600 }, 'assistant', (next) => { view = next })
    try {
      await fresh.send('新会话')
      assert.equal(view?.tools.length, 1)
      assert.equal(view?.status, 'completed', view?.error)
    } finally { fresh.dispose() }
    mode = 'many-tools'
    const many = new RuntimeDebugConversation({ url: 'http://127.0.0.1:32100', token: 'transient',
      launchId: 'launch', expiresAt: Date.now()/1000+600 }, 'assistant', (next) => { view = next })
    try {
      await many.send('多工具')
      assert.equal(view?.status, 'completed', view?.error)
      assert.equal(view?.tools.length, 40)
      assert.equal(view?.tools.at(-1)?.id, 'extra-40')
      // 工具详情可限量，但执行事件必须保留完整请求，包含首尾生命周期。
      assert.equal(view?.events?.length, 175)
      assert.equal(view?.events?.[0].type, 'RUN_STARTED')
      assert.equal(view?.events?.at(-1)?.type, 'RUN_FINISHED')
      mode = 'completed'
      await many.send('下一次请求')
      assert.equal(view?.events?.length, 11, '新请求不混入上一轮事件')
    } finally { many.dispose() }
    mode = 'completed'
    const selected = new RuntimeDebugConversation({ url: 'http://127.0.0.1:32100', token: 'transient',
      launchId: 'launch', expiresAt: Date.now()/1000+600 }, 'assistant', (next) => { view = next }, {
        endpoint: { apiContractId: 'business', endpointId: 'detect', label: '检测', kind: 'business', relation: 'agent_call', responseMode: 'ag-ui',
          method: 'POST', path: '/api/detect/{id}', parameters: [{ name: 'q', in: 'query' }], requestSchema: {}, responseSchema: {} },
        values: { pathParameters: { id: 'a b' }, query: { q: 'test' }, headers: { traceparent: 'demo' }, context: {} }
      })
    try {
      await selected.send('执行任务', {}, undefined, { text: '测试' })
      assert.equal(lastUrl, 'http://127.0.0.1:32100/api/detect/a%20b?q=test')
      assert.deepEqual(lastInput.forwardedProps.input, { text: '测试' })
      assert.equal(view?.status, 'completed', view?.error)
      assert.ok(view?.events?.some((event) => event.type === 'RUN_FINISHED'))
      assert.equal('Authorization' in (view?.request?.headers || {}), false)
      assert.equal(view?.http?.statusCode, 200)
    } finally { selected.dispose() }
    mode = 'oversized'
    const bounded = new RuntimeDebugConversation({ url: 'http://127.0.0.1:32100', token: 'transient',
      launchId: 'launch', expiresAt: Date.now()/1000+600 }, 'assistant', (next) => { view = next })
    try {
      await bounded.send('大响应')
      assert.equal(view?.status, 'unknown')
      assert.match(view?.error || '', /512 KB/)
      assert.equal(view?.tools.length, 0)
    } finally { bounded.dispose() }
    assert.throws(() => new RuntimeDebugConversation({ url: 'https://example.com', token: 'transient',
      launchId: 'launch', expiresAt: 9999999999 }, 'assistant', () => undefined), /本机/)
    console.log('Runtime 调试：交互拒绝续接、工具事件、断流防重放和地址边界通过')
  } finally { client.dispose(); globalThis.fetch = original }
}
void main().catch((error) => { console.error(error); process.exitCode = 1 })
