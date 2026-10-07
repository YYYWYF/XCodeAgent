import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  ownedWorkbenchExecution,
  restoreWorkbenchPresentation,
  workbenchExecutionRunning
} from '../src/renderer/src/components/AiChatPanel/workbenchRunPresentation'
import { latestApplicationLifecycle } from '../src/renderer/src/service/activeApplicationPlanning'
import type { ApplicationLifecycle, WorkbenchExecution, ExecutionRecoveryCandidate } from '../src/renderer/src/typings'
import type { SessionIdentity } from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import type { DagGenerationSnapshot } from '../src/renderer/src/service/agUiAgent'

const session = { sessionId: 'session-1', workflowId: 'app-1' } as SessionIdentity

/** 构造无本地流连接、但后端仍在生成任务的重入状态。 */
function fixture(revision = 1): ApplicationLifecycle {
  const execution: WorkbenchExecution = {
    runId: 'run-1',
    threadId: 'graph-thread',
    ownerSessionId: session.sessionId,
    scope: 'page',
    targetId: 'orders',
    phase: 'prepare_build_tasks',
    status: 'running',
    startedAt: '2026-10-05T00:00:00Z',
    updatedAt: '2026-10-05T00:00:00Z'
  }
  const dag = {
    schemaVersion: 'dag-generation.v1',
    planningRunId: 'plan-1',
    revision,
    status: 'active',
    phase: 'generating_units',
    units: [],
    globalIssues: [],
    summary: {}
  } as DagGenerationSnapshot
  return {
    application: { id: 'app-1' },
    revision: 1,
    activeExecutions: { 'run-1': execution },
    extensions: { workbenchProgress: { 'run-1': { ...execution, dagGeneration: dag } } }
  } as ApplicationLifecycle
}

test('重入恢复运行状态和已有任务进度，重复读取不增加消息', () => {
  const lifecycle = fixture()
  assert.equal(workbenchExecutionRunning(ownedWorkbenchExecution(lifecycle, session)), true)
  const first = restoreWorkbenchPresentation([], lifecycle, session)
  assert.equal(first.workflow?.runId, 'run-1')
  assert.equal(first.messages[0].processSteps?.[0].dagGeneration?.revision, 1)
  const second = restoreWorkbenchPresentation(first.messages, fixture(2), session)
  assert.equal(second.messages.length, 1)
  assert.equal(second.messages[0].processSteps?.[0].dagGeneration?.revision, 2)
})

test('其它会话和错线程进度不能借用', () => {
  const lifecycle = fixture()
  assert.equal(
    restoreWorkbenchPresentation([], lifecycle, { sessionId: 'other' } as SessionIdentity).workflow,
    undefined
  )
  lifecycle.extensions.workbenchProgress!['run-1'].threadId = 'other-thread'
  assert.equal(
    restoreWorkbenchPresentation([], lifecycle, session).messages[0].processSteps?.[0]
      .dagGeneration,
    undefined
  )
})

test('失败收口运行态，已完成任务不重新显示执行中', () => {
  const lifecycle = fixture()
  lifecycle.activeExecutions!['run-1'].status = 'failed'
  assert.equal(workbenchExecutionRunning(ownedWorkbenchExecution(lifecycle, session)), false)
  assert.equal(
    restoreWorkbenchPresentation([], lifecycle, session).workflow?.summary.status,
    'failed'
  )
  lifecycle.activeExecutions!['run-1'].status = 'completed'
  assert.equal(restoreWorkbenchPresentation([], lifecycle, session).workflow, undefined)
})

test('Durable 已确认中断时，旧 running 不得继续阻塞恢复入口', () => {
  const lifecycle = fixture()
  lifecycle.extensions.executionRecovery = {
    schemaVersion: 'execution-recovery.v1', generatedAt: '2026-10-05T00:01:00Z',
    candidates: [{ executionKind: 'workbench', sourceRunId: 'run-1', ownerSessionId: session.sessionId, threadId: 'graph-thread', executionStatus: 'interrupted' } as ExecutionRecoveryCandidate]
  }
  assert.equal(workbenchExecutionRunning(ownedWorkbenchExecution(lifecycle, session)), false)
  assert.equal(restoreWorkbenchPresentation([], lifecycle, session).workflow, undefined)
})

test('相同 lifecycle revision 的进度可更新，迟到读取不能倒退或复活旧 Run', () => {
  const merged = latestApplicationLifecycle(fixture(1), fixture(4))
  assert.equal(merged.extensions.workbenchProgress!['run-1'].dagGeneration?.revision, 4)
  const late = latestApplicationLifecycle(merged, fixture(2))
  assert.equal(late.extensions.workbenchProgress!['run-1'].dagGeneration?.revision, 4)
  const ended = { ...fixture(), revision: 2, activeExecutions: {}, extensions: {} }
  assert.deepEqual(latestApplicationLifecycle(late, ended).extensions.workbenchProgress, {})
  assert.equal(latestApplicationLifecycle(ended, fixture()), ended)
})

test('重复或较旧的同运行进度保留生命周期引用，真实新进度仍更新', () => {
  const current = fixture(4)
  assert.equal(latestApplicationLifecycle(current, fixture(4)), current)
  assert.equal(latestApplicationLifecycle(current, fixture(2)), current)
  const newer = latestApplicationLifecycle(current, fixture(5))
  assert.notEqual(newer, current)
  assert.equal(newer.extensions.workbenchProgress!['run-1'].dagGeneration?.revision, 5)
})

test('其他会话的迟到进度不能清掉当前运行的有效进度', () => {
  const current = fixture(4)
  const foreign = fixture(5)
  foreign.extensions.workbenchProgress!['run-1'].ownerSessionId = 'other-session'
  assert.equal(latestApplicationLifecycle(current, foreign), current)
})
