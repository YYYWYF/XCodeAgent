import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setTimeout } from 'node:timers/promises'
import { createServer as createHttpServer } from 'node:http'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { Readable } from 'node:stream'
import { until, mountSharedConsumer } from './sessionHarness.mjs'

/** 历史卡片保留禁用动作，测试只驱动当前可用按钮。 */
function click(label) {
  const button = [...document.querySelectorAll('button')].find(item => item.textContent === label && !item.disabled)
  assert.ok(button, `Missing active button: ${label}`)
  button.click()
  button.click()
}

/** 标准中断/查询夹具只校验公开成品接线，真实 Python 服务另有集成测试。 */
function businessFixture(inputs, effects) {
  const waiting = new Map()
  return createHttpServer(async (request, response) => {
    let body = ''
    for await (const chunk of request) body += chunk
    const input = JSON.parse(body)
    inputs.push(input)
    const { threadId, runId } = input
    const inspect = input.forwardedProps?.inspectInteraction === true
    const resume = input.resume?.[0]
    if (!inspect && !resume) waiting.set(threadId, { schemaVersion: 1, interactionId: 'approval-1',
      kind: 'confirmation', message: '是否更新测试订单？', payload: { orderId: 'ORDER-100' } })
    if (resume) {
      assert.equal(resume.interruptId, 'approval-1')
      if (resume.payload === true) effects.writes += 1
      waiting.delete(threadId)
    }
    const pending = waiting.get(threadId) ?? null
    const value = { schemaVersion: 1, agentId: 'assistant', threadId, runId,
      status: pending ? 'awaiting_user' : 'completed', pendingInteraction: pending,
      result: pending || inspect ? null : { updated: resume.payload === true } }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    response.write(`data: ${JSON.stringify({ type: 'RUN_STARTED', threadId, runId })}\n\n`)
    await setTimeout(60)
    const events = [
      { type: 'TEXT_MESSAGE_START', messageId: runId, role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: runId, delta: pending ? '等待确认' : '操作结束' },
      { type: 'TEXT_MESSAGE_END', messageId: runId },
      { type: 'CUSTOM', name: 'agent_runtime_result.v1', value },
      { type: 'STATE_SNAPSHOT', snapshot: { agentRuntime: value } },
      { type: 'RUN_FINISHED', threadId, runId, result: { agentRuntime: value }, outcome: pending
        ? { type: 'interrupt', interrupts: [{ id: pending.interactionId, reason: pending.kind,
          message: pending.message, responseSchema: { type: 'boolean' } }] } : { type: 'success' } },
    ]
    for (const event of events) response.write(`data: ${JSON.stringify(event)}\n\n`)
    response.end()
  })
}

/** 成功回执不追加空框或重复成功提示，实际业务值仍可阅读。 */
test('completed conversations hide empty receipts and preserve meaningful results without a success heading', async (t) => {
  const results = [null, {}, [], { runId: 'internal-run' }, { updated: false, count: 0 }]
  const server = createHttpServer(async (request, response) => {
    let body = ''
    for await (const chunk of request) body += chunk
    const { threadId, runId } = JSON.parse(body)
    const value = { schemaVersion: 1, agentId: 'assistant', threadId, runId,
      status: 'completed', pendingInteraction: null, result: results.shift() }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    for (const event of [
      { type: 'RUN_STARTED', threadId, runId },
      { type: 'TEXT_MESSAGE_START', messageId: runId, role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: runId, delta: '已回复测试问题' },
      { type: 'TEXT_MESSAGE_END', messageId: runId },
      { type: 'CUSTOM', name: 'agent_runtime_result.v1', value },
      { type: 'RUN_FINISHED', threadId, runId },
    ]) response.write(`data: ${JSON.stringify(event)}\n\n`)
    response.end()
  })
  const { getHost } = await mountSharedConsumer(t, 'business', 'assistant', server)
  for (let index = 0; index < 5; index++) {
    await getHost().sendMessage(`测试问题 ${index}`)
    await setTimeout(80)
    assert.match(document.body.textContent, /已回复测试问题/)
    assert.doesNotMatch(document.body.textContent, /业务已完成。/)
    assert.equal(document.querySelectorAll('.shared-agent-step').length, index === 4 ? 1 : 0)
  }
  assert.match(document.querySelector('.shared-agent-step').textContent, /updated：否count：0/)
})

/** 原生批准/拒绝回答同一待办；查询不得追加消息或执行工具。 */
for (const approve of [true, false]) test(`business ${approve ? 'approval' : 'rejection'} renders anchored cards and resumes once`, async (t) => {
  const inputs = []
  const effects = { writes: 0 }
  const { getHost, snapshot, showPage } = await mountSharedConsumer(t, 'business', 'assistant', businessFixture(inputs, effects), false, true)
  await getHost().sendMessage('更新测试订单')
  await until(() => document.querySelector('[data-runtime-business-status="awaiting_user"]'), 'Missing pending result')
  assert.equal(snapshot().interrupts[0].id, 'approval-1')
  await until(() => document.querySelector('[data-observation-business-status="awaiting_user"]'), 'Observer did not consume pending receipt')
  assert.doesNotMatch(document.querySelector('[data-agent-ui-observation]').textContent, /ORDER-100|是否更新测试订单/)
  const messageCount = snapshot().messages.length
  const card = document.querySelector('[aria-label="待处理交互"]')
  assert.ok(card.closest('[data-slot="aui_thread-viewport"]'))
  assert.equal(document.querySelector('.shared-agent-conversation > .shared-agent-business'), null)
  assert.equal([...document.querySelectorAll('button')].some(button => button.textContent === '检查执行状态'), false)
  assert.equal(card.querySelector('pre'), null)
  assert.equal(snapshot().messages.length, messageCount)
  assert.equal(inputs.length, 1)
  assert.equal(effects.writes, 0)
  click(approve ? '确认' : '取消')
  await until(() => document.querySelector('[data-runtime-business-status="completed"]'), 'Resume did not complete')
  assert.equal(effects.writes, approve ? 1 : 0)
  assert.equal(inputs.length, 2)
  assert.equal(inputs[1].resume[0].payload, approve)
  assert.match(card.textContent, approve ? /已确认/ : /已取消/)
  assert.ok([...card.querySelectorAll('button')].every(button => button.disabled))
  const threadId = snapshot().conversation.id
  const beforeSwitch = inputs.length

  assert.equal(snapshot().interrupts.length, 0)
  await until(() => document.querySelector('[data-observation-business-status="completed"]'), 'Observer did not settle resume result')
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, new RegExp(`updated：${approve ? '是' : '否'}`))
  showPage('home', false)
  await setTimeout(60)
  showPage('home', true)
  await setTimeout(60)
  assert.equal(snapshot().conversation.id, threadId)
  assert.equal(inputs.length, beforeSwitch)
  assert.ok(document.body.contains(card))
  await getHost().startNewConversation()
  await until(() => snapshot().conversation.id !== threadId, 'New conversation failed')
  await until(() => !document.querySelector('.shared-agent-step'), 'Cards leaked to another thread')

})

/** 使用当前模板的真实 HTTP、DeepAgents、工具和 SQLite，只有模型由确定性测试模型替代。 */
test('Python Runtime integrates page context, real tools, approval, rejection, clarification and inspection', {
  skip: !process.env.AGENT_UI_TEST_PYTHON,
}, async (t) => {
  const python = spawn(process.env.AGENT_UI_TEST_PYTHON, [path.resolve(import.meta.dirname, 'python_runtime_fixture.py'),
    '--template-root', process.env.AGENT_UI_TEMPLATE_ROOT ?? path.resolve(import.meta.dirname, '../../../../springboot-template')],
  { stdio: ['ignore', 'pipe', 'pipe'] })
  let stderr = ''
  python.stderr.on('data', chunk => { stderr += chunk })
  t.after(async () => {
    if (python.exitCode !== null) return
    const stopped = new Promise(resolve => python.once('exit', resolve))
    python.kill('SIGTERM')
    await stopped
  })
  const port = await new Promise((resolve, reject) => {
    let text = ''
    const timeout = globalThis.setTimeout(() => reject(new Error('Python startup timed out: ' + stderr)), 15000)
    python.once('error', reject)
    python.once('exit', code => { clearTimeout(timeout); reject(new Error(`Python exited ${code}: ${stderr}`)) })
    python.stdout.on('data', chunk => {
      text += chunk
      const line = text.split('\n').find(value => value.startsWith('{"port":'))
      if (line) { clearTimeout(timeout); resolve(JSON.parse(line).port) }
    })
  })
  const nativeFetch = globalThis.fetch
  for (let attempt = 0; attempt < 100; attempt++) {
    try { if ((await nativeFetch(`http://127.0.0.1:${port}/health`)).ok) break } catch { /* 等待自有子进程启动。 */ }
    if (attempt === 99) throw new Error('Python health failed: ' + stderr)
    await setTimeout(50)
  }
  const inputs = []
  let cookie = ''
  let dropNext = false
  const proxy = createHttpServer(async (request, response) => {
    try {
      let body = ''
      for await (const chunk of request) body += chunk
      inputs.push(JSON.parse(body))
      const upstream = await nativeFetch(`http://127.0.0.1:${port}/agents/assistant/run`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...(cookie ? { Cookie: cookie } : {}) }, body,
      })
      const issued = upstream.headers.getSetCookie()[0]
      if (issued) cookie = issued.split(';')[0]
      if (dropNext) {
        dropNext = false
        await upstream.arrayBuffer()
        // 真实服务器已完成 checkpoint；测试故意丢弃终态，模拟客户端未知而服务端待确认。
        response.writeHead(200, { 'Content-Type': 'text/event-stream' })
        response.write(`data: ${JSON.stringify({ type: 'RUN_STARTED', threadId: inputs.at(-1).threadId, runId: inputs.at(-1).runId })}\n\n`)
        globalThis.setTimeout(() => response.destroy(), 20)
        return
      }
      response.writeHead(upstream.status, { 'Content-Type': upstream.headers.get('content-type') })
      Readable.fromWeb(upstream.body).pipe(response)
    } catch (error) { response.writeHead(500); response.end(String(error)) }
  })
  const { getHost, snapshot, setContext } = await mountSharedConsumer(t, 'python-business', 'assistant', proxy, false, true)
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: '订单', value: 'ORDER-100' }], input: { amount: 12 } })
  await setTimeout(100)
  await getHost().sendMessage('查询订单')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('ORDER-100'), 'Actual context/tool result missing: ' + stderr)
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, /writes：0/)
  assert.deepEqual(JSON.parse(inputs[0].context.find(item => item.description === 'xcodeagent.input').value), { amount: 12 })
  const thread = snapshot().conversation.id
  await getHost().sendMessage('更新订单')
  await until(() => document.querySelector('[data-runtime-business-status="awaiting_user"]'), 'Actual Python interrupt missing: ' + stderr)
  assert.equal(snapshot().interrupts[0].id, 'approval-ORDER-100')
  click('取消')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('updated：否'), 'Actual rejection failed: ' + stderr)
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, /writes：0/)
  await getHost().sendMessage('更新订单')
  await until(() => document.querySelector('[data-runtime-business-status="awaiting_user"]'), 'Second interrupt missing: ' + stderr)
  click('确认')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('updated：是'), 'Actual approval failed: ' + stderr)
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, /writes：1/)
  await getHost().sendMessage('澄清订单')
  await until(() => document.querySelector('.shared-agent-step textarea') && document.body.textContent.includes('请补充订单说明'), 'Clarification missing: ' + stderr)
  const input = document.querySelector('.shared-agent-step textarea')
  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(input, '加急处理')
  input.dispatchEvent(new window.Event('input', { bubbles: true }))
  await until(() => [...document.querySelectorAll('button')].some(button => button.textContent === '提交回答' && !button.disabled), 'Answer did not enable')
  click('提交回答')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('加急处理'), 'Actual clarification resume failed: ' + stderr)
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: '订单', value: 'ORDER-200' }] })
  await setTimeout(100)
  await getHost().sendMessage('查询订单')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('ORDER-200'), 'Record switch used stale context: ' + stderr)
  assert.equal(snapshot().conversation.id, thread)
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, /writes：0/)
  dropNext = true
  await getHost().sendMessage('更新订单').catch(() => undefined)
  await until(() => document.querySelector('[data-runtime-business-status="unknown"]'), 'Dropped native outcome did not stay unknown')
  assert.equal(snapshot().interrupts.length, 0)
  click('检查执行状态')
  await until(() => [...document.querySelectorAll('.shared-agent-step')].at(-1)?.textContent.includes('等待处理'), 'Lost interrupt was not recovered by inspection')
  click('确认')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('updated：是'), 'Inspected pending could not resume without private package mutation')
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, /writes：1/)
  await getHost().sendMessage('查询订单')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('amount：42'), 'Native conversation failed after inspected recovery')
  assert.equal(snapshot().conversation.id, thread)
  await getHost().sendMessage('更新订单')
  await until(() => document.querySelector('[data-runtime-business-status="awaiting_user"]'), 'Target-bound pending missing')
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: '订单', value: 'ORDER-300' }] })
  await setTimeout(100)
  await until(() => document.body.textContent.includes('已失效'), 'Changed target did not expire')
  assert.equal([...document.querySelectorAll('.shared-agent-step button')].filter(button => button.textContent === '确认').at(-1).disabled, true)
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: '订单', value: 'ORDER-200' }], input: { amount: 'invalid' } })
  await setTimeout(100)
  click('检查执行状态')
  await until(() => [...document.querySelectorAll('.shared-agent-step')].at(-1)?.textContent.includes('等待处理'), 'Original target pending disappeared')
  click('取消')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('updated：否'), 'Original target rejection failed')
  await getHost().sendMessage('查询订单')
  await until(() => document.querySelector('[data-runtime-business-status="failed"]'), 'Invalid business input was accepted')
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: '订单', value: 'ORDER-200' }], input: { amount: 12 } })
  await setTimeout(100)
  await getHost().sendMessage('查询订单')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('amount：42'), 'Corrected business input failed')

  // 真实 Python 在模型节点抛连接错误；错误只作为原生失败显示，不污染下一轮历史。
  const beforeFailure = inputs.length
  await getHost().sendMessage('连接失败回归').catch(() => undefined)
  await until(() => document.querySelector('[data-runtime-business-status="failed"]'), 'Native model failure missing: ' + stderr)
  await until(() => document.querySelector('[data-observation-business-status="failed"]'), 'Observer lost actual Python failure receipt')
  assert.equal(inputs.length, beforeFailure + 1)
  assert.equal(snapshot().run.status, 'error')
  assert.ok(!snapshot().messages.some(message => message.content === 'Connection error.'))
  await until(() => document.body.textContent.includes('本轮未完成。'), 'Failed turn did not render its native response')
  await until(() => [...document.querySelectorAll('button')].some(button => button.textContent.includes('重试')
    || button.getAttribute('aria-label') === '重试'), 'Native retry action missing')
  const retry = [...document.querySelectorAll('button')].filter(button => button.textContent.includes('重试')
    || button.getAttribute('aria-label') === '重试').at(-1)
  retry.click()
  await until(() => inputs.length === beforeFailure + 2, 'Retry did not reach Python')
  assert.equal(inputs.at(-1).messages.at(-1).content, '连接失败回归', JSON.stringify(inputs.at(-1).messages))
  await until(() => document.body.textContent.includes('显式重试成功'), 'Native explicit retry failed: ' + stderr)
  assert.equal(inputs.length, beforeFailure + 2)
  assert.equal(inputs.at(-1).messages.at(-1).id, inputs.at(-2).messages.at(-1).id)
  await getHost().sendMessage('失败后继续回归')
  await until(() => document.body.textContent.includes('失败后继续成功'), 'Conversation poisoned after retry: ' + stderr)
  assert.equal(snapshot().conversation.id, thread)
})

/** 流正常结束但无业务回执，以及查询无待办，都不能升级为成功或重放写入。 */
test('missing terminal result and empty inspection remain unknown', async (t) => {
  const inputs = []
  const fixture = createHttpServer(async (request, response) => {
    let body = ''
    for await (const chunk of request) body += chunk
    const input = JSON.parse(body)
    inputs.push(input)
    const { runId, threadId } = input
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    const events = [{ type: 'RUN_STARTED', runId, threadId }]
    if (input.forwardedProps?.inspectInteraction) events.push({ type: 'CUSTOM', name: 'agent_runtime_result.v1',
      value: { schemaVersion: 1, agentId: 'assistant', runId, threadId,
        status: 'completed', pendingInteraction: null, result: null } })
    events.push({ type: 'RUN_FINISHED', runId, threadId })
    for (const event of events) response.write(`data: ${JSON.stringify(event)}\n\n`)
    response.end()
  })
  const { getHost } = await mountSharedConsumer(t, 'missing-result', 'assistant', fixture)
  await getHost().sendMessage('执行操作')
  await until(() => document.querySelector('[data-runtime-business-status="unknown"]'), 'Missing result became success')
  click('检查执行状态')
  await until(() => inputs.length === 2 && ![...document.querySelectorAll('.shared-agent-step button')].at(-1)?.disabled, 'Inspection did not settle')
  assert.equal(document.querySelector('[data-runtime-business-status="completed"]'), null)
  assert.deepEqual(inputs[1].messages, [])
  assert.equal(inputs[1].resume, undefined)
  assert.equal(inputs[1].forwardedProps.inspectInteraction, true)
})
