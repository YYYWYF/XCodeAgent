import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApplicationPlanningRuntime } from '../src/renderer/src/service/applicationPlanningRuntime'
import { reduceApplicationPlanningCurrentState, type ApplicationPlanningCurrentState } from '../src/renderer/src/service/activeApplicationPlanning'
import type { ApplicationConfig, WorkflowRunPayload } from '../src/renderer/src/typings'
import { initialConnectionState } from '../src/renderer/src/service/connectionState'
import type { SendWorkflowMessageOptions } from '../src/renderer/src/service/agUiAgent'

test('首次创建显式重生成先同步当前资格，再用独立 AG-UI 恢复入口并保留确认门', async () => {
  for (const canRestart of [true, false]) {
    let state: ApplicationPlanningCurrentState = {
      application: { id: 'app', appName: '测试', workspaceRoot: '/workspace' } as ApplicationConfig,
      threadId: 'planning', transportState: 'idle', connection: initialConnectionState(true),
      lifecycle: {
        application: { id: 'app', name: '测试' }, revision: 1, updatedAt: '', activeExecutions: {}, extensions: {},
        initialization: { stage: 'generating_technical_plan', status: 'cancelled', threadId: 'planning' },
        endedPlanningExecution: { runId: 'stopped', threadId: 'planning', stage: 'generating_technical_plan',
          workspaceUnchanged: false, canRestart }
      }
    }
    const workflow: WorkflowRunPayload = { runId: 'stopped', threadId: 'planning', events: [],
      summary: { status: 'cancelled', phase: 'technical_planning' }, state: {} }
    state.workflow = workflow
    const calls: SendWorkflowMessageOptions[] = []
    let reads = 0
    const runtime = new ApplicationPlanningRuntime({
      applicationId: 'app', threadId: 'planning', getCurrentState: () => state,
      dispatchCurrentEvent: (event) => { state = reduceApplicationPlanningCurrentState(state, event) },
      publishContent: () => {}, publishReply: () => {}, publishWorkflow: () => {},
      onTechnicalPlanConfirmed: async () => { throw new Error('重生成不能自动确认') },
      onRevisionContinuation: async () => { throw new Error('首次创建不能进入正式修订开发') },
      readAuthoritativeSnapshot: async () => {
        reads += 1
        return { workflow: state.workflow!, lifecycle: state.lifecycle, recovery: {
          schemaVersion: 'application-planning-recovery.v1', classification: calls.length ? 'awaiting_user' : 'cancelled',
          sourceRunId: state.workflow!.runId, threadId: 'planning', canContinue: false,
          userActionRequired: true, inputCommitted: false, reasonCode: 'stopped', message: '', recoveryActionPlan: null
        } }
      },
      session: { hasActiveRun: () => false, stop: async () => {},
        sendMessage: async () => { throw new Error('不能复用普通规划入口') } },
      createRecoverySession: () => ({ sendMessage: async (_message, options) => {
        assert.equal(reads, 1, '发送前必须同步当前 Backend 资格')
        calls.push(options)
        const pending = { ...workflow, runId: 'new-run',
          summary: { status: 'requires_user_input', phase: 'technical_planning' },
          state: { clarification: { mode: 'technical_plan_confirmation' },
            application_planning_interrupt: { gateId: 'new-gate', artifact: 'technical_plan', artifactRevision: 'new-version' } }
        } as WorkflowRunPayload
        return { threadId: 'planning', runId: 'new-run', answer: '', workflow: pending, toolCalls: [], processSteps: [] }
      } })
    })
    await runtime.retryCurrentFailure(true)
    assert.equal(calls.length, canRestart ? 1 : 0, JSON.stringify({ canRestart, reads,
      recoveryActionError: state.recoveryActionError, syncError: state.syncError, status: state.transportState,
      connectionError: state.connection.lastError }))
    if (canRestart) {
      assert.deepEqual(calls[0].executionRecovery, { action: 'restart_initial_planning', sourceRunId: 'stopped' })
      assert.equal(state.workflow?.summary.status, 'requires_user_input')
      assert.equal(state.workflow?.state?.application_planning_interrupt?.gateId, 'new-gate')
    } else assert.match(state.recoveryActionError || '', /没有可重新生成/)
  }
})
