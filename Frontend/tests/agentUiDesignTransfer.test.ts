import assert from 'node:assert/strict'
import {
  runAgentUiDesignTransfer,
  type AgentUiTransferInput
} from '../src/renderer/src/service/agentUiDesignTransfer'

const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
const originalFetch = globalThis.fetch
const installationPlanId = 'a'.repeat(64)
let calls = 0
let scenario:
  | 'ready'
  | 'blocked'
  | 'failed'
  | 'missing'
  | 'wrong-run'
  | 'wrong-installation'
  | 'wrong-plan'
  | 'no-finish'
  | 'network' = 'ready'
Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: { devAgentStudio: { agentBaseUrl: 'http://backend.test' } }
})

/** 只为官方 SDK 提供标准帧，验证业务身份与终态；产品不自行解析 SSE。 */
globalThis.fetch = async (_url, init): Promise<Response> => {
  calls += 1
  if (scenario === 'network') throw new Error('connection lost')
  const body = JSON.parse(String(init?.body))
  const input = body.forwardedProps.agentUiDesignTransfer as AgentUiTransferInput
  const value = {
    threadId: body.threadId,
    runId: scenario === 'wrong-run' ? 'old-run' : body.runId,
    status: scenario === 'failed' ? 'failed' : 'completed',
    ...(scenario === 'failed'
      ? { error: { message: '设计尚未确认' } }
      : input.action === 'plan'
        ? {
            plan: {
              planId: 'b'.repeat(64),
              readOnly: true,
              executionAvailable: true,
              status: scenario === 'blocked' ? 'blocked' : 'reviewable',
              target: { sourceRoot: 'src/助手' },
              installation: {
                planId: scenario === 'wrong-installation' ? 'c'.repeat(64) : installationPlanId
              }
            }
          }
        : {
            transfer: {
              planId: scenario === 'wrong-plan' ? 'd'.repeat(64) : input.planId,
              installationPlanId:
                scenario === 'wrong-installation' ? 'c'.repeat(64) : installationPlanId,
              action: input.action === 'initialize' ? 'initialize' : 'confirm',
              state:
                input.action === 'cancel'
                  ? 'stopping'
                  : input.action === 'initialize'
                    ? 'initialized'
                    : 'verified'
            }
          })
  }
  const events: object[] = [
    { type: 'RUN_STARTED', threadId: body.threadId, runId: body.runId },
    { type: 'TEXT_MESSAGE_START', messageId: 'message', role: 'assistant' },
    { type: 'TEXT_MESSAGE_CONTENT', messageId: 'message', delta: '只读差异计划' },
    { type: 'TEXT_MESSAGE_END', messageId: 'message' }
  ]
  if (scenario !== 'missing')
    events.push(
      { type: 'CUSTOM', name: 'agent-ui-design-transfer', value },
      { type: 'STATE_SNAPSHOT', snapshot: { agentUiDesignTransfer: value } }
    )
  if (scenario !== 'no-finish')
    events.push({
      type: 'RUN_FINISHED',
      threadId: body.threadId,
      runId: body.runId,
      ...(scenario === 'missing' ? {} : { result: { agentUiDesignTransfer: value } })
    })
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'Content-Type': 'text/event-stream' }
  })
}

/** 一次请求只生成一份计划，断流或切换时不自动重放。 */
async function request(
  signal?: AbortSignal,
  action: AgentUiTransferInput['action'] = 'plan',
  targetSourceRoot: string | undefined = 'src/助手'
): ReturnType<typeof runAgentUiDesignTransfer> {
  return runAgentUiDesignTransfer(
    {
      workspace: '/workspace/test',
      action,
      installationPlanId,
      targetSourceRoot,
      ...(action === 'plan' ? {} : { planId: 'b'.repeat(64) })
    },
    { threadId: 'transfer-thread', signal }
  )
}

try {
  assert.equal((await request()).plan?.status, 'reviewable')
  scenario = 'blocked'
  assert.equal((await request()).plan?.status, 'blocked')
  scenario = 'failed'
  assert.equal((await request()).error?.message, '设计尚未确认')
  for (scenario of ['missing', 'wrong-run', 'wrong-installation'] as const) {
    await assert.rejects(request(), /缺少当前计划终态/)
  }
  scenario = 'network'
  await assert.rejects(request())
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(request(controller.signal), /已取消/)
  assert.equal(calls, 7)
  scenario = 'ready'
  assert.equal((await request(undefined, 'plan', undefined)).plan?.target.sourceRoot, 'src/助手')
  assert.equal((await request(undefined, 'initialize')).transfer?.state, 'initialized')
  assert.equal((await request(undefined, 'confirm')).transfer?.state, 'verified')
  assert.equal((await request(undefined, 'get')).transfer?.state, 'verified')
  assert.equal((await request(undefined, 'cancel')).transfer?.state, 'stopping')
  for (scenario of ['wrong-plan', 'wrong-run', 'wrong-installation', 'no-finish'] as const) {
    await assert.rejects(request(undefined, 'confirm'), /缺少当前计划终态/)
  }
  scenario = 'network'
  const before = calls
  await assert.rejects(request(undefined, 'confirm'))
  assert.equal(calls, before + 1, 'failed writes must never be replayed')
  console.log('Agent UI transfer SDK tests passed (18 scenarios).')
} finally {
  globalThis.fetch = originalFetch
  if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
  else Reflect.deleteProperty(globalThis, 'window')
}
