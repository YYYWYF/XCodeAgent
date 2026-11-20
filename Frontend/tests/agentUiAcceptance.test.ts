import assert from 'node:assert/strict'
import { test } from 'node:test'
import { runAgentUiAcceptance } from '../src/renderer/src/service/agentUiAcceptance'

/** 通过实际官方客户端消费测试 HTTP 流，验证身份、终态和不自动重发。 */
test('分层验收客户端只消费匹配身份的完整 AG-UI 结果', async () => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  Object.defineProperty(globalThis, 'window', {
    value: { devAgentStudio: { agentBaseUrl: 'http://localhost:8000' } },
    configurable: true
  })
  try {
    for (const mode of ['complete', 'missing_terminal', 'foreign', 'failed'] as const) {
      let calls = 0
      globalThis.fetch = async (_input, init): Promise<Response> => {
        calls += 1
        const input = JSON.parse(String(init?.body)) as {
          threadId: string
          runId: string
          forwardedProps: unknown
        }
        assert.deepEqual(input.forwardedProps, {
          runtimeAcceptance: { action: 'get', workspace: '/formal-target' }
        })
        const value = {
          threadId: mode === 'foreign' ? 'other-thread' : input.threadId,
          runId: input.runId,
          status: mode === 'failed' ? 'failed' : 'completed',
          matrix: { cases: [], ready: true },
          error: { message: '来源已变化' }
        }
        const events = [
          { type: 'RUN_STARTED', threadId: input.threadId, runId: input.runId },
          { type: 'CUSTOM', name: 'runtime-acceptance', value },
          { type: 'STATE_SNAPSHOT', snapshot: { runtimeAcceptance: value } },
          ...(mode === 'missing_terminal'
            ? []
            : [
                {
                  type: 'RUN_FINISHED',
                  threadId: input.threadId,
                  runId: input.runId,
                  result: { runtimeAcceptance: value }
                }
              ])
        ]
        return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
          headers: { 'Content-Type': 'text/event-stream' }
        })
      }
      if (mode === 'complete')
        assert.equal((await runAgentUiAcceptance('/formal-target')).ready, true)
      else await assert.rejects(runAgentUiAcceptance('/formal-target'))
      assert.equal(calls, 1, '失败/断流不自动重发')
    }
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', { value: originalWindow, configurable: true })
  }
})
