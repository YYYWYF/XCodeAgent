import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, Fragment } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ConnectionStatusBanner from '../src/renderer/src/components/ConnectionStatusBanner'
import RecoverySurface from '../src/renderer/src/components/AiChatPanel/recoverySurface'
import {
  beginConnectionRequest,
  completeConnectionRequest,
  failConnectionRequest,
  initialConnectionState
} from '../src/renderer/src/service/connectionState'
import { latestApplicationLifecycle } from '../src/renderer/src/service/activeApplicationPlanning'
import type {
  ApplicationLifecycle,
  ExecutionRecoveryCandidate,
  ExecutionRecoveryProjection
} from '../src/renderer/src/typings'
import './executionRecoveryCard.test'
import './executionRecoveryLifecycleMerge.test'
import './executionRecoveryState.test'
import './workflowConversationRuntime.test'

/** 构造 Backend 签发的唯一 durable Recovery 候选。 */
function recoveryCandidate(
  incidentId = 'incident-A',
  actionId = 'action-A'
): ExecutionRecoveryCandidate {
  return {
    sourceRunId: `run-${incidentId}`,
    ownerSessionId: 'session-A',
    threadId: 'thread-A',
    executionKind: 'workbench',
    executionStatus: 'failed',
    availability: 'ready',
    canContinue: true,
    reasonCode: 'RETRY_FAILED_NODE',
    message: '上一次执行失败。',
    recoveryActionPlan: {
      schemaVersion: 'recovery-action-plan.v1',
      incidentId,
      sourceRunId: `run-${incidentId}`,
      threadId: 'thread-A',
      executionKind: 'workbench',
      status: 'recoverable',
      reasonCode: 'RETRY_FAILED_NODE',
      message: '可以重试失败节点。',
      primaryAction: {
        actionId,
        kind: 'retry_operation',
        targetNode: 'backend-owned-node',
        label: '重试失败节点',
        description: '执行 Backend 签发的恢复动作。',
        requiresConfirmation: false
      },
      alternateActions: []
    },
    updatedAt: '2026-09-16T00:00:00.000Z'
  }
}

/** 构造包含显式 GET-time recovery projection 的 lifecycle。 */
function lifecycle(candidates: ExecutionRecoveryCandidate[]): ApplicationLifecycle {
  const projection: ExecutionRecoveryProjection = {
    schemaVersion: 'execution-recovery.v1',
    generatedAt: '2026-09-16T00:00:00.000Z',
    candidates
  }
  return {
    application: { id: 'app-A', name: 'App A' },
    updatedAt: '2026-09-16T00:00:00.000Z',
    revision: 3,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {},
    extensions: { executionRecovery: projection }
  } as ApplicationLifecycle
}

/** 同时渲染两个独立状态面，验证它们可以任意组合而不互相覆盖。 */
function renderSurfaces(
  connection: ReturnType<typeof initialConnectionState>,
  candidate?: ExecutionRecoveryCandidate
): string {
  return renderToStaticMarkup(
    createElement(
      Fragment,
      null,
      createElement(ConnectionStatusBanner, {
        connection,
        onReconnect: () => undefined
      }),
      createElement(RecoverySurface, {
        actionDisabled: connection.status !== 'healthy',
        activeExecutionRecovery: candidate,
        isApplicationPlanningPhase: false,
        onExecuteRecoveryAction: () => undefined,
        recoveryRunning: false
      })
    )
  )
}

test('J1/J4 transport state and durable Recovery render independently', () => {
  const disconnected = failConnectionRequest(initialConnectionState(true), 1, 'SSE disconnected')
  const disconnectedOnly = renderSurfaces(disconnected)
  assert.match(disconnectedOnly, /Backend 暂时不可用/)
  assert.doesNotMatch(disconnectedOnly, /工作台执行需要恢复/)

  const healthyRecovery = renderSurfaces(initialConnectionState(true), recoveryCandidate())
  assert.doesNotMatch(healthyRecovery, /connection-status-banner/)
  assert.match(healthyRecovery, /工作台执行需要恢复/)
})

test('J5/J7 disconnect and failed refresh preserve last-known-good Incident', () => {
  const durable = lifecycle([recoveryCandidate()])
  const disconnected = failConnectionRequest(initialConnectionState(true), 4, 'Backend unavailable')
  const markup = renderSurfaces(
    disconnected,
    durable.extensions.executionRecovery?.candidates[0]
  )
  assert.match(markup, /Backend 暂时不可用/)
  assert.match(markup, /工作台执行需要恢复/)
  assert.match(markup, /disabled=""/)
  assert.equal(
    durable.extensions.executionRecovery?.candidates[0]?.recoveryActionPlan.incidentId,
    'incident-A'
  )
})

test('J2/J6 only a successful empty durable projection clears the Incident', () => {
  const known = lifecycle([recoveryCandidate()])
  const reconnecting = beginConnectionRequest(initialConnectionState(true), 5)
  const healthy = completeConnectionRequest(reconnecting, 5)
  assert.equal(healthy.status, 'healthy')
  assert.equal(known.extensions.executionRecovery?.candidates.length, 1)

  const cleared = latestApplicationLifecycle(known, lifecycle([]))
  assert.deepEqual(cleared.extensions.executionRecovery?.candidates, [])
})

test('J3/J9/J10 newer Backend action identity replaces the old candidate without target inference', () => {
  const oldLifecycle = lifecycle([recoveryCandidate('incident-A', 'action-A')])
  const newLifecycle = lifecycle([recoveryCandidate('incident-C', 'action-D')])
  const merged = latestApplicationLifecycle(oldLifecycle, newLifecycle)
  const candidate = merged.extensions.executionRecovery?.candidates[0]
  assert.equal(candidate?.recoveryActionPlan.incidentId, 'incident-C')
  assert.equal(candidate?.recoveryActionPlan.primaryAction?.actionId, 'action-D')
  assert.equal(candidate?.recoveryActionPlan.primaryAction?.targetNode, 'backend-owned-node')
  assert.deepEqual(
    {
      action: 'execute',
      incidentId: candidate?.recoveryActionPlan.incidentId,
      actionId: candidate?.recoveryActionPlan.primaryAction?.actionId
    },
    { action: 'execute', incidentId: 'incident-C', actionId: 'action-D' }
  )
})

test('J11/J12 reload starts connecting and identical durable refresh is idempotent', () => {
  const connecting = initialConnectionState()
  assert.equal(connecting.status, 'connecting')
  const first = lifecycle([recoveryCandidate()])
  const second = lifecycle([recoveryCandidate()])
  const merged = latestApplicationLifecycle(first, second)
  const markup = renderSurfaces(
    completeConnectionRequest(connecting, 1),
    merged.extensions.executionRecovery?.candidates[0]
  )
  assert.equal(markup.split('工作台执行需要恢复').length - 1, 1)
  assert.equal(merged.extensions.executionRecovery?.candidates.length, 1)
})

test('connection request generation rejects a late failure from an older request', () => {
  const initial = initialConnectionState(true)
  const requestA = beginConnectionRequest(initial, 1)
  const requestB = beginConnectionRequest(requestA, 2)
  const succeededB = completeConnectionRequest(requestB, 2, 200)
  const lateFailureA = failConnectionRequest(succeededB, 1, 'late failure', 300)
  assert.deepEqual(lateFailureA, succeededB)
})
