import assert from 'node:assert/strict'
import type { ApplicationLifecycle, WorkflowDesignStageRevisionStart, WorkflowRunPayload } from '../src/renderer/src/typings'
import type { ApplicationPlanningAuthoritativeSnapshot } from '../src/renderer/src/service/applicationPlanningRecovery'
import type { SessionIdentity } from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import { pendingFormalRevisionStart, retryFormalRevisionStart, technicalRevisionStageEntryReady } from '../src/renderer/src/components/AiChatPanel/formalRevisionStartRetry'

const session = {
  workflowId: 'app', threadId: 'graph', workbenchPhase: 'product', revisionContext: {
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

// 已交接技术专项按精确来源读取当前 Backend Graph，展示会话可拥有独立 Thread。
const handedOffPlanSession = { ...planSession, threadId: 'target-graph', revisionContext: {
  ...planSession.revisionContext!, planningThreadId: 'source-checkpoint'
} } as SessionIdentity
const handedOffLifecycle = { ...active, initialization: { ...active.initialization, threadId: 'target-graph' },
  activeFormalRevision: { ...active.activeFormalRevision!, formalBranch: 'workbench_plan_revision', planningThreadId: 'target-graph' }
} as ApplicationLifecycle
let continued = 0
await retryFormalRevisionStart({
  session: handedOffPlanSession, read: async () => ({ lifecycle: handedOffLifecycle }),
  onLifecycle: () => {}, start: async () => { assert.fail('已接纳修订不能重新提交') },
  recover: () => { continued += 1 }
})
assert.equal(continued, 1)
await assert.rejects(retryFormalRevisionStart({
  session: { ...handedOffPlanSession, revisionContext: { ...handedOffPlanSession.revisionContext!, sourceRunId: 'other-source' } },
  read: async () => ({ lifecycle: handedOffLifecycle }), onLifecycle: () => {},
  start: async () => { assert.fail('其他来源不能启动') }, recover: () => { assert.fail('其他来源不能恢复') }
}), /应用或规划会话已变化/)
// 线程已交接、节点 started 仍携带旧就绪态时不切换；begin 成功并同步界面后才切换。
const readyLifecycle = { ...handedOffLifecycle, activeRunId: 'revision-run', initialization: {
  ...handedOffLifecycle.initialization, stage: 'ready_for_workbench'
} } as ApplicationLifecycle
const begunLifecycle = { ...readyLifecycle, initialization: {
  ...readyLifecycle.initialization, stage: 'generating_technical_plan'
} } as ApplicationLifecycle
const begunWorkflow = { runId: 'revision-run', threadId: 'target-graph',
  summary: { phase: 'technical_planning_begin', status: 'running' },
  state: { lifecycle: begunLifecycle }, events: []
} as WorkflowRunPayload
assert.equal(technicalRevisionStageEntryReady(handedOffPlanSession, {
  ...begunWorkflow, state: { lifecycle: readyLifecycle },
  events: [{ type: 'workflow.node.started', nodeName: 'technical_planning_begin' }]
} as WorkflowRunPayload, readyLifecycle), false)
assert.equal(technicalRevisionStageEntryReady(handedOffPlanSession, begunWorkflow, readyLifecycle), false)
assert.equal(technicalRevisionStageEntryReady(handedOffPlanSession, begunWorkflow, begunLifecycle), true)
// 其他线程、Run、来源或旧修订不能触发本轮跳转。
assert.equal(technicalRevisionStageEntryReady(handedOffPlanSession, { ...begunWorkflow, runId: 'old-run' }, begunLifecycle), false)
assert.equal(technicalRevisionStageEntryReady(handedOffPlanSession, { ...begunWorkflow, threadId: 'other' }, begunLifecycle), false)
assert.equal(technicalRevisionStageEntryReady({ ...handedOffPlanSession, revisionContext: {
  ...handedOffPlanSession.revisionContext!, sourceRunId: 'other'
} }, begunWorkflow, begunLifecycle), false)
assert.equal(technicalRevisionStageEntryReady(handedOffPlanSession, begunWorkflow, {
  ...begunLifecycle, activeFormalRevision: { ...begunLifecycle.activeFormalRevision!, changeId: 'other' }
}), false)
const awaitingLifecycle = { ...begunLifecycle, initialization: {
  ...begunLifecycle.initialization, stage: 'awaiting_technical_plan_confirmation'
} } as ApplicationLifecycle
assert.equal(technicalRevisionStageEntryReady(handedOffPlanSession, {
  ...begunWorkflow, state: { lifecycle: awaitingLifecycle }
}, awaitingLifecycle), true)
console.log('formal revision start retry tests passed')
