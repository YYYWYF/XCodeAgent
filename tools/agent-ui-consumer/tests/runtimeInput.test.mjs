import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'node:http'
import { setTimeout } from 'node:timers/promises'
import { mountSharedConsumer, until, click } from './sessionHarness.mjs'

const inputSchema = { type: 'object', properties: {
  orderNumber: { type: 'string', title: '订单编号', description: '需要修改的订单编号。' },
  targetStatus: { type: 'string', title: '目标状态', enum: ['已确认', '已取消'] },
  newRemark: { type: 'string', title: '新备注' },
  count: { type: 'integer', title: '数量', enum: [0, 1] },
  enabled: { type: 'boolean', title: '启用', enum: [false, true] },
} }

/** HTTP 夹具记录 SDK 请求，并返回完整生命周期和可恢复的确认待办。 */
function fixture(inputs) {
  return createServer(async (request, response) => {
    let body = ''
    for await (const chunk of request) body += chunk
    const input = JSON.parse(body)
    inputs.push(input)
    const { threadId, runId } = input
    const pending = !input.resume?.length && input.messages.at(-1)?.content === '申请修改'
      ? { schemaVersion: 1, interactionId: 'input-confirmation', kind: 'confirmation', message: '确认这次修改？', payload: {} } : null
    const value = { schemaVersion: 1, agentId: 'assistant', threadId, runId,
      status: pending ? 'awaiting_user' : 'completed', pendingInteraction: pending, result: pending ? null : {} }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    response.write(`data: ${JSON.stringify({ type: 'RUN_STARTED', threadId, runId })}\n\n`)
    await setTimeout(80)
    const events = [
      { type: 'TEXT_MESSAGE_START', messageId: runId, role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: runId, delta: pending ? '请确认' : '已查询' },
      { type: 'TEXT_MESSAGE_END', messageId: runId },
      { type: 'CUSTOM', name: 'agent_runtime_result.v1', value },
      { type: 'STATE_SNAPSHOT', snapshot: { agentRuntime: value } },
      { type: 'RUN_FINISHED', threadId, runId, outcome: pending
        ? { type: 'interrupt', interrupts: [{ id: pending.interactionId, reason: pending.kind,
          message: pending.message, responseSchema: { type: 'boolean' } }] } : { type: 'success' } },
    ]
    for (const event of events) response.write(`data: ${JSON.stringify(event)}\n\n`)
    response.end()
  })
}

/** 用真实原生输入事件更新 React 控件，保留空字符串、零和 false 的区别。 */
function fill(label, value) {
  const field = document.querySelector(`[aria-label="${label}"]`)
  assert.ok(field, `Missing field: ${label}`)
  const prototype = field.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(prototype, 'value').set.call(field, value)
  field.dispatchEvent(new window.Event(field.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true }))
}

/** 只观察正式标准 Context，不读取组件内部状态。 */
function businessInput(request) {
  const entry = request.context.find((item) => item.description === 'xcodeagent.input')
  return entry ? JSON.parse(entry.value) : undefined
}

/** 默认只保留入口；展开才提供字段，退出后不再携带旧参数。 */
test('business input is opt-in, typed and cleared on exit without replacing the conversation', async (t) => {
  const inputs = []
  const { getHost, snapshot } = await mountSharedConsumer(t, 'business', 'assistant', fixture(inputs), false, false, { inputSchema })
  assert.equal(document.querySelector('[aria-label="订单编号"]'), null)
  const thread = snapshot().conversation.id
  await getHost().sendMessage('查询订单')
  assert.equal(businessInput(inputs.at(-1)), undefined)
  click('填写业务参数')
  await until(() => document.querySelector('[aria-label="订单编号"]'), 'Form did not expand')
  assert.match(document.querySelector('.shared-agent-business').textContent, /需要修改的订单编号/)
  assert.equal(document.querySelector('[aria-label="目标状态"]').tagName, 'SELECT')
  fill('订单编号', 'ORDER-100')
  fill('目标状态', '0')
  fill('数量', '0')
  fill('启用', '0')
  fill('新备注', 'temporary')
  await setTimeout(30)
  fill('新备注', '')
  await setTimeout(30)
  await getHost().sendMessage('携带参数')
  assert.deepEqual(businessInput(inputs.at(-1)), { orderNumber: 'ORDER-100', targetStatus: '已确认', count: 0, enabled: false, newRemark: '' })
  click('退出填写并清空')
  await until(() => !document.querySelector('[aria-label="订单编号"]'), 'Form did not close')
  await getHost().sendMessage('普通查询')
  assert.equal(businessInput(inputs.at(-1)), undefined)
  click('填写业务参数')
  await until(() => document.querySelector('[aria-label="订单编号"]'), 'Form did not reopen')
  assert.equal(document.querySelector('[aria-label="订单编号"]').value, '')
  assert.equal(document.querySelector('[aria-label="目标状态"]').value, '')
  assert.equal(snapshot().conversation.id, thread)
})

/** 原会话运行及等待确认时不得改变输入，确认恢复必须使用同一参数。 */
test('business input stays locked during runs and pending confirmation, including panel hiding', async (t) => {
  const inputs = []
  const { getHost, snapshot, showPage } = await mountSharedConsumer(t, 'business', 'assistant', fixture(inputs), false, false, { inputSchema })
  showPage('home', true)
  await until(() => document.querySelector('[data-design-page="home"]'), 'Missing floating page')
  click('填写业务参数')
  await until(() => document.querySelector('[aria-label="订单编号"]'), 'Missing form')
  fill('订单编号', 'ORDER-100')
  await setTimeout(30)
  const sending = getHost().sendMessage('申请修改')
  await until(() => document.querySelector('[aria-label="订单编号"]')?.matches(':disabled'), 'Input was editable during run')
  await sending
  await until(() => document.querySelector('[data-runtime-business-status="awaiting_user"]'), 'Missing confirmation')
  assert.equal(document.querySelector('[aria-label="订单编号"]').matches(':disabled'), true)
  assert.equal([...document.querySelectorAll('button')].find((button) => button.textContent === '退出填写并清空').disabled, true)
  showPage('home', false)
  await until(() => document.getElementById('shared-agent-conversation').hidden, 'Panel did not hide')
  showPage('home', true)
  await until(() => !document.getElementById('shared-agent-conversation').hidden, 'Panel did not reopen')
  assert.equal(document.querySelector('[aria-label="订单编号"]').value, 'ORDER-100')
  assert.equal(snapshot().interrupts.length, 1)
  click('确认')
  // 普通空结果不展示业务卡片，解锁以官方运行状态和实际恢复请求为准。
  await until(() => inputs.length === 2 && snapshot().run.status !== 'running'
    && snapshot().interrupts.length === 0, 'Confirmation did not resume')
  assert.deepEqual(businessInput(inputs[1]), businessInput(inputs[0]))
  assert.equal(inputs[1].resume[0].payload, true)
  await until(() => !document.querySelector('[aria-label="订单编号"]').matches(':disabled'), 'Completed input stayed locked')
})

/** 会话切换、页面往返与账号切换均不复用上一轮表单草稿。 */
test('business input clears on thread, page and identity changes', async (t) => {
  const inputs = []
  const { getHost, snapshot, showPage } = await mountSharedConsumer(t, 'business', 'assistant', fixture(inputs), false, false, { inputSchema })
  await getHost().sendMessage('查询订单')
  await until(() => snapshot().run.status !== 'running', 'Initial conversation did not settle')
  click('填写业务参数')
  await until(() => document.querySelector('[aria-label="订单编号"]'), 'Missing form')
  fill('订单编号', 'OLD-THREAD')
  await setTimeout(30)
  const oldThread = snapshot().conversation.id
  await getHost().startNewConversation()
  await until(() => !document.querySelector('[aria-label="订单编号"]'), 'New thread retained input')
  await until(() => snapshot()?.conversation.id !== oldThread && getHost().getSnapshot().status === 'ready', 'New thread did not settle')
  // 官方线程列表在新建回执后还有初始选择提交，与挂载夹具采用相同的提交等待。
  await setTimeout(200)
  await getHost().sendMessage('查询订单').catch((cause) => { throw new Error('New thread query failed', { cause }) })
  assert.equal(businessInput(inputs.at(-1)), undefined)
  click('填写业务参数')
  await until(() => document.querySelector('[aria-label="订单编号"]'), 'Missing form')
  fill('订单编号', 'OLD-PAGE')
  showPage('home', true)
  await until(() => document.querySelector('[data-design-page="home"]'), 'Missing home')
  showPage('chat')
  await until(() => document.querySelector('[data-design-page="chat"]'), 'Missing chat')
  assert.equal(document.querySelector('[aria-label="订单编号"]'), null)
  await getHost().sendMessage('查询订单').catch((cause) => { throw new Error('Page query failed', { cause }) })
  assert.equal(businessInput(inputs.at(-1)), undefined)
  click('填写业务参数')
  await until(() => document.querySelector('[aria-label="订单编号"]'), 'Missing form')
  fill('订单编号', 'OLD-IDENTITY')
  const thread = snapshot().conversation.id
  showPage('chat', true, 'user-b')
  await until(() => snapshot()?.conversation.id && snapshot().conversation.id !== thread, 'Identity did not change')
  assert.equal(document.querySelector('[aria-label="订单编号"]'), null)
})
