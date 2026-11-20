import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setTimeout } from 'node:timers/promises'
import { mountSharedConsumer, until } from './sessionHarness.mjs'

/** 实际受管 loader 与共享 Context 共同验证实时脱敏、页面隔离和零观察请求。 */
test('managed business observation tracks context changes without reading values or sending requests', async (t) => {
  const { setContext, showPage, requests, snapshot, getHost } = await mountSharedConsumer(t, 'simple-chat', 'assistant', undefined, false, true)
  const panel = () => document.querySelector('[data-agent-ui-observation="readonly"]')
  const context = () => panel()?.querySelector('[data-observation-context]')
  await until(() => context()?.textContent.includes('chat'), 'Context summary was not mounted')
  const thread = snapshot().conversation.id
  const before = requests.length
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: 'private-label', value: 'private-order-100' }],
    input: { amount: 12345, token: 'private-token' } })
  await until(() => context()?.textContent.includes('orderId') && context()?.textContent.includes('amount'), 'Context field names did not update')
  const updated = context().getAttribute('data-context-revision')
  assert.match(context().textContent, /页面记录/)
  assert.match(context().textContent, /业务输入/)
  assert.doesNotMatch(panel().textContent, /private-|12345/)
  assert.ok(context().querySelector('time')?.getAttribute('datetime'))
  setContext({ actionId: 'ask', contextItems: [{ id: 'orderId', label: 'private-label', value: 'private-order-200' }] })
  await until(() => context()?.getAttribute('data-context-revision') !== updated && !context()?.textContent.includes('amount'), 'Record value change did not invalidate the summary')
  showPage('home')
  await until(() => context()?.textContent.includes('home'), 'Page switch retained old context')
  assert.equal(snapshot().conversation.id, thread)
  assert.doesNotMatch(context().textContent, /orderId|amount/)
  setContext({ contextItems: Array.from({ length: 40 }, (_, index) => ({ id: 'field' + index, label: 'private-label', value: 'private-value' })) })
  await until(() => context()?.textContent.includes('仅显示前 32 项字段'), 'Large context was not bounded')
  assert.equal(context().querySelectorAll('li').length, 32)
  await setTimeout(120)
  assert.equal(requests.length, before)
  assert.equal(panel().querySelectorAll('button,input,textarea').length, 0)
  // 唯一业务动作仍由原 Host 发起；观察面板不能因没有公开 run.id 而永远只报未验证。
  await getHost().sendMessage('观察回归')
  await until(() => panel()?.querySelector('[data-observation-business-status="unknown"]'), 'Missing business receipt was incorrectly treated as success')
  assert.equal(requests.length, before + 1)
  await getHost().startNewConversation()
  await until(() => panel()?.querySelector('[data-observation-business-status="idle"]'), 'New thread retained previous business status')
})
