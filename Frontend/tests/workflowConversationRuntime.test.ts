import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, type MutableRefObject, type ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { AgUiChatSession } from '../src/renderer/src/service/agUiAgent'
import {
  currentDagConfirmationPlan,
  pendingDagConfirmationExecution,
  pendingDagConfirmationWorkflow,
  pendingDagOwnerSessionId,
  resolvePendingPlanGuard
} from '../src/renderer/src/components/AiChatPanel/stageOutputState'
import {
  createSessionIdentity,
  type SessionExecutionEntry,
  type SessionIdentity
} from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import { useWorkflowConversation } from '../src/renderer/src/components/AiChatPanel/hooks/useWorkflowConversation'
import { workflowClarification } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/workflowClarification'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  ExecutionRecoveryCandidate,
  WorkflowBuildTaskPlanConfirmation,
  WorkflowRunPayload
} from '../src/renderer/src/typings'

const WORKSPACE_ROOT = '/tmp/xcodeagent-workflow-runtime-test'
const APPLICATION_ID = 'application-workflow-runtime-test'
const OWNER_SESSION_ID = 'session-owner'
const OWNER_THREAD_ID = 'thread-owner'
const DRAFT_DIGEST = 'a'.repeat(64)
type WorkflowConversationParams = Parameters<typeof useWorkflowConversation>[0]

/** 构造 generation 完成后服务端签发的 DAG 确认载荷。 */
function buildDagConfirmation(): Record<string, unknown> {
  return {
    mode: 'build_task_plan_confirmation',
    status: 'requires_user_input',
    actionValues: ['confirm', 'abandon', 'regenerate'],
    draftIdentity: {
      ownerSessionId: OWNER_SESSION_ID,
      planningRunId: 'planning-runtime-test',
      draftDigest: DRAFT_DIGEST
    },
    taskPlan: {
      confirmationStatus: 'pending',
      scopeTasks: [{ id: 'task-runtime-test', title: '测试任务' }]
    }
  }
}

/** 构造实际 Workflow 终态，确保 production sendWorkflowMessage 能识别 DAG confirmation。 */
function buildGenerationWorkflow(): WorkflowRunPayload {
  const clarification = buildDagConfirmation()
  return {
    runId: 'workflow-runtime-test',
    threadId: OWNER_THREAD_ID,
    events: [],
    summary: {
      status: 'requires_user_input',
      phase: 'prepare_build_tasks',
      clarification,
      buildTaskPlanConfirmation: clarification,
      buildExecutionScope: { type: 'page', targetId: 'runtime-test-page' }
    }
  } as unknown as WorkflowRunPayload
}

/** 构造 GET application lifecycle 返回的 PendingPlan 权威投影。 */
function buildPendingLifecycle(): ApplicationLifecycle {
  const confirmation = buildDagConfirmation()
  return {
    application: { id: APPLICATION_ID },
    updatedAt: '2026-09-11T00:00:02.000Z',
    revision: 2,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {},
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'pending_plan',
        status: 'awaiting_confirmation',
        planningRunId: 'planning-runtime-test',
        workflowRunId: 'workflow-runtime-test',
        ownerSessionId: OWNER_SESSION_ID,
        draftDigest: DRAFT_DIGEST,
        confirmation,
        message: '已读取当前 PendingPlan。'
      }
    }
  } as unknown as ApplicationLifecycle
}

/** 构造无 PendingPlan 的初始 lifecycle，模拟 generation 开始前的 none/idle 状态。 */
function buildIdleLifecycle(): ApplicationLifecycle {
  return {
    application: { id: APPLICATION_ID },
    updatedAt: '2026-09-11T00:00:00.000Z',
    revision: 1,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {},
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'none',
        status: 'idle',
        message: '当前没有 PendingPlan。'
      }
    }
  } as unknown as ApplicationLifecycle
}

/** 创建测试用的 development session identity，保持和生产运行态键一致。 */
function buildSessionIdentity(
  sessionId = OWNER_SESSION_ID,
  threadId = OWNER_THREAD_ID
): SessionIdentity {
  return createSessionIdentity({
    workspaceRoot: WORKSPACE_ROOT,
    editorMode: 'frontend',
    sessionId,
    threadId,
    workflowId: APPLICATION_ID,
    workbenchPhase: 'development'
  })
}

/** 生成 AG-UI SSE 响应，分别承载 Workflow 结果或 lifecycle GET 结果。 */
function sseResponse(
  threadId: string,
  runId: string,
  payload: { workflow: WorkflowRunPayload } | { applicationLifecycle: ApplicationLifecycle }
): Response {
  const result =
    'workflow' in payload
      ? { workflow: payload.workflow }
      : {
          applicationLifecycle: {
            schemaVersion: 1,
            runId,
            threadId,
            status: 'completed',
            action: 'get',
            lifecycle: payload.applicationLifecycle
          }
        }
  const events = [
    { type: 'RUN_STARTED', threadId, runId },
    { type: 'STATE_SNAPSHOT', snapshot: result },
    { type: 'RUN_FINISHED', threadId, runId, result }
  ]
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'content-type': 'text/event-stream' },
    status: 200
  })
}

/** 生成恢复端点返回 stale action 的最小 AG-UI RUN_ERROR 流。 */
function sseErrorResponse(threadId: string, runId: string): Response {
  const events = [
    { type: 'RUN_STARTED', threadId, runId },
    {
      type: 'RUN_ERROR',
      message: '恢复动作已经过期，请刷新当前 Recovery Incident。',
      code: 'STALE_RECOVERY_ACTION'
    }
  ]
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'content-type': 'text/event-stream' },
    status: 200
  })
}

/** 构造 stale 后由 Backend 投影的新 Incident。 */
function buildRecoveryLifecycle(incidentId: string, actionId: string): ApplicationLifecycle {
  return {
    application: { id: APPLICATION_ID },
    updatedAt: `2026-09-11T00:00:${incidentId.endsWith('B') ? '03' : '02'}.000Z`,
    revision: incidentId.endsWith('B') ? 3 : 2,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {},
    extensions: {
      executionRecovery: {
        schemaVersion: 'execution-recovery.v1',
        generatedAt: '2026-09-11T00:00:03.000Z',
        candidates: [
          {
            sourceRunId: incidentId === 'incident-B' ? 'run-B' : 'run-A',
            ownerSessionId: OWNER_SESSION_ID,
            threadId: OWNER_THREAD_ID,
            executionKind: 'workbench',
            executionStatus: 'failed',
            availability: 'ready',
            canContinue: true,
            reasonCode: 'RETRY_FAILED_NODE',
            message: '当前失败可以安全重试。',
            failureDiagnostic: {
              sourceRunId: incidentId === 'incident-B' ? 'run-B' : 'run-A',
              origin: 'model_call',
              code: 'MODEL_ERROR',
              httpStatus: 404,
              message: 'model not found'
            },
            recoveryActionPlan: {
              schemaVersion: 'recovery-action-plan.v1',
              incidentId,
              sourceRunId: incidentId === 'incident-B' ? 'run-B' : 'run-A',
              threadId: OWNER_THREAD_ID,
              executionKind: 'workbench',
              status: 'recoverable',
              reasonCode: 'RETRY_FAILED_NODE',
              message: '当前失败可以安全重试。',
              primaryAction: {
                actionId,
                kind: 'retry_failed_node',
                label: '重试当前失败操作',
                description: '重新执行当前失败操作。',
                requiresConfirmation: false
              },
              alternateActions: []
            },
            updatedAt: '2026-09-11T00:00:03.000Z'
          }
        ]
      }
    }
  } as unknown as ApplicationLifecycle
}

/** 取得可直接提交给 hook 的当前恢复候选。 */
function recoveryCandidate(): ExecutionRecoveryCandidate {
  const lifecycle = buildRecoveryLifecycle('incident-A', 'action-A')
  return lifecycle.extensions?.executionRecovery?.candidates?.[0] as ExecutionRecoveryCandidate
}

/** 通过服务端 SSE 伪造生产 hook 所需的最小会话依赖。 */
function buildRuntimeParams(input: {
  activeSession: SessionIdentity
  application: ApplicationConfig
  applicationLifecycle: ApplicationLifecycle
  agUiSessionsRef: MutableRefObject<Record<string, AgUiChatSession>>
  acquireSessionExecution: WorkflowConversationParams['acquireSessionExecution']
  releaseSessionExecution: (sessionKey: string) => void
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  persistSession?: WorkflowConversationParams['persistSession']
}): WorkflowConversationParams {
  const noopAsync = async (): Promise<void> => undefined
  const noopIdentity = async (): Promise<SessionIdentity> => input.activeSession
  return {
    acquireSessionExecution: input.acquireSessionExecution,
    activeSession: input.activeSession,
    agUiSessionsRef: input.agUiSessionsRef,
    application: input.application,
    applicationLifecycle: input.applicationLifecycle,
    draft: '生成 DAG',
    draftKey: input.activeSession.key,
    selectedSkills: [],
    conversationEnabled: false,
    inputMode: 'design',
    editorMode: 'frontend',
    createTestSession: noopIdentity,
    onRollbackTestSession: noopAsync,
    createReviewSession: noopIdentity,
    createAcceptanceSession: noopIdentity,
    ensureActiveSession: noopIdentity,
    ensureDevelopmentSession: async () => input.activeSession,
    getSessionMessages: () => [],
    persistSession: input.persistSession || noopAsync,
    onApplicationLifecycleChange: input.onApplicationLifecycleChange,
    onStartDesignStageRevision: noopAsync,
    onStartWorkbenchPlanRevision: noopIdentity,
    onRollbackFormalRevisionSession: noopAsync,
    onRevisionContinuation: noopAsync,
    onEnterTestPhase: () => undefined,
    onEnterReviewPhase: () => undefined,
    onEnterAcceptancePhase: () => undefined,
    onElementContextConsumed: () => undefined,
    onPreviewReady: () => undefined,
    publishAiMessage: () => undefined,
    releaseSessionExecution: input.releaseSessionExecution,
    sessionExecutions: [],
    setDraftByKey: () => undefined,
    setSelectedSkillsByKey: () => undefined,
    setSessionMessages: () => undefined,
    updateSessionExecutionStatus: () => undefined,
    workbenchPhase: 'development'
  }
}

test('DAG generation 完成后 production hook 先收口 runtime，再发布 PendingPlan 并允许 owner actions', async () => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const ownerIdentity = buildSessionIdentity()
  const otherIdentity = buildSessionIdentity('session-other', 'thread-other')
  const application = {
    id: APPLICATION_ID,
    appName: 'Runtime test application',
    workspaceRoot: WORKSPACE_ROOT,
    source: 'existing-workspace'
  } as unknown as ApplicationConfig
  const generationWorkflow = buildGenerationWorkflow()
  const pendingLifecycle = buildPendingLifecycle()
  const lifecycleState = buildIdleLifecycle()
  const executionLog: Array<{ type: 'acquire' | 'release'; sessionKey: string }> = []
  const lifecyclePublishExecutionCounts: number[] = []
  let activeExecution: SessionExecutionEntry | undefined
  let lifecycleGetCount = 0
  let workflowRequestCount = 0
  let lifecycleAfterWorkflow: ApplicationLifecycle = pendingLifecycle

  const acquireSessionExecution: WorkflowConversationParams['acquireSessionExecution'] = (
    identity,
    conversation
  ) => {
    if (activeExecution) return activeExecution
    activeExecution = { identity, conversation, status: 'starting' }
    executionLog.push({ type: 'acquire', sessionKey: identity.key })
    return undefined
  }
  const releaseSessionExecution = (sessionKey: string): void => {
    if (activeExecution?.identity.key !== sessionKey) return
    activeExecution = undefined
    executionLog.push({ type: 'release', sessionKey })
  }

  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { xcodeAgent: { agentBaseUrl: 'http://agent.test' } }
  })
  globalThis.fetch = async (input, init) => {
    const url = String(input)
    const request = JSON.parse(String(init?.body)) as {
      threadId: string
      runId: string
      forwardedProps?: {
        planControlAction?: string
      }
    }
    if (url.endsWith('/application-lifecycle/run')) {
      lifecycleGetCount += 1
      return sseResponse(request.threadId, request.runId, {
        applicationLifecycle: lifecycleAfterWorkflow
      })
    }
    workflowRequestCount += 1
    lifecycleAfterWorkflow =
      request.forwardedProps?.planControlAction === 'abandon'
        ? buildIdleLifecycle()
        : pendingLifecycle
    return sseResponse(request.threadId, request.runId, { workflow: generationWorkflow })
  }

  let captured: ReturnType<typeof useWorkflowConversation> | undefined
  const agUiSessionsRef = { current: {} as Record<string, AgUiChatSession> }
  const params = buildRuntimeParams({
    activeSession: ownerIdentity,
    application,
    applicationLifecycle: lifecycleState,
    agUiSessionsRef,
    acquireSessionExecution,
    releaseSessionExecution,
    onApplicationLifecycleChange: (incoming) => {
      lifecyclePublishExecutionCounts.push(activeExecution ? 1 : 0)
      Object.assign(lifecycleState, incoming)
      lifecycleState.extensions = incoming.extensions
    }
  })

  function Probe(): ReactElement {
    captured = useWorkflowConversation(params)
    return createElement('div')
  }

  try {
    renderToStaticMarkup(createElement(Probe))
    assert.ok(captured)

    await captured.handleSend()

    // GET projection 已经发布时，旧 generation 不得再表现为 loading/sessionExecution。
    assert.deepEqual(lifecyclePublishExecutionCounts, [0])
    assert.equal(activeExecution, undefined)
    assert.deepEqual(
      executionLog.map((entry) => entry.type),
      ['acquire', 'release']
    )
    assert.equal(workflowRequestCount, 1)
    assert.equal(lifecycleGetCount, 1)

    const pendingExecution = pendingDagConfirmationExecution(pendingLifecycle, OWNER_THREAD_ID)
    const pendingWorkflow = pendingDagConfirmationWorkflow([], pendingExecution, pendingLifecycle)
    assert.ok(pendingExecution)
    assert.ok(pendingWorkflow)
    assert.equal(pendingDagOwnerSessionId(pendingLifecycle), ownerIdentity.sessionId)
    assert.equal(workflowClarification(pendingWorkflow)?.mode, 'build_task_plan_confirmation')
    assert.equal(workflowClarification(pendingWorkflow)?.status, 'requires_user_input')
    assert.equal(currentDagConfirmationPlan(pendingWorkflow)?.scopeTasks?.length, 1)
    assert.equal(lifecycleState.extensions?.planningRefresh?.source, 'pending_plan')
    assert.equal(lifecycleState.extensions?.planningRefresh?.status, 'awaiting_confirmation')

    // PendingPlan owner 仍是唯一可操作会话，其他会话不能借 lifecycle guard 绕过归属。
    const guard = resolvePendingPlanGuard(pendingLifecycle)
    assert.equal(guard.locked, true)
    assert.equal(guard.ownerSessionId, ownerIdentity.sessionId)
    assert.equal(guard.ownerSessionId === ownerIdentity.sessionId, true)
    assert.equal(guard.ownerSessionId === otherIdentity.sessionId, false)
    assert.equal(
      guard.locked && guard.ownerSessionId !== otherIdentity.sessionId,
      true,
      '非 owner session 必须继续受到 PendingPlan guard 保护'
    )

    const actions: WorkflowBuildTaskPlanConfirmation['action'][] = [
      'confirm',
      'regenerate',
      'abandon'
    ]
    for (const action of actions) {
      const result = await captured.handleSubmitClarification(
        generationWorkflow,
        {
          build_task_plan_confirmation: {
            mode: 'build_task_plan_confirmation',
            action,
            planningRunId: 'planning-runtime-test',
            draftDigest: DRAFT_DIGEST
          }
        },
        { sessionIdentity: ownerIdentity }
      )
      assert.equal(result, true, `${action} 不应被旧 generation execution 静默拦截`)
      assert.equal(activeExecution, undefined)
    }

    assert.equal(workflowRequestCount, 4)
    // 首次 generation 1 次，三个 action 各沿已有 action wrapper 刷新 1 次；没有额外 generation refresh。
    assert.equal(lifecycleGetCount, 4)
    assert.equal(executionLog.filter((entry) => entry.type === 'acquire').length, 4)
    assert.equal(executionLog.filter((entry) => entry.type === 'release').length, 4)

    // Abandon 后回到普通生成入口，第二次 generation 仍须经过同一 generation-complete refresh。
    await captured.handleSend()
    assert.equal(workflowRequestCount, 5)
    assert.equal(lifecycleGetCount, 5)
    assert.equal(activeExecution, undefined)
    assert.equal(lifecycleState.extensions?.planningRefresh?.source, 'pending_plan')
    assert.equal(lifecycleState.extensions?.planningRefresh?.status, 'awaiting_confirmation')
    assert.deepEqual(lifecyclePublishExecutionCounts, [0, 0, 0, 0, 0])
    assert.equal(executionLog.filter((entry) => entry.type === 'acquire').length, 5)
    assert.equal(executionLog.filter((entry) => entry.type === 'release').length, 5)
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: originalWindow
    })
  }
})

test('STALE_RECOVERY_ACTION 会读取并替换最新 Incident，且不写入历史错误消息', async () => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const ownerIdentity = buildSessionIdentity()
  const application = {
    id: APPLICATION_ID,
    appName: 'Recovery runtime test application',
    workspaceRoot: WORKSPACE_ROOT,
    source: 'existing-workspace'
  } as unknown as ApplicationConfig
  const lifecycleA = buildRecoveryLifecycle('incident-A', 'action-A')
  const lifecycleB = buildRecoveryLifecycle('incident-B', 'action-B')
  const lifecycleUpdates: ApplicationLifecycle[] = []
  const persistedMessages: Array<{ messages: unknown[] }> = []
  let lifecycleGetCount = 0
  let captured: ReturnType<typeof useWorkflowConversation> | undefined
  const agUiSessionsRef = { current: {} as Record<string, AgUiChatSession> }

  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { xcodeAgent: { agentBaseUrl: 'http://agent.test' } }
  })
  globalThis.fetch = async (input, init) => {
    const url = String(input)
    const request = JSON.parse(String(init?.body)) as { threadId: string; runId: string }
    if (url.endsWith('/application-lifecycle/run')) {
      lifecycleGetCount += 1
      return sseResponse(request.threadId, request.runId, { applicationLifecycle: lifecycleB })
    }
    return sseErrorResponse(request.threadId, request.runId)
  }

  const params = buildRuntimeParams({
    activeSession: ownerIdentity,
    application,
    applicationLifecycle: lifecycleA,
    agUiSessionsRef,
    acquireSessionExecution: () => undefined,
    releaseSessionExecution: () => undefined,
    persistSession: async ({ messages }) => {
      persistedMessages.push({ messages })
    },
    onApplicationLifecycleChange: (lifecycle) => lifecycleUpdates.push(lifecycle)
  })

  function Probe(): ReactElement {
    captured = useWorkflowConversation(params)
    return createElement('div')
  }

  try {
    renderToStaticMarkup(createElement(Probe))
    assert.ok(captured)

    const result = await captured.handleExecuteRecoveryAction(recoveryCandidate())

    assert.equal(result, false)
    assert.ok(lifecycleGetCount >= 1)
    assert.equal(lifecycleUpdates.at(-1)?.extensions?.executionRecovery?.candidates?.[0]?.recoveryActionPlan?.incidentId, 'incident-B')
    assert.equal(captured.recoveryError, undefined)
    assert.equal(
      persistedMessages.some(({ messages }) => JSON.stringify(messages).includes('当前恢复操作已过期')),
      false
    )
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: originalWindow
    })
  }
})

test('J13 needs_attention without primaryAction sends no Recovery execute request', async () => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const ownerIdentity = buildSessionIdentity()
  const application = {
    id: APPLICATION_ID,
    appName: 'Recovery fail-closed application',
    workspaceRoot: WORKSPACE_ROOT,
    source: 'existing-workspace'
  } as unknown as ApplicationConfig
  const lifecycle = buildRecoveryLifecycle('incident-A', 'action-A')
  const baseCandidate = recoveryCandidate()
  const candidate: ExecutionRecoveryCandidate = {
    ...baseCandidate,
    availability: 'blocked',
    canContinue: false,
    reasonCode: 'NO_RECOVERY_POINT',
    recoveryActionPlan: {
      ...baseCandidate.recoveryActionPlan,
      status: 'needs_attention',
      reasonCode: 'NO_RECOVERY_POINT',
      primaryAction: null
    }
  }
  if (lifecycle.extensions.executionRecovery) {
    lifecycle.extensions.executionRecovery.candidates = [candidate]
  }
  let recoveryRequest: Record<string, unknown> | undefined
  let captured: ReturnType<typeof useWorkflowConversation> | undefined

  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { xcodeAgent: { agentBaseUrl: 'http://agent.test' } }
  })
  globalThis.fetch = async (input, init) => {
    const request = JSON.parse(String(init?.body)) as {
      threadId: string
      runId: string
      forwardedProps?: { executionRecovery?: Record<string, unknown> }
    }
    if (String(input).endsWith('/application-lifecycle/run')) {
      return sseResponse(request.threadId, request.runId, { applicationLifecycle: lifecycle })
    }
    recoveryRequest = request.forwardedProps?.executionRecovery
    return sseErrorResponse(request.threadId, request.runId)
  }

  const params = buildRuntimeParams({
    activeSession: ownerIdentity,
    application,
    applicationLifecycle: lifecycle,
    agUiSessionsRef: { current: {} as Record<string, AgUiChatSession> },
    acquireSessionExecution: () => undefined,
    releaseSessionExecution: () => undefined,
    onApplicationLifecycleChange: () => undefined
  })

  /** 捕获 Hook 暴露的当前恢复执行入口。 */
  function Probe(): ReactElement {
    captured = useWorkflowConversation(params)
    return createElement('div')
  }

  try {
    renderToStaticMarkup(createElement(Probe))
    assert.ok(captured)
    const result = await captured.handleExecuteRecoveryAction(candidate)
    assert.equal(result, false)
    assert.equal(recoveryRequest, undefined)
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: originalWindow
    })
  }
})

test('J14 recoverable Backend primaryAction sends only incidentId and actionId', async () => {
  const originalFetch = globalThis.fetch
  const originalWindow = globalThis.window
  const ownerIdentity = buildSessionIdentity()
  const application = {
    id: APPLICATION_ID,
    appName: 'Recovery action identity application',
    workspaceRoot: WORKSPACE_ROOT,
    source: 'existing-workspace'
  } as unknown as ApplicationConfig
  const lifecycle = buildRecoveryLifecycle('incident-J14', 'action-J14')
  const candidate = lifecycle.extensions.executionRecovery
    ?.candidates[0] as ExecutionRecoveryCandidate
  let recoveryRequest: Record<string, unknown> | undefined
  let captured: ReturnType<typeof useWorkflowConversation> | undefined

  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { xcodeAgent: { agentBaseUrl: 'http://agent.test' } }
  })
  globalThis.fetch = async (input, init) => {
    const request = JSON.parse(String(init?.body)) as {
      threadId: string
      runId: string
      forwardedProps?: { executionRecovery?: Record<string, unknown> }
    }
    if (String(input).endsWith('/application-lifecycle/run')) {
      return sseResponse(request.threadId, request.runId, { applicationLifecycle: lifecycle })
    }
    recoveryRequest = request.forwardedProps?.executionRecovery
    return sseErrorResponse(request.threadId, request.runId)
  }

  const params = buildRuntimeParams({
    activeSession: ownerIdentity,
    application,
    applicationLifecycle: lifecycle,
    agUiSessionsRef: { current: {} as Record<string, AgUiChatSession> },
    acquireSessionExecution: () => undefined,
    releaseSessionExecution: () => undefined,
    onApplicationLifecycleChange: () => undefined
  })

  /** 捕获 Hook 暴露的 Backend action identity 执行入口。 */
  function Probe(): ReactElement {
    captured = useWorkflowConversation(params)
    return createElement('div')
  }

  try {
    renderToStaticMarkup(createElement(Probe))
    assert.ok(captured)
    await captured.handleExecuteRecoveryAction(candidate)
    assert.deepEqual(recoveryRequest, {
      action: 'execute',
      incidentId: 'incident-J14',
      actionId: 'action-J14'
    })
    assert.equal('sourceRunId' in (recoveryRequest || {}), false)
    assert.equal('targetNode' in (recoveryRequest || {}), false)
    assert.equal('currentNode' in (recoveryRequest || {}), false)
    assert.equal('phase' in (recoveryRequest || {}), false)
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: originalWindow
    })
  }
})
