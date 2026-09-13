import assert from 'node:assert/strict'
import {
  applicationPlanningRecoveryProjection,
  parseRecoveryActionPlan
} from '../src/renderer/src/service/applicationPlanningRecovery'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

/** 构造只携带 Backend Recovery Projection 的最小 Workflow。 */
function recoveryWorkflow(value: Record<string, unknown>): WorkflowRunPayload {
  return {
    runId: 'run-A',
    threadId: 'planning-thread',
    summary: { status: 'failed', message: '规划中断' },
    events: [],
    state: {},
    result: { applicationPlanningRecovery: value }
  } as WorkflowRunPayload
}

const ready = applicationPlanningRecoveryProjection(
  recoveryWorkflow({
    schemaVersion: 'application-planning-recovery.v1',
    classification: 'ready_to_continue',
    sourceRunId: 'run-A',
    threadId: 'planning-thread',
    canContinue: true,
    userActionRequired: false,
    inputCommitted: true,
    reasonCode: 'INPUT_COMMITTED_EXECUTION_INTERRUPTED',
    message: '回答已保存，可以继续。',
    recoveryActionPlan: {
      schemaVersion: 'recovery-action-plan.v1',
      incidentId: 'incident-restart',
      sourceRunId: 'run-A',
      threadId: 'planning-thread',
      executionKind: 'application_planning',
      status: 'recoverable',
      reasonCode: 'INPUT_COMMITTED_EXECUTION_INTERRUPTED',
      message: '已验证正式规划输入，可以重新执行技术规划。',
      primaryAction: {
        actionId: 'action-restart',
        kind: 'restart_stage',
        label: '重新执行技术规划',
        description: '从 Technical Planning 阶段重新执行。',
        requiresConfirmation: false
      },
      alternateActions: []
    }
  })
)

assert.equal(ready?.classification, 'ready_to_continue')
assert.equal(ready?.sourceRunId, 'run-A')
assert.equal(ready?.inputCommitted, true)
assert.equal(ready?.recoveryActionPlan?.primaryAction?.kind, 'restart_stage')
assert.equal(ready?.recoveryActionPlan?.primaryAction?.label, '重新执行技术规划')

const diagnosticReady = applicationPlanningRecoveryProjection(
  recoveryWorkflow({
    schemaVersion: 'application-planning-recovery.v1',
    classification: 'ready_to_continue',
    sourceRunId: 'run-B',
    threadId: 'planning-thread',
    canContinue: true,
    userActionRequired: false,
    inputCommitted: true,
    reasonCode: 'INPUT_COMMITTED_EXECUTION_INTERRUPTED',
    message: '回答已保存，可以继续。',
    recoveryActionPlan: {
      schemaVersion: 'recovery-action-plan.v1',
      incidentId: 'incident-continue',
      sourceRunId: 'run-B',
      threadId: 'planning-thread',
      executionKind: 'application_planning',
      status: 'recoverable',
      reasonCode: 'INPUT_COMMITTED_EXECUTION_INTERRUPTED',
      message: '已找到可验证的恢复入口，可以继续执行。',
      primaryAction: {
        actionId: 'action-continue',
        kind: 'continue_checkpoint',
        label: '继续执行',
        description: '从安全 checkpoint 继续执行。',
        requiresConfirmation: false
      },
      alternateActions: []
    },
    failureDiagnostic: {
      sourceRunId: 'run-B',
      origin: 'model_call',
      code: 'MODEL_CONNECTION_ERROR',
      operation: 'technical_planning',
      provider: 'openai-compatible',
      model: 'mimo-v2.5-pro',
      httpStatus: 503,
      message: 'model unavailable'
    }
  })
)
assert.equal(diagnosticReady?.failureDiagnostic?.sourceRunId, 'run-B')
assert.equal(diagnosticReady?.failureDiagnostic?.httpStatus, 503)
assert.equal(diagnosticReady?.failureDiagnostic?.message, 'model unavailable')
assert.equal(diagnosticReady?.recoveryActionPlan?.primaryAction?.kind, 'continue_checkpoint')

assert.equal(
  parseRecoveryActionPlan(
    {
      schemaVersion: 'recovery-action-plan.v1',
      incidentId: 'incident-mismatch',
      sourceRunId: 'run-B',
      threadId: 'other-thread',
      executionKind: 'application_planning',
      status: 'recoverable',
      reasonCode: 'TEST',
      message: '不能接受',
      primaryAction: {
        actionId: 'action',
        kind: 'continue_checkpoint',
        label: '继续执行',
        description: '继续',
        requiresConfirmation: false
      },
      alternateActions: []
    },
    { threadId: 'planning-thread', sourceRunId: 'run-B' }
  ),
  undefined
)
assert.equal(
  parseRecoveryActionPlan(
    {
      schemaVersion: 'recovery-action-plan.v1',
      incidentId: 'incident-mismatch',
      sourceRunId: 'other-run',
      threadId: 'planning-thread',
      executionKind: 'application_planning',
      status: 'recoverable',
      reasonCode: 'TEST',
      message: '不能接受',
      primaryAction: {
        actionId: 'action',
        kind: 'continue_checkpoint',
        label: '继续执行',
        description: '继续',
        requiresConfirmation: false
      },
      alternateActions: []
    },
    { threadId: 'planning-thread', sourceRunId: 'run-B' }
  ),
  undefined
)
assert.equal(
  parseRecoveryActionPlan(
    {
      schemaVersion: 'recovery-action-plan.v1',
      incidentId: 'incident-no-action',
      sourceRunId: 'run-B',
      threadId: 'planning-thread',
      executionKind: 'application_planning',
      status: 'recoverable',
      reasonCode: 'TEST',
      message: '不能接受',
      primaryAction: null,
      alternateActions: []
    },
    { threadId: 'planning-thread', sourceRunId: 'run-B' }
  ),
  undefined
)
assert.equal(
  parseRecoveryActionPlan(
    {
      schemaVersion: 'recovery-action-plan.v1',
      incidentId: 'incident-bad-kind',
      sourceRunId: 'run-B',
      threadId: 'planning-thread',
      executionKind: 'application_planning',
      status: 'recoverable',
      reasonCode: 'TEST',
      message: '不能接受',
      primaryAction: {
        actionId: 'action',
        kind: 'invented_action',
        label: '继续执行',
        description: '继续',
        requiresConfirmation: false
      },
      alternateActions: []
    },
    { threadId: 'planning-thread', sourceRunId: 'run-B' }
  ),
  undefined
)
assert.equal(
  applicationPlanningRecoveryProjection(
    recoveryWorkflow({
      schemaVersion: 'application-planning-recovery.v0',
      classification: 'ready_to_continue'
    })
  ),
  undefined
)

console.log('application planning recovery tests passed')
