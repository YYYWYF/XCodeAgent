import assert from 'node:assert/strict'
import { test } from 'node:test'
import { latestApplicationLifecycle } from '../src/renderer/src/service/activeApplicationPlanning'
import type { ApplicationLifecycle, ExecutionRecoveryProjection } from '../src/renderer/src/typings'

/** 构造 recovery projection，覆盖 GET 投影的有值、阻断和空候选状态。 */
function recoveryProjection(
  availability: 'ready' | 'blocked',
  candidates: boolean
): ExecutionRecoveryProjection {
  return {
    schemaVersion: 'execution-recovery.v1',
    generatedAt: '2026-09-12T00:00:00.000Z',
    candidates: candidates
      ? [
          {
            sourceRunId: 'run-recovery',
            ownerSessionId: 'session-A',
            threadId: 'exec-thread-A',
            executionKind: 'workbench',
            executionStatus: 'interrupted',
            availability,
            canContinue: availability === 'ready',
            reasonCode: availability.toUpperCase(),
            message: availability,
            recoveryActionPlan: {
              schemaVersion: 'recovery-action-plan.v1',
              incidentId: `incident-${availability}`,
              sourceRunId: 'run-recovery',
              threadId: 'exec-thread-A',
              executionKind: 'workbench',
              status: availability === 'ready' ? 'recoverable' : 'needs_attention',
              reasonCode: availability.toUpperCase(),
              message: availability,
              primaryAction:
                availability === 'ready'
                  ? {
                      actionId: 'action-recovery',
                      kind: 'continue_checkpoint',
                      label: '继续执行',
                      description: '从已保存的现场继续执行。',
                      requiresConfirmation: false
                    }
                  : null,
              alternateActions: []
            },
            updatedAt: '2026-09-12T00:00:00.000Z'
          }
        ]
      : []
  }
}

/** 构造只包含 lifecycle revision 与 recovery extension 的测试快照。 */
function lifecycle(
  revision: number,
  projection?: ExecutionRecoveryProjection
): ApplicationLifecycle {
  return {
    application: { id: 'app-recovery-merge', name: '恢复合并测试' },
    updatedAt: '2026-09-12T00:00:00.000Z',
    revision,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    // Recovery 的来源必须仍由生命周期持有；普通帧只是缺少临时投影，执行并未结束。
    activeExecutions: {
      'run-recovery': {
        runId: 'run-recovery', threadId: 'exec-thread-A', ownerSessionId: 'session-A',
        scope: 'application', targetId: 'app-recovery-merge', phase: 'prepare_build_tasks',
        status: 'interrupted', startedAt: '2026-09-12T00:00:00.000Z', updatedAt: '2026-09-12T00:00:00.000Z'
      }
    },
    extensions: projection ? { executionRecovery: projection } : {}
  } as unknown as ApplicationLifecycle
}

test('同 revision 没有投影到 READY 投影会补入全局 lifecycle', () => {
  const merged = latestApplicationLifecycle(
    lifecycle(10),
    lifecycle(10, recoveryProjection('ready', true))
  )

  assert.equal(merged.extensions.executionRecovery?.candidates[0]?.availability, 'ready')
})

test('同 revision READY 会被新的 BLOCKED 投影覆盖', () => {
  const merged = latestApplicationLifecycle(
    lifecycle(10, recoveryProjection('ready', true)),
    lifecycle(10, recoveryProjection('blocked', true))
  )

  assert.equal(merged.extensions.executionRecovery?.candidates[0]?.availability, 'blocked')
})

test('同 revision 空 candidates 会清除旧 recovery 卡片', () => {
  const merged = latestApplicationLifecycle(
    lifecycle(10, recoveryProjection('ready', true)),
    lifecycle(10, recoveryProjection('ready', false))
  )

  assert.deepEqual(merged.extensions.executionRecovery?.candidates, [])
})

test('较低 revision 的 READY 投影不能复活较新 revision 的 BLOCKED', () => {
  const merged = latestApplicationLifecycle(
    lifecycle(11, recoveryProjection('blocked', true)),
    lifecycle(10, recoveryProjection('ready', true))
  )

  assert.equal(merged.revision, 11)
  assert.equal(merged.extensions.executionRecovery?.candidates[0]?.availability, 'blocked')
})

test('更高 revision 的普通 lifecycle 帧不会误清已有 GET recovery 投影', () => {
  const merged = latestApplicationLifecycle(
    lifecycle(10, recoveryProjection('ready', true)),
    lifecycle(11)
  )

  assert.equal(merged.revision, 11)
  assert.equal(merged.extensions.executionRecovery?.candidates[0]?.availability, 'ready')
})

/** 读取失败保留持有的旧证据；End 仍能清除，成功空读取也能恢复正常清除。 */
test('恢复读取失败不清旧候选，但 End 和成功空投影仍清除', () => {
  const previous = lifecycle(10, recoveryProjection('ready', true))
  const failed = lifecycle(10, { ...recoveryProjection('ready', false), error: {
    code: 'RECOVERY_PROJECTION_READ_FAILED', message: '恢复读取失败' } })
  const merged = latestApplicationLifecycle(previous, failed)
  assert.equal(merged.extensions.executionRecovery?.candidates.length, 1)
  assert.equal(merged.extensions.executionRecovery?.error?.code, 'RECOVERY_PROJECTION_READ_FAILED')
  const successful = latestApplicationLifecycle(merged, lifecycle(10, recoveryProjection('ready', false)))
  assert.deepEqual(successful.extensions.executionRecovery?.candidates, [])
  assert.equal(successful.extensions.executionRecovery?.error, undefined)
  const ended = { ...failed, revision: 11, activeExecutions: {} }
  assert.deepEqual(latestApplicationLifecycle(previous, ended).extensions.executionRecovery?.candidates, [])
})
