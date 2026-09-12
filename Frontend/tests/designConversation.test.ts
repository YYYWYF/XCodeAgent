import assert from 'node:assert/strict'

import { buildWorkflowForwardedProps } from '../src/renderer/src/service/agUiAgent'
import {
  appendPlanningLoadingPlaceholder,
  compactPlanningMessageHistory,
  isSupersededPlanningPhaseMessage,
  isSupersededPlanningProgressMessage,
  isSupersededPlanningStageEntryMessage,
  isSupersededTechnicalPlanTransitionMessage,
  isTemplateSupersededPlanningProgressMessage,
  latestUiDesignPreviewMessageIndex,
  rollbackPlanningSubmissionMessages
} from '../src/renderer/src/components/AiChatPanel/components/MessageList/uiDesignPreviewHistory'
import {
  ensureApplicationPlanningAction,
  planningRequirementsConfirmed,
  planningTechnicalPlanConfirmed,
  planningWorkflowNeedsChatLoading,
  planningWorkflowActivity,
  planningWorkflowCanPublishDuringRun,
  planningWorkflowIsActivelyRunning,
  planningWorkflowPhase,
  planningWorkflowRequiresUserInput,
  planningWorkflowSettlesLoading,
  planningWorkflowUiDesignSkipped,
  retainApplicationPlanningInterrupt,
  shouldBackfillPlanningWorkflow,
  shouldSuppressConfirmedTechnicalPlanTransitionChunk
} from '../src/renderer/src/components/Welcome/planningWorkflowState'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../src/renderer/src/typings'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import {
  activeFormalRevisionStageSession,
  appendRevisionDevelopmentEntryMessage,
  bindRevisionSessionChangeId,
  createFormalRevisionSessionContext,
  formalRevisionContinuationSourceSession,
  formalRevisionPlanningSourceSession,
  initialFormalRevisionPhase,
  planningStageTransitionKey,
  revisionDevelopmentSessionForContinuation
} from '../src/renderer/src/components/AiChatPanel/hooks/revisionSession'
import {
  sessionToRestoreForPhase,
  sessionsForWorkbenchPhase
} from '../src/renderer/src/components/AiChatPanel/hooks/phaseSessionSelection'
import {
  createSessionIdentity,
  isSameSessionExecutionScope,
  isSessionExecutionOwner
} from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import {
  normalizeRevisionSessionContext,
  type ChatSessionSummary
} from '../src/renderer/src/service/chatSessions'
import {
  revisionContinuationFromWorkflow,
  revisionContinuationHandoffFromWorkflow
} from '../src/renderer/src/service/applicationPagePlanning'
import { planningArtifactRecoveryKeys } from '../src/renderer/src/components/AiChatPanel/planningArtifactRecovery'
import {
  PRODUCT_CONVERSATION_PLACEHOLDER,
  productConversationRoute,
  productConversationSendBlocked
} from '../src/renderer/src/components/AiChatPanel/components/ChatComposer/productConversation'
import {
  buildProductConversationInteraction,
  productConversationSubmissionError
} from '../src/renderer/src/service/applicationPlanningProductConversation'
import {
  canonicalPlanningReviewMessageIndexes,
  isNonMutatingProductConversation,
  planningReviewIdentity,
  planningReviewMatchesActiveWorkflow
} from '../src/renderer/src/components/AiChatPanel/components/MessageList/productConversationPresentation'
import { workflowMessageContentForDisplay } from '../src/renderer/src/service/processStepHistory'
import {
  applicationPlanningDisplayStatus,
  reduceApplicationPlanningCurrentState,
  type ApplicationPlanningCurrentState
} from '../src/renderer/src/service/activeApplicationPlanning'
import {
  planningMessageActionsDisabled,
  planningMessageHostsSyncError,
  planningSyncErrorHostMessageIndex,
  resolvePlanningMessageWorkflow
} from '../src/renderer/src/components/AiChatPanel/components/MessageList/planningMessageWorkflow'

const canonicalPlanningApplication = {
  id: 'canonical-app',
  appName: 'Canonical App'
} as ApplicationConfig

/** 构造 reducer 测试使用的最小权威生命周期。 */
function canonicalPlanningLifecycle(
  revision: number,
  status: ApplicationLifecycle['initialization']['status'] = 'awaiting_user'
): ApplicationLifecycle {
  return {
    application: { id: canonicalPlanningApplication.id, name: canonicalPlanningApplication.appName },
    updatedAt: `2026-09-10T00:00:0${revision}Z`,
    revision,
    initialization: {
      stage:
        status === 'failed'
          ? 'generating_technical_plan'
          : 'awaiting_technical_plan_confirmation',
      threadId: 'canonical-thread',
      status
    },
    activeExecutions: {},
    extensions: {}
  }
}

const canonicalTechnicalPlanWorkflow = {
  runId: 'canonical-run',
  threadId: 'canonical-thread',
  summary: {
    status: 'requires_user_input',
    phase: 'technical_planning',
    clarification: {
      mode: 'technical_plan_confirmation',
      status: 'requires_user_input'
    }
  },
  events: [],
  state: {
    lifecycle: canonicalPlanningLifecycle(10),
    technical_plan: { architecture: { style: 'modular' } }
  },
  result: {}
} as WorkflowRunPayload

const canonicalPlanningBaseState: ApplicationPlanningCurrentState = {
  application: canonicalPlanningApplication,
  lifecycle: canonicalPlanningLifecycle(10),
  threadId: 'canonical-thread',
  transportState: 'idle',
  workflow: canonicalTechnicalPlanWorkflow
}

const staleLifecycleState = reduceApplicationPlanningCurrentState(canonicalPlanningBaseState, {
  type: 'lifecycle_received',
  applicationId: canonicalPlanningApplication.id,
  threadId: 'canonical-thread',
  lifecycle: canonicalPlanningLifecycle(9)
})
assert.equal(staleLifecycleState.lifecycle.revision, 10)

const foreignThreadWorkflow = {
  ...canonicalTechnicalPlanWorkflow,
  runId: 'foreign-run',
  threadId: 'foreign-thread'
} as WorkflowRunPayload
const foreignThreadState = reduceApplicationPlanningCurrentState(canonicalPlanningBaseState, {
  type: 'workflow_received',
  applicationId: canonicalPlanningApplication.id,
  threadId: 'canonical-thread',
  workflow: foreignThreadWorkflow
})
assert.equal(foreignThreadState, canonicalPlanningBaseState)

const retryingFailedState = reduceApplicationPlanningCurrentState(
  {
    ...canonicalPlanningBaseState,
    lifecycle: canonicalPlanningLifecycle(11, 'failed'),
    error: '上次运行失败',
    transportState: 'idle'
  },
  {
    type: 'run_started',
    applicationId: canonicalPlanningApplication.id,
    threadId: 'canonical-thread'
  }
)
assert.equal(applicationPlanningDisplayStatus(retryingFailedState), 'running')
assert.equal(retryingFailedState.error, undefined)

const historicalCompletedWorkflow = {
  ...canonicalTechnicalPlanWorkflow,
  runId: 'historical-run',
  summary: { status: 'completed', phase: 'technical_planning' },
  state: {}
} as WorkflowRunPayload
assert.equal(
  resolvePlanningMessageWorkflow(
    historicalCompletedWorkflow,
    canonicalTechnicalPlanWorkflow,
    true
  ),
  canonicalTechnicalPlanWorkflow
)
assert.equal(
  resolvePlanningMessageWorkflow(
    historicalCompletedWorkflow,
    canonicalTechnicalPlanWorkflow,
    false
  ),
  historicalCompletedWorkflow
)

assert.equal(planningMessageHostsSyncError('状态未同步', 2), true)
assert.equal(planningMessageHostsSyncError('状态未同步', -1), false)
assert.equal(planningMessageActionsDisabled(true, true), true)
assert.equal(planningMessageActionsDisabled(false, true), false)
// 当前审阅门仍由消息 0 承载时，即使同 thread 的最新 Assistant 消息位于 2，也只选择原确认卡。
const splitPlanningSyncErrorHost = planningSyncErrorHostMessageIndex(
  'technical-plan:gate-g:revision-g',
  new Map([['technical-plan:gate-g:revision-g', 0]]),
  2
)
assert.equal(splitPlanningSyncErrorHost, 0)
assert.deepEqual(
  [0, 2].filter((messageIndex) => messageIndex === splitPlanningSyncErrorHost),
  [0]
)

const technicalPlanGenerationErrorWorkflow = {
  ...canonicalTechnicalPlanWorkflow,
  runId: 'technical-plan-error',
  summary: {
    status: 'requires_user_input',
    phase: 'technical_planning',
    clarification: {
      mode: 'technical_plan_generation_error',
      status: 'requires_user_input'
    }
  }
} as WorkflowRunPayload
assert.equal(
  planningWorkflowRequiresUserInput(
    resolvePlanningMessageWorkflow(
      historicalCompletedWorkflow,
      technicalPlanGenerationErrorWorkflow,
      true
    )
  ),
  true
)
assert.equal(
  (
    resolvePlanningMessageWorkflow(
      historicalCompletedWorkflow,
      technicalPlanGenerationErrorWorkflow,
      true
    )?.summary.clarification as { mode?: string } | undefined
  )?.mode,
  'technical_plan_generation_error'
)

const runningTechnicalPlanningWorkflow = {
  ...canonicalTechnicalPlanWorkflow,
  runId: 'technical-plan-running',
  summary: { status: 'running', phase: 'technical_planning' }
} as WorkflowRunPayload
const runningState = reduceApplicationPlanningCurrentState(canonicalPlanningBaseState, {
  type: 'workflow_received',
  applicationId: canonicalPlanningApplication.id,
  threadId: 'canonical-thread',
  workflow: runningTechnicalPlanningWorkflow
})
const confirmedState = reduceApplicationPlanningCurrentState(runningState, {
  type: 'workflow_received',
  applicationId: canonicalPlanningApplication.id,
  threadId: 'canonical-thread',
  workflow: canonicalTechnicalPlanWorkflow
})
assert.equal(planningWorkflowPhase(confirmedState.workflow), 'technical_planning')
assert.equal(
  confirmedState.workflow?.summary.clarification &&
    (confirmedState.workflow.summary.clarification as { mode?: string }).mode,
  'technical_plan_confirmation'
)

const coldThenLiveState = reduceApplicationPlanningCurrentState(
  reduceApplicationPlanningCurrentState(
    { ...canonicalPlanningBaseState, lifecycle: canonicalPlanningLifecycle(9), workflow: undefined },
    {
      type: 'lifecycle_received',
      applicationId: canonicalPlanningApplication.id,
      threadId: 'canonical-thread',
      lifecycle: canonicalPlanningLifecycle(10)
    }
  ),
  {
    type: 'workflow_received',
    applicationId: canonicalPlanningApplication.id,
    threadId: 'canonical-thread',
    workflow: canonicalTechnicalPlanWorkflow
  }
)
const liveThenColdState = reduceApplicationPlanningCurrentState(
  reduceApplicationPlanningCurrentState(
    { ...canonicalPlanningBaseState, lifecycle: canonicalPlanningLifecycle(9), workflow: undefined },
    {
      type: 'workflow_received',
      applicationId: canonicalPlanningApplication.id,
      threadId: 'canonical-thread',
      workflow: canonicalTechnicalPlanWorkflow
    }
  ),
  {
    type: 'lifecycle_received',
    applicationId: canonicalPlanningApplication.id,
    threadId: 'canonical-thread',
    lifecycle: canonicalPlanningLifecycle(10)
  }
)
assert.deepEqual(
  {
    lifecycle: coldThenLiveState.lifecycle,
    workflow: coldThenLiveState.workflow
  },
  {
    lifecycle: liveThenColdState.lifecycle,
    workflow: liveThenColdState.workflow
  }
)

const planningSubmissionMessages: AgentChatMessage[] = [
  { id: 1, role: 'assistant', content: '技术规划待确认', createdAt: 1 },
  { id: 2, role: 'user', content: '技术规划确认：正确，继续', createdAt: 2 },
  { id: 3, role: 'assistant', content: '并行到达的其他消息', createdAt: 3 },
  { id: 4, role: 'assistant', content: '', planningLoading: true, createdAt: 4 }
]
assert.deepEqual(
  rollbackPlanningSubmissionMessages(planningSubmissionMessages, {
    sessionKey: 'planning-session',
    messageIds: [2, 4]
  }).map((item) => item.id),
  [1, 3]
)

assert.deepEqual(planningArtifactRecoveryKeys(false, 'product'), [])
assert.deepEqual(planningArtifactRecoveryKeys(false, 'planning'), [])
assert.deepEqual(planningArtifactRecoveryKeys(true, 'product'), [
  'requirement-spec',
  'product-plan',
  'ui-design'
])
assert.deepEqual(planningArtifactRecoveryKeys(true, 'planning'), ['product-plan', 'technical-plan'])
assert.deepEqual(planningArtifactRecoveryKeys(true, 'development'), [])

const planningInteraction = {
  gateId: 'requirement_spec:revision-1',
  artifact: 'requirement_spec' as const,
  artifactRevision: 'revision-1',
  action: 'design_change' as const,
  request: '新增报表页'
}
const forwardedProps = buildWorkflowForwardedProps({
  applicationPlanningInteraction: planningInteraction,
  editorMode: 'frontend',
  workflowScope: 'application_planning'
})
assert.deepEqual(forwardedProps.applicationPlanningInteraction, planningInteraction)
assert.equal(forwardedProps.workflowScope, 'application_planning')
assert.equal(forwardedProps.resumeState, undefined)

assert.equal(
  PRODUCT_CONVERSATION_PLACEHOLDER,
  '告诉产品 Agent 你想调整的需求、页面或 UI，也可以直接提问…'
)
assert.equal(PRODUCT_CONVERSATION_PLACEHOLDER.includes('暂停并自由输入'), false)
assert.deepEqual(
  buildProductConversationInteraction(
    {
      gateId: 'ui_designs:revision-2',
      artifact: 'ui_designs',
      artifactRevision: 'revision-2'
    },
    '  首页改成左侧导航  '
  ),
  {
    gateId: 'ui_designs:revision-2',
    artifact: 'ui_designs',
    artifactRevision: 'revision-2',
    action: 'design_change',
    request: '首页改成左侧导航'
  }
)
assert.equal(productConversationSendBlocked(true, true), true)
assert.equal(productConversationSendBlocked(true, false), false)
assert.equal(productConversationSendBlocked(false, true), false)
assert.equal(productConversationRoute(true, false), 'initial_planning')
assert.equal(productConversationRoute(true, true), 'completed_product')
assert.equal(productConversationRoute(false, true), 'development_conversation')
const completedProductForwardedProps = buildWorkflowForwardedProps({
  editorMode: 'frontend',
  productStageConversation: {
    request: '修改 OrderPage.tsx'
  },
  workflowAction: 'product_stage_conversation',
  workflowScope: 'application_planning'
})
assert.equal(completedProductForwardedProps.workflowAction, 'product_stage_conversation')
assert.equal(completedProductForwardedProps.workflowScope, 'application_planning')
assert.equal(completedProductForwardedProps.conversation, undefined)
assert.deepEqual(completedProductForwardedProps.productStageConversation, {
  request: '修改 OrderPage.tsx'
})
assert.match(
  productConversationSubmissionError(new Error('待确认产物已经更新，请基于最新版本重新提交。')),
  /刷新到最新产品设计状态/
)

/** 构造带稳定审阅门和产品对话结果的前端投影样本。 */
const reviewWorkflow = (
  kind: 'out_of_scope' | 'read_only' | 'clarification' | 'requirement_change',
  revision: string,
  mutating: boolean
): WorkflowRunPayload => ({
  runId: `run-${kind}-${revision}`,
  threadId: 'planning-thread',
  events: [],
  summary: {
    phase: mutating ? 'requirements' : 'design_chat_response',
    status: mutating ? 'running' : 'requires_user_input',
    message: mutating ? '' : `${kind} response`,
    productConversationResult: {
      kind,
      mutating,
      response: mutating ? '' : `${kind} response`,
      presentation: {
        artifactPresentation: mutating ? 'replace_on_revision' : 'preserve'
      }
    }
  },
  result: {
    application_planning_interrupt: {
      artifact: 'requirement_document',
      gateId: `requirement_document:${revision}`,
      artifactRevision: revision
    }
  }
})
const originalReviewWorkflow = {
  ...reviewWorkflow('requirement_change', 'revision-a', true),
  summary: { phase: 'product_planning', status: 'requires_user_input' }
} as WorkflowRunPayload
const reviewIdentityA = planningReviewIdentity(originalReviewWorkflow)
assert.ok(reviewIdentityA)
for (const kind of ['out_of_scope', 'read_only', 'clarification'] as const) {
  const nonMutatingWorkflow = reviewWorkflow(kind, 'revision-a', false)
  const messages: AgentChatMessage[] = [
    { id: 1, role: 'assistant', content: '', workflow: originalReviewWorkflow, createdAt: 1 },
    { id: 2, role: 'user', content: `${kind} request`, createdAt: 2 },
    {
      id: 3,
      role: 'assistant',
      content: `${kind} response`,
      workflow: nonMutatingWorkflow,
      createdAt: 3
    }
  ]
  assert.equal(isNonMutatingProductConversation(nonMutatingWorkflow), true)
  assert.equal(
    workflowMessageContentForDisplay(`${kind} response`, nonMutatingWorkflow, false),
    `${kind} response`
  )
  assert.equal(canonicalPlanningReviewMessageIndexes(messages).get(reviewIdentityA!), 0)
}
const nonMutatingWorkflowWithoutInterrupt = {
  ...reviewWorkflow('out_of_scope', 'revision-a', false),
  result: {}
} as WorkflowRunPayload
assert.equal(planningReviewIdentity(nonMutatingWorkflowWithoutInterrupt), undefined)
// 自由问答的回复帧不是确认权威；前端必须继续使用当前 planningWorkflow 的门禁身份。
assert.equal(planningReviewIdentity(originalReviewWorkflow), reviewIdentityA)
assert.equal(
  planningReviewMatchesActiveWorkflow(originalReviewWorkflow, originalReviewWorkflow),
  true
)
const originalUiReviewWorkflow = {
  ...originalReviewWorkflow,
  summary: { phase: 'ui_confirmation', status: 'requires_user_input' }
} as WorkflowRunPayload
const nonMutatingUiWorkflow = {
  ...reviewWorkflow('out_of_scope', 'revision-a', false),
  result: {
    ...reviewWorkflow('out_of_scope', 'revision-a', false).result,
    clarification: { mode: 'ui_design_confirmation', status: 'requires_user_input' }
  }
} as WorkflowRunPayload
const compactedNonMutatingUiMessages = compactPlanningMessageHistory([
  { id: 1, role: 'assistant', content: '', workflow: originalUiReviewWorkflow, createdAt: 1 },
  { id: 2, role: 'user', content: '修复另一个工程的 bug', createdAt: 2 },
  {
    id: 3,
    role: 'assistant',
    content: '请切换到目标工程的开发阶段处理。',
    workflow: nonMutatingUiWorkflow,
    createdAt: 3
  }
])
assert.deepEqual(
  compactedNonMutatingUiMessages.map((message) => message.id),
  [1, 2, 3]
)
const changedReviewWorkflow = reviewWorkflow('requirement_change', 'revision-b', true)
const changedReviewMessages: AgentChatMessage[] = [
  { id: 1, role: 'assistant', content: '', workflow: originalReviewWorkflow, createdAt: 1 },
  { id: 2, role: 'assistant', content: '', workflow: changedReviewWorkflow, createdAt: 2 }
]
const reviewIdentityB = planningReviewIdentity(changedReviewWorkflow)
assert.ok(reviewIdentityB)
assert.notEqual(reviewIdentityB, reviewIdentityA)
assert.equal(
  planningReviewMatchesActiveWorkflow(originalReviewWorkflow, changedReviewWorkflow),
  false
)
assert.equal(isNonMutatingProductConversation(changedReviewWorkflow), false)
assert.equal(canonicalPlanningReviewMessageIndexes(changedReviewMessages).get(reviewIdentityB!), 1)

const designRevisionInput = {
  request: '把订单页改成双列布局',
  target: { type: 'page', pageId: 'orders' },
  impact: {
    interactionId: 'impact-1',
    formalBranch: 'design_stage_revision',
    revisionType: 'ui_visual_change',
    earliestArtifact: 'ui-design',
    affectedArtifacts: ['ui-design', 'technical-plan'],
    affectedResources: ['page:orders'],
    reason: '页面布局发生变化',
    risks: []
  },
  sourceSessionId: 'source-session',
  sourceConversationThreadId: 'source-thread',
  sourceRunId: 'source-run'
} as WorkflowDesignStageRevisionStart
const designRevisionContext = createFormalRevisionSessionContext(
  designRevisionInput,
  'planning-graph-thread'
)
assert.deepEqual(designRevisionContext, {
  kind: 'formal_revision',
  sessionRole: 'design',
  formalBranch: 'design_stage_revision',
  impactInteractionId: 'impact-1',
  sourceSessionId: 'source-session',
  sourceConversationThreadId: 'source-thread',
  sourceRunId: 'source-run',
  planningThreadId: 'planning-graph-thread'
})
const initialPlanningEntryKey = planningStageTransitionKey(
  'planning-graph-thread',
  'ui-designs:initial-gate'
)
const revisionPlanningEntryKey = planningStageTransitionKey(
  'planning-graph-thread',
  'ui-designs:revision-gate',
  { ...designRevisionContext, changeId: 'change-1' }
)
assert.equal(revisionPlanningEntryKey, 'revision-plan:change-1:ui-designs:revision-gate')
assert.notEqual(revisionPlanningEntryKey, initialPlanningEntryKey)
assert.notEqual(
  planningStageTransitionKey('planning-graph-thread', 'ui-designs:revision-gate', {
    ...designRevisionContext,
    changeId: 'change-2'
  }),
  revisionPlanningEntryKey
)

const activeRevisionLifecycle = {
  activeFormalRevision: {
    changeId: 'change-1',
    formalBranch: 'design_stage_revision',
    impactInteractionId: 'impact-1',
    sourceThreadId: 'source-thread',
    sourceRunId: 'source-run',
    planningThreadId: 'planning-graph-thread',
    status: 'design_planning'
  }
} as ApplicationLifecycle
const revisionSessionBase = {
  title: '二次修改 · 产品 Agent',
  editorMode: 'frontend' as const,
  workbenchPhase: 'product' as const,
  workflowId: 'workflow-1',
  stage: 'DESIGN' as const,
  sequence: 2,
  entryKey: 'revision:design_stage_revision:impact-1',
  createdAt: 1,
  updatedAt: 1,
  messageCount: 1
}
const revisionSessionCandidates: ChatSessionSummary[] = [
  {
    ...revisionSessionBase,
    id: 'wrong-source',
    threadId: 'wrong-source-thread',
    revisionContext: {
      ...designRevisionContext,
      sourceConversationThreadId: 'another-source',
      changeId: 'change-1'
    }
  },
  {
    ...revisionSessionBase,
    id: 'wrong-change',
    threadId: 'wrong-change-thread',
    revisionContext: { ...designRevisionContext, changeId: 'change-2' }
  },
  {
    ...revisionSessionBase,
    id: 'wrong-planning',
    threadId: 'wrong-planning-thread',
    revisionContext: {
      ...designRevisionContext,
      planningThreadId: 'another-planning-thread',
      changeId: 'change-1'
    }
  },
  {
    ...revisionSessionBase,
    id: 'matching',
    threadId: 'revision-design-thread',
    revisionContext: { ...designRevisionContext, changeId: 'change-1' }
  },
  {
    ...revisionSessionBase,
    id: 'matching-plan',
    workbenchPhase: 'planning',
    stage: 'PLAN',
    sequence: 3,
    entryKey: 'revision-plan:change-1:gate-1',
    threadId: 'revision-plan-thread',
    revisionContext: { ...designRevisionContext, changeId: 'change-1' }
  }
]
assert.equal(
  activeFormalRevisionStageSession(
    revisionSessionCandidates,
    activeRevisionLifecycle,
    'workflow-1',
    'product'
  )?.threadId,
  'revision-design-thread'
)
assert.equal(
  formalRevisionPlanningSourceSession(
    [
      {
        ...revisionSessionBase,
        id: 'active-but-ordinary',
        threadId: 'ordinary-product-thread',
        revisionContext: undefined
      },
      ...revisionSessionCandidates
    ],
    activeRevisionLifecycle,
    'workflow-1'
  )?.threadId,
  'revision-design-thread'
)
assert.equal(
  activeFormalRevisionStageSession(
    [...revisionSessionCandidates].reverse(),
    activeRevisionLifecycle,
    'workflow-1',
    'planning'
  )?.threadId,
  'revision-plan-thread'
)
assert.equal(
  formalRevisionContinuationSourceSession(
    [
      {
        ...revisionSessionBase,
        id: 'orphan-plan',
        workbenchPhase: 'planning',
        stage: 'PLAN',
        entryKey: 'planning-entry:old-thread:old-gate',
        threadId: 'orphan-plan-thread',
        revisionContext: undefined
      },
      revisionSessionCandidates[3]
    ],
    activeRevisionLifecycle,
    'workflow-1'
  )?.threadId,
  'revision-design-thread'
)
assert.equal(initialFormalRevisionPhase('design_stage_revision'), 'product')
assert.equal(initialFormalRevisionPhase('workbench_plan_revision'), 'planning')

const workbenchRevisionContext = createFormalRevisionSessionContext(
  {
    ...designRevisionInput,
    impact: {
      ...designRevisionInput.impact,
      formalBranch: 'workbench_plan_revision',
      earliestArtifact: 'technical-plan'
    }
  },
  'planning-graph-thread'
)
assert.equal(workbenchRevisionContext.formalBranch, 'workbench_plan_revision')
assert.deepEqual(
  revisionContinuationFromWorkflow({
    runId: 'run-workbench-continuation',
    threadId: 'thread-workbench-continuation',
    events: [],
    summary: {
      revisionContinuation: {
        changeId: 'change-workbench',
        formalBranch: 'workbench_plan_revision',
        action: 'continue_revision_build',
        token: 't'.repeat(48),
        technicalPlanSha256: 'a'.repeat(64)
      }
    }
  } as WorkflowRunPayload),
  {
    changeId: 'change-workbench',
    formalBranch: 'workbench_plan_revision',
    action: 'continue_revision_build',
    token: 't'.repeat(48),
    technicalPlanSha256: 'a'.repeat(64)
  }
)
const continuationLifecycle = {
  ...activeRevisionLifecycle,
  activeFormalRevision: {
    ...activeRevisionLifecycle.activeFormalRevision,
    status: 'continuation_ready',
    technicalPlanSha256: 'a'.repeat(64)
  }
} as ApplicationLifecycle
const continuationWorkflow = {
  runId: 'run-workbench-continuation',
  threadId: 'thread-workbench-continuation',
  events: [],
  summary: {
    revisionContinuation: {
      changeId: 'change-1',
      formalBranch: 'design_stage_revision',
      action: 'continue_revision_build',
      token: 't'.repeat(48),
      technicalPlanSha256: 'a'.repeat(64)
    }
  },
  result: { lifecycle: continuationLifecycle }
} as WorkflowRunPayload
assert.deepEqual(revisionContinuationHandoffFromWorkflow(continuationWorkflow), {
  continuation: continuationWorkflow.summary.revisionContinuation,
  lifecycle: continuationLifecycle
})
assert.equal(
  revisionContinuationHandoffFromWorkflow({
    ...continuationWorkflow,
    result: {
      lifecycle: {
        ...continuationLifecycle,
        activeFormalRevision: {
          ...continuationLifecycle.activeFormalRevision,
          changeId: 'next-change',
          status: 'design_planning',
          technicalPlanSha256: null
        }
      }
    }
  } as WorkflowRunPayload),
  undefined
)
assert.throws(
  () =>
    revisionContinuationHandoffFromWorkflow({
      ...continuationWorkflow,
      result: {
        lifecycle: {
          ...continuationLifecycle,
          activeFormalRevision: {
            ...continuationLifecycle.activeFormalRevision,
            changeId: 'another-change'
          }
        }
      }
    } as WorkflowRunPayload),
  /缺少匹配的权威 lifecycle 快照/
)
assert.deepEqual(bindRevisionSessionChangeId(designRevisionContext, activeRevisionLifecycle), {
  ...designRevisionContext,
  changeId: 'change-1'
})

const boundDesignRevisionContext = bindRevisionSessionChangeId(
  designRevisionContext,
  activeRevisionLifecycle
)
const designRevisionIdentity = createSessionIdentity({
  workspaceRoot: '/workspace',
  editorMode: 'frontend',
  sessionId: 'revision-design-session',
  threadId: 'revision-design-thread',
  workflowId: 'workflow-1',
  workbenchPhase: 'planning',
  stage: 'PLAN',
  sequence: 3,
  entryKey: 'revision-plan:change-1:gate-1',
  revisionContext: boundDesignRevisionContext
})
assert.equal(
  isSameSessionExecutionScope(designRevisionIdentity, {
    ...designRevisionIdentity,
    key: 'another-session',
    sessionId: 'another-session',
    editorMode: 'backend'
  }),
  true
)
assert.equal(
  isSameSessionExecutionScope(designRevisionIdentity, {
    ...designRevisionIdentity,
    key: 'development-session',
    sessionId: 'development-session',
    workbenchPhase: 'development'
  }),
  false
)
assert.equal(
  isSessionExecutionOwner(
    { identity: designRevisionIdentity, status: 'running', conversation: false },
    designRevisionIdentity
  ),
  true
)
assert.equal(
  isSessionExecutionOwner(
    { identity: designRevisionIdentity, status: 'running', conversation: false },
    { ...designRevisionIdentity, key: 'other-session', sessionId: 'other-session' }
  ),
  false
)
const developmentContinuation = {
  changeId: 'change-1',
  formalBranch: 'design_stage_revision' as const,
  action: 'continue_revision_build' as const,
  token: 't'.repeat(48),
  technicalPlanSha256: 'b'.repeat(64)
}
const developmentSession = {
  ...revisionSessionBase,
  id: 'revision-development-session',
  threadId: 'revision-development-thread',
  workbenchPhase: 'development' as const,
  stage: 'DEVELOPMENT' as const,
  sequence: 1,
  entryKey: `revision-development:change-1:${'b'.repeat(64)}`,
  revisionContext: undefined
}
const oldDevelopmentSession = {
  ...developmentSession,
  id: 'old-development-session',
  threadId: 'old-development-thread',
  updatedAt: 999
}
const sourceDevelopmentSession = {
  ...developmentSession,
  id: 'source-session',
  threadId: 'source-thread',
  entryKey: 'development-entry:source',
  revisionContext: undefined
}
assert.equal(
  sessionToRestoreForPhase(
    [oldDevelopmentSession, developmentSession],
    'development',
    developmentSession.id,
    oldDevelopmentSession.id
  )?.id,
  developmentSession.id
)
assert.equal(
  sessionToRestoreForPhase(
    [oldDevelopmentSession, developmentSession],
    'development',
    'missing-explicit-session',
    oldDevelopmentSession.id
  ),
  undefined
)
assert.deepEqual(sessionsForWorkbenchPhase([oldDevelopmentSession], 'planning'), [])
assert.equal(
  revisionDevelopmentSessionForContinuation(
    [...revisionSessionCandidates, developmentSession, sourceDevelopmentSession],
    designRevisionIdentity,
    developmentContinuation
  )?.id,
  'source-session'
)
assert.equal(
  revisionDevelopmentSessionForContinuation(
    [...revisionSessionCandidates, developmentSession],
    { ...designRevisionIdentity, threadId: 'another-plan-thread' },
    developmentContinuation
  ),
  undefined
)
assert.equal(
  activeFormalRevisionStageSession(
    [developmentSession],
    activeRevisionLifecycle,
    'workflow-1',
    'planning'
  ),
  undefined
)
const existingDevelopmentMessages = [
  { id: 1, role: 'user', content: '原开发需求', createdAt: 1 },
  { id: 2, role: 'assistant', content: '原开发结果', createdAt: 2 },
  { id: 3, role: 'assistant', content: '', createdAt: 3 }
] as AgentChatMessage[]
const revisionDevelopmentEntryMessage = {
  id: 4,
  role: 'assistant',
  content: '',
  createdAt: 4,
  revisionHandoff: {
    kind: 'revision_development_entry',
    formalBranch: 'design_stage_revision',
    targetSessionId: 'source-session',
    targetConversationThreadId: 'source-thread',
    impactInteractionId: 'impact-1',
    changeId: 'change-1',
    request: '把订单页改成双列布局'
  }
} as AgentChatMessage
assert.deepEqual(
  appendRevisionDevelopmentEntryMessage(
    existingDevelopmentMessages,
    revisionDevelopmentEntryMessage
  ).map((message) => message.id),
  [1, 2, 4]
)
assert.deepEqual(
  bindRevisionSessionChangeId(
    { ...designRevisionContext, sourceRunId: 'another-run' },
    activeRevisionLifecycle
  ),
  { ...designRevisionContext, sourceRunId: 'another-run' }
)
assert.equal(
  normalizeRevisionSessionContext({
    kind: 'formal_revision',
    sessionRole: 'development',
    formalBranch: 'workbench_plan_revision',
    impactInteractionId: 'impact-1',
    sourceSessionId: 'source-session',
    sourceConversationThreadId: 'source-thread',
    sourceRunId: 'source-run',
    planningThreadId: 'planning-thread',
    changeId: 'change-1',
    handoffFromSessionId: 'planning-session',
    handoffFromConversationThreadId: 'planning-thread',
    technicalPlanSha256: 'a'.repeat(64)
  }),
  undefined
)

assert.equal(
  planningRequirementsConfirmed({
    state: { requirementsConfirmed: false },
    result: { requirementsConfirmed: true }
  } as WorkflowRunPayload),
  false
)

const regeneratedRequirementWithStaleTerminalConfirmation = {
  runId: 'run-requirement-revision',
  threadId: 'thread-requirement-revision',
  summary: {
    status: 'requires_user_input',
    phase: 'requirements'
  },
  events: [],
  state: {
    lifecycle: {
      initialization: { stage: 'awaiting_requirement_document_confirmation' }
    },
    technical_plan: { confirmation_status: 'pending_user_confirmation' }
  },
  result: {
    application_planning_confirmation: { confirmedAt: 'stale' },
    technical_plan: { confirmation_status: 'confirmed' }
  }
} as WorkflowRunPayload

assert.equal(
  planningTechnicalPlanConfirmed(regeneratedRequirementWithStaleTerminalConfirmation),
  false
)

const completedTechnicalPlanWorkflow = {
  ...regeneratedRequirementWithStaleTerminalConfirmation,
  summary: {
    status: 'completed',
    phase: 'technical_planning'
  },
  state: {
    lifecycle: { initialization: { stage: 'generating_application_template_files' } },
    technical_plan: { confirmation_status: 'confirmed' }
  }
} as WorkflowRunPayload

assert.equal(planningTechnicalPlanConfirmed(completedTechnicalPlanWorkflow), true)
assert.equal(
  planningRequirementsConfirmed({ state: { requirementsConfirmed: true } } as WorkflowRunPayload),
  true
)
assert.equal(
  planningRequirementsConfirmed({
    summary: { status: 'running', phase: 'ui_confirmation' },
    events: [
      {
        type: 'workflow.node.started',
        nodeName: 'technical_planning',
        status: 'running'
      }
    ],
    state: { requirementsConfirmed: false }
  } as WorkflowRunPayload),
  true
)
assert.equal(
  planningRequirementsConfirmed({
    state: { lifecycle: { initialization: { stage: 'generating_requirement_document' } } }
  } as WorkflowRunPayload),
  false
)
assert.equal(
  planningRequirementsConfirmed(undefined, '.xcodeagent/specs/requirement-spec.md'),
  true
)
assert.equal(
  planningRequirementsConfirmed(undefined, '.xcodeagent/drafts/specs/requirement-spec.md'),
  false
)
assert.equal(
  planningRequirementsConfirmed(
    { state: { requirementsConfirmed: false } } as WorkflowRunPayload,
    '.xcodeagent/specs/requirement-spec.md'
  ),
  false
)

const summaryOnlyQuestionsWorkflow = {
  runId: 'run-requirements',
  threadId: 'thread-requirements',
  summary: {
    status: 'requires_user_input',
    phase: 'requirements',
    clarification: {
      mode: 'ask_user_question',
      questions: [{ id: 'audience', prompt: '主要用户是谁？' }]
    }
  },
  events: [],
  state: {},
  result: {}
} as WorkflowRunPayload

assert.equal(planningWorkflowRequiresUserInput(summaryOnlyQuestionsWorkflow), false)
assert.equal(planningWorkflowCanPublishDuringRun(summaryOnlyQuestionsWorkflow), true)
assert.equal(
  planningWorkflowCanPublishDuringRun({
    ...summaryOnlyQuestionsWorkflow,
    summary: { status: 'running', phase: 'requirements' }
  } as WorkflowRunPayload),
  true
)
assert.equal(
  ensureApplicationPlanningAction(summaryOnlyQuestionsWorkflow, { audience: '运营人员' })
    .__applicationPlanningAction,
  'answer'
)
assert.equal(
  ensureApplicationPlanningAction(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: {
        ...summaryOnlyQuestionsWorkflow.summary,
        phase: 'product_planning',
        clarification: {
          mode: 'requirement_document_confirmation',
          status: 'requires_user_input',
          questions: []
        }
      }
    } as WorkflowRunPayload,
    { requirement_document_confirmation: '正确，继续规划' }
  ).__applicationPlanningAction,
  'confirm'
)
assert.equal(
  ensureApplicationPlanningAction(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: {
        ...summaryOnlyQuestionsWorkflow.summary,
        phase: 'product_planning',
        clarification: {
          mode: 'requirement_document_confirmation',
          status: 'requires_user_input',
          questions: []
        }
      }
    } as WorkflowRunPayload,
    { requirement_document_confirmation: '增加审批角色' }
  ).__applicationPlanningAction,
  'revise'
)
assert.equal(
  ensureApplicationPlanningAction(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: {
        ...summaryOnlyQuestionsWorkflow.summary,
        phase: 'planning_stage_entry',
        clarification: {
          mode: 'planning_stage_entry_confirmation',
          status: 'requires_user_input',
          questions: []
        }
      }
    } as WorkflowRunPayload,
    { planning_stage_entry: 'enter' }
  ).__applicationPlanningAction,
  'enter_planning'
)
assert.equal(
  latestUiDesignPreviewMessageIndex([
    {
      id: 1,
      role: 'assistant',
      content: '',
      createdAt: 1,
      workflow: {
        ...summaryOnlyQuestionsWorkflow,
        summary: { status: 'requires_user_input', phase: 'ui_confirmation' }
      } as WorkflowRunPayload
    },
    { id: 2, role: 'user', content: '换一个模板', createdAt: 2 },
    {
      id: 3,
      role: 'assistant',
      content: '',
      createdAt: 3,
      workflow: {
        ...summaryOnlyQuestionsWorkflow,
        summary: {
          status: 'requires_user_input',
          phase: 'ui_confirmation',
          clarification: { mode: 'ui_design_confirmation', status: 'requires_user_input' }
        }
      } as WorkflowRunPayload
    }
  ] as AgentChatMessage[]),
  2
)

const compactedPlanningMessages = compactPlanningMessageHistory([
  {
    id: 1,
    role: 'assistant',
    content: '',
    createdAt: 1,
    workflow: {
      ...summaryOnlyQuestionsWorkflow,
      summary: { status: 'requires_user_input', phase: 'ui_confirmation' }
    } as WorkflowRunPayload
  },
  {
    id: 2,
    role: 'assistant',
    content: '',
    createdAt: 2,
    workflow: {
      ...summaryOnlyQuestionsWorkflow,
      summary: {
        status: 'requires_user_input',
        phase: 'ui_confirmation',
        clarification: { mode: 'ui_design_confirmation', status: 'requires_user_input' }
      }
    } as WorkflowRunPayload
  },
  {
    id: 3,
    role: 'assistant',
    content: '当前阶段需要你的确认后继续。',
    createdAt: 3,
    workflow: {
      ...summaryOnlyQuestionsWorkflow,
      summary: {
        status: 'requires_user_input',
        phase: 'planning_stage_entry',
        clarification: {
          mode: 'planning_stage_entry_confirmation',
          status: 'requires_user_input'
        }
      }
    } as WorkflowRunPayload
  },
  { id: 4, role: 'user', content: '进入计划阶段', createdAt: 4 },
  {
    id: 5,
    role: 'assistant',
    content: '',
    createdAt: 5,
    workflow: {
      ...summaryOnlyQuestionsWorkflow,
      summary: { status: 'failed', phase: 'failed' }
    } as WorkflowRunPayload
  }
] as AgentChatMessage[])
assert.deepEqual(
  compactedPlanningMessages.map((message) => message.id),
  [2, 3]
)

const planningHandoffMessages = [
  {
    id: 10,
    role: 'assistant',
    content: '',
    createdAt: 10,
    workflow: {
      ...summaryOnlyQuestionsWorkflow,
      summary: {
        status: 'requires_user_input',
        phase: 'planning_stage_entry',
        clarification: {
          mode: 'planning_stage_entry_confirmation',
          status: 'requires_user_input'
        }
      }
    } as WorkflowRunPayload
  },
  {
    id: 11,
    role: 'assistant',
    content: '',
    createdAt: 11,
    workflow: {
      ...summaryOnlyQuestionsWorkflow,
      summary: { status: 'running', phase: 'technical_planning' }
    } as WorkflowRunPayload
  }
] as AgentChatMessage[]

assert.equal(isSupersededPlanningStageEntryMessage(planningHandoffMessages, 0), true)
assert.deepEqual(
  compactPlanningMessageHistory(planningHandoffMessages).map((message) => message.id),
  [11]
)
const confirmedTechnicalPlanTransitionMessages = [
  planningHandoffMessages[1],
  {
    id: 12,
    role: 'assistant',
    content: '',
    createdAt: 12,
    revisionHandoff: {
      kind: 'revision_development',
      formalBranch: 'design_stage_revision',
      targetSessionId: 'development-session',
      targetConversationThreadId: 'development-thread',
      impactInteractionId: 'impact-1',
      changeId: 'change-1',
      request: '删除导出功能'
    }
  }
] as AgentChatMessage[]
assert.equal(
  isSupersededTechnicalPlanTransitionMessage(confirmedTechnicalPlanTransitionMessages, 0),
  true
)
assert.deepEqual(
  compactPlanningMessageHistory(confirmedTechnicalPlanTransitionMessages).map(
    (message) => message.id
  ),
  [12]
)
assert.equal(planningWorkflowSettlesLoading(summaryOnlyQuestionsWorkflow), true)
assert.equal(
  planningWorkflowNeedsChatLoading(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: { status: 'running', phase: 'requirements' }
    } as WorkflowRunPayload,
    true,
    false,
    false,
    ''
  ),
  true
)
assert.equal(
  planningWorkflowNeedsChatLoading(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: { status: 'running', phase: 'requirements' }
    } as WorkflowRunPayload,
    true,
    false,
    false,
    '正在分析需求'
  ),
  false
)
assert.equal(
  planningWorkflowNeedsChatLoading(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: { status: 'running', phase: 'requirements' }
    } as WorkflowRunPayload,
    true,
    false,
    true,
    ''
  ),
  false
)
assert.equal(
  planningWorkflowNeedsChatLoading(summaryOnlyQuestionsWorkflow, true, true, true, ''),
  false
)
// TechnicalPlan 二次修改确认后的主 Workflow 已进入开发前置门禁，
// 规划会话不应再显示“恢复计划阶段”的 loading 占位。
assert.equal(
  planningWorkflowNeedsChatLoading(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: { status: 'running', phase: 'development_readiness_gate' }
    } as WorkflowRunPayload,
    true,
    false,
    false,
    ''
  ),
  false
)
assert.equal(shouldSuppressConfirmedTechnicalPlanTransitionChunk(undefined, true), true)
assert.equal(
  shouldSuppressConfirmedTechnicalPlanTransitionChunk(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: { status: 'running', phase: 'technical_planning' }
    } as WorkflowRunPayload,
    true
  ),
  true
)
assert.equal(
  shouldSuppressConfirmedTechnicalPlanTransitionChunk(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: { status: 'running', phase: 'development_readiness_gate' }
    } as WorkflowRunPayload,
    true
  ),
  false
)
assert.equal(
  shouldSuppressConfirmedTechnicalPlanTransitionChunk(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: { status: 'failed', phase: 'technical_planning' }
    } as WorkflowRunPayload,
    true
  ),
  false
)
assert.equal(shouldSuppressConfirmedTechnicalPlanTransitionChunk(undefined, false), false)

const pendingQuestionMessage = {
  id: 12,
  role: 'assistant',
  content: '',
  createdAt: 12,
  workflow: summaryOnlyQuestionsWorkflow
} as AgentChatMessage
const staleLoadingPlaceholder = {
  id: 13,
  role: 'assistant',
  content: '',
  createdAt: 13,
  planningLoading: true
} as AgentChatMessage
assert.deepEqual(
  appendPlanningLoadingPlaceholder([pendingQuestionMessage], staleLoadingPlaceholder),
  [pendingQuestionMessage]
)
assert.deepEqual(appendPlanningLoadingPlaceholder([], staleLoadingPlaceholder), [
  staleLoadingPlaceholder
])

const runningRequirementsMessage = {
  id: 14,
  role: 'assistant',
  content: '',
  createdAt: 14,
  workflow: {
    ...summaryOnlyQuestionsWorkflow,
    summary: { status: 'running', phase: 'requirements' }
  } as WorkflowRunPayload
} as AgentChatMessage
const requirementDraftConfirmationMessage = {
  id: 15,
  role: 'assistant',
  content: '',
  createdAt: 15,
  workflow: summaryOnlyQuestionsWorkflow
} as AgentChatMessage
assert.equal(isSupersededPlanningProgressMessage(runningRequirementsMessage, 15), true)
assert.equal(isSupersededPlanningProgressMessage(staleLoadingPlaceholder, 15), true)
assert.equal(isTemplateSupersededPlanningProgressMessage(staleLoadingPlaceholder, true), true)
assert.equal(isTemplateSupersededPlanningProgressMessage(runningRequirementsMessage, true), true)
assert.equal(
  isTemplateSupersededPlanningProgressMessage(requirementDraftConfirmationMessage, true),
  false
)
assert.equal(isTemplateSupersededPlanningProgressMessage(staleLoadingPlaceholder, false), false)
assert.deepEqual(
  compactPlanningMessageHistory([
    runningRequirementsMessage,
    staleLoadingPlaceholder,
    requirementDraftConfirmationMessage
  ]).map((message) => message.id),
  [15]
)

const clarificationOnlyQuestionsWorkflow = {
  ...summaryOnlyQuestionsWorkflow,
  summary: {
    ...summaryOnlyQuestionsWorkflow.summary,
    status: 'running',
    clarification: {
      ...summaryOnlyQuestionsWorkflow.summary.clarification,
      status: 'requires_user_input'
    }
  }
} as WorkflowRunPayload

assert.equal(planningWorkflowRequiresUserInput(clarificationOnlyQuestionsWorkflow), false)
assert.equal(planningWorkflowIsActivelyRunning(clarificationOnlyQuestionsWorkflow), true)
assert.equal(planningWorkflowSettlesLoading(clarificationOnlyQuestionsWorkflow), false)
assert.equal(
  planningWorkflowIsActivelyRunning({
    ...clarificationOnlyQuestionsWorkflow,
    summary: { status: 'running', phase: 'requirements' }
  } as WorkflowRunPayload),
  true
)
assert.equal(shouldBackfillPlanningWorkflow(summaryOnlyQuestionsWorkflow, false), false)
assert.equal(shouldBackfillPlanningWorkflow(summaryOnlyQuestionsWorkflow, true), false)

const previousRunWithInterrupt = {
  ...summaryOnlyQuestionsWorkflow,
  runId: 'previous-run',
  result: {
    application_planning_interrupt: {
      gateId: 'requirement_spec:previous',
      artifact: 'requirement_spec',
      artifactRevision: 'previous'
    }
  }
} as WorkflowRunPayload
const nextRunWithoutInterrupt = {
  ...summaryOnlyQuestionsWorkflow,
  runId: 'next-run',
  summary: { status: 'running', phase: 'requirements' },
  result: {}
} as WorkflowRunPayload
assert.equal(
  retainApplicationPlanningInterrupt(previousRunWithInterrupt, nextRunWithoutInterrupt).result
    ?.application_planning_interrupt,
  undefined
)

const technicalPlanningWithStaleEntry = {
  ...summaryOnlyQuestionsWorkflow,
  runId: 'technical-planning-run',
  summary: { status: 'running', phase: 'technical_planning' },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'technical_planning',
      status: 'running'
    }
  ],
  state: {
    clarification: {
      mode: 'planning_stage_entry_confirmation',
      status: 'requires_user_input'
    }
  },
  result: {}
} as WorkflowRunPayload

assert.equal(planningWorkflowRequiresUserInput(technicalPlanningWithStaleEntry), false)
assert.equal(planningWorkflowCanPublishDuringRun(technicalPlanningWithStaleEntry), true)
assert.equal(
  planningWorkflowNeedsChatLoading(technicalPlanningWithStaleEntry, true, false, false, '', true),
  true
)

const latePlanningEntryFrame = {
  ...technicalPlanningWithStaleEntry,
  summary: {
    status: 'requires_user_input',
    phase: 'planning_stage_entry',
    clarification: {
      mode: 'planning_stage_entry_confirmation',
      status: 'requires_user_input'
    }
  },
  events: []
} as WorkflowRunPayload

assert.equal(
  retainApplicationPlanningInterrupt(technicalPlanningWithStaleEntry, latePlanningEntryFrame)
    .summary.phase,
  'technical_planning'
)

const awaitingPlanningEntryWithStaleTechnicalProjection = {
  ...summaryOnlyQuestionsWorkflow,
  summary: {
    status: 'requires_user_input',
    phase: 'technical_planning',
    clarification: {
      mode: 'planning_stage_entry_confirmation',
      status: 'requires_user_input'
    }
  },
  state: {
    lifecycle: {
      initialization: { stage: 'awaiting_planning_stage_entry', status: 'awaiting_user' }
    },
    application_planning_interrupt: {
      type: 'application_planning_review',
      gateId: 'ui-designs:entry',
      artifact: 'ui_designs',
      artifactRevision: 'entry'
    }
  },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'technical_planning',
      status: 'running'
    }
  ]
} as WorkflowRunPayload

assert.equal(
  planningWorkflowPhase(awaitingPlanningEntryWithStaleTechnicalProjection),
  'planning_stage_entry'
)
assert.equal(
  planningWorkflowRequiresUserInput(awaitingPlanningEntryWithStaleTechnicalProjection),
  true
)
assert.equal(
  planningWorkflowUiDesignSkipped({
    ...awaitingPlanningEntryWithStaleTechnicalProjection,
    state: {
      ...awaitingPlanningEntryWithStaleTechnicalProjection.state,
      clarification: { ui_design_skipped: true },
      ui_designs: { confirmation_status: 'skipped', pages: [{ pageId: 'old-page' }] }
    }
  } as WorkflowRunPayload),
  true
)
const entryAuthoritativeWorkflow = {
  ...summaryOnlyQuestionsWorkflow,
  summary: {
    status: 'requires_user_input',
    phase: 'planning_stage_entry',
    clarification: {
      mode: 'planning_stage_entry_confirmation',
      status: 'requires_user_input'
    }
  }
} as WorkflowRunPayload
const retainedUiDesignMessage = {
  id: 20,
  role: 'assistant',
  content: '',
  createdAt: 20,
  workflow: {
    ...summaryOnlyQuestionsWorkflow,
    summary: { status: 'requires_user_input', phase: 'ui_confirmation' }
  } as WorkflowRunPayload
} as AgentChatMessage
assert.equal(
  isSupersededPlanningPhaseMessage(retainedUiDesignMessage, 'planning_stage_entry'),
  false
)
assert.equal(isSupersededPlanningPhaseMessage(retainedUiDesignMessage, 'technical_planning'), false)
assert.deepEqual(
  compactPlanningMessageHistory(
    [
      retainedUiDesignMessage,
      {
        id: 21,
        role: 'assistant',
        content: '',
        createdAt: 21,
        workflow: {
          ...summaryOnlyQuestionsWorkflow,
          summary: { status: 'running', phase: 'technical_planning' }
        } as WorkflowRunPayload
      },
      {
        id: 22,
        role: 'assistant',
        content: '请确认是否进入计划阶段。',
        createdAt: 22,
        workflow: entryAuthoritativeWorkflow
      }
    ] as AgentChatMessage[],
    entryAuthoritativeWorkflow
  ).map((message) => message.id),
  [20, 22]
)

const analyzingDesignIntentWorkflow = {
  runId: 'run-design-change',
  threadId: 'thread-design-change',
  summary: {
    status: 'running',
    phase: 'design_intent_analysis'
  },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'design_intent_analysis',
      status: 'running'
    }
  ],
  state: {},
  result: {}
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(analyzingDesignIntentWorkflow), {
  status: 'running',
  title: '正在识别设计变更意图',
  detail: '正在判断这次输入属于需求事实、产品行为还是 UI 设计。'
})

const initialRequirementWorkflow = {
  ...analyzingDesignIntentWorkflow,
  summary: {
    status: 'running',
    phase: 'requirements'
  },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'requirements',
      status: 'running'
    }
  ]
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(initialRequirementWorkflow), {
  status: 'running',
  title: '正在分析需求',
  detail: '正在识别产品目标、用户角色、页面与业务流程中的信息缺口。',
  intentLabel: undefined
})

const generatingRequirementDocumentWorkflow = {
  ...initialRequirementWorkflow,
  summary: {
    status: 'running',
    phase: 'product_planning'
  },
  state: {
    lifecycle: {
      initialization: { stage: 'generating_requirement_document' }
    }
  }
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(generatingRequirementDocumentWorkflow), {
  status: 'running',
  title: '正在生成需求文档',
  detail: '正在把已确认的需求草稿写入正式 Markdown 文档。',
  intentLabel: undefined
})

const initialProductPlanningWorkflow = {
  ...initialRequirementWorkflow,
  summary: {
    status: 'running',
    phase: 'product_planning'
  },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'product_planning',
      status: 'running'
    }
  ]
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(initialProductPlanningWorkflow), {
  status: 'running',
  title: '正在整理需求',
  detail: '正在梳理页面目标、核心操作、状态与产品验收标准。',
  intentLabel: undefined
})

const firstProductPlanWithPendingArtifact = {
  ...initialProductPlanningWorkflow,
  state: {
    product_plan: { confirmation_status: 'pending_user_confirmation' },
    design_change_submission: true,
    design_change_target: 'product_planning'
  }
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(firstProductPlanWithPendingArtifact), {
  status: 'running',
  title: '正在整理需求',
  detail: '正在梳理页面目标、核心操作、状态与产品验收标准。',
  intentLabel: undefined
})

const initialUiDesignWorkflow = {
  ...initialRequirementWorkflow,
  summary: {
    status: 'running',
    phase: 'ui_confirmation'
  },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'ui_confirmation',
      status: 'running'
    }
  ]
} as WorkflowRunPayload

assert.equal(planningWorkflowActivity(initialUiDesignWorkflow), undefined)

const revisingRequirementWorkflow = {
  ...initialRequirementWorkflow,
  state: {
    design_change_submission: true,
    design_change_existing_artifacts: {
      requirements: true,
      product_planning: true,
      ui_confirmation: true,
      technical_planning: false
    }
  },
  events: [
    {
      type: 'workflow.node.completed',
      nodeName: 'design_intent_analysis',
      status: 'completed',
      data: { detail: { target: 'requirements' } }
    },
    {
      type: 'workflow.node.started',
      nodeName: 'requirements',
      status: 'running'
    }
  ]
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(revisingRequirementWorkflow), {
  status: 'running',
  title: '正在重新分析需求',
  detail: '正在合并本次补充，并保留未受影响的需求事实。',
  intentLabel: '需求层变更'
})

const classifiedRequirementWorkflow = {
  ...revisingRequirementWorkflow,
  summary: {
    status: 'completed',
    phase: 'design_intent_analysis'
  },
  state: {
    design_change_submission: true,
    design_change_request: '新增报表页',
    design_change_target: 'requirements',
    design_change_existing_artifacts: {
      requirements: true
    }
  },
  events: [
    {
      type: 'workflow.node.completed',
      nodeName: 'design_intent_analysis',
      status: 'completed',
      data: { detail: { target: 'requirements' } }
    }
  ]
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(classifiedRequirementWorkflow), {
  status: 'running',
  title: '正在重新分析需求',
  detail: '正在合并本次补充，并保留未受影响的需求事实。',
  intentLabel: '需求层变更'
})

const regeneratingProductPlanWorkflow = {
  ...analyzingDesignIntentWorkflow,
  summary: {
    status: 'running',
    phase: 'product_planning'
  },
  events: [
    {
      type: 'workflow.node.completed',
      nodeName: 'design_intent_analysis',
      status: 'completed',
      data: {
        detail: {
          target: 'product_planning',
          reason: '页面操作和可见结果发生变化'
        }
      }
    },
    {
      type: 'workflow.node.started',
      nodeName: 'product_planning',
      status: 'running'
    }
  ],
  state: {
    design_change_submission: true,
    design_change_existing_artifacts: {
      product_planning: true
    }
  },
  result: {}
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(regeneratingProductPlanWorkflow), {
  status: 'running',
  title: '正在调整需求',
  detail: '页面操作和可见结果发生变化',
  intentLabel: '产品行为调整'
})

const regeneratingUiDesignWorkflow = {
  ...regeneratingProductPlanWorkflow,
  summary: {
    status: 'running',
    phase: 'ui_confirmation'
  },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'ui_confirmation',
      status: 'running'
    }
  ],
  state: {
    design_change_submission: true,
    design_change_target: 'ui_confirmation',
    design_change_existing_artifacts: {
      ui_confirmation: true
    }
  }
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(regeneratingUiDesignWorkflow), {
  status: 'running',
  title: '正在重新生成 UI 设计稿',
  detail: '正在更新受影响页面的布局、视觉与交互呈现。',
  intentLabel: 'UI 设计层变更'
})

const firstTechnicalPlanAfterDesignRevision = {
  ...regeneratingProductPlanWorkflow,
  summary: {
    status: 'running',
    phase: 'technical_planning'
  },
  events: [
    ...regeneratingProductPlanWorkflow.events,
    {
      type: 'workflow.node.started',
      nodeName: 'technical_planning',
      status: 'running'
    }
  ],
  state: {
    design_change_submission: true,
    design_change_target: 'product_planning',
    design_change_existing_artifacts: {
      product_planning: true,
      ui_confirmation: true,
      technical_planning: false
    }
  }
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(firstTechnicalPlanAfterDesignRevision), {
  status: 'running',
  title: '正在生成技术规划',
  detail: '正在根据已确认的上游设计生成技术实现方案。',
  intentLabel: undefined
})

const regeneratingTechnicalPlan = {
  ...firstTechnicalPlanAfterDesignRevision,
  state: {
    ...firstTechnicalPlanAfterDesignRevision.state,
    design_change_existing_artifacts: {
      technical_planning: true
    }
  }
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(regeneratingTechnicalPlan), {
  status: 'running',
  title: '正在重新生成技术规划',
  detail: '正在根据本次设计变更更新技术实现方案。',
  intentLabel: '产品行为调整'
})

const uiCardActionWithHistoricalIntent = {
  ...regeneratingProductPlanWorkflow,
  summary: {
    status: 'running',
    phase: 'ui_confirmation'
  },
  state: {
    design_change_submission: false,
    design_change_target: 'ui_confirmation'
  }
} as WorkflowRunPayload

assert.equal(planningWorkflowActivity(uiCardActionWithHistoricalIntent), undefined)
