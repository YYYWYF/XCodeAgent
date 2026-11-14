import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setTimeout } from 'node:timers/promises'
import { createServer as createHttpServer } from 'node:http'
import path from 'node:path'
import { spawn } from 'node:child_process'
import { Readable } from 'node:stream'
import { until, mountSharedConsumer, click } from './sessionHarness.mjs'

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

/** 原生批准/拒绝回答同一待办；查询不得追加消息或执行工具。 */
for (const approve of [true, false]) test(`business ${approve ? 'approval' : 'rejection'} resumes native interrupt after readonly inspection`, async (t) => {
  const inputs = []
  const effects = { writes: 0 }
  const { getHost, snapshot } = await mountSharedConsumer(t, 'business', 'assistant', businessFixture(inputs, effects))
  await getHost().sendMessage('更新测试订单')
  await until(() => document.querySelector('[data-runtime-business-status="awaiting_user"]'), 'Missing pending result')
  assert.equal(snapshot().interrupts[0].id, 'approval-1')
  const messageCount = snapshot().messages.length
  click('查询当前状态')
  await until(() => document.body.textContent.includes('已查询到当前待办'), 'Inspection did not settle')
  assert.equal(snapshot().messages.length, messageCount)
  assert.equal(inputs.length, 2)
  assert.deepEqual(inputs[1].messages, [])
  assert.equal(inputs[1].forwardedProps.inspectInteraction, true)
  assert.equal(effects.writes, 0)
  click(approve ? '同意并继续' : '拒绝')
  await until(() => document.querySelector('[data-runtime-business-status="completed"]'), 'Resume did not complete')
  assert.equal(effects.writes, approve ? 1 : 0)
  assert.equal(inputs.length, 3)
  assert.equal(inputs[2].resume[0].payload, approve)
  assert.equal(snapshot().interrupts.length, 0)
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, new RegExp(`"updated": ${approve}`))
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
  const { getHost, snapshot, setContext } = await mountSharedConsumer(t, 'python-business', 'assistant', proxy)
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: '订单', value: 'ORDER-100' }], input: { amount: 12 } })
  await setTimeout(100)
  await getHost().sendMessage('查询订单')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('ORDER-100'), 'Actual context/tool result missing: ' + stderr)
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, /"writes": 0/)
  assert.deepEqual(JSON.parse(inputs[0].context.find(item => item.description === 'xcodeagent.input').value), { amount: 12 })
  const thread = snapshot().conversation.id
  await getHost().sendMessage('更新订单')
  await until(() => document.querySelector('[data-runtime-business-status="awaiting_user"]'), 'Actual Python interrupt missing: ' + stderr)
  assert.equal(snapshot().interrupts[0].id, 'approval-ORDER-100')
  const count = snapshot().messages.length
  click('查询当前状态')
  await until(() => document.body.textContent.includes('已查询到当前待办'), 'Actual readonly inspection failed: ' + stderr)
  assert.equal(snapshot().messages.length, count)
  assert.deepEqual(inputs.at(-1).messages, [])
  click('拒绝')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('"updated": false'), 'Actual rejection failed: ' + stderr)
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, /"writes": 0/)
  await getHost().sendMessage('更新订单')
  await until(() => document.querySelector('[data-runtime-business-status="awaiting_user"]'), 'Second interrupt missing: ' + stderr)
  click('同意并继续')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('"updated": true'), 'Actual approval failed: ' + stderr)
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, /"writes": 1/)
  await getHost().sendMessage('澄清订单')
  await until(() => document.querySelector('input') && document.body.textContent.includes('请补充订单说明'), 'Clarification missing: ' + stderr)
  const input = document.querySelector('input')
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set.call(input, '加急处理')
  input.dispatchEvent(new window.Event('input', { bubbles: true }))
  await until(() => [...document.querySelectorAll('button')].some(button => button.textContent === '提交回答' && !button.disabled), 'Answer did not enable')
  click('提交回答')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('加急处理'), 'Actual clarification resume failed: ' + stderr)
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: '订单', value: 'ORDER-200' }] })
  await setTimeout(100)
  await getHost().sendMessage('查询订单')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('ORDER-200'), 'Record switch used stale context: ' + stderr)
  assert.equal(snapshot().conversation.id, thread)
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, /"writes": 0/)
  dropNext = true
  await getHost().sendMessage('更新订单').catch(() => undefined)
  await until(() => document.querySelector('[data-runtime-business-status="unknown"]'), 'Dropped native outcome did not stay unknown')
  assert.equal(snapshot().interrupts.length, 0)
  click('查询当前状态')
  await until(() => document.body.textContent.includes('已查询到当前待办'), 'Lost interrupt was not recovered by inspection')
  click('同意并继续')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('"updated": true'), 'Inspected pending could not resume without private package mutation')
  assert.match(document.querySelector('[aria-label="业务结果"]').textContent, /"writes": 1/)
  await getHost().sendMessage('查询订单')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('"amount": 42'), 'Native conversation failed after inspected recovery')
  assert.equal(snapshot().conversation.id, thread)
  await getHost().sendMessage('更新订单')
  await until(() => document.querySelector('[data-runtime-business-status="awaiting_user"]'), 'Target-bound pending missing')
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: '订单', value: 'ORDER-300' }] })
  await setTimeout(100)
  click('同意并继续')
  await until(() => document.querySelector('[data-runtime-business-status="failed"]'), 'Changed target incorrectly approved')
  assert.match(document.body.textContent, /页面或业务目标已变化/)
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: '订单', value: 'ORDER-200' }], input: { amount: 'invalid' } })
  await setTimeout(100)
  click('查询当前状态')
  await until(() => document.body.textContent.includes('已查询到当前待办'), 'Original target pending disappeared')
  click('拒绝')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('"updated": false'), 'Original target rejection failed')
  await getHost().sendMessage('查询订单')
  await until(() => document.querySelector('[data-runtime-business-status="failed"]'), 'Invalid business input was accepted')
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: '订单', value: 'ORDER-200' }], input: { amount: 12 } })
  await setTimeout(100)
  await getHost().sendMessage('查询订单')
  await until(() => document.querySelector('[aria-label="业务结果"]')?.textContent.includes('"amount": 42'), 'Corrected business input failed')

  // 真实 Python 在模型节点抛连接错误；错误只作为原生失败显示，不污染下一轮历史。
  const beforeFailure = inputs.length
  await getHost().sendMessage('连接失败回归').catch(() => undefined)
  await until(() => document.querySelector('[data-runtime-business-status="failed"]'), 'Native model failure missing: ' + stderr)
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
