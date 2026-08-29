import { useRef, useState } from 'react'
import type { MutableRefObject, SetStateAction } from 'react'
import {
  AgUiChatSession,
  AgUiRunError,
  getConversationUrl,
  getWorkflowUrl
} from '../../../service/agUiAgent'
import {
  getApplicationPlanningUrl,
  revisionContinuationFromWorkflow
} from '../../../service/applicationPagePlanning'
import type { ProcessStepRecord, ToolCallRecord } from '../../../service/agUiAgent'
import { isAuthenticationFailure } from '../../../service/authentication'
import type {
  ApplicationConfig,
  ApplicationPlanningInteraction,
  ApplicationLifecycle,
  ChatMessageSkill,
  EditorMode,
  WorkflowBuildExecutionScope,
  WorkflowBuildTaskPlanConfirmation,
  WorkflowDebugOptions,
  WorkflowAction,
  WorkflowDesignStageRevisionStart,
  WorkflowWorkbenchPlanRevisionStart,
  WorkflowRevisionDraftInteraction,
  WorkflowRevisionImpact,
  WorkflowRevisionContinuation,
  WorkflowRunPayload,
  WorkflowTestTarget
} from '../../../typings'
import {
  isConversationWorkflow,
  shouldUseConversation,
  type ChatInputMode
} from '../conversationMode'
import {
  buildClarificationContinuationMessage,
  workflowOriginalRequest,
  type ClarificationAnswers
} from '../components/WorkflowRunCard'
import { workflowClarification } from '../components/WorkflowRunCard/workflowClarification'
import type { AgentChatMessage } from '../types'
import {
  beginOptimisticSkillSend,
  rollbackSkillSelection,
  selectedSkillNames
} from '../skillSelection'
import { stoppedAnswer, workflowCodeChanges, workflowPreviewTarget } from '../utils'
import type { WorkflowPreviewTarget } from '../utils'
import type {
  PersistSessionInput,
  AcceptancePhaseSessionTarget,
  ReviewPhaseSessionTarget,
  TestPhaseSessionTarget
} from './useChatSessions'
import {
  sessionIdentityMatchesTarget,
  type SessionIdentity,
  type SessionRunStatus
} from './sessionRuntime'
import {
  planExecutionForPage,
  withWorkflowExecutionStatus,
  workflowCodeReviewRetry,
  workflowInteractionAvailability
} from '../planExecutionMode'

type SessionRunEntry = {
  identity: SessionIdentity
  status: SessionRunStatus
  conversation: boolean
}

type ConversationTarget =
  | {
      type: 'page'
      pageId: string
    }
  | {
      type: 'endpoint'
      apiContractId: string
      endpointId: string
    }

/** 从会话身份提取当前页面或接口目标，让“这个页面”等指代随普通自然语言请求到达后端。 */
function conversationTargetFromIdentity(identity: SessionIdentity): ConversationTarget | undefined {
  if (identity.apiContractId && identity.endpointId) {
    return {
      type: 'endpoint',
      apiContractId: identity.apiContractId,
      endpointId: identity.endpointId
    }
  }
  if (identity.pageId) {
    return {
      type: 'page',
      pageId: identity.pageId
    }
  }
  return undefined
}

type UseWorkflowConversationParams = {
  activeSession?: SessionIdentity
  agUiSessionsRef: MutableRefObject<Record<string, AgUiChatSession>>
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  draft: string
  draftKey: string
  selectedSkills: ChatMessageSkill[]
  selectedApiContractId?: string
  selectedEndpointId?: string
  selectedEndpointLabel?: string
  selectedEntityId?: string
  selectedEntityLabel?: string
  selectedPageId?: string
  selectedPageLabel?: string
  conversationEnabled: boolean
  inputMode: ChatInputMode
  editorMode: EditorMode
  createTestSession: (target: TestPhaseSessionTarget) => Promise<SessionIdentity>
  createReviewSession: (target: ReviewPhaseSessionTarget) => Promise<SessionIdentity>
  createAcceptanceSession: (target: AcceptancePhaseSessionTarget) => Promise<SessionIdentity>
  acceptanceConversationSessionKey?: string
  ensureActiveSession: () => Promise<SessionIdentity>
  ensureEndpointSession: (
    apiContractId: string,
    endpointId: string,
    endpointLabel: string
  ) => Promise<SessionIdentity>
  ensureEntitySession: (entityId: string, entityLabel: string) => Promise<SessionIdentity>
  ensurePageSession: (pageId: string, pageLabel: string) => Promise<SessionIdentity>
  getSessionMessages: (sessionKey: string) => AgentChatMessage[]
  persistSession: (input: PersistSessionInput) => Promise<void>
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onStartDesignStageRevision: (input: WorkflowDesignStageRevisionStart) => Promise<void>
  onStartWorkbenchPlanRevision: (
    input: WorkflowWorkbenchPlanRevisionStart
  ) => Promise<SessionIdentity>
  onRevisionContinuation: (continuation: WorkflowRevisionContinuation) => Promise<void>
  onEnterTestPhase: () => void
  onEnterReviewPhase: () => void
  onEnterAcceptancePhase: () => void
  onPreviewReady: (target: WorkflowPreviewTarget) => void
  publishAiMessage: (mode: EditorMode, content: string) => void
  runningSessionsRef: MutableRefObject<Map<string, SessionIdentity>>
  setDraftByKey: (sessionKey: string, value: string) => void
  setSelectedSkillsByKey: (sessionKey: string, value: ChatMessageSkill[]) => void
  setSessionMessages: (sessionKey: string, value: SetStateAction<AgentChatMessage[]>) => void
}

type UseWorkflowConversationResult = {
  activeWorkflow?: WorkflowRunPayload
  conversationRunning: boolean
  error?: string
  handleAcceptPreview: () => Promise<boolean>
  handleContinueRevisionBuild: (
    continuation: WorkflowRevisionContinuation,
    sessionIdentity: SessionIdentity
  ) => Promise<boolean>
  handleEndPlan: (runId?: string) => Promise<void>
  handleResumePlan: (workflowDebug?: WorkflowDebugOptions) => Promise<void>
  handleRetryCodeReview: () => Promise<void>
  handleRetryPlan: () => Promise<void>
  handleStopPlan: (runId?: string) => Promise<void>
  handleSend: (workflowDebug?: WorkflowDebugOptions) => Promise<void>
  handleStartDetailConfirmation: (
    selectedPageId: string,
    pageLabel: string,
    _hasDetailPlan?: boolean,
    templateParams?: {
      templateId?: string
      templateName?: string
      templateSourcePath?: string
    }
  ) => Promise<boolean>
  handleStartEndpointDevelopment: (target: {
    apiContractId?: string
    endpointId: string
    endpointLabel: string
    hasDetailPlan?: boolean
  }) => Promise<boolean>
  handleStartEntityDetailConfirmation: (target: {
    entityId: string
    entityLabel: string
    hasDetailPlan?: boolean
  }) => Promise<boolean>
  handleStopGenerating: () => void
  handleSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => Promise<boolean>
  loading: boolean
  planEnded: boolean
  sessionRunStates: Record<string, SessionRunStatus>
  stopping: boolean
  workspaceBusy: boolean
}

/** 从 Workflow 快照中读取最近一次页面选择，作为确认继续时的兜底上下文。 */
function workflowSelectedPageId(workflow: WorkflowRunPayload): string | undefined {
  const stateValue = workflow.state?.selectedPageId
  const resultValue = workflow.result?.selectedPageId
  const statePageId = typeof stateValue === 'string' ? stateValue.trim() : ''
  const resultPageId = typeof resultValue === 'string' ? resultValue.trim() : ''
  return statePageId || resultPageId || undefined
}

/** 从确认卡提交的答案中提取 Build DAG 结构化动作，避免落回普通问题文本协议。 */
function buildTaskPlanConfirmationAction(
  answers: ClarificationAnswers
): WorkflowBuildTaskPlanConfirmation | undefined {
  const value = answers.build_task_plan_confirmation
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const action = String((value as Record<string, unknown>).action || '')
  if (!['confirm', 'patch', 'regenerate'].includes(action)) return undefined
  const patches = (value as Record<string, unknown>).patches
  return {
    mode: 'build_task_plan_confirmation',
    action: action as WorkflowBuildTaskPlanConfirmation['action'],
    ...(Array.isArray(patches) ? { patches } : {})
  }
}

/** 为 Build DAG 结构化动作生成可追踪的用户消息，不把动作语义编码进自然语言。 */
function buildTaskPlanConfirmationMessage(
  action: WorkflowBuildTaskPlanConfirmation['action']
): string {
  const messages: Record<WorkflowBuildTaskPlanConfirmation['action'], string> = {
    confirm: '已确认 Build DAG，请进入 Build。',
    patch: '已提交 Build DAG 任务修改，请重新校验并确认。',
    regenerate: '请重新生成 Build DAG。'
  }
  return messages[action]
}

/** 从开发完成确认载荷读取测试目标，确保刷新后仍能生成一致的用户消息。 */
function testPhaseConfirmationTarget(
  workflow: WorkflowRunPayload
): WorkflowTestTarget | undefined {
  const clarification = workflowClarification(workflow)
  const candidates = [
    clarification?.testTarget,
    workflow.summary?.testTarget,
    workflow.state?.testTarget,
    workflow.result?.testTarget
  ]
  return candidates.find(
    (value): value is WorkflowTestTarget =>
      Boolean(
        value &&
          typeof value === 'object' &&
          !Array.isArray(value) &&
          String((value as Record<string, unknown>).label || '').trim()
      )
  )
}

/** 将测试目标转换为用户可追踪的阶段进入消息。 */
function testPhaseConfirmationMessage(workflow: WorkflowRunPayload): string {
  const target = testPhaseConfirmationTarget(workflow)
  if (!target) return '开始测试应用：当前应用'
  const typeLabels: Record<string, string> = {
    page: '页面',
    endpoint: '接口',
    data_source: '数据源',
    application: '应用'
  }
  return `开始测试${typeLabels[target.type] || '目标'}：${target.label}`
}

/** 从 Workflow 快照中读取最近一次实体选择，作为实体设计确认继续时的兜底上下文。 */
function workflowSelectedEntityId(workflow: WorkflowRunPayload): string | undefined {
  const stateValue = workflow.state?.selectedEntityId ?? workflow.state?.selected_entity_id
  const resultValue = workflow.result?.selectedEntityId ?? workflow.result?.selected_entity_id
  const stateEntityId = typeof stateValue === 'string' ? stateValue.trim() : ''
  const resultEntityId = typeof resultValue === 'string' ? resultValue.trim() : ''
  return stateEntityId || resultEntityId || undefined
}

/** 从 Workflow 快照中恢复 endpoint 构建范围，供详情确认后的继续执行使用。 */
function workflowEndpointExecutionScope(
  workflow: WorkflowRunPayload
): WorkflowBuildExecutionScope | undefined {
  const stateApiContractId =
    workflow.state?.selected_api_contract_id || workflow.state?.selectedApiContractId
  const resultApiContractId =
    workflow.result?.selected_api_contract_id || workflow.result?.selectedApiContractId
  const stateEndpointId = workflow.state?.selected_endpoint_id || workflow.state?.selectedEndpointId
  const resultEndpointId =
    workflow.result?.selected_endpoint_id || workflow.result?.selectedEndpointId
  const apiContractId = String(stateApiContractId || resultApiContractId || '').trim()
  const endpointId = String(stateEndpointId || resultEndpointId || '').trim()
  return apiContractId && endpointId
    ? { type: 'endpoint', targetId: endpointId, apiContractId }
    : undefined
}

/** 优先恢复 Workflow 中的 endpoint 目标，再使用当前 API 会话补齐 handoff 上下文。 */
function endpointExecutionScopeForWorkflow(
  workflow: WorkflowRunPayload,
  activeSession?: SessionIdentity,
  selectedApiContractId?: string,
  selectedEndpointId?: string
): WorkflowBuildExecutionScope | undefined {
  const workflowScope = workflowEndpointExecutionScope(workflow)
  if (workflowScope) return workflowScope
  const apiContractId = String(activeSession?.apiContractId || selectedApiContractId || '').trim()
  const endpointId = String(activeSession?.endpointId || selectedEndpointId || '').trim()
  return apiContractId && endpointId
    ? { type: 'endpoint', targetId: endpointId, apiContractId }
    : undefined
}

/** 读取当前工作流的结构化澄清模式，兼容流式快照和最终结果。 */
function workflowClarificationMode(workflow: WorkflowRunPayload): string {
  return String(workflowClarification(workflow)?.mode || '')
}

/** 判断用户是否在 SmallTask 正式工作流升级卡上明确选择了确认。 */
function smallTaskHandoffApproved(answers: ClarificationAnswers): boolean {
  return confirmationAnswerApproved(answers, 'small_task_handoff')
}

/** 判断用户是否确认继续执行前后端实现修复。 */
function implementationFixConfirmationApproved(answers: ClarificationAnswers): boolean {
  return confirmationAnswerApproved(answers, 'implementation_fix_confirmation')
}

/** 解析通用 yes/no 确认答案，兼容结构化单选和字符串答案。 */
function confirmationAnswerApproved(
  answers: ClarificationAnswers,
  key: 'small_task_handoff' | 'implementation_fix_confirmation'
): boolean {
  const answer = answers[key]
  if (typeof answer === 'string') {
    return ['是', 'yes', 'approved', 'approve', '同意', '确认', '批准'].includes(
      answer.trim().toLowerCase()
    )
  }
  if (!answer || Array.isArray(answer) || typeof answer !== 'object' || !('selected' in answer)) {
    return false
  }
  const selected = Array.isArray(answer.selected) ? answer.selected : [answer.selected]
  return selected.some((item) =>
    ['是', 'yes', 'approved', 'approve', '同意', '确认', '批准'].includes(
      String(item).trim().toLowerCase()
    )
  )
}

/** 读取当前 impact 卡以及用户对该卡的一次性决定。 */
function revisionImpactSubmission(
  workflow: WorkflowRunPayload,
  answers: ClarificationAnswers
): { impact: WorkflowRevisionImpact; decision: 'approved' | 'rejected' } | undefined {
  const rawImpact =
    (workflowClarification(workflow) as Record<string, unknown> | undefined)?.revisionImpact ||
    workflow.summary.revisionImpact ||
    workflow.state?.revision_impact
  const decision = answers.revision_impact_confirmation
  if (!rawImpact || typeof rawImpact !== 'object') return undefined
  if (decision !== 'approved' && decision !== 'rejected') return undefined
  return { impact: rawImpact as WorkflowRevisionImpact, decision }
}

/** 从专用草稿卡读取已经绑定 change/lifecycle/hash 的当前结构化动作。 */
function revisionDraftInteractionSubmission(
  answers: ClarificationAnswers
): WorkflowRevisionDraftInteraction | undefined {
  const value = answers.revision_draft_interaction
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const interaction = value as Partial<WorkflowRevisionDraftInteraction>
  if (
    !interaction.changeId ||
    !interaction.interactionId ||
    !interaction.artifactKey ||
    !interaction.draftSha256 ||
    !interaction.basedOnLifecycleRevision ||
    !['confirm', 'save', 'revise', 'discard'].includes(String(interaction.action))
  ) {
    return undefined
  }
  return interaction as WorkflowRevisionDraftInteraction
}

/** 把 TechnicalPlan 原生确认卡转换为 planning Graph 的结构化恢复动作。 */
function technicalPlanConfirmationSubmission(
  workflow: WorkflowRunPayload,
  answers: ClarificationAnswers,
  request: string
): ApplicationPlanningInteraction | undefined {
  // TechnicalPlan 的 gateId/artifactRevision 只存在服务端原生 interrupt 中；
  // clarification 只是展示文案，不能作为恢复信封的唯一来源。
  const interrupt = [workflow.result, workflow.state]
    .map((source) => source?.application_planning_interrupt)
    .find((value) => value && typeof value === 'object' && !Array.isArray(value)) as
    | Record<string, unknown>
    | undefined
  const clarification = (interrupt?.clarification || workflowClarification(workflow)) as
    | Record<string, unknown>
    | undefined
  if (clarification?.mode !== 'technical_plan_confirmation') return undefined
  const gateId = String(interrupt?.gateId || '').trim()
  const artifactRevision = String(interrupt?.artifactRevision || '').trim()
  if (!gateId || !artifactRevision) return undefined
  const value = answers.technical_plan_confirmation
  const selected =
    typeof value === 'string'
      ? value.trim().toLowerCase()
      : value && typeof value === 'object' && !Array.isArray(value) && 'selected' in value
        ? String((value as { selected?: unknown }).selected || '').trim().toLowerCase()
        : ''
  const action =
    [
      'confirm',
      '确认',
      'yes',
      '是',
      '正确',
      '正确，继续',
      '正确,继续',
      '继续',
      '确认当前版本'
    ].includes(selected)
      ? 'confirm'
      : 'revise'
  return {
    gateId,
    artifact: 'technical_plan',
    artifactRevision,
    action,
    request: request.trim() || (action === 'confirm' ? '确认当前 TechnicalPlan。' : '')
  }
}

/** 为草稿结构化动作生成历史消息，实际执行语义仍只来自 revisionInteraction。 */
function revisionDraftInteractionMessage(action: WorkflowRevisionDraftInteraction['action']): string {
  return {
    save: '保存当前正式产物草稿。',
    revise: '已提交草稿修改意见，请重新生成当前草稿。',
    confirm: '确认当前正式产物版本，并继续收口受影响产物。',
    discard: '放弃当前未确认草稿，已经确认的计划保持不变。'
  }[action]
}

/** 从 SmallTask 确认卡读取用户批准的具体路径。 */
function smallTaskRequestedPaths(workflow: WorkflowRunPayload): string[] {
  const clarification = workflowClarification(workflow)
  const value = clarification?.requestedPaths
  return Array.isArray(value)
    ? value
        .map((item) => String(item).trim())
        .filter(Boolean)
        .slice(0, 100)
    : []
}

export function useWorkflowConversation({
  activeSession,
  agUiSessionsRef,
  application,
  applicationLifecycle,
  draft,
  draftKey,
  selectedSkills,
  selectedApiContractId,
  selectedEndpointId,
  selectedEndpointLabel,
  selectedEntityId,
  selectedEntityLabel,
  selectedPageId,
  selectedPageLabel,
  conversationEnabled,
  inputMode,
  editorMode,
  createTestSession,
  createReviewSession,
  createAcceptanceSession,
  acceptanceConversationSessionKey,
  ensureActiveSession,
  ensureEndpointSession,
  ensureEntitySession,
  ensurePageSession,
  getSessionMessages,
  persistSession,
  onApplicationLifecycleChange,
  onStartDesignStageRevision,
  onStartWorkbenchPlanRevision,
  onRevisionContinuation,
  onEnterTestPhase,
  onEnterReviewPhase,
  onEnterAcceptancePhase,
  onPreviewReady,
  publishAiMessage,
  runningSessionsRef,
  setDraftByKey,
  setSelectedSkillsByKey,
  setSessionMessages
}: UseWorkflowConversationParams): UseWorkflowConversationResult {
  const stopRequestedRef = useRef<Record<string, boolean>>({})
  const notifiedPreviewTargetsRef = useRef<Set<string>>(new Set())
  // 测试阶段切换会先异步创建新会话，用 runId 防止按钮连点创建多个空白测试会话。
  const testPhaseTransitionRunIdsRef = useRef<Set<string>>(new Set())
  // 审查阶段切换同样需要先创建新会话，用 runId 防止确认卡重复提交。
  const reviewPhaseTransitionRunIdsRef = useRef<Set<string>>(new Set())
  // 验收阶段切换需要跨 thread 原子接管执行，用 runId 防止确认卡重复创建会话。
  const acceptancePhaseTransitionRunIdsRef = useRef<Set<string>>(new Set())
  // 一键修复在当前审查会话中恢复，用独立 runId 集合阻止确认卡连续提交。
  const codeReviewRepairRunIdsRef = useRef<Set<string>>(new Set())
  const [runStates, setRunStates] = useState<Record<string, SessionRunEntry>>({})
  const [errors, setErrors] = useState<Record<string, string | undefined>>({})
  const [liveWorkflows, setLiveWorkflows] = useState<Record<string, WorkflowRunPayload>>({})
  // 记录用户已明确结束的会话，保证自由输入不依赖后端控制请求或生命周期回传时序。
  const [endedPlanSessionKeys, setEndedPlanSessionKeys] = useState<Record<string, boolean>>({})

  const selectedTarget = {
    apiContractId: selectedApiContractId,
    endpointId: selectedEndpointId,
    entityId: selectedEntityId,
    pageId: selectedPageId
  }
  const matchingActiveSession =
    activeSession && sessionIdentityMatchesTarget(activeSession, selectedTarget)
      ? activeSession
      : undefined
  // 首次创建目标会话时 React 还未提交 activeSession；只接住同一页面、接口或自由对话的运行态，避免进度跨目标串线。
  const activeRun =
    (matchingActiveSession ? runStates[matchingActiveSession.key] : undefined) ||
    Object.values(runStates).find(
      (entry) =>
        entry.identity.workspaceRoot === application.workspaceRoot &&
        entry.identity.editorMode === editorMode &&
        sessionIdentityMatchesTarget(entry.identity, selectedTarget)
    )
  const activeRuntimeKey = matchingActiveSession?.key || activeRun?.identity.key
  const loading = activeRun?.status === 'running' || activeRun?.status === 'stopping'
  const stopping = activeRun?.status === 'stopping'
  const conversationRunning = Boolean(activeRun?.conversation)
  const planEnded = Boolean(endedPlanSessionKeys[activeRuntimeKey || draftKey])
  const error = activeRuntimeKey ? errors[activeRuntimeKey] : undefined
  const activeWorkflow = activeRuntimeKey
    ? activeRun
      ? liveWorkflows[activeRuntimeKey]
      : (liveWorkflows[activeRuntimeKey] ?? latestWorkflow(getSessionMessages(activeRuntimeKey)))
    : undefined
  // 当前阶段允许同工作区与同页面的独立会话并行，busy 只保留为现有组件接口兼容值。
  const workspaceBusy = false
  const sessionRunStates = Object.values(runStates).reduce<Record<string, SessionRunStatus>>(
    (states, entry) => {
      if (
        entry.identity.workspaceRoot === application.workspaceRoot &&
        entry.identity.editorMode === editorMode
      ) {
        states[entry.identity.sessionId] = entry.status
      }
      return states
    },
    {}
  )

  /** 首次发送时创建目标会话；简单模式补充输入优先复用当前会话和 thread。 */
  const handleSend = async (workflowDebug?: WorkflowDebugOptions): Promise<void> => {
    const message = draft.trim() || workflowDebugMessage(workflowDebug)
    if (!message || loading || workspaceBusy) return
    const acceptanceConversationSession =
      acceptanceConversationSessionKey && activeSession?.key === acceptanceConversationSessionKey
        ? activeSession
        : undefined
    const sessionIdentity = acceptanceConversationSession ||
      (isConversationWorkflow(activeWorkflow) && matchingActiveSession
        ? matchingActiveSession
        : selectedEntityId
          ? await ensureEntitySession(
              selectedEntityId,
              selectedEntityLabel || selectedEntityId
            )
          : selectedApiContractId && selectedEndpointId
            ? await ensureEndpointSession(
                selectedApiContractId,
                selectedEndpointId,
                selectedEndpointLabel || selectedEndpointId
              )
            : selectedPageId
              ? await ensurePageSession(selectedPageId, selectedPageLabel || selectedPageId)
              : await ensureActiveSession())
    await sendWorkflowMessage(message, {
      clearDraft: true,
      detailTargetType: selectedEntityId
        ? 'entity'
        : selectedApiContractId && selectedEndpointId
          ? 'endpoint'
          : undefined,
      selectedEntityId,
      selectedApiContractId,
      selectedEndpointId,
      buildExecutionScope:
        selectedApiContractId && selectedEndpointId
          ? {
              type: 'endpoint',
              targetId: selectedEndpointId,
              apiContractId: selectedApiContractId
            }
          : undefined,
      selectedSkills,
      selectedPageId:
        selectedEntityId || (selectedApiContractId && selectedEndpointId)
          ? ''
          : selectedPageId,
      sessionIdentity,
      titleFrom: message,
      workflowDebug,
      // 验收“不通过”只恢复普通对话；即使之前输入模式是 workflow，也必须走 conversation 端点。
      conversation:
        Boolean(acceptanceConversationSession) ||
        shouldUseConversation(conversationEnabled, activeWorkflow, workflowDebug, inputMode)
    })
  }

  /** 在开发阶段会话中消费 TechnicalPlan continuation，并进入工作区扫描与 DAG 链。 */
  const handleContinueRevisionBuild = async (
    continuation: WorkflowRevisionContinuation,
    sessionIdentity: SessionIdentity
  ): Promise<boolean> => {
    if (loading || workspaceBusy) return false
    return sendWorkflowMessage('TechnicalPlan 已确认，进入工作区扫描并重新生成 Build DAG。', {
      workflowAction: 'continue_revision_build',
      revisionContinuation: {
        changeId: continuation.changeId,
        token: continuation.token
      },
      sessionIdentity,
      titleFrom: 'TechnicalPlan 修改 · 生成 DAG',
      conversation: false
    })
  }

  /** 发送并持久化 Workflow 对话，认证失败时恢复发送前的界面状态。 */
  const sendWorkflowMessage = async (
    message: string,
    options?: {
      clearDraft?: boolean
      clarificationAnswers?: ClarificationAnswers
      applicationPlanningInteraction?: ApplicationPlanningInteraction
      originalRequest?: string
      selectedSkills?: ChatMessageSkill[]
      resumeState?: WorkflowRunPayload
      titleFrom?: string
      workflowDebug?: WorkflowDebugOptions
      workflowAction?: WorkflowAction
      buildExecutionScope?: WorkflowBuildExecutionScope
      planControlAction?: 'stop' | 'end'
      planControlRunId?: string
      resumeExecutionRunId?: string
      selectedPageId?: string
      selectedApiContractId?: string
      selectedEndpointId?: string
      selectedEntityId?: string
      selectedEntityLabel?: string
      endpointLabel?: string
      detailTargetType?: 'page' | 'endpoint' | 'entity'
      sessionIdentity?: SessionIdentity
      pageTemplate?: {
        id?: string
        name?: string
        sourcePath?: string
      }
      conversation?: boolean
      conversationTarget?: ConversationTarget
      conversationApprovedPaths?: string[]
      conversationHandoffDecision?: 'approved' | 'rejected'
      conversationImpactInteractionId?: string
      revisionRequest?: Record<string, unknown>
      revisionContinuation?: { changeId: string; token: string }
      revisionInteraction?: WorkflowRevisionDraftInteraction
      workflowScope?: string
    }
  ): Promise<boolean> => {
    const trimmedMessage = message.trim()
    if (!trimmedMessage) return false

    const identity = options?.sessionIdentity || (await ensureActiveSession())
    if (runningSessionsRef.current.has(identity.key)) {
      setErrors((current) => ({
        ...current,
        [identity.key]: '当前会话正在执行。'
      }))
      return false
    }

    const endpointUrl = options?.conversation
      ? getConversationUrl()
      : options?.workflowScope === 'application_planning'
        ? getApplicationPlanningUrl()
        : getWorkflowUrl()
    const currentAgUiSession = agUiSessionsRef.current[identity.key]
    const agUiSession =
      currentAgUiSession && currentAgUiSession.endpointUrl === endpointUrl
        ? currentAgUiSession
        : (agUiSessionsRef.current[identity.key] = new AgUiChatSession(
            identity.threadId,
            endpointUrl
          ))
    const optimisticSkills = beginOptimisticSkillSend(options?.selectedSkills || [])
    const userMessage: AgentChatMessage = {
      id: Date.now(),
      role: 'user',
      content: trimmedMessage,
      skills: optimisticSkills.messageSkills,
      createdAt: Date.now()
    }
    const assistantMessageId = Date.now() + 1
    const assistantMessage: AgentChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      createdAt: Date.now()
    }
    const previousMessages = getSessionMessages(identity.key)
    const nextMessages = [...previousMessages, userMessage, assistantMessage]

    runningSessionsRef.current.set(identity.key, identity)
    setRunStates((current) => ({
      ...current,
      [identity.key]: {
        identity,
        status: 'running',
        conversation: Boolean(options?.conversation)
      }
    }))
    setErrors((current) => ({ ...current, [identity.key]: undefined }))
    if (!options?.planControlAction) {
      setLiveWorkflows((current) => omitKey(current, identity.key))
      setEndedPlanSessionKeys((current) => {
        const next = omitKey(current, identity.key)
        return draftKey === identity.key ? next : omitKey(next, draftKey)
      })
    }
    stopRequestedRef.current[identity.key] = false
    setSessionMessages(identity.key, nextMessages)
    if (options?.clearDraft) {
      setDraftByKey(identity.key, '')
      setSelectedSkillsByKey(identity.key, optimisticSkills.nextDraftSkills)
      if (draftKey !== identity.key) {
        setDraftByKey(draftKey, '')
        setSelectedSkillsByKey(draftKey, [])
      }
    }

    let streamedContent = ''
    let streamedWorkflow: WorkflowRunPayload | undefined
    let streamedToolCalls: ToolCallRecord[] = []
    let streamedProcessSteps: ProcessStepRecord[] = []
    let latestMessages = nextMessages
    const updateAssistantMessage = (
      content: string,
      workflow?: WorkflowRunPayload,
      toolCalls?: ToolCallRecord[],
      processSteps?: ProcessStepRecord[],
      error?: string
    ): AgentChatMessage[] => {
      const nextCodeChanges = workflowCodeChanges(workflow)
      const updateMessages = (currentMessages: AgentChatMessage[]): AgentChatMessage[] =>
        currentMessages.map((currentMessage) =>
          currentMessage.id === assistantMessageId
            ? {
                ...currentMessage,
                content,
                workflow: workflow ?? currentMessage.workflow,
                error,
                codeChanges: nextCodeChanges ?? currentMessage.codeChanges,
                toolCalls: toolCalls ?? currentMessage.toolCalls,
                processSteps: processSteps ?? currentMessage.processSteps
              }
            : currentMessage
        )
      latestMessages = updateMessages(latestMessages)
      setSessionMessages(identity.key, updateMessages)
      return latestMessages
    }

    /** 在 AG-UI 实时回调中立即转交一次成功启动信号，避免被最终运行态更新批处理丢失。 */
    const updateWorkflow = (nextWorkflow: WorkflowRunPayload): void => {
      streamedWorkflow = nextWorkflow
      setLiveWorkflows((current) => ({ ...current, [identity.key]: nextWorkflow }))
      updateAssistantMessage(streamedContent, nextWorkflow, streamedToolCalls)

      if (editorMode !== 'frontend') return
      const previewTarget = workflowPreviewTarget(nextWorkflow, true)
      if (!previewTarget || notifiedPreviewTargetsRef.current.has(previewTarget.key)) return
      notifiedPreviewTargetsRef.current.add(previewTarget.key)
      onPreviewReady(previewTarget)
    }

    try {
      await persistSession({
        editorMode: identity.editorMode,
        messages: nextMessages,
        sessionId: identity.sessionId,
        threadId: identity.threadId,
        apiContractId: identity.apiContractId,
        endpointId: identity.endpointId,
        endpointLabel: identity.endpointLabel,
        entityId: identity.entityId,
        entityLabel: identity.entityLabel,
        pageId: identity.pageId,
        titleFrom: options?.titleFrom || trimmedMessage
      })
      const {
        answer: rawAnswer,
        workflow,
        toolCalls: rawToolCalls
      } = await agUiSession.sendMessage(trimmedMessage, {
        workspaceRoot: identity.workspaceRoot,
        editorMode: identity.editorMode,
        application,
        clarificationAnswers: options?.clarificationAnswers,
        applicationPlanningInteraction: options?.applicationPlanningInteraction,
        originalRequest: options?.originalRequest,
        onApplicationLifecycle: onApplicationLifecycleChange,
        selectedSkillNames: selectedSkillNames(options?.selectedSkills),
        selectedPageId:
          options && 'selectedPageId' in options ? options.selectedPageId : identity.pageId,
        selectedApiContractId: options?.selectedApiContractId,
        selectedEndpointId: options?.selectedEndpointId,
        selectedEntityId: options?.selectedEntityId,
        detailTargetType: options?.detailTargetType,
        buildExecutionScope: options?.buildExecutionScope,
        workflowAction: options?.workflowAction,
        workflowDebug: options?.workflowDebug,
        planControlAction: options?.planControlAction,
        planControlRunId: options?.planControlRunId,
        resumeExecutionRunId: options?.resumeExecutionRunId,
        resumeState: options?.resumeState,
        pageTemplate: options?.pageTemplate,
        conversation: options?.conversation,
        conversationTarget:
          options?.conversationTarget || conversationTargetFromIdentity(identity),
        conversationApprovedPaths: options?.conversationApprovedPaths,
        conversationHandoffDecision: options?.conversationHandoffDecision,
        conversationImpactInteractionId: options?.conversationImpactInteractionId,
        revisionRequest: options?.revisionRequest,
        revisionContinuation: options?.revisionContinuation,
        revisionInteraction: options?.revisionInteraction,
        workflowScope: options?.workflowScope,
        onContent: (content) => {
          streamedContent = content
          updateAssistantMessage(content, streamedWorkflow, streamedToolCalls)
        },
        onWorkflow: (nextWorkflow) => {
          updateWorkflow(nextWorkflow)
        },
        onToolCalls: (nextToolCalls) => {
          streamedToolCalls = nextToolCalls
          updateAssistantMessage(streamedContent, streamedWorkflow, nextToolCalls)
        },
        onProcessSteps: (nextProcessSteps) => {
          streamedProcessSteps = nextProcessSteps
          updateAssistantMessage(
            streamedContent,
            streamedWorkflow,
            streamedToolCalls,
            nextProcessSteps
          )
        }
      })
      const stopped = Boolean(stopRequestedRef.current[identity.key])
      const answer = stopped ? stoppedAnswer(streamedContent || rawAnswer) : rawAnswer.trim()
      const finalWorkflow = stopped
        ? withWorkflowExecutionStatus(workflow ?? streamedWorkflow, 'stopped')
        : (workflow ?? streamedWorkflow)
      const completedMessages = updateAssistantMessage(
        answer || 'Workflow 已返回，但内容为空。',
        finalWorkflow,
        rawToolCalls.length > 0 ? rawToolCalls : streamedToolCalls,
        streamedProcessSteps
      )
      if (finalWorkflow) {
        setLiveWorkflows((current) => ({
          ...current,
          [identity.key]: finalWorkflow
        }))
      }

      await persistSession({
        editorMode: identity.editorMode,
        messages: completedMessages,
        sessionId: identity.sessionId,
        threadId: identity.threadId,
        apiContractId: identity.apiContractId,
        endpointId: identity.endpointId,
        endpointLabel: identity.endpointLabel,
        entityId: identity.entityId,
        entityLabel: identity.entityLabel,
        pageId: identity.pageId,
        titleFrom: options?.titleFrom || trimmedMessage
      })
      if (options?.workflowAction === 'submit_revision_interaction') {
        const continuation = revisionContinuationFromWorkflow(finalWorkflow)
        if (continuation) await onRevisionContinuation(continuation)
      }
      publishAiMessage(identity.editorMode, answer)
      return true
    } catch (caughtError) {
      if (isAuthenticationFailure(caughtError)) {
        setSessionMessages(identity.key, previousMessages)
        if (options?.clearDraft) setDraftByKey(identity.key, trimmedMessage)
        if (options?.clearDraft) {
          setSelectedSkillsByKey(identity.key, rollbackSkillSelection(options.selectedSkills))
        }
        await persistSession({
          editorMode: identity.editorMode,
          messages: previousMessages,
          sessionId: identity.sessionId,
          threadId: identity.threadId,
          apiContractId: identity.apiContractId,
          endpointId: identity.endpointId,
          endpointLabel: identity.endpointLabel,
          entityId: identity.entityId,
          entityLabel: identity.entityLabel,
          pageId: identity.pageId
        })
        // 认证失败由全局登录门禁处理，同时在当前对话区保留可见错误，避免门禁未及时出现时形成空白。
        setErrors((current) => ({
          ...current,
          [identity.key]: '当前登录状态已失效，请重新登录后重试。'
        }))
        return false
      }
      if (stopRequestedRef.current[identity.key] || isAbortedStreamError(caughtError)) {
        const answer = stoppedAnswer(streamedContent)
        const stoppedWorkflow = withWorkflowExecutionStatus(streamedWorkflow, 'stopped')
        const completedMessages = updateAssistantMessage(
          answer,
          stoppedWorkflow,
          streamedToolCalls,
          streamedProcessSteps
        )
        if (stoppedWorkflow) {
          setLiveWorkflows((current) => ({
            ...current,
            [identity.key]: stoppedWorkflow
          }))
        }
        await persistSession({
          editorMode: identity.editorMode,
          messages: completedMessages,
          sessionId: identity.sessionId,
          threadId: identity.threadId,
          apiContractId: identity.apiContractId,
          endpointId: identity.endpointId,
          endpointLabel: identity.endpointLabel,
          entityId: identity.entityId,
          entityLabel: identity.entityLabel,
          pageId: identity.pageId,
          titleFrom: message
        })
        publishAiMessage(identity.editorMode, answer)
        return false
      }
      const runError = caughtError instanceof AgUiRunError ? caughtError : undefined
      const failedWorkflow = runError?.workflow ?? streamedWorkflow
      const failedToolCalls = runError?.toolCalls?.length ? runError.toolCalls : streamedToolCalls
      const failedProcessSteps = runError?.processSteps?.length
        ? runError.processSteps
        : streamedProcessSteps
      const failedContent =
        runError?.message ||
        (caughtError instanceof Error ? caughtError.message : '调用 Workflow 失败。')
      const failedMessages = updateAssistantMessage(
        '',
        failedWorkflow,
        failedToolCalls,
        failedProcessSteps,
        failedContent
      )
      if (failedWorkflow) {
        setLiveWorkflows((current) => ({
          ...current,
          [identity.key]: failedWorkflow
        }))
      }
      await persistSession({
        editorMode: identity.editorMode,
        messages: failedMessages,
        sessionId: identity.sessionId,
        threadId: identity.threadId,
        apiContractId: identity.apiContractId,
        endpointId: identity.endpointId,
        endpointLabel: identity.endpointLabel,
        entityId: identity.entityId,
        entityLabel: identity.entityLabel,
        pageId: identity.pageId,
        titleFrom: options?.titleFrom || message
      })
      setErrors((current) => ({
        ...current,
        [identity.key]: failedContent
      }))
      return false
    } finally {
      runningSessionsRef.current.delete(identity.key)
      setRunStates((current) => omitKey(current, identity.key))
      stopRequestedRef.current[identity.key] = false
    }
  }

  /** 将结构化确认转换为可追踪的用户消息，并通过当前 AG-UI 会话恢复 Workflow。 */
  const handleSubmitClarification = async (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ): Promise<boolean> => {
    const conversation = isConversationWorkflow(workflow)
    if (
      !conversation &&
      workflowInteractionAvailability(workflow, applicationLifecycle) !== 'active'
    )
      return false
    const originalRequest = workflowOriginalRequest(workflow)
    const clarificationMode = workflowClarificationMode(workflow)
    const handoffApproved = smallTaskHandoffApproved(answers)
    const implementationFixApproved = implementationFixConfirmationApproved(answers)
    const revisionSubmission = revisionImpactSubmission(workflow, answers)
    const revisionDraftInteraction = revisionDraftInteractionSubmission(answers)
    const technicalPlanInteraction = technicalPlanConfirmationSubmission(
      workflow,
      answers,
      originalRequest
    )
    const endpointScope = endpointExecutionScopeForWorkflow(
      workflow,
      activeSession,
      selectedApiContractId,
      selectedEndpointId
    )
    const workflowBuildScope = workflow.summary.buildExecutionScope || endpointScope
    const continuationPageId = endpointScope
      ? undefined
      : workflowSelectedPageId(workflow) || activeSession?.pageId || selectedPageId
    const continuationEntityId =
      workflowSelectedEntityId(workflow) || activeSession?.entityId || selectedEntityId
    if (conversation && clarificationMode === 'revision_impact_confirmation' && revisionSubmission) {
      const { impact, decision } = revisionSubmission
      if (decision === 'rejected') {
        return sendWorkflowMessage('用户已取消本次正式修改，当前正式产物保持不变。', {
          originalRequest,
          conversation: true,
          conversationHandoffDecision: 'rejected',
          conversationImpactInteractionId: impact.interactionId,
          titleFrom: originalRequest || '取消正式修改'
        })
      }
      if (!originalRequest || loading || workspaceBusy) return false
      const sourceIdentity = activeSession || (await ensureActiveSession())
      const target = conversationTargetFromIdentity(sourceIdentity) || { type: 'application' }
      if (impact.formalBranch === 'design_stage_revision') {
        try {
          await onStartDesignStageRevision({
            request: originalRequest,
            target,
            impact,
            sourceSessionId: sourceIdentity.sessionId,
            sourceConversationThreadId: sourceIdentity.threadId,
            sourceRunId: workflow.runId
          })
          return true
        } catch (error) {
          // handoff 失败时把原因写回来源会话，避免按钮点击后只有无反馈的静默 Promise rejection。
          const message = error instanceof Error ? error.message : String(error)
          const errorKey = activeRuntimeKey || draftKey
          setErrors((current) => ({ ...current, [errorKey]: message }))
          return false
        }
      }
      const revisionIdentity = await onStartWorkbenchPlanRevision({
        request: originalRequest,
        target,
        impact,
        sourceSessionId: sourceIdentity.sessionId,
        sourceConversationThreadId: sourceIdentity.threadId,
        sourceRunId: workflow.runId
      })
      // 影响范围确认已经由结构化 revisionRequest 表达；复用原始请求作为协议消息，
      // 避免把确认提示伪装成新的用户输入，具体恢复节点由服务端 action 路由决定。
      return sendWorkflowMessage(originalRequest, {
        originalRequest,
        workflowAction: 'start_technical_revision',
        workflowScope: 'application_planning',
        revisionRequest: {
          source: 'conversation_handoff',
          formalBranch: impact.formalBranch,
          target,
          request: originalRequest,
          confirmedImpact: { interactionId: impact.interactionId }
        },
        sessionIdentity: revisionIdentity,
        conversation: false,
        titleFrom: originalRequest
      })
    }
    if (
      !conversation &&
      clarificationMode === 'technical_plan_confirmation' &&
      technicalPlanInteraction
    ) {
      if (loading || workspaceBusy) return false
      const confirmationMessage =
        technicalPlanInteraction.request || '确认当前 TechnicalPlan。'
      return sendWorkflowMessage(confirmationMessage, {
        originalRequest: originalRequest || confirmationMessage,
        workflowScope: 'application_planning',
        applicationPlanningInteraction: technicalPlanInteraction,
        titleFrom: 'TechnicalPlan 重新规划确认',
        conversation: false
      })
    }
    if (!conversation && clarificationMode === 'technical_plan_confirmation') {
      // 缺少原生 interrupt 时禁止落入通用 continuation（它会误发到主 Workflow，
      // 进而显示 Build 前置门禁）；要求重新获取当前 planning checkpoint。
      setErrors((current) => ({
        ...current,
        [activeRuntimeKey || draftKey]:
          '当前 TechnicalPlan 确认缺少服务端 planning checkpoint，请刷新后重试。'
      }))
      return false
    }
    if (
      !conversation &&
      clarificationMode === 'revision_draft_confirmation' &&
      revisionDraftInteraction
    ) {
      if (loading || workspaceBusy) return false
      return sendWorkflowMessage(revisionDraftInteractionMessage(revisionDraftInteraction.action), {
        originalRequest,
        workflowAction: 'submit_revision_interaction',
        revisionInteraction: revisionDraftInteraction,
        buildExecutionScope: workflowBuildScope,
        titleFrom: `正式产物：${revisionDraftInteraction.artifactKey}`,
        conversation: false
      })
    }
    if (conversation && clarificationMode === 'implementation_fix_confirmation') {
      return sendWorkflowMessage(
        implementationFixApproved
          ? '用户已确认实现修改范围，请继续执行。'
          : '用户未确认实现修改范围，本次修改已停止。',
        {
          originalRequest,
          conversation: true,
          conversationHandoffDecision: implementationFixApproved ? 'approved' : 'rejected',
          titleFrom: originalRequest || '实现修改确认'
        }
      )
    }
    if (conversation && clarificationMode === 'small_task_scope_confirmation') {
      return sendWorkflowMessage(
        handoffApproved
          ? '用户已确认扩大代码范围，请继续执行原修改。'
          : '用户未批准扩大代码范围，本次修改已停止。',
        {
          originalRequest,
          conversation: true,
          conversationApprovedPaths: handoffApproved ? smallTaskRequestedPaths(workflow) : [],
          conversationHandoffDecision: handoffApproved ? 'approved' : 'rejected',
          titleFrom: originalRequest || 'SmallTask 范围确认'
        }
      )
    }
    if (!conversation && clarificationMode === 'build_task_plan_confirmation') {
      const action = buildTaskPlanConfirmationAction(answers)
      if (!action || loading || workspaceBusy) return false
      return sendWorkflowMessage(buildTaskPlanConfirmationMessage(action.action), {
        clarificationAnswers: answers,
        originalRequest,
        resumeState: workflow,
        buildExecutionScope: workflowBuildScope,
        titleFrom: 'Build DAG 确认',
        conversation: false
      })
    }
    if (!conversation && clarificationMode === 'test_phase_confirmation') {
      const answer = answers.test_phase_confirmation
      const action =
        answer && typeof answer === 'object' && !Array.isArray(answer)
          ? String((answer as Record<string, unknown>).action || '')
          : ''
      if (
        action !== 'confirm' ||
        loading ||
        workspaceBusy ||
        testPhaseTransitionRunIdsRef.current.has(workflow.runId)
      ) {
        return false
      }
      testPhaseTransitionRunIdsRef.current.add(workflow.runId)
      const target = testPhaseConfirmationTarget(workflow)
      const targetId = target?.id || workflowBuildScope?.targetId
      let testSession: SessionIdentity
      try {
        testSession = await createTestSession({
          targetLabel: target?.label || '当前应用',
          pageId: activeSession?.pageId || (target?.type === 'page' ? targetId : undefined),
          apiContractId: activeSession?.apiContractId || workflowBuildScope?.apiContractId,
          endpointId:
            activeSession?.endpointId || (target?.type === 'endpoint' ? targetId : undefined),
          endpointLabel: activeSession?.endpointLabel || target?.label,
          entityId:
            activeSession?.entityId || (target?.type === 'data_source' ? targetId : undefined),
          entityLabel: activeSession?.entityLabel || target?.label
        })
      } catch {
        testPhaseTransitionRunIdsRef.current.delete(workflow.runId)
        return false
      }
      onEnterTestPhase()
      const started = await sendWorkflowMessage(testPhaseConfirmationMessage(workflow), {
        clarificationAnswers: answers,
        originalRequest,
        resumeState: workflow,
        buildExecutionScope: workflowBuildScope,
        resumeExecutionRunId: workflow.runId,
        sessionIdentity: testSession,
        titleFrom: '进入测试阶段',
        conversation: false
      })
      if (!started) testPhaseTransitionRunIdsRef.current.delete(workflow.runId)
      return started
    }
    if (!conversation && clarificationMode === 'review_phase_confirmation') {
      const answer = answers.review_phase_confirmation
      const action =
        answer && typeof answer === 'object' && !Array.isArray(answer)
          ? String((answer as Record<string, unknown>).action || '')
          : ''
      if (
        action !== 'confirm' ||
        loading ||
        workspaceBusy ||
        reviewPhaseTransitionRunIdsRef.current.has(workflow.runId)
      ) {
        return false
      }
      reviewPhaseTransitionRunIdsRef.current.add(workflow.runId)
      const target = testPhaseConfirmationTarget(workflow)
      const targetId = target?.id || workflowBuildScope?.targetId
      let reviewSession: SessionIdentity
      try {
        reviewSession = await createReviewSession({
          targetLabel: target?.label || '当前应用',
          pageId: activeSession?.pageId || (target?.type === 'page' ? targetId : undefined),
          apiContractId: activeSession?.apiContractId || workflowBuildScope?.apiContractId,
          endpointId:
            activeSession?.endpointId || (target?.type === 'endpoint' ? targetId : undefined),
          endpointLabel: activeSession?.endpointLabel || target?.label,
          entityId:
            activeSession?.entityId || (target?.type === 'data_source' ? targetId : undefined),
          entityLabel: activeSession?.entityLabel || target?.label
        })
      } catch {
        reviewPhaseTransitionRunIdsRef.current.delete(workflow.runId)
        return false
      }
      // 会话创建成功后立即切换顶部阶段，避免等待审查 Agent 首帧造成视觉滞后。
      onEnterReviewPhase()
      const started = await sendWorkflowMessage('开始审查前后端代码', {
        clarificationAnswers: answers,
        originalRequest,
        resumeState: workflow,
        buildExecutionScope: workflowBuildScope,
        resumeExecutionRunId: workflow.runId,
        sessionIdentity: reviewSession,
        titleFrom: '进入审查阶段',
        conversation: false
      })
      if (!started) reviewPhaseTransitionRunIdsRef.current.delete(workflow.runId)
      return started
    }
    if (!conversation && clarificationMode === 'acceptance_phase_confirmation') {
      const answer = answers.acceptance_phase_confirmation
      const action =
        answer && typeof answer === 'object' && !Array.isArray(answer)
          ? String((answer as Record<string, unknown>).action || '')
          : ''
      if (
        action !== 'confirm' ||
        loading ||
        workspaceBusy ||
        acceptancePhaseTransitionRunIdsRef.current.has(workflow.runId)
      ) {
        return false
      }
      acceptancePhaseTransitionRunIdsRef.current.add(workflow.runId)
      const target = testPhaseConfirmationTarget(workflow)
      const targetId = target?.id || workflowBuildScope?.targetId
      let acceptanceSession: SessionIdentity
      // 先切换顶部阶段，让后续新会话选择直接落在验收阶段，避免审查阶段覆盖值滞留。
      onEnterAcceptancePhase()
      try {
        acceptanceSession = await createAcceptanceSession({
          targetLabel: target?.label || selectedPageLabel || activeSession?.pageId || '当前应用',
          pageId: activeSession?.pageId || (target?.type === 'page' ? targetId : undefined),
          apiContractId: activeSession?.apiContractId || workflowBuildScope?.apiContractId,
          endpointId:
            activeSession?.endpointId || (target?.type === 'endpoint' ? targetId : undefined),
          endpointLabel: activeSession?.endpointLabel || target?.label,
          entityId:
            activeSession?.entityId || (target?.type === 'data_source' ? targetId : undefined),
          entityLabel: activeSession?.entityLabel || target?.label
        })
      } catch {
        onEnterReviewPhase()
        acceptancePhaseTransitionRunIdsRef.current.delete(workflow.runId)
        return false
      }
      // 会话创建完成后用同一 AG-UI 请求恢复审查执行并运行验收子图。
      const started = await sendWorkflowMessage('正在启动项目准备验收', {
        clarificationAnswers: answers,
        originalRequest,
        resumeState: workflow,
        buildExecutionScope: workflowBuildScope,
        resumeExecutionRunId: workflow.runId,
        sessionIdentity: acceptanceSession,
        titleFrom: '进入验收阶段',
        conversation: false
      })
      if (!started) acceptancePhaseTransitionRunIdsRef.current.delete(workflow.runId)
      return started
    }
    if (!conversation && clarificationMode === 'code_review_repair_confirmation') {
      const answer = answers.code_review_repair_confirmation
      const action =
        answer && typeof answer === 'object' && !Array.isArray(answer)
          ? String((answer as Record<string, unknown>).action || '')
          : ''
      if (
        action !== 'repair_all' ||
        loading ||
        workspaceBusy ||
        codeReviewRepairRunIdsRef.current.has(workflow.runId)
      ) {
        return false
      }
      codeReviewRepairRunIdsRef.current.add(workflow.runId)
      const started = await sendWorkflowMessage('开始一键修复扫描出的代码问题', {
        clarificationAnswers: answers,
        originalRequest,
        resumeState: workflow,
        buildExecutionScope: workflowBuildScope,
        resumeExecutionRunId: workflow.runId,
        titleFrom: '一键修复代码审查问题',
        conversation: false
      })
      if (!started) codeReviewRepairRunIdsRef.current.delete(workflow.runId)
      return started
    }
    const continuationMessage = buildClarificationContinuationMessage(workflow, answers)
    if (!continuationMessage || loading || workspaceBusy) return false
    return sendWorkflowMessage(continuationMessage, {
      clarificationAnswers: answers,
      originalRequest,
      resumeState: workflow,
      selectedPageId: continuationEntityId ? '' : continuationPageId,
      selectedApiContractId: endpointScope?.apiContractId,
      selectedEndpointId: endpointScope?.targetId,
      selectedEntityId: continuationEntityId,
      detailTargetType: endpointScope
        ? 'endpoint'
        : continuationEntityId
          ? 'entity'
          : continuationPageId
            ? 'page'
            : undefined,
      buildExecutionScope: workflowBuildScope,
      resumeExecutionRunId:
        clarificationMode === 'unit_test_confirmation' ||
        clarificationMode === 'frontend_performance_confirmation'
          ? workflow.runId
          : undefined,
      titleFrom: originalRequest || '补充需求确认',
      conversation
    })
  }

  /** 以用户选择的页面实现契约作为主 Workflow 开发起点。 */
  const handleStartDetailConfirmation = async (
    selectedPageId: string,
    pageLabel: string,
    _hasDetailPlan?: boolean,
    templateParams?: {
      templateId?: string
      templateName?: string
      templateSourcePath?: string
    }
  ): Promise<boolean> => {
    if (!selectedPageId || loading || workspaceBusy) return false
    const identity = await ensurePageSession(selectedPageId, pageLabel)
    return sendWorkflowMessage(
      `开始开发页面：${pageLabel}`,
      {
        selectedPageId,
        detailTargetType: 'page',
        sessionIdentity: identity,
        titleFrom: `开发页面：${pageLabel}`,
        ...(templateParams?.templateSourcePath
          ? {
              pageTemplate: {
                id: templateParams.templateId,
                name: templateParams.templateName,
                sourcePath: templateParams.templateSourcePath
              }
            }
          : {})
      }
    )
  }

  /** 以用户选择的具体 endpoint 作为开发就绪检查起点。 */
  const handleStartEndpointDevelopment = async (target: {
    apiContractId?: string
    endpointId: string
    endpointLabel: string
    hasDetailPlan?: boolean
  }): Promise<boolean> => {
    if (!target.apiContractId || !target.endpointId || loading || workspaceBusy) return false
    const identity = await ensureEndpointSession(
      target.apiContractId,
      target.endpointId,
      target.endpointLabel
    )
    return sendWorkflowMessage(
      `开始开发接口：${target.endpointLabel}`,
      {
        selectedApiContractId: target.apiContractId,
        selectedEndpointId: target.endpointId,
        selectedPageId: '',
        detailTargetType: 'endpoint',
        buildExecutionScope: {
          type: 'endpoint',
          targetId: target.endpointId,
          apiContractId: target.apiContractId
        },
        endpointLabel: target.endpointLabel,
        sessionIdentity: identity,
        titleFrom: `开发接口：${target.endpointLabel}`
      }
    )
  }

  /** 以用户选择的实体作为独立 EntitySourceBinding 起点。 */
  const handleStartEntityDetailConfirmation = async (target: {
    entityId: string
    entityLabel: string
    hasDetailPlan?: boolean
  }): Promise<boolean> => {
    if (!target.entityId || loading || workspaceBusy) return false
    const identity = await ensureEntitySession(target.entityId, target.entityLabel)
    return sendWorkflowMessage(
      `${target.hasDetailPlan ? '查看实体数据源绑定' : '开始实体数据源绑定'}：${target.entityLabel}`,
      {
        selectedEntityId: target.entityId,
        selectedEntityLabel: target.entityLabel,
        selectedPageId: '',
        selectedApiContractId: '',
        selectedEndpointId: '',
        detailTargetType: 'entity',
        sessionIdentity: identity,
        titleFrom: `实体数据源绑定：${target.entityLabel}`
      }
    )
  }

  const handleStopGenerating = (): void => {
    const runningIdentity = activeRun?.identity
    if (!runningIdentity || !loading || stopping) return
    const agUiSession = agUiSessionsRef.current[runningIdentity.key]
    if (!agUiSession) return

    stopRequestedRef.current[runningIdentity.key] = true
    setRunStates((current) => ({
      ...current,
      [runningIdentity.key]: {
        identity: runningIdentity,
        status: 'stopping',
        conversation: Boolean(current[runningIdentity.key]?.conversation)
      }
    }))
    setLiveWorkflows((current) => {
      const workflow = current[runningIdentity.key]
      const stoppingWorkflow = withWorkflowExecutionStatus(workflow, 'stopping')
      return stoppingWorkflow ? { ...current, [runningIdentity.key]: stoppingWorkflow } : current
    })
    agUiSession.stop()
  }

  /** 通过结构化验收动作继续 acceptance 节点，禁止普通文本冒充验收通过。 */
  const handleAcceptPreview = async (): Promise<boolean> => {
    if (!activeWorkflow || loading || workspaceBusy) return false
    return handleSubmitClarification(activeWorkflow, { page_acceptance: 'accepted' })
  }

  /** 从当前可恢复节点重新执行失败或已停止的计划切片。 */
  const handleRetryPlan = async (): Promise<void> => {
    if (!activeWorkflow || loading || workspaceBusy) return
    const execution = planExecutionForPage(
      activeWorkflow.summary.lifecycle,
      activeSession?.pageId || selectedPageId,
      { runId: activeWorkflow.runId, threadId: activeWorkflow.threadId }
    )
    const isStopped = execution?.status === 'stopped' || activeWorkflow.summary.status === 'stopped'
    await sendWorkflowMessage('重试当前计划任务。', {
      resumeState: activeWorkflow,
      resumeExecutionRunId: execution?.runId || activeWorkflow.runId,
      selectedPageId: workflowSelectedPageId(activeWorkflow) || activeSession?.pageId,
      titleFrom: '重试计划任务',
      ...(!isStopped ? { workflowAction: 'retry_failed_tasks' as const } : {})
    })
  }

  /** 在原审查会话和执行范围内重新调用失败的扫描或修复模型子步骤。 */
  const handleRetryCodeReview = async (): Promise<void> => {
    if (!activeWorkflow || !workflowCodeReviewRetry(activeWorkflow) || loading || workspaceBusy) {
      return
    }
    const execution = planExecutionForPage(
      activeWorkflow.summary.lifecycle,
      activeSession?.pageId || selectedPageId,
      { runId: activeWorkflow.runId, threadId: activeWorkflow.threadId }
    )
    await sendWorkflowMessage('重试当前代码审查请求。', {
      resumeState: activeWorkflow,
      resumeExecutionRunId: execution?.runId || activeWorkflow.runId,
      selectedPageId: workflowSelectedPageId(activeWorkflow) || activeSession?.pageId,
      buildExecutionScope: activeWorkflow.summary.buildExecutionScope,
      titleFrom: '重试代码审查',
      workflowAction: 'retry_code_review'
    })
  }

  /** 按暂停态调试面板选择的节点恢复当前计划，并保留原执行身份与状态快照。 */
  const handleResumePlan = async (workflowDebug?: WorkflowDebugOptions): Promise<void> => {
    if (!activeWorkflow || !workflowDebug?.resumeFrom || loading || workspaceBusy) return
    const execution = planExecutionForPage(
      activeWorkflow.summary.lifecycle,
      activeSession?.pageId || selectedPageId,
      { runId: activeWorkflow.runId, threadId: activeWorkflow.threadId }
    )
    await sendWorkflowMessage(`从 ${workflowDebug.resumeFrom} 节点继续执行 workflow 调试。`, {
      resumeState: activeWorkflow,
      // Mock 或旧会话可能没有 lifecycle projection，但 Workflow runId 仍是可校验的恢复令牌。
      resumeExecutionRunId: execution?.runId || activeWorkflow.runId,
      selectedPageId: workflowSelectedPageId(activeWorkflow) || activeSession?.pageId,
      titleFrom: '从指定节点继续执行',
      workflowDebug
    })
  }

  /** 通过同一 AG-UI 端点结束计划并释放生命周期中的工作区锁。 */
  const handleEndPlan = async (runId?: string): Promise<void> => {
    const execution = planExecutionForPage(
      activeWorkflow?.summary.lifecycle,
      activeSession?.pageId || selectedPageId,
      { runId: activeWorkflow?.runId, threadId: activeWorkflow?.threadId }
    )
    const targetRunId = runId || execution?.runId || activeWorkflow?.runId
    const controlIdentity = activeRun?.identity || matchingActiveSession || activeSession
    const endedSessionKeys = Array.from(
      new Set(
        [activeRuntimeKey, controlIdentity?.key, draftKey].filter((key): key is string =>
          Boolean(key)
        )
      )
    )

    // 先释放前端输入门禁；即使没有 runId 或后端控制请求失败，用户也不能被卡在计划栏。
    if (endedSessionKeys.length > 0) {
      setEndedPlanSessionKeys((current) => {
        const next = { ...current }
        endedSessionKeys.forEach((key) => {
          next[key] = true
        })
        return next
      })
      setLiveWorkflows((current) => {
        const next = { ...current }
        endedSessionKeys.forEach((key) => {
          const workflow = current[key] || (key === activeRuntimeKey ? activeWorkflow : undefined)
          const endedWorkflow = withWorkflowExecutionStatus(workflow, 'stopped', targetRunId)
          if (endedWorkflow) next[key] = endedWorkflow
        })
        return next
      })
    }

    // 结束动作的 UI 解锁不等待后端；请求仍尽力释放服务端工作区锁。
    if (loading || workspaceBusy || !targetRunId) return
    await sendWorkflowMessage('结束当前计划。', {
      planControlAction: 'end',
      planControlRunId: targetRunId,
      selectedPageId: activeSession?.pageId || selectedPageId,
      sessionIdentity: controlIdentity,
      titleFrom: '结束计划'
    })
  }

  /** 在当前 Run 已暂停等待时暂停计划，但保留 checkpoint 和恢复入口。 */
  const handleStopPlan = async (runId?: string): Promise<void> => {
    if (loading || workspaceBusy) return
    const execution = planExecutionForPage(
      activeWorkflow?.summary.lifecycle,
      activeSession?.pageId || selectedPageId,
      { runId: activeWorkflow?.runId, threadId: activeWorkflow?.threadId }
    )
    const targetRunId = runId || execution?.runId || activeWorkflow?.runId
    if (!targetRunId) return
    const resumeWorkflow = activeWorkflow
    if (activeRuntimeKey && resumeWorkflow) {
      setLiveWorkflows((current) => ({
        ...current,
        [activeRuntimeKey]:
          withWorkflowExecutionStatus(resumeWorkflow, 'stopping', targetRunId) || resumeWorkflow
      }))
    }
    const stopped = await sendWorkflowMessage('暂停当前计划执行。', {
      planControlAction: 'stop',
      planControlRunId: targetRunId,
      selectedPageId: activeSession?.pageId || selectedPageId,
      titleFrom: '暂停计划'
    })
    if (stopped && activeRuntimeKey && resumeWorkflow) {
      setLiveWorkflows((current) => ({
        ...current,
        [activeRuntimeKey]:
          withWorkflowExecutionStatus(resumeWorkflow, 'stopped', targetRunId) || resumeWorkflow
      }))
    }
  }

  return {
    activeWorkflow,
    conversationRunning,
    error,
    handleAcceptPreview,
    handleContinueRevisionBuild,
    handleEndPlan,
    handleResumePlan,
    handleRetryCodeReview,
    handleRetryPlan,
    handleStopPlan,
    handleSend,
    handleStartEndpointDevelopment,
    handleStartEntityDetailConfirmation,
    handleStartDetailConfirmation,
    handleStopGenerating,
    handleSubmitClarification,
    loading,
    planEnded,
    sessionRunStates,
    stopping,
    workspaceBusy
  }
}

function latestWorkflow(messages: AgentChatMessage[]): WorkflowRunPayload | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const workflow = messages[index].workflow
    if (workflow) return workflow
  }
  return undefined
}

function omitKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record }
  delete next[key]
  return next
}

function workflowDebugMessage(workflowDebug?: WorkflowDebugOptions): string {
  if (!workflowDebug?.enabled || !workflowDebug.resumeFrom) return ''
  return `从 ${workflowDebug.resumeFrom} 节点继续执行 workflow 调试。`
}

function isAbortedStreamError(error: unknown): boolean {
  const inspected = new Set<unknown>()
  let current = error

  while (current && typeof current === 'object' && !inspected.has(current)) {
    inspected.add(current)
    const candidate = current as { name?: unknown; message?: unknown; cause?: unknown }
    const name = typeof candidate.name === 'string' ? candidate.name.toLowerCase() : ''
    const message = typeof candidate.message === 'string' ? candidate.message.toLowerCase() : ''
    if (name === 'aborterror' || message.includes('aborted') || message.includes('aborterror')) {
      return true
    }
    current = candidate.cause
  }

  return false
}
