import assert from 'node:assert/strict'
import type { ApplicationLifecycle, WorkflowDesignStageRevisionStart } from '../src/renderer/src/typings'
import type { ApplicationPlanningAuthoritativeSnapshot } from '../src/renderer/src/service/applicationPlanningRecovery'
import type { SessionIdentity } from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import { pendingFormalRevisionStart, retryFormalRevisionStart } from '../src/renderer/src/components/AiChatPanel/formalRevisionStartRetry'

const session = {
  workflowId: 'app', workbenchPhase: 'product', revisionContext: {
    kind: 'formal_revision', sessionRole: 'design', formalBranch: 'design_stage_revision',
    impactInteractionId: 'impact', sourceSessionId: 'source-session',
    sourceConversationThreadId: 'source-thread', sourceRunId: 'source-run', planningThreadId: 'graph'
  }
} as SessionIdentity
const lifecycle = {
  application: { id: 'app' }, initialization: { threadId: 'graph' }, activeFormalRevision: null,
  pendingRevisionImpact: {
    interactionId: 'impact', status: 'pending', sourceThreadId: 'source-thread', sourceRunId: 'source-run',
    request: '只需要一个自己录入自己查看的页面', target: { type: 'application' },
    impact: { formalBranch: 'design_stage_revision', revisionType: 'requirement_scope_change',
      earliestArtifact: 'requirement-spec', affectedArtifacts: ['requirement-spec', 'product-plan'] }
  }
} as unknown as ApplicationLifecycle
const completedSnapshot = {
  lifecycle, workflow: { threadId: 'graph', state: { phase: 'completed' }, summary: {} },
  recovery: { classification: 'completed' }
} as ApplicationPlanningAuthoritativeSnapshot

/** 隔离派发回调，验证旧 Graph 已完成时补交原确认，避免恢复无操作。 */
async function retry(snapshot = completedSnapshot, current = lifecycle): Promise<{ started: WorkflowDesignStageRevisionStart[]; recovered: number }> {
  const result = { started: [] as WorkflowDesignStageRevisionStart[], recovered: 0 }
  await retryFormalRevisionStart({
    session, read: async () => ({ snapshot, lifecycle: current }), onLifecycle: () => {},
    start: async request => { result.started.push(request) }, recover: () => { result.recovered += 1 }
  })
  return result
}

const submitted = await retry()
assert.equal(submitted.recovered, 0)
assert.equal(submitted.started.length, 1)
assert.deepEqual(submitted.started[0], {
  request: lifecycle.pendingRevisionImpact!.request, target: lifecycle.pendingRevisionImpact!.target,
  impact: { ...(lifecycle.pendingRevisionImpact!.impact as object), interactionId: 'impact', status: 'pending' },
  sourceSessionId: 'source-session', sourceConversationThreadId: 'source-thread', sourceRunId: 'source-run'
})

// Backend 已接受确认或已写入入口 checkpoint 时，只恢复执行，不重复提交。
const active = { ...lifecycle, activeFormalRevision: {
  ...session.revisionContext!, sourceThreadId: 'source-thread', changeId: 'change', status: 'design_planning'
} } as ApplicationLifecycle
assert.deepEqual(await retry(completedSnapshot, active), { started: [], recovered: 1 })
const admitted = { ...completedSnapshot, workflow: {
  ...completedSnapshot.workflow, state: { phase: 'design_intent_analysis' }
}, recovery: { ...completedSnapshot.recovery, classification: 'failed' } } as ApplicationPlanningAuthoritativeSnapshot
assert.deepEqual(await retry(admitted), { started: [], recovered: 1 })

// 不同来源、应用或过期确认不能由旧设计会话重新提交。
for (const pending of [undefined, { ...lifecycle.pendingRevisionImpact, sourceRunId: 'other' },
  { ...lifecycle.pendingRevisionImpact, interactionId: 'other' }]) {
  const stale = { ...lifecycle, pendingRevisionImpact: pending } as ApplicationLifecycle
  assert.equal(pendingFormalRevisionStart(stale, session), undefined)
  await assert.rejects(retry(completedSnapshot, stale), /确认已失效/)
}
await assert.rejects(retry(completedSnapshot, { ...active, application: { ...active.application, id: 'other' } }), /应用或规划会话已变化/)
let mutated = false
await assert.rejects(retryFormalRevisionStart({
  session, read: async () => { throw new Error('offline') }, onLifecycle: () => {},
  start: async () => { mutated = true }, recover: () => { mutated = true }
}), /offline/)
assert.equal(mutated, false)

// 技术专项走 PLAN 会话，同一确认补交机制不能误退回 DESIGN。
const planSession = { ...session, workbenchPhase: 'planning', revisionContext: {
  ...session.revisionContext!, formalBranch: 'workbench_plan_revision'
} } as SessionIdentity
const planLifecycle = { ...lifecycle, pendingRevisionImpact: {
  ...lifecycle.pendingRevisionImpact, impact: { formalBranch: 'workbench_plan_revision' }
} } as ApplicationLifecycle
assert.equal(pendingFormalRevisionStart(planLifecycle, planSession)?.impact.formalBranch, 'workbench_plan_revision')
assert.equal(pendingFormalRevisionStart(planLifecycle, session), undefined)
console.log('formal revision start retry tests passed')
