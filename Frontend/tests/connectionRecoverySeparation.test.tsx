import assert from 'node:assert/strict'
import { test } from 'node:test'

test('Backend 停服优先于预览断线和 UI 待确认，恢复按钮必须同步 Backend', () => {
  const backend = { status: 'unavailable' as const, requestGeneration: 1, lastError: 'Failed to fetch' }
  const preview = { status: 'unavailable' as const, requestGeneration: 2, lastError: '预览断线' }
  assert.equal(prioritizedConnection(backend, preview), backend)
  assert.equal(prioritizedConnection(backend, { ...preview, status: 'healthy' }), backend)
  assert.equal(globalFallbackState({ connectionStatus: backend.status, hasRecoveryIncident: false }).visible, true)
})
import { createElement, Fragment } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ConnectionStatusBanner from '../src/renderer/src/components/ConnectionStatusBanner'
import RecoverySurface from '../src/renderer/src/components/AiChatPanel/recoverySurface'
import RetryEntryNotice from '../src/renderer/src/components/RetryEntryNotice'
import {
  globalFallbackState,
  prioritizedConnection,
  conversationUsesWorkflowRecovery,
  acceptancePreviewCanFocus,
  latestConversationFailure,
  NO_RECOVERY_ENTRY_ERROR,
  workbenchRecoveryCoveredByExecution
} from '../src/renderer/src/components/AiChatPanel/globalFallbackState'
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
import './workbenchRunPresentation.test'
import './workbenchRunRefresh.test'
import './templateRecovery.test'
import './planningFailureScope.test'
import './applicationLifecycleTimeout.test'

test('二次修改断线及恢复候选始终复用 Workflow 重试，预览断线不抢入口', () => {
  const healthy = { conversationActive: true, workflowConnectionStatus: 'healthy',
    previewConnectionUnavailable: false, hasRecovery: false, hasError: false }
  assert.equal(conversationUsesWorkflowRecovery(healthy), false)
  assert.equal(conversationUsesWorkflowRecovery({ ...healthy, previewConnectionUnavailable: true }), true)
  assert.equal(conversationUsesWorkflowRecovery({ ...healthy, workflowConnectionStatus: 'unavailable' }), true)
  assert.equal(conversationUsesWorkflowRecovery({ ...healthy, workflowConnectionStatus: 'reconnecting' }), true)
  assert.equal(conversationUsesWorkflowRecovery({ ...healthy, hasRecovery: true }), true)
  assert.equal(conversationUsesWorkflowRecovery({ ...healthy, hasError: true }), true)
  assert.equal(conversationUsesWorkflowRecovery({ ...healthy, conversationActive: false,
    previewConnectionUnavailable: true }), false)
})

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
        kind: 'retry_failed_node',
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
    activeExecutions: Object.fromEntries(candidates.map((candidate) => [candidate.sourceRunId, {
      runId: candidate.sourceRunId,
      threadId: candidate.threadId,
      ownerSessionId: candidate.ownerSessionId,
      scope: 'application', targetId: 'app-A', phase: 'prepare_build_tasks', status: 'failed',
      startedAt: candidate.updatedAt, updatedAt: candidate.updatedAt
    }])),
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
  assert.doesNotMatch(disconnectedOnly, /当前执行失败/)

  const healthyRecovery = renderSurfaces(initialConnectionState(true), recoveryCandidate())
  assert.doesNotMatch(healthyRecovery, /connection-status-banner/)
  assert.match(healthyRecovery, /当前执行失败/)
})

test('J5/J7 disconnect and failed refresh preserve last-known-good Incident', () => {
  const durable = lifecycle([recoveryCandidate()])
  const disconnected = failConnectionRequest(initialConnectionState(true), 4, 'Backend unavailable')
  const markup = renderSurfaces(
    disconnected,
    durable.extensions.executionRecovery?.candidates[0]
  )
  assert.match(markup, /Backend 暂时不可用/)
  assert.match(markup, /当前执行失败/)
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
  newLifecycle.revision = oldLifecycle.revision + 1
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
  assert.equal(markup.split('data-testid="workbench-recovery-incident"').length - 1, 1)
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

test('健康连接的正常同步不显示重连，真实断连后的同步仍显示重连', () => {
  const healthyRead = beginConnectionRequest(initialConnectionState(true), 1)
  assert.equal(healthyRead.status, 'healthy')
  assert.doesNotMatch(renderSurfaces(healthyRead), /正在重新连接 Backend/)
  const failed = failConnectionRequest(healthyRead, 1, 'Failed to fetch')
  const reconnect = beginConnectionRequest(failed, 2)
  assert.equal(reconnect.status, 'reconnecting')
  assert.match(renderSurfaces(reconnect), /正在重新连接 Backend/)
  assert.equal(beginConnectionRequest(initialConnectionState(), 1).status, 'connecting')
})

/** 无恢复入口只显示可关闭提示，正常连接不应重复提供重试。 */
test('healthy GET without Recovery candidate shows a dismissible notice', () => {
  const fallback = globalFallbackState({
    connectionStatus: 'healthy',
    hasRecoveryIncident: false,
    recoveryError: NO_RECOVERY_ENTRY_ERROR
  })
  const html = fallback.visible && fallback.error
    ? renderToStaticMarkup(createElement(RetryEntryNotice))
    : ''

  assert.equal(fallback.visible, true)
  assert.match(html, /role="status"/)
  assert.match(html, /找不到恢复入口，请手动发起流程。/)
  assert.match(html, /aria-label="关闭恢复提示"/)
  assert.doesNotMatch(html, />重试</)
})

test('没有恢复入口的最新消息错误也在底部展示，新请求不继承历史错误', () => {
  const failed = { id: 1, role: 'assistant' as const, content: '', createdAt: 1, error: '任务失败' }
  const error = latestConversationFailure([failed])
  const fallback = globalFallbackState({ connectionStatus: 'healthy', hasRecoveryIncident: false, globalFallbackError: error })
  assert.equal(fallback.visible, true)
  assert.equal(fallback.error, '任务失败')
  assert.equal(latestConversationFailure([failed, { id: 2, role: 'user', content: '重试', createdAt: 2 }]), undefined)
  assert.equal(latestConversationFailure([failed, { id: 3, role: 'assistant', content: '', createdAt: 3 }]), undefined)
  assert.equal(latestConversationFailure([failed, { id: 4, role: 'assistant', content: '完成', createdAt: 4 }]), undefined)
})

test('active retry hides the previous workbench failure until the new execution settles', () => {
  const base = {
    isApplicationPlanningPhase: false,
    recoveryRunning: false,
    loading: false,
    planExecutionMode: 'failed' as const
  }
  assert.equal(workbenchRecoveryCoveredByExecution(base), false)
  assert.equal(workbenchRecoveryCoveredByExecution({ ...base, recoveryRunning: true }), true)
  assert.equal(workbenchRecoveryCoveredByExecution({ ...base, planExecutionMode: 'running' }), true)
  assert.equal(workbenchRecoveryCoveredByExecution({ ...base, loading: true }), true)
  assert.equal(workbenchRecoveryCoveredByExecution({ ...base, isApplicationPlanningPhase: true }), false)
})

test('成功结束只撤下旧恢复错误，后续连接和独立流程错误仍有入口', () => {
  const input = { connectionStatus: 'healthy', hasRecoveryIncident: false, recoveryError: '无法安全执行当前恢复操作' }
  assert.equal(globalFallbackState(input).visible, true)
  assert.deepEqual(globalFallbackState({ ...input, recoveryEnded: true }), { visible: false, error: undefined })
  assert.equal(globalFallbackState({ ...input, recoveryEnded: true, connectionStatus: 'offline' }).visible, true)
  assert.equal(globalFallbackState({ ...input, recoveryEnded: true, globalFallbackError: '预览连接失败' }).error, '预览连接失败')
})

test('验收预览在断线、恢复候选或错误出现时让出分栏，入口消失后恢复全宽', () => {
  for (const input of [
    { connectionStatus: 'offline', hasRecoveryIncident: false },
    { connectionStatus: 'healthy', hasRecoveryIncident: true },
    { connectionStatus: 'healthy', hasRecoveryIncident: false, globalFallbackError: '启动失败' }
  ]) {
    assert.equal(acceptancePreviewCanFocus('acceptance', true, globalFallbackState(input).visible, 'awaiting_acceptance'), false)
  }
  assert.equal(acceptancePreviewCanFocus('acceptance', true, false, 'awaiting_acceptance'), true)
  assert.equal(acceptancePreviewCanFocus('acceptance', false, false, 'awaiting_acceptance'), false)
  assert.equal(acceptancePreviewCanFocus('build', true, false, 'awaiting_acceptance'), false)
})
