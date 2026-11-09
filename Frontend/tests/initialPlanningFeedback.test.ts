import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ApplicationPlanningRuntime } from '../src/renderer/src/service/applicationPlanningRuntime'
import { reduceApplicationPlanningCurrentState, type ApplicationPlanningCurrentState } from '../src/renderer/src/service/activeApplicationPlanning'
import { planningStageAccess, stoppedPlanningAcceptsFeedback, stoppedPlanningForSession } from '../src/renderer/src/components/AiChatPanel/planningStageAccess'
import { initialConnectionState } from '../src/renderer/src/service/connectionState'
import { buildWorkflowForwardedProps, type SendWorkflowMessageOptions } from '../src/renderer/src/service/agUiAgent'
import type { ApplicationConfig, ApplicationLifecycle, WorkflowRunPayload } from '../src/renderer/src/typings'

test('首次创建和正式修订三阶段停止后，底部新意见复用恢复入口并展示新的确认门', async () => {
  for (const formal of [false, true]) for (const [stage, phase, artifact] of [
    ['generating_requirement_document', 'product_planning', 'requirement_document'],
    ['generating_ui_designs', 'ui_confirmation', 'ui_designs'],
    ['generating_technical_plan', 'technical_planning', 'technical_plan']
  ] as const) {
    let state: ApplicationPlanningCurrentState = {
      application: { id: 'feedback', workspaceRoot: '/workspace/feedback' } as ApplicationConfig,
      threadId: 'thread', transportState: 'idle', connection: initialConnectionState(true),
      lifecycle: { application: { id: 'feedback', name: '测试' }, revision: 1, updatedAt: '', activeExecutions: {}, extensions: {},
        initialization: { stage: formal ? 'ready_for_workbench' : stage, status: formal ? 'completed' : 'cancelled', threadId: 'thread' },
        endedPlanningExecution: { runId: 'stopped', threadId: 'thread', stage,
          canRestart: !formal, canSubmitFeedback: true, workspaceUnchanged: formal } },
      workflow: { threadId: 'thread', runId: 'stopped', events: [], state: {}, summary: {
        status: 'completed', phase: 'plan_control', planControl: { action: 'end', status: 'ended' } } }
    }
    const calls: SendWorkflowMessageOptions[] = []
    let reads = 0
    const runtime = new ApplicationPlanningRuntime({
      applicationId: 'feedback', threadId: 'thread', getCurrentState: () => state,
      dispatchCurrentEvent: (event) => { state = reduceApplicationPlanningCurrentState(state, event) },
      publishWorkflow: () => {}, publishContent: () => {}, publishReply: () => {},
      onTechnicalPlanConfirmed: async () => { throw new Error('不能自动确认') },
      onRevisionContinuation: async () => { throw new Error('首次创建不能进入开发续接') },
      readTechnicalPlanningReply: async () => { throw new Error('已停止首次生成的补充无需技术问答分类') },
      session: { hasActiveRun: () => false, stop: async () => {},
        sendMessage: async () => { throw new Error('不能消费停止前的审阅门') } },
      createRecoverySession: () => ({ sendMessage: async (message, options) => {
        assert.equal(message, '增加补充说明')
        calls.push(options)
        const lifecycle = { ...state.lifecycle, revision: 2, endedPlanningExecution: undefined, activeRunId: 'new',
          initialization: { ...state.lifecycle.initialization, stage, status: 'awaiting_user' } } as ApplicationLifecycle
        const workflow = { threadId: 'thread', runId: 'new', events: [], summary: {
          status: 'requires_user_input', phase, lifecycle }, state: { lifecycle,
          application_planning_interrupt: { artifact, gateId: 'new-gate', artifactRevision: 'new-revision' } } } as WorkflowRunPayload
        return { threadId: 'thread', runId: 'new', answer: '', workflow, toolCalls: [], processSteps: [] }
      } }),
      readAuthoritativeSnapshot: async () => {
        reads += 1
        return { workflow: state.workflow!, lifecycle: state.lifecycle, recovery: {
          schemaVersion: 'application-planning-recovery.v1', classification: 'awaiting_user', sourceRunId: 'new', threadId: 'thread',
          canContinue: false, userActionRequired: true, inputCommitted: false, reasonCode: '', message: '', recoveryActionPlan: null
        } }
      }
    })
    const stopped = stoppedPlanningForSession({ lifecycle: state.lifecycle, session: { threadId: 'thread' },
      phase: stage === 'generating_technical_plan' ? 'planning' : 'product' })
    assert.equal(stoppedPlanningAcceptsFeedback(stopped), true, stage)
    const access = { lifecycle: state.lifecycle, enteredDevelopment: formal,
      phase: stage === 'generating_technical_plan' ? 'planning' as const : 'product' as const }
    assert.equal(planningStageAccess({ ...access, sessionThreadId: 'thread' }).historyOnly, false)
    assert.equal(planningStageAccess({ ...access, sessionThreadId: 'old-thread' }).historyOnly, true)
    await runtime.submitClarification(state.workflow!, {}, undefined, undefined, '增加补充说明', stage === 'generating_technical_plan')
    assert.equal(calls.length, 1, stage)
    assert.deepEqual(calls[0].executionRecovery, { action: formal ? 'continue' : 'restart_initial_planning', sourceRunId: 'stopped', feedback: { request: '增加补充说明' } })
    assert.deepEqual(buildWorkflowForwardedProps(calls[0]).executionRecovery, calls[0].executionRecovery)
    assert.equal(state.workflow?.state?.application_planning_interrupt?.gateId, 'new-gate')
    assert.equal(state.workflow?.summary.status, 'requires_user_input')
    assert.equal(state.lifecycle.endedPlanningExecution, undefined)
    assert.equal(reads, 1)
    runtime.dispose()
  }
})

test('未获新意见资格的停止阶段和历史会话保持原入口权限', () => {
  assert.equal(stoppedPlanningAcceptsFeedback(undefined), false)
  assert.equal(stoppedPlanningAcceptsFeedback({ runId: 'r', threadId: 't', stage: 'generating_ui_designs',
    workspaceUnchanged: true, canRestart: false }), false)
  assert.equal(stoppedPlanningAcceptsFeedback({ runId: 'r', threadId: 't', stage: 'generating_application_template_files',
    workspaceUnchanged: true, canRestart: true }), false)
})
