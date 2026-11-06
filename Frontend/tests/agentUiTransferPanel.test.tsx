import assert from 'node:assert/strict'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import AgentUiTransfer from '../src/renderer/src/components/AgentUiTransfer'
import { transferMessages } from '../src/renderer/src/components/AgentUiTransfer/messages'

const t = transferMessages()
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
const originalFetch = globalThis.fetch
const calls: Array<{
  workspace: string
  action: string
  planId?: string
  targetSourceRoot?: string
}> = []
let initialized = false
let failConfirm = false
let delayAction: string | undefined
let delayedSignal: AbortSignal | null | undefined
let release: (() => void) | undefined
let root: ReactTestRenderer
Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: { devAgentStudio: { agentBaseUrl: 'http://backend.test' } }
})

/** 使用官方 SDK 消费真实标准帧；替代的仅为网络来源和 Antd DOM。 */
globalThis.fetch = async (_url, init): Promise<Response> => {
  const body = JSON.parse(String(init?.body))
  const input = body.forwardedProps.agentUiDesignTransfer
  calls.push(input)
  if (failConfirm && input.action === 'confirm') throw new Error('connection outcome unknown')
  if (delayAction === input.action) {
    delayedSignal = init?.signal
    await new Promise<void>((resolve) => {
      release = resolve
    })
  }
  if (input.action === 'initialize') initialized = true
  const value = {
    threadId: body.threadId,
    runId: body.runId,
    status: 'completed',
    ...(input.action === 'plan'
      ? {
          plan: {
            planId: (initialized ? 'c' : 'b').repeat(64),
            readOnly: true,
            executionAvailable: true,
            status: 'reviewable',
            hostIntegration: {
              status: 'reviewable',
              reason: '真实 Runtime 映射待接入，发送保持禁用。',
              connection: 'blocked_T27'
            },
            source: { revision: 'a'.repeat(64) },
            target: { sourceRoot: 'src/正式助手' },
            installation: { planId: 'a'.repeat(64) },
            blockers: [],
            conflicts: [],
            excluded: [],
            validation: [],
            changes: [],
            recovery: 'preserve files',
            initialization: { required: !initialized }
          }
        }
      : {
          transfer: {
            planId: input.planId,
            installationPlanId: 'a'.repeat(64),
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
  return new Response(
    [
      { type: 'RUN_STARTED', threadId: body.threadId, runId: body.runId },
      { type: 'CUSTOM', name: 'agent-ui-design-transfer', value },
      { type: 'STATE_SNAPSHOT', snapshot: { agentUiDesignTransfer: value } },
      {
        type: 'RUN_FINISHED',
        threadId: body.threadId,
        runId: body.runId,
        result: { agentUiDesignTransfer: value }
      }
    ]
      .map((event) => `data: ${JSON.stringify(event)}\n\n`)
      .join(''),
    {
      headers: { 'Content-Type': 'text/event-stream' }
    }
  )
}

/** 通过用户可见文字选择按钮，不依赖组件内部状态或 hook 顺序。 */
function button(label: string): ReturnType<ReactTestRenderer['root']['findByType']> {
  const found = root.root.findAllByType('button').find((item) => item.props.children === label)
  assert.ok(found, `missing button: ${label}`)
  return found
}

/** 等待官方客户端异步完成订阅后再观察 React 投影。 */
async function click(label: string): Promise<void> {
  const selected = button(label)
  assert.ok(!selected.props.disabled, `button disabled: ${label}`)
  await act(async () => {
    selected.props.onClick()
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
}

/** 用户勾选只放行当前计划，重新读取与执行后均需再次审阅。 */
function review(): void {
  act(() => root.root.findByType('input').props.onChange({ target: { checked: true } }))
}

try {
  act(() => {
    root = create(createElement(AgentUiTransfer, { workspaceRoot: '/workspace/a', key: 'a' }))
  })
  await click(t.open)
  await click(t.inspect)
  assert.match(JSON.stringify(root.toJSON()), /真实 Runtime 映射待接入，发送保持禁用/)
  assert.deepEqual(
    calls.map((call) => call.action),
    ['plan']
  )
  assert.equal(button(t.initialize).props.disabled, true)
  review()
  await click(t.initialize)
  assert.deepEqual(
    calls.map((call) => call.action),
    ['plan', 'initialize']
  )
  assert.equal(calls[1].targetSourceRoot, 'src/正式助手')
  assert.equal(
    root.root.findAllByType('input').length,
    0,
    'initialization must require a fresh plan'
  )
  await click(t.inspect)
  assert.equal(button(t.transfer).props.disabled, true)
  review()
  failConfirm = true
  const before = calls.length
  await click(t.transfer)
  assert.equal(calls.length, before + 1, 'connection failure must not replay a write')
  failConfirm = false
  await click(t.query)
  assert.equal(calls.at(-1)?.action, 'get')
  assert.equal(calls.at(-1)?.planId, 'c'.repeat(64))
  await click(t.inspect)
  review()
  delayAction = 'confirm'
  await click(t.transfer)
  await click(t.stop)
  assert.equal(
    delayedSignal?.aborted,
    true,
    'stop must cancel the original request even before a record exists'
  )
  assert.equal(calls.at(-1)?.action, 'cancel')
  await act(async () => {
    release?.()
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  delayAction = 'plan'
  await click(t.inspect)
  assert.ok(release)
  act(() =>
    root.update(createElement(AgentUiTransfer, { workspaceRoot: '/workspace/b', key: 'b' }))
  )
  await act(async () => {
    release?.()
    await new Promise((resolve) => setTimeout(resolve, 20))
  })
  await click(t.open)
  assert.equal(
    root.root.findAllByType('input').length,
    0,
    'late old-workspace response must be discarded'
  )
  assert.ok(!root.root.findAllByType('button').some((item) => item.props.children === t.query))
  console.log(
    'Agent UI transfer panel tests passed (review, replan, no replay, query, workspace switch).'
  )
} finally {
  act(() => root?.unmount())
  globalThis.fetch = originalFetch
  if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
  else Reflect.deleteProperty(globalThis, 'window')
}
