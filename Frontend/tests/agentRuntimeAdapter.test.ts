import assert from 'node:assert/strict'
import { RuntimeAgentConversationAdapter } from '../../Backend/app/builtin_templates/agent-ui-frontend/src/apis/agentConversationRuntime'
import type { AgentUiTemplateConfig } from '../../Backend/app/builtin_templates/agent-ui-frontend/src/typings/agentConversation'

const config = { agentId: 'orders', name: '订单助手', actionId: 'ask_orders', contextItems: [],
  runtime: { path: '/agents/orders/run', pageId: 'orders_page' } } as unknown as AgentUiTemplateConfig

/** 构造由官方 SDK 实际解析的完整流，不以手工调用订阅回调代替传输验证。 */
function events(body: { runId: string; threadId: string }, status: 'completed' | 'awaiting_user', includeResult = true,
  kind: 'clarification' | 'confirmation' = 'clarification'): Response {
  const payload = { schemaVersion: 1, agentId: 'orders', runId: body.runId, threadId: body.threadId, status,
    pendingInteraction: status === 'awaiting_user' ? { schemaVersion: 1, interactionId: 'clarify-1',
      kind, message: '请补充订单编号', payload: { field: 'order_id' } } : null }
  const rows = [
    { type: 'RUN_STARTED', ...body },
    { type: 'TEXT_MESSAGE_START', messageId: `assistant-${body.runId}`, role: 'assistant' },
    { type: 'TEXT_MESSAGE_CONTENT', messageId: `assistant-${body.runId}`, delta: '真实响应' },
    { type: 'TEXT_MESSAGE_END', messageId: `assistant-${body.runId}` },
    ...(includeResult ? [{ type: 'CUSTOM', name: 'agent_runtime_result.v1', value: payload },
      { type: 'STATE_SNAPSHOT', snapshot: { agentRuntime: payload } }] : []),
    { type: 'RUN_FINISHED', ...body }
  ]
  return new Response(rows.map((row) => `data: ${JSON.stringify(row)}\n\n`).join(''),
    { headers: { 'Content-Type': 'text/event-stream' } })
}

/** 验证真实 SDK 的消息、终态、澄清恢复以及失败不重放。 */
async function main(): Promise<void> {
  const originalFetch = globalThis.fetch
  const requests: Record<string, any>[] = []
  try {
    globalThis.fetch = async (_url, init) => {
      const body = JSON.parse(String(init?.body))
      requests.push(body)
      return events(body, requests.length === 1 ? 'awaiting_user' : 'completed')
    }
    const adapter = new RuntimeAgentConversationAdapter(config)
    const thread = await adapter.createThread()
    const statuses: string[] = []
    adapter.subscribe((result) => { statuses.push(result.status) })
    const waiting = await adapter.sendMessage({ runId: 'run-one', threadId: thread.id,
      agentId: 'orders', actionId: 'ask_orders', content: '查询订单', contextItems: [] })
    assert.equal(waiting.status, 'waiting_input')
    assert.ok(waiting.messages.some((message) => message.role === 'assistant' && message.content === '真实响应'))
    assert.ok(statuses.includes('running'))
    assert.equal((await adapter.loadRun(thread.id)).pendingInteraction?.interactionId, 'clarify-1')
    await assert.rejects(adapter.resolveInteraction({ runId: waiting.runId, interactionId: 'clarify-1', value: true }), /澄清/)
    assert.equal(requests.length, 1)
    const completed = await adapter.resolveInteraction({ runId: waiting.runId, interactionId: 'clarify-1',
      value: 'ORDER-100', responseRunId: 'run-two' })
    assert.equal(completed.status, 'completed')
    assert.equal(requests[0].threadId, requests[1].threadId)
    assert.deepEqual(requests[1].forwardedProps, { pageId: 'orders_page', actionId: 'ask_orders', contextItems: [],
      interactionResponse: { interactionId: 'clarify-1', value: 'ORDER-100' } })
    assert.equal(requests[1].messages.filter((message: { role: string }) => message.role === 'user').length, 1)
    await assert.rejects(adapter.retry('run-two', 'run-three'), /不会自动重放/)
    assert.equal(requests.length, 2)

    let confirmationCalls = 0
    globalThis.fetch = async (_url, init) => {
      const body = JSON.parse(String(init?.body))
      confirmationCalls += 1
      if (confirmationCalls === 2) assert.equal(body.forwardedProps.interactionResponse.value, false)
      return events(body, confirmationCalls === 1 ? 'awaiting_user' : 'completed', true, 'confirmation')
    }
    const confirmation = new RuntimeAgentConversationAdapter(config)
    const confirmationThread = await confirmation.createThread()
    await confirmation.sendMessage({ runId: 'approval-one', threadId: confirmationThread.id,
      agentId: 'orders', actionId: 'ask_orders', content: '修改订单', contextItems: [] })
    await assert.rejects(confirmation.resolveInteraction({ runId: 'approval-one', interactionId: 'clarify-1', value: 'true' }), /明确选择/)
    await confirmation.resolveInteraction({ runId: 'approval-one', interactionId: 'clarify-1', value: false })
    assert.equal(confirmationCalls, 2)

    const interrupted = new RuntimeAgentConversationAdapter(config)
    const interruptedThread = await interrupted.createThread()
    globalThis.fetch = async (_url, init) => events(JSON.parse(String(init?.body)), 'awaiting_user')
    await interrupted.sendMessage({ runId: 'pending-first', threadId: interruptedThread.id,
      agentId: 'orders', actionId: 'ask_orders', content: '修改订单', contextItems: [] })
    globalThis.fetch = async () => { throw new Error('模拟网络中断') }
    const failed = await interrupted.resolveInteraction({ runId: 'pending-first', interactionId: 'clarify-1',
      value: 'ORDER-100', responseRunId: 'pending-failed' })
    assert.equal(failed.requiresSync, true)
    assert.equal(failed.pendingInteraction?.interactionId, 'clarify-1')
    await assert.rejects(interrupted.resolveInteraction({ runId: failed.runId, interactionId: 'clarify-1', value: 'ORDER-100' }), /同步/)
    let syncCalls = 0
    globalThis.fetch = async (_url, init) => {
      const body = JSON.parse(String(init?.body))
      syncCalls += 1
      assert.deepEqual(body.forwardedProps, { inspectInteraction: true })
      return events(body, 'awaiting_user')
    }
    const synced = await interrupted.refreshRun(interruptedThread.id, 'pending-sync')
    assert.equal(syncCalls, 1)
    assert.equal(synced.status, 'waiting_input')
    assert.equal(synced.requiresSync, false)
    assert.equal(synced.pendingInteraction?.interactionId, 'clarify-1')

    globalThis.fetch = async (_url, init) => events(JSON.parse(String(init?.body)), 'completed', false)
    const incomplete = new RuntimeAgentConversationAdapter(config)
    const next = await incomplete.createThread()
    const result = await incomplete.sendMessage({ runId: 'missing-result', threadId: next.id,
      agentId: 'orders', actionId: 'ask_orders', content: '查询', contextItems: [] })
    assert.equal(result.status, 'error', 'RUN_FINISHED 不能单独冒充业务完成')
    assert.throws(() => new RuntimeAgentConversationAdapter({ ...config,
      runtime: { ...config.runtime!, path: 'https://foreign.invalid/run' } }), /配置无效/)

    globalThis.fetch = async (_url, init) => {
      const body = JSON.parse(String(init?.body))
      const stream = new ReadableStream<Uint8Array>({ start(controller) {
        const encoder = new TextEncoder()
        for (const event of [{ type: 'RUN_STARTED', runId: body.runId, threadId: body.threadId },
          { type: 'TEXT_MESSAGE_START', messageId: 'streaming', role: 'assistant' },
          { type: 'TEXT_MESSAGE_CONTENT', messageId: 'streaming', delta: '尚未结束' }]) {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
        }
        init?.signal?.addEventListener('abort', () => controller.error(new DOMException('Aborted', 'AbortError')))
      } })
      return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })
    }
    const stopping = new RuntimeAgentConversationAdapter(config)
    const stoppingThread = await stopping.createThread()
    let streamed!: () => void
    const received = new Promise<void>((resolve) => { streamed = resolve })
    stopping.subscribe((result) => { if (result.messages.some((message) => message.content === '尚未结束')) streamed() })
    const running = stopping.sendMessage({ runId: 'stop-one', threadId: stoppingThread.id,
      agentId: 'orders', actionId: 'ask_orders', content: '查询', contextItems: [] })
    await received
    await stopping.stop('stop-one')
    assert.equal((await running).status, 'stopped')
    console.log('Runtime adapter: official SDK stream, interaction, identity, and no-replay checks passed')
  } finally { globalThis.fetch = originalFetch }
}

void main().catch((error) => { console.error(error); process.exitCode = 1 })
