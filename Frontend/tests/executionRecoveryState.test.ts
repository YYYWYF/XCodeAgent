import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  executionRecoveryForSession,
  executionRecoveryProjection,
  shouldShowLegacyExecutionRecovery
} from '../src/renderer/src/components/AiChatPanel/executionRecoveryState'
import type { ApplicationLifecycle, ExecutionRecoveryCandidate } from '../src/renderer/src/typings'

/** 构造只包含当前恢复投影扩展的 lifecycle 测试快照。 */
function lifecycleWithCandidates(candidates: unknown[]): ApplicationLifecycle {
  return {
    application: { id: 'app-recovery-state-test', name: '恢复测试' },
    updatedAt: '2026-09-12T00:00:00.000Z',
    revision: 1,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {},
    extensions: {
      executionRecovery: {
        schemaVersion: 'execution-recovery.v1',
        generatedAt: '2026-09-12T00:00:00.000Z',
        candidates
      }
    }
  } as unknown as ApplicationLifecycle
}

/** 创建前端可接受的公开恢复候选。 */
function candidate(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    sourceRunId: 'run-A',
    ownerSessionId: 'session-A',
    threadId: 'exec-thread-A',
    executionKind: 'workbench',
    executionStatus: 'interrupted',
    availability: 'ready',
    canContinue: true,
    reasonCode: 'READY_NATIVE',
    message: '上一次执行被中断，可以从已保存的现场继续。',
    updatedAt: '2026-09-12T00:00:01.000Z',
    ...overrides
  }
}

test('错误 schema 不会被当作 execution recovery projection', () => {
  const lifecycle = lifecycleWithCandidates([])
  lifecycle.extensions.executionRecovery = {
    schemaVersion: 'execution-recovery.v0'
  } as never
  assert.equal(executionRecoveryProjection(lifecycle), undefined)
})

test('recovery candidate 只按 active session sessionId 匹配并选择最新记录', () => {
  const lifecycle = lifecycleWithCandidates([
    candidate({
      sourceRunId: 'run-old',
      ownerSessionId: 'session-A',
      updatedAt: '2026-09-12T00:00:01.000Z'
    }),
    candidate({
      sourceRunId: 'run-new',
      ownerSessionId: 'session-A',
      updatedAt: '2026-09-12T00:00:02.000Z'
    }),
    candidate({ sourceRunId: 'run-other', ownerSessionId: 'session-B', threadId: 'exec-thread-A' })
  ])

  assert.equal(executionRecoveryForSession(lifecycle, 'session-A')?.sourceRunId, 'run-new')
  assert.equal(executionRecoveryForSession(lifecycle, 'session-B')?.sourceRunId, 'run-other')
  assert.equal(executionRecoveryForSession(lifecycle, 'session-C'), undefined)
  assert.equal(executionRecoveryForSession(lifecycle, undefined), undefined)
})

test('缺少 ownerSessionId 的候选不会被投影到前端', () => {
  const lifecycle = lifecycleWithCandidates([candidate({ ownerSessionId: undefined })])

  assert.deepEqual(executionRecoveryProjection(lifecycle)?.candidates, [])
})

/** 从当前投影构造纯函数测试使用的合法恢复候选。 */
function projectedCandidate(
  executionKind: ExecutionRecoveryCandidate['executionKind']
): ExecutionRecoveryCandidate {
  const projected = executionRecoveryForSession(
    lifecycleWithCandidates([candidate({ executionKind })]),
    'session-A'
  )
  if (!projected) throw new Error('测试候选未成功投影。')
  return projected
}

test('旧恢复卡仅允许 Workbench 且不能覆盖 Planning 控制面', () => {
  const applicationPlanningCandidate = projectedCandidate('application_planning')
  const workbenchCandidate = projectedCandidate('workbench')

  assert.equal(
    shouldShowLegacyExecutionRecovery(applicationPlanningCandidate, {
      isApplicationPlanningPhase: true,
      hasBusinessInteraction: false,
      acceptanceAwaiting: false
    }),
    false
  )
  assert.equal(
    shouldShowLegacyExecutionRecovery(workbenchCandidate, {
      isApplicationPlanningPhase: true,
      hasBusinessInteraction: false,
      acceptanceAwaiting: false
    }),
    false
  )
  assert.equal(
    shouldShowLegacyExecutionRecovery(workbenchCandidate, {
      isApplicationPlanningPhase: false,
      hasBusinessInteraction: false,
      acceptanceAwaiting: false
    }),
    true
  )
})

test('旧恢复卡在 Workbench 业务交互或验收等待时隐藏', () => {
  const workbenchCandidate = projectedCandidate('workbench')

  assert.equal(
    shouldShowLegacyExecutionRecovery(workbenchCandidate, {
      isApplicationPlanningPhase: false,
      hasBusinessInteraction: true,
      acceptanceAwaiting: false
    }),
    false
  )
  assert.equal(
    shouldShowLegacyExecutionRecovery(workbenchCandidate, {
      isApplicationPlanningPhase: false,
      hasBusinessInteraction: false,
      acceptanceAwaiting: true
    }),
    false
  )
})
