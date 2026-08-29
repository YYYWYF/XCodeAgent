import assert from 'node:assert/strict'
import {
  assertChatSessionTargetBinding,
  assertChatSessionTargetType,
  nextStageSessionSequence,
  stageForWorkbenchPhase,
  withStageSessionCreationLock,
  type AgentStage
} from '../src/main/stageSessions'

assert.equal(stageForWorkbenchPhase('product'), 'DESIGN')
assert.equal(stageForWorkbenchPhase('planning'), 'PLAN')
assert.equal(stageForWorkbenchPhase('development'), 'DEVELOPMENT')
assert.equal(stageForWorkbenchPhase('test'), undefined)
assert.equal(assertChatSessionTargetType('page'), 'page')
assert.throws(() => assertChatSessionTargetType('unknown'))
assert.doesNotThrow(() => assertChatSessionTargetBinding('workflow', {}))
assert.doesNotThrow(() => assertChatSessionTargetBinding('page', { pageId: 'home' }))
assert.doesNotThrow(() =>
  assertChatSessionTargetBinding('api', { apiContractId: 'catalog', endpointId: 'list' })
)
assert.doesNotThrow(() => assertChatSessionTargetBinding('entity', { entityId: 'Product' }))
assert.throws(() => assertChatSessionTargetBinding('page', {}))
assert.throws(() =>
  assertChatSessionTargetBinding('api', {
    apiContractId: 'catalog',
    endpointId: 'list',
    pageId: 'home'
  })
)

const existing = [
  { workflowId: 'workflow-1', stage: 'DEVELOPMENT' as AgentStage, sequence: 1 },
  { workflowId: 'workflow-1', stage: 'DEVELOPMENT' as AgentStage, sequence: 3 },
  { workflowId: 'workflow-1', stage: 'PLAN' as AgentStage, sequence: 8 },
  { workflowId: 'workflow-2', stage: 'DEVELOPMENT' as AgentStage, sequence: 9 }
]
assert.equal(nextStageSessionSequence(existing, 'workflow-1', 'DEVELOPMENT'), 4)
assert.equal(nextStageSessionSequence(existing, 'workflow-1', 'DESIGN'), 1)

const concurrentSessions: Array<{
  workflowId: string
  stage: AgentStage
  sequence: number
}> = []
await Promise.all(
  Array.from({ length: 12 }, (_, index) =>
    withStageSessionCreationLock('workflow-1:DEVELOPMENT', async () => {
      const sequence = nextStageSessionSequence(concurrentSessions, 'workflow-1', 'DEVELOPMENT')
      await Promise.resolve()
      concurrentSessions.push({ workflowId: 'workflow-1', stage: 'DEVELOPMENT', sequence })
      return index
    })
  )
)
assert.deepEqual(
  concurrentSessions.map((session) => session.sequence),
  Array.from({ length: 12 }, (_, index) => index + 1)
)

console.log('stage session tests passed')
