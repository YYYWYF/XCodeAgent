import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'
import { createRequire } from 'node:module'
import { createHash } from 'node:crypto'

const source = await readFile(new URL('../../../Backend/app/services/agent_ui_preview_observer.tsx', import.meta.url), 'utf8')
const ast = ts.createSourceFile('observer.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
// 纯观察函数不执行 React 组件；去除运行时 imports 后使用同一实现验证公开 Host 边界。
const pure = ts.createPrinter().printList(ts.ListFormat.MultiLine,
  ts.factory.createNodeArray(ast.statements.filter(statement => !ts.isImportDeclaration(statement))), ast)
const compiled = ts.transpileModule(pure, { compilerOptions: { module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.React } })
const { observePreviewHost } = await import('data:text/javascript;base64,' + Buffer.from(compiled.outputText).toString('base64'))

/** 只提供只读公共 seam，任何额外发送/查询方法均不存在。 */
function fixture() {
  let snapshot = { status: 'ready', runtime: { conversation: { id: 'thread-a' },
    run: { status: 'idle' }, messages: [{ content: 'private-message' }], interrupts: [] } }
  const changes = new Set(); const events = new Map(); const values = []
  const host = { getSnapshot: () => snapshot,
    subscribe: fn => { changes.add(fn); return () => changes.delete(fn) },
    on: (name, fn) => { events.set(name, fn); return () => events.delete(name) },
    onAppEvent: (name, fn) => { events.set(name, fn); return () => events.delete(name) } }
  const dispose = observePreviewHost(host, value => values.push(value))
  return { values, events, dispose,
    set: value => { snapshot = value; for (const fn of changes) fn() },
    snapshot: () => snapshot,
    count: () => changes.size + events.size }
}

test('observer publishes bounded metadata without payload or invented run identity', () => {
  const f = fixture()
  try {
    assert.equal(f.values.at(-1).runId, null)
    for (let i = 0; i < 50; i++) f.events.get('agent_runtime_result.v1')({
      producer: { type: 'root' }, payload: { threadId: 'thread-a', runId: 'untrusted-run', secret: 'private-token' } })
    const last = f.values.at(-1)
    assert.equal(last.events.length, 20)
    assert.equal(last.eventCount, 50)
    assert.equal(last.runId, null)
    assert.ok(last.events.every(event => event.runVerified === false))
    assert.doesNotMatch(JSON.stringify(last), /private-message|private-token|untrusted-run/)
  } finally { f.dispose() }
  assert.equal(f.count(), 0)
})

test('thread switch and disposal fence late events without replaying or sending', () => {
  const f = fixture()
  const late = f.events.get('agent_runtime_error.v1')
  f.set({ ...f.snapshot(), runtime: { ...f.snapshot().runtime, conversation: { id: 'thread-b' } } })
  late({ producer: { type: 'root' }, payload: { threadId: 'thread-a', runId: 'old' } })
  assert.equal(f.values.at(-1).threadId, 'thread-b')
  assert.equal(f.values.at(-1).events.length, 0)
  const count = f.values.length
  f.dispose()
  late({ producer: { type: 'root' }, payload: { threadId: 'thread-b' } })
  assert.equal(f.values.length, count)
})

/** 当前公开快照具备可信 run 时，只接受该身份，不把旧事件用于新运行。 */
test('canonical run identity filters late events instead of inferring it from payloads', () => {
  const f = fixture()
  try {
    f.set({ ...f.snapshot(), runtime: { ...f.snapshot().runtime, run: { id: 'current-run', status: 'running' } } })
    const event = f.events.get('agent_runtime_result.v1')
    event({ producer: { type: 'root' }, payload: { threadId: 'thread-a', runId: 'old-run' } })
    assert.equal(f.values.at(-1).eventCount, 0)
    event({ producer: { type: 'child' }, payload: { threadId: 'thread-a', runId: 'current-run' } })
    assert.equal(f.values.at(-1).eventCount, 0)
    event({ producer: { type: 'root' }, payload: { threadId: 'thread-a', runId: 'current-run' } })
    assert.equal(f.values.at(-1).events.at(-1).runVerified, true)
    assert.equal(f.values.at(-1).runStatus, 'running')
  } finally { f.dispose() }
})

test('managed observation instruments the current Provider without changing its formal endpoint', () => {
  const require = createRequire(import.meta.url)
  const loader = require('../../../Backend/app/services/agent_ui_preview_observer.cjs')
  const code = '<SharedAgentApplication endpoint="/agents/assistant/run">{children}</SharedAgentApplication>'
  const options = { sha256: createHash('sha256').update(code).digest('hex'), sessionId: 'a'.repeat(32), mode: 'formal-text' }
  const output = loader.call({ getOptions: () => options }, code)
  assert.match(output, /endpoint="\/agents\/assistant\/run"/)
  assert.match(output, /<AgentUiPreviewObserver epoch=/)
  assert.doesNotMatch(output, /fetch\(|HttpAgent|sendMessage\(|resumeInterrupts\(/)
  assert.throws(() => loader.call({ getOptions: () => options }, code + ' '))
})
