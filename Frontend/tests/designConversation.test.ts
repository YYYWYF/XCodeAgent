import assert from 'node:assert/strict'

import { buildWorkflowForwardedProps } from '../src/renderer/src/service/agUiAgent'
import {
  appendPlanningLoadingPlaceholder,
  compactPlanningMessageHistory,
  isSupersededPlanningPhaseMessage,
  isSupersededPlanningProgressMessage,
  isSupersededPlanningStageEntryMessage,
  isTemplateSupersededPlanningProgressMessage,
  latestUiDesignPreviewMessageIndex
} from '../src/renderer/src/components/AiChatPanel/components/MessageList/uiDesignPreviewHistory'
import {
  ensureApplicationPlanningAction,
  planningRequirementsConfirmed,
  planningTechnicalPlanConfirmed,
  planningWorkflowNeedsChatLoading,
  planningWorkflowActivity,
  planningWorkflowCanPublishDuringRun,
  planningWorkflowPhase,
  planningWorkflowRequiresUserInput,
  planningWorkflowSettlesLoading,
  planningWorkflowUiDesignSkipped,
  retainApplicationPlanningInterrupt,
  shouldBackfillPlanningWorkflow,
  shouldCreatePlanningWindow
} from '../src/renderer/src/components/Welcome/planningWorkflowState'
import type {
  ApplicationLifecycle,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../src/renderer/src/typings'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import { sessionsForPlanningThread } from '../src/renderer/src/components/AiChatPanel/hooks/phaseSessionSelection'
import {
  activeFormalRevisionConversationThreadId,
  bindRevisionSessionChangeId,
  createFormalRevisionSessionContext,
  createRevisionDevelopmentSessionContext,
  formalRevisionSessionPhase,
  revisionDevelopmentSessionForContinuation
} from '../src/renderer/src/components/AiChatPanel/hooks/revisionSession'
import { createSessionIdentity } from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import type { ChatSessionSummary } from '../src/renderer/src/service/chatSessions'
import { revisionContinuationFromWorkflow } from '../src/renderer/src/service/applicationPagePlanning'

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

const phasePlanningSessions = [
  { id: 'product', threadId: 'shared-thread', workbenchPhase: 'product' as const },
  { id: 'planning', threadId: 'shared-thread', workbenchPhase: 'planning' as const },
  { id: 'other', threadId: 'other-thread', workbenchPhase: 'planning' as const }
]
assert.deepEqual(sessionsForPlanningThread(phasePlanningSessions, 'planning', 'shared-thread'), [
  phasePlanningSessions[1]
])

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
    threadId: 'revision-conversation-thread',
    revisionContext: { ...designRevisionContext, changeId: 'change-1' }
  }
]
assert.equal(
  activeFormalRevisionConversationThreadId(revisionSessionCandidates, activeRevisionLifecycle),
  'revision-conversation-thread'
)
assert.equal(
  activeFormalRevisionConversationThreadId(
    revisionSessionCandidates.slice(0, 3),
    activeRevisionLifecycle
  ),
  undefined
)
assert.equal(formalRevisionSessionPhase('design_stage_revision'), 'product')
assert.equal(formalRevisionSessionPhase('workbench_plan_revision'), 'planning')

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
  revisionContext: boundDesignRevisionContext
})
const developmentContinuation = {
  changeId: 'change-1',
  formalBranch: 'design_stage_revision' as const,
  action: 'continue_revision_build' as const,
  token: 't'.repeat(48),
  technicalPlanSha256: 'b'.repeat(64)
}
const developmentRevisionContext = createRevisionDevelopmentSessionContext(
  designRevisionIdentity,
  developmentContinuation
)
assert.deepEqual(developmentRevisionContext, {
  ...boundDesignRevisionContext,
  sessionRole: 'development',
  changeId: 'change-1',
  handoffFromSessionId: 'revision-design-session',
  handoffFromConversationThreadId: 'revision-design-thread',
  technicalPlanSha256: 'b'.repeat(64)
})
const developmentSession = {
  ...revisionSessionBase,
  id: 'revision-development-session',
  threadId: 'revision-development-thread',
  workbenchPhase: 'development' as const,
  revisionContext: developmentRevisionContext
}
assert.equal(
  revisionDevelopmentSessionForContinuation(
    [...revisionSessionCandidates, developmentSession],
    developmentContinuation
  )?.id,
  'revision-development-session'
)
assert.equal(
  activeFormalRevisionConversationThreadId(
    [developmentSession],
    activeRevisionLifecycle
  ),
  undefined
)
assert.deepEqual(
  bindRevisionSessionChangeId(
    { ...designRevisionContext, sourceRunId: 'another-run' },
    activeRevisionLifecycle
  ),
  { ...designRevisionContext, sourceRunId: 'another-run' }
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

assert.equal(planningWorkflowRequiresUserInput(summaryOnlyQuestionsWorkflow), true)
assert.equal(planningWorkflowCanPublishDuringRun(summaryOnlyQuestionsWorkflow), false)
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
  { id: 4, role: 'user', content: '进入规划阶段', createdAt: 4 },
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
// 规划会话不应再显示“恢复规划阶段”的 loading 占位。
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

assert.equal(planningWorkflowRequiresUserInput(clarificationOnlyQuestionsWorkflow), true)
assert.equal(planningWorkflowSettlesLoading(clarificationOnlyQuestionsWorkflow), true)
assert.equal(shouldBackfillPlanningWorkflow(summaryOnlyQuestionsWorkflow, false), true)
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
  planningWorkflowNeedsChatLoading(
    technicalPlanningWithStaleEntry,
    true,
    false,
    false,
    '',
    true
  ),
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
assert.equal(shouldCreatePlanningWindow(undefined), true)
assert.equal(shouldCreatePlanningWindow('planning-conversation-thread'), false)

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
assert.equal(isSupersededPlanningPhaseMessage(retainedUiDesignMessage, 'planning_stage_entry'), false)
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
        content: '请确认是否进入规划阶段。',
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
  detail: '正在判断这次改动应回到需求、产品规划还是 UI 设计阶段。'
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
  title: '正在生成产品规划',
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
  title: '正在生成产品规划',
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
  title: '正在重新生成产品规划',
  detail: '页面操作和可见结果发生变化',
  intentLabel: '产品规划层变更'
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
  intentLabel: '产品规划层变更'
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
