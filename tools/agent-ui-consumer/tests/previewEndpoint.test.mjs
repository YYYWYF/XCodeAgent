import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { createRequire } from 'node:module'
import { test } from 'node:test'

const require = createRequire(import.meta.url)
const transform = require('../../../Backend/app/services/agent_ui_preview_endpoint.cjs')
const code = '<SharedAgentApplication agentId="assistant" sessionKey={session} endpoint="">{children}</SharedAgentApplication>'
const options = { sha256: createHash('sha256').update(code).digest('hex'), agentId: 'assistant',
  mode: 'real-text', endpoint: '/agents/assistant/run', sessionId: 'a'.repeat(32) }

/** 真实预览沿唯一官方 Host 直连正式入口；切来源/重启时重建会话，不改源码。 */
test('real preview connects the official host without a mock route or another sender', () => {
  const output = transform.call({ getOptions: () => options }, code)
  assert.match(output, /endpoint="\/agents\/assistant\/run"/)
  assert.match(output, /agentId="assistant"/)
  assert.match(output, /key="real-text:aaaa/)
  assert.doesNotMatch(output, /__agent_ui_mock|fetch|HttpAgent/)
  assert.match(code, /endpoint=""/)
})

/** 非法 endpoint、旧编译输入和会话身份不能获得真实连接。 */
test('changed code and foreign endpoints fail closed', () => {
  for (const change of [{ endpoint: '/agents/other/run' }, { mode: 'unknown' }, { sessionId: '' }]) {
    assert.throws(() => transform.call({ getOptions: () => ({ ...options, ...change }) }, code))
  }
  assert.throws(() => transform.call({ getOptions: () => options }, code + ' '))
})

/** 模拟来源与真实来源明确区分，Mock 不订阅真实业务结果。 */
test('mock preview stays explicit and isolated', () => {
  const output = transform.call({ getOptions: () => ({ ...options, mode: 'official-mock' }) }, code)
  assert.match(output, /endpoint="\/__agent_ui_mock"/)
  assert.match(output, /agentId=\{undefined\}/)
  assert.match(output, /key="official-mock:/)
})

/** 两类 endpoint 改写与观察在同一次受检编译中组合，均不增加发送 owner。 */
test('both preview sources compose read-only observation after endpoint validation', () => {
  for (const mode of ['real-text', 'official-mock']) {
    const output = transform.call({ getOptions: () => ({ ...options, mode, observe: true }) }, code)
    assert.match(output, /<AgentUiPreviewObserver epoch="aaaa/)
    assert.ok(output.includes(`source="${mode}"`))
    assert.doesNotMatch(output, /fetch\(|new HttpAgent|\.sendMessage\(|\.resumeInterrupts\(/)
  }
})
