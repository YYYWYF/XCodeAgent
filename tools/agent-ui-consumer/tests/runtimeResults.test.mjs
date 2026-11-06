import assert from 'node:assert/strict'
import { test } from 'node:test'
import path from 'node:path'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const source = await readFile(path.resolve(import.meta.dirname, '../../agent-ui-design/runtimeResults.ts'), 'utf8')
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
const { decodeRuntimeResult, observeRuntimeResults } = await import('data:text/javascript;base64,' + Buffer.from(compiled.outputText).toString('base64'))

/** 使用当前 Python 公开结果形状，默认不生成结构化业务输出。 */
function payload(overrides = {}) {
  return { schemaVersion: 1, agentId: 'assistant', threadId: 'thread-a', runId: 'run-a',
    status: 'completed', pendingInteraction: null, ...overrides }
}

/** 只提供公开只读订阅接口，观察器若尝试发送或恢复请求会直接失败。 */
function fixture() {
  let snapshot = { status: 'ready', runtime: { conversation: { id: 'thread-a' }, run: { status: 'idle' } } }
  const updates = new Set()
  const events = new Map()
  const values = []
  const host = {
    getSnapshot: () => snapshot,
    subscribe: (listener) => { updates.add(listener); return () => updates.delete(listener) },
    onAppEvent: (name, listener) => {
      const listeners = events.get(name) ?? new Set()
      events.set(name, listeners); listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
  const dispose = observeRuntimeResults(host, 'assistant', (value) => values.push(value))
  return {
    dispose, values,
    current: () => values.at(-1),
    activeListeners: () => updates.size + [...events.values()].reduce((sum, set) => sum + set.size, 0),
    set: (status, id = 'run-a', thread = 'thread-a') => {
      snapshot = { status: 'ready', runtime: { conversation: { id: thread }, run: { status, id } } }
      for (const listener of [...updates]) listener()
    },
    emit: (value, name = 'agent_runtime_result.v1', producer = { type: 'root' }) => {
      for (const listener of [...(events.get(name) ?? [])]) listener({ name, payload: value, producer })
    },
  }
}

/** 业务合同严格区分事件名、状态、必填字段和待交互内容。 */
test('current Python result and error envelopes are validated without coercion', () => {
  assert.equal(decodeRuntimeResult('agent_runtime_result.v1', payload()).status, 'completed')
  const error = { schemaVersion: 1, agentId: 'assistant', threadId: 'thread-a', runId: 'run-a',
    status: 'failed', error: { code: 'denied', message: '拒绝执行', recoverable: false } }
  assert.equal(decodeRuntimeResult('agent_runtime_error.v1', error).status, 'failed')
  const pending = { schemaVersion: 1, interactionId: 'approval-a', kind: 'confirmation', message: '确认？', payload: {} }
  assert.equal(decodeRuntimeResult('agent_runtime_result.v1', payload({ status: 'awaiting_user', pendingInteraction: pending })).status, 'awaiting_user')
  for (const value of [null, [], payload({ schemaVersion: '1' }), payload({ agentId: '' }),
    payload({ pendingInteraction: undefined }), payload({ status: 'awaiting_user' }),
    payload({ status: 'completed', pendingInteraction: pending }), payload({ extra: true }),
    payload({ status: 'awaiting_user', pendingInteraction: { ...pending, message: 1 } })]) {
    assert.equal(decodeRuntimeResult('agent_runtime_result.v1', value), null)
  }
  assert.equal(decodeRuntimeResult('agent_runtime_error.v1', payload()), null)
  assert.equal(decodeRuntimeResult('agent_runtime_result.v1', error), null)
  assert.equal(decodeRuntimeResult('other.v1', payload()), null)
})

/** 业务结果只有在匹配的运行结束后成为已完成证据，空终态仍为未知。 */
test('wire completion alone is unknown; a valid current result settles after run end', () => {
  const f = fixture()
  f.set('running')
  f.emit(payload())
  assert.equal(f.current().status, 'running')
  f.set('idle')
  assert.equal(f.current().status, 'completed')
  f.set('running', 'run-b')
  f.set('idle', 'run-b')
  assert.equal(f.current().status, 'unknown')
  f.dispose()
})

/** 旧身份、错运行、子 Agent 和畸形结果都不能提供当前运行的成功证据。 */
test('foreign, late, subagent and malformed events do not contaminate the current run', () => {
  const f = fixture()
  f.set('running')
  for (const overrides of [{ agentId: 'other' }, { threadId: 'other' }, { runId: 'old' }, { schemaVersion: 2 }]) f.emit(payload(overrides))
  f.emit(payload(), undefined, { type: 'subagent', id: 'child' })
  f.set('idle')
  assert.equal(f.current().status, 'unknown')
  f.emit(payload())
  assert.equal(f.current().status, 'unknown')
  f.set('idle', undefined, 'thread-b')
  assert.equal(f.current().status, 'idle')
  f.emit(payload())
  assert.equal(f.current().status, 'idle')
  f.dispose()
  assert.equal(f.activeListeners(), 0)
  const count = f.values.length
  f.set('running'); f.emit(payload()); f.set('idle')
  assert.equal(f.values.length, count)
})

/** 明确业务失败不同于断流；冲突终态或传输失败不能宣称成功。 */
test('business failure is distinct from transport failure and conflicting terminal events', () => {
  const f = fixture()
  const failure = { schemaVersion: 1, agentId: 'assistant', threadId: 'thread-a', runId: 'run-a',
    status: 'failed', error: { code: 'denied', message: '拒绝执行', recoverable: false } }
  f.set('running'); f.emit(failure, 'agent_runtime_error.v1'); f.set('idle')
  assert.equal(f.current().status, 'failed')
  f.set('running', 'run-b'); f.emit(payload({ runId: 'run-b' })); f.set('error', 'run-b')
  assert.equal(f.current().status, 'unknown')
  f.set('running'); f.emit(payload()); f.emit(failure, 'agent_runtime_error.v1'); f.set('idle')
  assert.equal(f.current().status, 'unknown')
  f.dispose()
})

/** 当前发行快照没有 run.id，不能信任事件载荷自己声明的运行身份。 */
test('missing public run identity keeps even a well-formed event unverified', () => {
  const f = fixture()
  f.set('running', '')
  assert.equal(f.current().status, 'running')
  f.emit(payload())
  f.set('idle', '')
  assert.equal(f.current().status, 'unknown')
  assert.equal(f.current().reason, 'run_identity_unavailable')
  f.set('running', '')
  assert.equal(f.current().status, 'running')
  f.set('idle', '')
  assert.equal(f.current().status, 'unknown')
  f.dispose()
})
