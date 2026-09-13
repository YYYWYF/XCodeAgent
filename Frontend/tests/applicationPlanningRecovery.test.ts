import assert from 'node:assert/strict'
import {
  applicationPlanningRecoveryProjection
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
    message: '回答已保存，可以继续。'
  })
)

assert.equal(ready?.classification, 'ready_to_continue')
assert.equal(ready?.sourceRunId, 'run-A')
assert.equal(ready?.inputCommitted, true)

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
