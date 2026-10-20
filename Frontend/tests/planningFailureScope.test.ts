import assert from 'node:assert/strict'
import { test } from 'node:test'
import { planningConnectionForSession, planningFailureStateForSession, planningSnapshotForStage } from '../src/renderer/src/components/AiChatPanel/planningFailureScope'

test('计划会话晚绑定可接入技术中断快照，但不接入上游 UI 确认和其他阶段快照', () => {
  const failed = { runId: 'failed', summary: { phase: 'technical_planning', status: 'failed' },
    state: { phase: 'technical_planning_generate' } } as WorkflowRunPayload
  assert.equal(planningSnapshotForStage(failed, 'planning'), failed)
  assert.equal(planningSnapshotForStage(failed, 'product'), undefined)
  const ui = { ...failed, state: { phase: 'ui_confirmation' }, summary: { ...failed.summary, phase: 'ui_confirmation' } }
  assert.equal(planningSnapshotForStage(ui, 'planning'), undefined)
  assert.equal(planningSnapshotForStage(ui, 'product'), ui)
})
import { resolveWorkflowForDisplay } from '../src/renderer/src/components/AiChatPanel/planExecutionMode'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import type { ApplicationPlanningCurrentState } from '../src/renderer/src/service/activeApplicationPlanning'
import type { SessionIdentity } from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

/** 构造共享 Graph thread 下的会话与后端失败，模拟阶段切换和冷进入。 */
function fixture() {
  const workflow = { runId: 'failed-run', threadId: 'shared-thread' } as WorkflowRunPayload
  const state = {
    application: { id: 'app' }, workflow, error: '计划生成失败',
    recoveryActionError: '重试失败', syncError: '同步失败',
    recovery: { sourceRunId: workflow.runId }
  } as ApplicationPlanningCurrentState
  state.threadId = workflow.threadId
  const session = {
    sessionId: 'plan-session', workflowId: 'app', workbenchPhase: 'planning'
  } as SessionIdentity
  return { state, session, sessionWorkflow: workflow, currentPhase: 'planning', executionPhase: 'planning' }
}

test('确认断线不因旧恢复源或缺失 checkpoint 隐藏，同线程重试失败仍可见', () => {
  const input = fixture()
  const connection = { status: 'unavailable' as const, requestGeneration: 3, lastError: 'Failed to fetch' }
  const state = { ...input.state, connection,
    recovery: { ...input.state.recovery!, sourceRunId: 'previous-run' } }
  assert.equal(planningFailureStateForSession({ ...input, state }), undefined)
  assert.equal(planningConnectionForSession({ ...input, state }), connection)
  assert.equal(planningConnectionForSession({ ...input, state: { ...state, checkpointMissing: true } }), connection)
  assert.equal(planningConnectionForSession({ ...input, state: { ...state,
    connection: { ...connection, requestGeneration: 4 } } })?.status, 'unavailable')
  assert.equal(planningConnectionForSession({ ...input, state,
    sessionWorkflow: { ...input.sessionWorkflow, threadId: 'another-thread' } }), connection)
  assert.equal(planningConnectionForSession({ ...input, state,
    session: { ...input.session, workflowId: 'another-app' } }), undefined)
  assert.equal(planningConnectionForSession({ ...input, state, currentPhase: 'product' }), connection)
})

test('当前执行所属计划会话保留失败和重试状态', () => {
  const input = fixture()
  assert.equal(planningFailureStateForSession(input), input.state)
})

test('独立 Planning 流已写入本轮失败消息时，旧聊天 Run 缓存不能遮住恢复入口', () => {
  const input = fixture()
  const stale = { ...input.sessionWorkflow, runId: 'old-confirmed-run' }
  const messages = [{ id: 1, role: 'assistant', content: '', workflow: stale },
    { id: 2, role: 'assistant', content: '', workflow: input.sessionWorkflow }] as AgentChatMessage[]
  assert.equal(planningFailureStateForSession({ ...input,
    sessionWorkflow: resolveWorkflowForDisplay(stale, messages) }), undefined)
  assert.equal(planningFailureStateForSession({ ...input,
    sessionWorkflow: resolveWorkflowForDisplay(undefined, messages) }), input.state)
})

test('设计与计划共享线程也不能串用失败，切回所属阶段仍可重试', () => {
  const input = fixture()
  assert.equal(planningFailureStateForSession({ ...input, currentPhase: 'product',
    session: { ...input.session, workbenchPhase: 'product' } }), undefined)
  assert.equal(planningFailureStateForSession(input), input.state)
  const design = { ...input, currentPhase: 'product', executionPhase: 'product',
    session: { ...input.session, workbenchPhase: 'product' as const } }
  assert.equal(planningFailureStateForSession(design), input.state)
  assert.equal(planningFailureStateForSession({ ...design, currentPhase: 'planning',
    session: input.session }), undefined)
})

test('无本地历史仍展示后端恢复入口，其他执行历史保持隔离', () => {
  const input = fixture()
  assert.equal(planningFailureStateForSession({ ...input, session: undefined }), input.state)
  assert.equal(planningFailureStateForSession({ ...input, sessionWorkflow: undefined }), input.state)
  assert.equal(planningFailureStateForSession({ ...input,
    sessionWorkflow: { ...input.sessionWorkflow, runId: 'other-run' } }), undefined)
  assert.equal(planningFailureStateForSession({ ...input,
    sessionWorkflow: { ...input.sessionWorkflow, threadId: 'other-thread' } }), undefined)
  assert.equal(planningFailureStateForSession({ ...input,
    session: { ...input.session, workflowId: 'other-app' } }), undefined)
})

test('无存档以及恢复源 Run 与会话执行不一致时不显示旧错误', () => {
  const input = fixture()
  assert.equal(planningFailureStateForSession({ ...input,
    state: { ...input.state, checkpointMissing: true } }), undefined)
  assert.equal(planningFailureStateForSession({ ...input, state: {
    ...input.state, recovery: { ...input.state.recovery!, sourceRunId: 'different-source' }
  } }), undefined)
})
