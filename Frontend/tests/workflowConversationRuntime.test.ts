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

/** 通过服务端 SSE 伪造生产 hook 所需的最小会话依赖。 */
function buildRuntimeParams(input: {
  activeSession: SessionIdentity
  application: ApplicationConfig
  applicationLifecycle: ApplicationLifecycle
  agUiSessionsRef: MutableRefObject<Record<string, AgUiChatSession>>
  acquireSessionExecution: WorkflowConversationParams['acquireSessionExecution']
  releaseSessionExecution: (sessionKey: string) => void
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
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
    persistSession: noopAsync,
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
