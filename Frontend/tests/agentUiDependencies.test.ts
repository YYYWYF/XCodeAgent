import assert from 'node:assert/strict'
import { runAgentUiDependencies } from '../src/renderer/src/service/agentUiDependencies'

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
const originalFetch = globalThis.fetch
const planId = 'a'.repeat(64)
let calls = 0
let scenario: 'verified' | 'failed' | 'missing' | 'wrong-plan' | 'network' = 'verified'
Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: { devAgentStudio: { agentBaseUrl: 'http://backend.test' } }
})

/** 提供标准 AG-UI 测试帧，生产服务只通过官方 SDK 消费。 */
globalThis.fetch = async (_url, init): Promise<Response> => {
  calls += 1
  if (scenario === 'network') throw new Error('connection lost')
  const body = JSON.parse(String(init?.body))
  assert.equal(body.forwardedProps.agentUiDependencies.action, 'confirm')
  assert.equal(body.forwardedProps.agentUiDependencies.planId, planId)
  const value = {
    threadId: body.threadId,
    runId: body.runId,
    status: scenario === 'failed' ? 'failed' : 'completed',
    installation: {
      planId: scenario === 'wrong-plan' ? 'b'.repeat(64) : planId,
      threadId: body.threadId,
      runId: body.runId,
      state: scenario === 'failed' ? 'failed' : 'verified'
    }
  }
  const events: object[] = [
    { type: 'RUN_STARTED', threadId: body.threadId, runId: body.runId },
    { type: 'TEXT_MESSAGE_START', messageId: 'message', role: 'assistant' },
    { type: 'TEXT_MESSAGE_CONTENT', messageId: 'message', delta: '安装检查' },
    { type: 'TEXT_MESSAGE_END', messageId: 'message' }
  ]
  if (scenario !== 'missing') {
    events.push(
      { type: 'CUSTOM', name: 'agent-ui-dependencies', value },
      { type: 'STATE_SNAPSHOT', snapshot: { agentUiDependencies: value } }
    )
  }
  events.push({
    type: 'RUN_FINISHED',
    threadId: body.threadId,
    runId: body.runId,
    ...(scenario === 'missing' ? {} : { result: { agentUiDependencies: value } })
  })
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'Content-Type': 'text/event-stream' }
  })
}

/** 同一确认仅发送一次；未知结果由读取原计划处理，不隐式重发。 */
async function request(): ReturnType<typeof runAgentUiDependencies> {
  return runAgentUiDependencies(
    { workspace: '/workspace/test', action: 'confirm', planId, reviewId: 'c'.repeat(32) },
    { threadId: 'test-thread' }
  )
}

try {
  assert.equal((await request()).installation?.state, 'verified')
  scenario = 'failed'
  assert.equal((await request()).status, 'failed')
  scenario = 'missing'
  await assert.rejects(request(), /缺少终态证据/)
  scenario = 'wrong-plan'
  await assert.rejects(request(), /缺少终态证据/)
  scenario = 'network'
  await assert.rejects(request())
  assert.equal(calls, 5)
  console.log('Agent UI dependency SDK tests passed (5 scenarios).')
} finally {
  globalThis.fetch = originalFetch
  if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
  else Reflect.deleteProperty(globalThis, 'window')
}
