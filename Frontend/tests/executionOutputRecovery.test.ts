import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mergeExecutionOutput } from '../src/renderer/src/components/AiChatPanel/executionOutputRecovery'
import { latestApplicationLifecycle } from '../src/renderer/src/service/activeApplicationPlanning'
import type { ApplicationLifecycle } from '../src/renderer/src/typings'
import type { SessionIdentity } from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'

/** 构造同一已接受请求的展示恢复记录，不以界面快照授权任何业务动作。 */
function fixture(sequence = 1, finished = false): ApplicationLifecycle {
  return { application: { id: 'app' }, revision: 1, initialization: {}, activeExecutions: {}, extensions: {
    recoverySnapshot: { applicationId: 'app', threadId: 'thread', sessionId: 'session', kind: 'session', lifecycleRevision: 1,
      checkpointAvailable: true, checkpointId: 'checkpoint', observedAt: '2026-11-08T00:00:03Z',
      workflow: { runId: 'run', threadId: 'thread', summary: { status: finished ? 'completed' : 'running' }, events: [], state: {} },
      output: { requestId: 'request', runId: 'run', threadId: 'thread', ownerSessionId: 'session',
      startedAt: '2026-11-08T00:00:01Z', submissionOrder: 1, sequence, finished,
      content: '真实后台回复', toolCalls: [], processSteps: [] } }
  } } as unknown as ApplicationLifecycle
}
const identity = { workflowId: 'app', sessionId: 'session', threadId: 'thread' } as SessionIdentity

test('冷恢复按提交身份更新原回复并保留用户输入，不重复追加消息', () => {
  const messages: AgentChatMessage[] = [
    { id: 1, role: 'user', content: '原提问', createdAt: Date.parse('2026-11-08T00:00:00Z'), executionRequestId: 'request' },
    { id: 2, role: 'assistant', content: '', createdAt: Date.parse('2026-11-08T00:00:00Z'), executionRequestId: 'request' }
  ]
  const restored = mergeExecutionOutput(messages, fixture(2, true), identity)
  assert.equal(restored.length, 2)
  assert.equal(restored[1].id, 2)
  assert.equal(restored[1].content, '真实后台回复')
  assert.equal(mergeExecutionOutput(restored, fixture(2, true), identity), restored)
  assert.equal(mergeExecutionOutput(messages, fixture(), { ...identity, sessionId: 'other' }), messages)
})

test('同业务 revision 的后台输出可以完成，迟到序号不覆盖新结果', () => {
  const current = fixture(1)
  const finished = latestApplicationLifecycle(current, fixture(2, true))
  assert.equal(finished.extensions.recoverySnapshot?.output?.finished, true)
  assert.equal(latestApplicationLifecycle(finished, current).extensions.recoverySnapshot?.output?.finished, true)
})

test('后来明确发出的新消息不能被旧任务结果覆盖', () => {
  const messages: AgentChatMessage[] = [{ id: 3, role: 'user', content: '新提问', createdAt: Date.parse('2026-11-08T00:00:02Z'), executionRequestId: 'new' }]
  assert.equal(mergeExecutionOutput(messages, fixture(2, true), identity), messages)
})

test('恢复结果不能复活已经结束的旧确认，也不能覆盖另一个当前 Run', () => {
  const lifecycle = fixture(2, true)
  lifecycle.extensions.recoverySnapshot!.workflow = { ...lifecycle.extensions.recoverySnapshot!.workflow!,
    summary: { status: 'completed', phase: 'ended', message: '任务已结束', planControl: { action: 'end', status: 'ended' } } }
  const messages = mergeExecutionOutput([], lifecycle, identity)
  assert.equal(messages[0].workflow?.summary.phase, 'ended')
  assert.equal(messages[0].content, '任务已结束')
  lifecycle.extensions.recoverySnapshot!.workflow = { ...lifecycle.extensions.recoverySnapshot!.workflow!, runId: 'new-run' }
  assert.equal(mergeExecutionOutput(messages, lifecycle, identity), messages)
})
