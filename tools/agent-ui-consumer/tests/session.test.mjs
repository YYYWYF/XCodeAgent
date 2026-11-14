import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setTimeout } from 'node:timers/promises'
import { createServer as createHttpServer } from 'node:http'
import { until, mountConsumer, mountSharedConsumer, click } from './sessionHarness.mjs'

/** 独立页导航和官方会话同时可见，来回切页仍保留唯一 Composer 和当前线程。 */
test('design preview retains standalone navigation beside one official conversation', async (t) => {
  const { snapshot, requests } = await mountSharedConsumer(t, 'simple-chat', undefined, undefined, true)
  const thread = snapshot().conversation.id
  const composer = document.querySelector('textarea')
  assert.ok(document.querySelector('[data-action-id="back_home"]'))
  assert.equal(document.querySelectorAll('textarea').length, 1)
  assert.equal(document.querySelector('.agent-design-source'), null)
  document.querySelector('[data-action-id="back_home"]').click()
  await until(() => document.querySelector('[data-design-page="home"]'), 'Navigation did not reach home')
  assert.equal(document.querySelector('.agent-design-source'), null)
  document.querySelector('[data-action-id="go_chat"]').click()
  await until(() => document.querySelector('[data-design-page="chat"]'), 'Navigation did not return to chat')
  assert.equal(document.querySelector('textarea'), composer)
  assert.equal(snapshot().conversation.id, thread)
  assert.equal(requests.length, 0)
})

/** 仅测试的当前 Python 事件夹具；不调用模型，也不冒充实际生成的 Runtime 验收。 */
function runtimeResultFixture(inputs = []) {
  return createHttpServer(async (request, response) => {
    let body = ''
    for await (const chunk of request) body += chunk
    const input = JSON.parse(body)
    inputs.push(input)
    const { threadId, runId } = input
    const value = { schemaVersion: 1, agentId: 'assistant', threadId, runId, status: 'completed', pendingInteraction: null, result: null }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    response.write(`data: ${JSON.stringify({ type: 'RUN_STARTED', threadId, runId })}\n\n`)
    await setTimeout(60)
    for (const event of [
      { type: 'TEXT_MESSAGE_START', messageId: runId, role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: runId, delta: '合同夹具回复' },
      { type: 'TEXT_MESSAGE_END', messageId: runId },
      { type: 'CUSTOM', name: 'agent_runtime_result.v1', value },
      { type: 'STATE_SNAPSHOT', snapshot: { agentRuntime: value } },
      { type: 'RUN_FINISHED', threadId, runId, result: { agentRuntime: value } },
    ]) response.write(`data: ${JSON.stringify(event)}\n\n`)
    response.end()
  })
}

/** 官方客户端第二轮回传公开状态镜像；Python 必须校验后忽略，不能当业务输入。 */
test('plain text requests preserve the Python request boundary across two turns', async (t) => {
  const inputs = []
  const { getHost, snapshot } = await mountSharedConsumer(t, 'runtime-text', 'assistant', runtimeResultFixture(inputs))
  await getHost().sendMessage('记住测试词：青竹')
  await until(() => inputs.length === 1 && snapshot().run.status === 'idle', 'First turn did not settle')
  await getHost().sendMessage('测试词是什么？')
  await until(() => inputs.length === 2 && snapshot().run.status === 'idle', 'Second turn did not settle')
  assert.deepEqual(inputs[0].state ?? {}, {})
  assert.equal(inputs[1].state.agentRuntime.threadId, inputs[0].threadId)
  assert.equal(inputs[1].state.agentRuntime.runId, inputs[0].runId)
  assert.equal(inputs[1].state.agentRuntime.result, null)
  for (const input of inputs) {
    assert.equal(input.protocolVersion, '1.0')
    assert.deepEqual(Object.keys(input).filter(key => !['threadId', 'runId', 'tools', 'context', 'forwardedProps', 'state', 'messages', 'protocolVersion'].includes(key)), [])
    assert.equal(JSON.parse(input.context[0].value).pageId, 'chat')
    assert.deepEqual(input.tools ?? [], [])
    assert.ok(input.messages.every(message => Object.keys(message).sort().join(',') === 'content,id,role'))
  }
  assert.equal(inputs[0].threadId, inputs[1].threadId)
})

/** 即使收到当前请求的合法 Python 结果，包未公开 runId 时仍不能独立核验。 */
test('well-formed Python HTTP result is consumed without requiring package run identity', async (t) => {
  const { getHost, snapshot, requests } = await mountSharedConsumer(t, 'runtime-result', 'assistant', runtimeResultFixture())
  await getHost().sendMessage('仅合同夹具')
  await until(() => document.querySelector('[data-runtime-business-status="completed"]'), 'Backend receipt did not settle')
  assert.equal(snapshot().run.id, undefined)
  assert.equal(requests.length, 1)
  assert.ok(document.querySelector('[data-runtime-business-status="completed"]'))
  assert.match(document.body.textContent, /合同夹具回复/)
})

/** 实际安装包没有公开 run.id；官方 Mock 的 wire 结束也不能成为 X 的业务成功。 */
test('shared Host reports unknown without public run identity and clears it on user change', async (t) => {
  const { getHost, snapshot, showPage, requests } = await mountSharedConsumer(t, 'simple-chat', 'assistant')
  await getHost().sendMessage('本轮结果观察')
  await until(() => document.querySelector('[data-runtime-business-status="unknown"]'), 'Missing unknown business state')
  assert.equal(snapshot().run.id, undefined)
  assert.match(document.body.textContent, /请查询当前状态/)
  assert.equal(requests.length, 1)
  showPage('home', true)
  await until(() => document.getElementById('shared-agent-conversation')?.classList.contains('is-floating'), 'Page switch did not settle')
  assert.ok(document.querySelector('[data-runtime-business-status="unknown"]'))
  showPage('chat', true, 'user-b')
  await until(() => !document.querySelector('[data-runtime-business-status]'), 'Previous result leaked across users')
  assert.equal(requests.length, 1)
})

/** 页面展示变化不应卸载 Composer，用户切换则必须清理整棵会话树。 */
test('shared application preserves draft across routes and resets it on identity change', async (t) => {
  const { showPage, snapshot, requests } = await mountSharedConsumer(t)
  showPage('home', true)
  await until(() => document.querySelector('textarea'), 'Composer missing')
  const composer = document.querySelector('textarea')
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
  setter.call(composer, '尚未发送的草稿')
  composer.dispatchEvent(new window.Event('input', { bubbles: true }))
  await setTimeout(50)
  const thread = snapshot().conversation.id
  showPage('chat', true)
  await until(() => document.body.textContent.includes('独立页欢迎'), 'Standalone presentation missing')
  assert.equal(document.querySelector('textarea'), composer)
  assert.equal(composer.value, '尚未发送的草稿')
  showPage('home', false)
  await until(() => document.getElementById('shared-agent-conversation').hidden, 'Panel not hidden')
  showPage('home', true)
  await until(() => !document.getElementById('shared-agent-conversation').hidden, 'Panel not reopened')
  assert.equal(document.querySelector('textarea'), composer)
  assert.equal(snapshot().conversation.id, thread)
  showPage('home', true, 'user-b')
  await until(() => snapshot()?.conversation.id !== thread && document.querySelector('textarea') !== composer,
    'Identity change reused the previous session')
  assert.equal(document.querySelector('textarea').value, '')
  assert.equal(requests.length, 0)
})

/** 身份变化清理运行 owner；迟到事件和旧 Host 方法不能写入下一用户。 */
test('switching identity ends the old Host and does not replay an active run', async (t) => {
  const { showPage, snapshot, requests, getHost } = await mountSharedConsumer(t, 'cancel-before-first-output')
  const oldHost = getHost()
  const thread = snapshot().conversation.id
  void oldHost.sendMessage('本轮仅发送一次').catch(() => undefined)
  await until(() => requests.length === 1 && snapshot().run.status === 'running', 'Run did not start')
  showPage('home', true, 'user-b')
  await until(() => snapshot()?.conversation.id !== thread && snapshot()?.run.status === 'idle', 'Old run leaked into next identity')
  await assert.rejects(oldHost.sendMessage('旧身份不能再发送'), (error) =>
    ['AGENT_UI_SESSION_ENDED', 'AGENT_UI_NOT_READY'].includes(error.code))
  assert.equal(snapshot().messages.length, 0)
  assert.equal(requests.length, 1)
  showPage('chat', true, null)
  await until(() => !document.getElementById('shared-agent-conversation'), 'Logout retained the old conversation')
  assert.match(document.body.textContent, /业务首页/)
  assert.ok(document.querySelector('.shared-agent-page-slot'), 'Logout broke the standalone page')
})

/** 全页只移动常驻容器到页面内容区，Shell 导航和会话 DOM 均不被重建。 */
test('standalone placement preserves the Shell and the same conversation element', async (t) => {
  const { showPage, snapshot } = await mountSharedConsumer(t)
  showPage('home', true)
  await until(() => document.querySelector('textarea'), 'Composer missing')
  const section = document.getElementById('shared-agent-conversation')
  const thread = snapshot().conversation.id
  showPage('chat', true)
  await until(() => document.querySelector('.shared-agent-page-slot'), 'Page slot missing')
  const slot = document.querySelector('.shared-agent-page-slot')
  slot.getBoundingClientRect = () => ({ top: 90, left: 200, width: 640, height: 480 })
  window.dispatchEvent(new window.Event('resize'))
  await until(() => section.style.width === '640px', 'Host did not use the actual content bounds')
  assert.equal(section.hidden, false)
  assert.equal(section.style.left, '200px')
  assert.equal(document.getElementById('shared-agent-conversation'), section)
  assert.equal(snapshot().conversation.id, thread)
  assert.equal(document.querySelector('main').parentElement.hidden, false)
})


/** 官方建议与正常发送/运行状态保留，页面不包含人为主题、状态或呈现切换控件。 */
test('official defaults and real conversation work without demonstration controls', async (t) => {
  const { requests, snapshot } = await mountConsumer(t)
  for (const label of ['账户交易查询', '转账汇款指引', '银行卡服务']) assert.ok(document.body.textContent.includes(label))
  assert.doesNotMatch(document.body.textContent, /切换明暗主题|发送测试消息|当前场景|消息 0/)
  assert.equal(document.querySelector('nav[aria-label="会话呈现方式"]'), null)
  const id = snapshot().conversation.id
  click('账户交易查询')
  await until(() => snapshot().run.status === 'running', 'Run did not start')
  await until(() => snapshot().run.status === 'idle' && snapshot().messages.length === 2, 'Text run did not settle')
  // Host 终态先于 React 的流式文本渲染完成，必须等真实 DOM 收敛再断言回复。
  await until(() => /你好，这是一个纯文本流式回复/.test(document.body.textContent), 'Completed reply did not render')
  assert.match(document.body.textContent, /你好，这是一个纯文本流式回复/)
  assert.equal(snapshot().conversation.id, id)
  assert.equal(requests.length, 1)
})

/** 首字前停止必须释放发送占用，且不会出现自动补发请求。 */
test('stop before first output settles without replay', async (t) => {
  const { requests, snapshot } = await mountConsumer(t, 'cancel-before-first-output')
  click('账户交易查询')
  await until(() => snapshot().run.status === 'running' && requests.length === 1, 'Run did not start')
  click('停止生成')
  await until(() => snapshot().run.status === 'idle', 'Cancellation did not settle')
  await setTimeout(200)
  assert.equal(requests.length, 1)
})

/** 官方复制按钮写入完整回复，剪贴板失败不能显示复制成功或发起新运行。 */
test('native response copy writes real text and reports clipboard failure', async (t) => {
  const { getHost, snapshot, requests } = await mountSharedConsumer(t)
  const writes = []
  let rejectCopy = true
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
    /** 只替代测试环境剪贴板端口；复制控件和回复数据仍由实际官方组件提供。 */
    async writeText(value) {
      if (rejectCopy) throw new Error('clipboard denied by test')
      writes.push(value)
    },
  } })
  const originalError = console.error
  t.after(() => { console.error = originalError })
  console.error = (...values) => {
    if (values[0] !== '[agent-ui] response copy failed:') originalError(...values)
  }
  await getHost().sendMessage('生成一段完整回复供复制')
  await until(() => snapshot().run.status === 'idle' && snapshot().messages.length === 2, 'Reply did not settle')
  const button = document.querySelector('[data-slot="copy-action-idle"]')?.closest('button')
  assert.ok(button && !button.disabled)
  button.click()
  await until(() => document.querySelector('[data-slot="copy-action-error"]'), 'Copy failure was hidden')
  assert.equal(document.querySelector('[data-slot="copy-action-copied"]'), null)
  assert.deepEqual(writes, [])
  rejectCopy = false
  button.click()
  await until(() => document.querySelector('[data-slot="copy-action-copied"]'), 'Copy did not complete')
  assert.equal(document.querySelector('[data-slot="copy-action-error"]'), null)
  assert.equal(writes.length, 1)
  assert.equal(writes[0], snapshot().messages.find(({ role }) => role === 'assistant').content)
  assert.match(writes[0], /你好，这是一个纯文本流式回复/)
  assert.equal(requests.length, 1)
})

/** 中途断开真实 HTTP 连接，必须报告错误而非隐式重放原请求。 */
test('broken stream reports error without replay', async (t) => {
  const { requests, mock, snapshot } = await mountConsumer(t, 'cancel-before-first-output')
  click('账户交易查询')
  await until(() => snapshot().run.status === 'running' && requests.length === 1, 'Run did not start')
  mock.closeAllConnections()
  await until(() => snapshot().run.status === 'error' || document.querySelector('[role="alert"]'), 'Dropped stream did not report failure')
  await setTimeout(200)
  assert.equal(requests.length, 1)
})

/** 使用已经实际转入的共享应用，切页和关闭浮窗不创建第二个运行或丢失会话。 */
test('transferred shared Host keeps one session across page props and hidden surfaces', {
  skip: !process.env.AGENT_UI_SHARED_PROJECT,
}, async (t) => {
  const { requests, snapshot, showPage } = await mountConsumer(t, 'cancel-before-first-output', {
    root: process.env.AGENT_UI_SHARED_PROJECT,
    module: process.env.AGENT_UI_SHARED_HOST_MODULE,
  })
  const conversation = snapshot().conversation.id
  assert.match(document.body.textContent, /独立页欢迎/)
  assert.equal(document.querySelectorAll('.agent-ui-platform-shell').length, 1)
  showPage('home', true)
  await until(() => document.body.textContent.includes('首页欢迎'), 'Page-specific welcome did not change')
  assert.equal(snapshot().conversation.id, conversation)
  click('关闭助手')
  await until(() => document.getElementById('shared-agent-conversation').hidden, 'Closing the panel unmounted the host')
  assert.equal(snapshot().conversation.id, conversation)
  click('打开助手')
  await until(() => !document.getElementById('shared-agent-conversation').hidden, 'Launcher did not reopen the host')
  // 通过输入框发送，不依赖目标设计是否保留某条示例建议。
  const composer = document.querySelector('textarea')
  assert.ok(composer, 'Transferred Composer missing')
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
  setter.call(composer, '共享交接验收')
  composer.dispatchEvent(new window.Event('input', { bubbles: true }))
  await until(() => document.querySelector('button[aria-label="发送消息"]')?.disabled === false,
    'Transferred Composer did not enable sending')
  click('发送消息')
  await until(() => snapshot().run.status === 'running' && requests.length === 1, 'Shared run did not start')
  showPage('chat', true)
  await until(() => document.getElementById('shared-agent-conversation').classList.contains('is-standalone'), 'Standalone page did not render')
  assert.equal(snapshot().conversation.id, conversation)
  assert.equal(snapshot().run.status, 'running')
  assert.equal(document.querySelectorAll('.agent-ui-platform-shell').length, 1)
  click('停止生成')
  await until(() => snapshot().run.status === 'idle', 'Shared run did not stop')
  assert.equal(requests.length, 1)
})
