import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  executionRecoveryForSession,
  executionRecoveryProjection
} from '../src/renderer/src/components/AiChatPanel/executionRecoveryState'
import { workbenchRecoveryIncident } from '../src/renderer/src/service/recoveryIncident'
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
  const executionKind =
    (overrides.executionKind as 'application_planning' | 'workbench' | undefined) || 'workbench'
  const sourceRunId = (overrides.sourceRunId as string | undefined) || 'run-A'
  const threadId = (overrides.threadId as string | undefined) || 'exec-thread-A'
  return {
    sourceRunId,
    ownerSessionId: 'session-A',
    threadId,
    executionKind,
    executionStatus: 'interrupted',
    availability: 'ready',
    canContinue: true,
    reasonCode: 'READY_NATIVE',
    message: '上一次执行被中断，可以从已保存的现场继续。',
    recoveryActionPlan: {
      schemaVersion: 'recovery-action-plan.v1',
      incidentId: 'incident-A',
      sourceRunId,
      threadId,
      executionKind,
      status: 'recoverable',
      reasonCode: 'READY_NATIVE',
      message: '上一次执行可以安全恢复。',
      primaryAction: {
        actionId: 'action-A',
        kind: 'continue_checkpoint',
        label: '继续执行',
        description: '从已保存的现场继续执行。',
        requiresConfirmation: false
      },
      alternateActions: []
    },
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

test('缺少或失配 RecoveryActionPlan 的候选 fail closed', () => {
  const basePlan = candidate().recoveryActionPlan as Record<string, unknown>
  const lifecycle = lifecycleWithCandidates([
    candidate({ recoveryActionPlan: undefined }),
    candidate({
      recoveryActionPlan: {
        ...basePlan,
        threadId: 'other-thread'
      }
    })
  ])
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

test('Workbench 当前 Incident 只接受 Workbench ActionPlan', () => {
  const applicationPlanningCandidate = projectedCandidate('application_planning')
  const workbenchCandidate = projectedCandidate('workbench')

  assert.equal(workbenchRecoveryIncident(applicationPlanningCandidate), undefined)
  assert.equal(workbenchRecoveryIncident(workbenchCandidate)?.kind, 'recoverable')
})
