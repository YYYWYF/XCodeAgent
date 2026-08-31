import { useEffect, useMemo, useRef, useState } from 'react'
import type { MutableRefObject, SetStateAction } from 'react'
import {
  AgUiChatSession,
  getDirectModificationUrl,
  getWorkflowUrl
} from '../../../service/agUiAgent'
import type { ProcessStepRecord, ToolCallRecord } from '../../../service/agUiAgent'
import { isAuthenticationFailure } from '../../../service/authentication'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  ChatMessageSkill,
  EditorMode,
  WorkflowBuildExecutionScope,
  WorkflowDebugOptions,
  WorkflowRunPayload
} from '../../../typings'
import type { WorkbenchPhase } from '../../../workbenchPhase'
import {
  isDirectModificationWorkflow,
  shouldUseDirectModification
} from '../directModificationMode'
import {
  buildClarificationContinuationMessage,
  workflowOriginalRequest,
  type ClarificationAnswers
} from '../components/WorkflowRunCard'
import type { AgentChatMessage } from '../types'
import type { RelatedEndpointContext } from './useChatSessions'
import {
  beginOptimisticSkillSend,
  rollbackSkillSelection,
  selectedSkillNames
} from '../skillSelection'
import { stoppedAnswer, workflowCodeChanges, workflowPreviewTarget } from '../utils'
import type { WorkflowPreviewTarget } from '../utils'
import type { PersistSessionInput } from './useChatSessions'
import {
  sessionIdentityMatchesTarget,
  type SessionIdentity,
  type SessionRunStatus
} from './sessionRuntime'
import {
  planExecutionForPage,
  withWorkflowExecutionStatus,
  workflowInteractionAvailability,
  workflowResumeNode
} from '../planExecutionMode'

type SessionRunEntry = {
  identity: SessionIdentity
  status: SessionRunStatus
  directModification: boolean
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
  selectedPageId?: string
  directModificationEnabled: boolean
  /** 当前是否处于开发阶段；仅此阶段允许主会话匹配任意产物目标。 */
  developmentPhase?: boolean
  designPhase?: boolean
  /** 当前查看阶段是否为计划阶段，用于选择项目 Agent 的默认会话。 */
  planningPhase?: boolean
  /** 当前是否处于测试阶段默认对话。 */
  testingPhase?: boolean
  /** 当前是否处于验收阶段普通对话，用于把反馈交给范围审核而不是页面构建。 */
  acceptancePhase?: boolean
  autoStartDesign?: boolean
  /** 用户确认开发产物全部完成后，进入测试阶段并创建应用级测试会话。 */
  autoStartTesting?: boolean
  /** 用户确认测试合格后，进入审查阶段并创建应用级非功能检查会话。 */
  autoStartReview?: boolean
  editorMode: EditorMode
  ensureActiveSession: () => Promise<SessionIdentity>
  /** 创建/复用研发 Agent 的开发阶段主对话。 */
  ensureDevelopmentSession: () => Promise<SessionIdentity>
  /** 创建/复用产品 Agent 的分析阶段默认会话。 */
  ensureAnalysisSession: () => Promise<SessionIdentity>
  /** 创建/复用项目 Agent 的计划阶段默认会话。 */
  ensurePlanningSession: () => Promise<SessionIdentity>
  /** 创建/复用无页面归属的应用级会话(审查阶段专用)。 */
  ensureReviewSession: () => Promise<SessionIdentity>
  /** 创建/复用无页面归属的应用级测试会话。 */
  ensureTestingSession: () => Promise<SessionIdentity>
  /** 创建/复用无页面归属的应用级验收会话。 */
  ensureAcceptanceSession: () => Promise<SessionIdentity>
  ensureEndpointSession: (
    apiContractId: string,
    endpointId: string,
    endpointLabel: string
  ) => Promise<SessionIdentity>
  ensurePageSession: (
    pageId: string,
    pageLabel: string,
    endpointContext?: RelatedEndpointContext
  ) => Promise<SessionIdentity>
  getSessionMessages: (sessionKey: string) => AgentChatMessage[]
  persistSession: (input: PersistSessionInput) => Promise<void>
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onPreviewReady: (target: WorkflowPreviewTarget) => void
  publishAiMessage: (mode: EditorMode, content: string) => void
  runningSessionsRef: MutableRefObject<Map<string, SessionIdentity>>
  setDraftByKey: (sessionKey: string, value: string) => void
  setSelectedSkillsByKey: (sessionKey: string, value: ChatMessageSkill[]) => void
  setSessionMessages: (sessionKey: string, value: SetStateAction<AgentChatMessage[]>) => void
}

type UseWorkflowConversationResult = {
  activeWorkflow?: WorkflowRunPayload
  directModificationRunning: boolean
  error?: string
  handleAcceptPreview: () => Promise<boolean>
  handleAdjustPlan: (feedback: string) => Promise<void>
  handleEndPlan: (runId?: string) => Promise<void>
  handleResumePlan: (workflowDebug?: WorkflowDebugOptions) => Promise<void>
  handleRetryPlan: () => Promise<void>
  handleStopPlan: (runId?: string) => Promise<void>
  handleSend: (
    workflowDebug?: WorkflowDebugOptions,
    explicitMessage?: string,
    options?: {
      sessionIdentity?: SessionIdentity
      selectedFilePaths?: string[]
      suppressUserMessage?: boolean
      /** 结构化入口显式指定的产物目标，覆盖当前查看上下文（如后台任务的验收入口）。 */
      selectedPageId?: string
      selectedApiContractId?: string
      selectedEndpointId?: string
      detailTargetType?: 'page' | 'endpoint' | 'application'
      /** 结构化入口显式指定的工作流范围（如 artifact_acceptance）。 */
      workflowScope?: string
    }
  ) => Promise<void>
  handleStartDetailConfirmation: (
    selectedPageId: string,
    pageLabel: string,
    hasDetailPlan?: boolean,
    templateParams?: {
      templateId?: string
      templateName?: string
      templateSourcePath?: string
    },
    relatedEndpoint?: RelatedEndpointContext
  ) => Promise<boolean>
  handleStartEndpointDetailConfirmation: (target: {
    apiContractId?: string
    endpointId: string
    endpointLabel: string
    hasDetailPlan?: boolean
  }) => Promise<boolean>
  handleStopGenerating: () => void
  handleSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => Promise<boolean>
  loading: boolean
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

/** 从 Workflow 快照中恢复 endpoint 构建范围，供详情确认后的继续执行使用。 */
function workflowEndpointExecutionScope(
  workflow: WorkflowRunPayload
): WorkflowBuildExecutionScope | undefined {
  const detailTargetType = String(
    workflow.state?.detailTargetType || workflow.result?.detailTargetType || ''
  ).trim()
  // 页面任务可以携带依赖接口身份，但仍由页面工作流一次交付，不能误分流到独立接口工作流。
  if (detailTargetType && detailTargetType !== 'endpoint') return undefined
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

/** 从工作流快照读取规划确认模式，决定确认后是否切换阶段默认会话。 */
function workflowClarificationMode(workflow: WorkflowRunPayload): string {
  for (const source of [
    workflow.summary?.clarification,
    workflow.state?.clarification,
    workflow.result?.clarification
  ]) {
    if (source && typeof source === 'object') {
      const mode = (source as { mode?: unknown }).mode
      if (typeof mode === 'string') return mode
    }
  }
  return ''
}

/** 读取 yes/no 确认答案，供需求或计划退回当前阶段继续修改。 */
function clarificationAnswerIsYes(answers: ClarificationAnswers, key: string): boolean {
  const value = answers[key]
  if (value && typeof value === 'object' && !Array.isArray(value) && 'selected' in value) {
    const selected = value.selected
    return (Array.isArray(selected) ? selected : [selected]).some((item) => String(item) === '是')
  }
  if (Array.isArray(value)) return value.some((item) => String(item) === '是')
  return value === '是'
}

/** 根据会话身份确定消息创建时的 Agent 阶段，避免历史消息跟随当前阶段漂移。 */
function agentPhaseForSession(
  identity: SessionIdentity,
  fallback: WorkbenchPhase,
  options: {
    autoStartReview?: boolean
    autoStartTesting?: boolean
    designPhase?: boolean
    planningPhase?: boolean
    testingPhase?: boolean
  }
): WorkbenchPhase {
  if (
    identity.sessionKind === 'analysis' ||
    identity.sessionKind === 'planning' ||
    identity.sessionKind === 'development' ||
    identity.sessionKind === 'testing' ||
    identity.sessionKind === 'review' ||
    identity.sessionKind === 'acceptance'
  ) {
    return identity.sessionKind
  }
  if (identity.pageId || identity.endpointId) return 'development'
  if (options.autoStartReview) return 'review'
  if (options.autoStartTesting || options.testingPhase) return 'testing'
  if (options.designPhase) return options.planningPhase ? 'planning' : 'analysis'
  return fallback
}

/** 读取测试 Workflow 身份；同一用例的后续节点复用消息，下一条用例切换到新消息。 */
function workflowSegmentKey(workflow?: WorkflowRunPayload): string {
  const state = (workflow?.state || {}) as Record<string, unknown>
  const result = (workflow?.result || {}) as Record<string, unknown>
  return String(
    state.testWorkflowKey ||
      result.testWorkflowKey ||
      state.testWorkflowType ||
      state.workflowType ||
      result.testWorkflowType ||
      result.workflowType ||
      ''
  ).trim()
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
  selectedPageId,
  directModificationEnabled,
  developmentPhase = false,
  designPhase,
  planningPhase,
  testingPhase,
  autoStartDesign,
  autoStartTesting,
  autoStartReview,
  acceptancePhase,
  editorMode,
  ensureActiveSession,
  ensureDevelopmentSession,
  ensureAnalysisSession,
  ensurePlanningSession,
  ensureReviewSession,
  ensureTestingSession,
  ensureAcceptanceSession,
  getSessionMessages,
  persistSession,
  onApplicationLifecycleChange,
  onPreviewReady,
  publishAiMessage,
  runningSessionsRef,
  setDraftByKey,
  setSelectedSkillsByKey,
  setSessionMessages
}: UseWorkflowConversationParams): UseWorkflowConversationResult {
  const stopRequestedRef = useRef<Record<string, boolean>>({})
  const notifiedPreviewTargetsRef = useRef<Set<string>>(new Set())
  const acceptanceHandoffRef = useRef(false)
  const [runStates, setRunStates] = useState<Record<string, SessionRunEntry>>({})
  const [errors, setErrors] = useState<Record<string, string | undefined>>({})
  const [liveWorkflows, setLiveWorkflows] = useState<Record<string, WorkflowRunPayload>>({})

  useEffect(() => {
    // 离开验收阶段时清掉一次性回话标记，避免下次普通输入被误当成验收意见。
    if (!acceptancePhase) acceptanceHandoffRef.current = false
  }, [acceptancePhase])

  const selectedTarget = {
    apiContractId: selectedApiContractId,
    endpointId: selectedEndpointId,
    pageId: selectedPageId,
    allowDevelopmentMainSession: developmentPhase
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
  const directModificationRunning = Boolean(activeRun?.directModification)
  const error = activeRuntimeKey ? errors[activeRuntimeKey] : undefined
  const activeWorkflow = activeRuntimeKey
    ? activeRun
      ? liveWorkflows[activeRuntimeKey]
      : (liveWorkflows[activeRuntimeKey] ?? latestWorkflow(getSessionMessages(activeRuntimeKey)))
    : undefined
  // 每个阶段只有一个主对话，Workflow 调度不再需要通过会话 owner 互相抢占写权限。
  const workspaceBusy = false
  const sessionRunStates = useMemo(
    () =>
      Object.values(runStates).reduce<Record<string, SessionRunStatus>>((states, entry) => {
        if (
          entry.identity.workspaceRoot === application.workspaceRoot &&
          entry.identity.editorMode === editorMode
        ) {
          states[entry.identity.sessionId] = entry.status
        }
        return states
      }, {}),
    [application.workspaceRoot, editorMode, runStates]
  )

  /** 验收不通过后只把用户意见追加到固定会话，暂不启动后续处理流程。 */
  const appendAcceptanceFeedbackMessage = async (
    identity: SessionIdentity,
    message: string
  ): Promise<void> => {
    const previousMessages = getSessionMessages(identity.key)
    const now = Date.now()
    const nextMessages: AgentChatMessage[] = [
      ...previousMessages,
      {
        id: now,
        role: 'user',
        content: message,
        skills: selectedSkills.length > 0 ? selectedSkills : undefined,
        createdAt: now
      }
    ]
    setSessionMessages(identity.key, nextMessages)
    setDraftByKey(identity.key, '')
    setSelectedSkillsByKey(identity.key, [])
    if (draftKey !== identity.key) {
      setDraftByKey(draftKey, '')
      setSelectedSkillsByKey(draftKey, [])
    }
    await persistSession({
      editorMode: identity.editorMode,
      messages: nextMessages,
      sessionId: identity.sessionId,
      threadId: identity.threadId,
      apiContractId: identity.apiContractId,
      endpointId: identity.endpointId,
      endpointLabel: identity.endpointLabel,
      pageId: identity.pageId,
      sessionKind: identity.sessionKind,
      titleFrom: '应用验收'
    })
  }

  /** 首次发送时创建目标会话；简单模式补充输入优先复用当前会话和 thread。 */
  const handleSend = async (
    workflowDebug?: WorkflowDebugOptions,
    explicitMessage?: string,
    options?: {
      sessionIdentity?: SessionIdentity
      selectedFilePaths?: string[]
      suppressUserMessage?: boolean
      /** 结构化入口显式指定的产物目标，覆盖当前查看上下文（如后台任务的验收入口）。 */
      selectedPageId?: string
      selectedApiContractId?: string
      selectedEndpointId?: string
      detailTargetType?: 'page' | 'endpoint' | 'application'
      /** 结构化入口显式指定的工作流范围（如 artifact_acceptance）。 */
      workflowScope?: string
    }
  ): Promise<void> => {
    const message = (explicitMessage || draft).trim() || workflowDebugMessage(workflowDebug)
    if (!message || loading || workspaceBusy) return
    // 验收入口由应用预览承载；进入对话后只把用户意见追加到验收会话。
    const acceptanceFeedback = Boolean(acceptancePhase)
    const workflowPhase = String(activeWorkflow?.summary?.phase || '')
    const planningDesignConversation =
      Boolean(designPhase) &&
      (Boolean(planningPhase) ||
        activeSession?.sessionKind === 'planning' ||
        workflowPhase === 'project_planning')
    // 自动推进会先创建目标阶段的默认会话；普通消息则严格追加到当前已打开的会话。
    const automaticStageSession = autoStartTesting || autoStartReview
    if (
      acceptanceFeedback &&
      (options?.sessionIdentity || activeSession)?.sessionKind !== 'acceptance'
    ) {
      setErrors((current) => ({
        ...current,
        [activeSession?.key || draftKey]: '验收意见需要先进入应用验收对话。'
      }))
      return
    }
    const sessionIdentity =
      options?.sessionIdentity ||
      (automaticStageSession
        ? // 审查是比测试更靠后的阶段；同一次渲染里两个自动开启标志可能尚未互斥，
          // 必须让 autoStartReview 优先命中审查会话，否则审查开启消息会被误投到测试会话并重放测试剧本。
          autoStartReview
          ? await ensureReviewSession()
          : autoStartTesting
            ? await ensureTestingSession()
            : await ensureAcceptanceSession()
        : activeSession ||
          (acceptanceFeedback
            ? await ensureAcceptanceSession()
            : isDirectModificationWorkflow(activeWorkflow) && matchingActiveSession
              ? matchingActiveSession
              : testingPhase
                ? await ensureTestingSession()
                : selectedApiContractId && selectedEndpointId
                  ? await ensureDevelopmentSession()
                  : selectedPageId
                    ? await ensureDevelopmentSession()
                    : designPhase
                      ? planningDesignConversation
                        ? await ensurePlanningSession()
                        : await ensureAnalysisSession()
                      : await ensureActiveSession()))
    if (acceptanceFeedback && options?.suppressUserMessage) {
      // 仅用于“不通过”入口的产品 Agent 提示，不把入口动作伪装成用户意见。
      acceptanceHandoffRef.current = true
    } else if (acceptanceFeedback && acceptanceHandoffRef.current) {
      acceptanceHandoffRef.current = false
      await appendAcceptanceFeedbackMessage(sessionIdentity, message)
      return
    }
    const designWorkflowScope = planningDesignConversation
      ? 'application_workbench_planning'
      : 'application_analysis'
    // 结构化入口（如后台任务的验收入口）可显式指定产物目标，覆盖当前查看上下文。
    const targetApiContractId = options?.selectedApiContractId ?? selectedApiContractId
    const targetEndpointId = options?.selectedEndpointId ?? selectedEndpointId
    const targetPageId = options?.selectedPageId ?? selectedPageId
    await sendWorkflowMessage(message, {
      clearDraft: true,
      detailTargetType: acceptanceFeedback
        ? 'application'
        : targetApiContractId && targetEndpointId
          ? 'endpoint'
          : options?.detailTargetType,
      selectedApiContractId: acceptanceFeedback ? undefined : targetApiContractId,
      selectedEndpointId: acceptanceFeedback ? undefined : targetEndpointId,
      buildExecutionScope:
        !acceptanceFeedback && targetApiContractId && targetEndpointId
          ? {
              type: 'endpoint',
              targetId: targetEndpointId,
              apiContractId: targetApiContractId
            }
          : undefined,
      selectedSkills,
      originalRequest: acceptanceFeedback ? message : undefined,
      selectedFilePaths: acceptanceFeedback ? undefined : options?.selectedFilePaths,
      selectedPageId: acceptanceFeedback
        ? undefined
        : targetApiContractId && targetEndpointId
          ? ''
          : targetPageId,
      sessionIdentity,
      titleFrom: acceptanceFeedback
        ? '应用验收'
        : designPhase && !selectedApiContractId && !selectedEndpointId
          ? planningDesignConversation
            ? '项目计划'
            : '需求分析'
          : autoStartReview
            ? '代码审查'
            : autoStartTesting || testingPhase
              ? '应用测试'
              : message,
      workflowDebug,
      suppressUserMessage: options?.suppressUserMessage,
      acceptanceFeedback,
      directModification: acceptanceFeedback
        ? false
        : shouldUseDirectModification(directModificationEnabled, activeWorkflow, workflowDebug),
      workflowScope:
        options?.workflowScope ||
        (acceptanceFeedback
          ? 'application_acceptance_feedback'
          : designPhase && !targetApiContractId && !targetEndpointId
            ? designWorkflowScope
            : // 与会话路由一致：审查开启必须优先于测试判定，避免误标为 application_testing。
              autoStartReview
              ? 'application_review'
              : autoStartTesting || testingPhase
                ? 'application_testing'
                : undefined)
    })
  }

  /** 发送并持久化 Workflow 对话，认证失败时恢复发送前的界面状态。 */
  const sendWorkflowMessage = async (
    message: string,
    options?: {
      clearDraft?: boolean
      clarificationAnswers?: ClarificationAnswers
      originalRequest?: string
      selectedSkills?: ChatMessageSkill[]
      selectedFilePaths?: string[]
      resumeState?: WorkflowRunPayload
      titleFrom?: string
      workflowDebug?: WorkflowDebugOptions
      buildExecutionScope?: WorkflowBuildExecutionScope
      planControlAction?: 'stop' | 'end'
      planControlRunId?: string
      resumeExecutionRunId?: string
      acceptanceFeedback?: boolean
      selectedPageId?: string
      selectedApiContractId?: string
      selectedEndpointId?: string
      endpointLabel?: string
      detailTargetType?: 'page' | 'endpoint' | 'application'
      sessionIdentity?: SessionIdentity
      pageTemplate?: {
        id?: string
        name?: string
        sourcePath?: string
      }
      directModification?: boolean
      workflowScope?: string
      /** 结构化卡片提交只作为 Workflow 续跑参数，不重复写入用户对话历史。 */
      suppressUserMessage?: boolean
      /** 文件 Diff 授权后的续跑复用原 assistant 消息，保持同一页面只有一条研发工作流轨迹。 */
      reuseAssistantMessage?: boolean
    }
  ): Promise<boolean> => {
    const trimmedMessage = message.trim()
    if (!trimmedMessage) return false

    const identity = options?.sessionIdentity || (await ensureActiveSession())
    const messageAgentPhase = agentPhaseForSession(identity, 'development', {
      autoStartReview,
      autoStartTesting,
      designPhase,
      planningPhase,
      testingPhase
    })
    if (runningSessionsRef.current.has(identity.key)) {
      setErrors((current) => ({
        ...current,
        [identity.key]: '当前会话正在执行。'
      }))
      return false
    }

    const endpointUrl = options?.directModification ? getDirectModificationUrl() : getWorkflowUrl()
    const currentAgUiSession = agUiSessionsRef.current[identity.key]
    const agUiSession =
      currentAgUiSession && currentAgUiSession.endpointUrl === endpointUrl
        ? currentAgUiSession
        : (agUiSessionsRef.current[identity.key] = new AgUiChatSession(
            identity.threadId,
            endpointUrl
          ))
    const optimisticSkills = beginOptimisticSkillSend(options?.selectedSkills || [])
    const previousMessages = getSessionMessages(identity.key)
    const reusableAssistantMessage = options?.reuseAssistantMessage
      ? [...previousMessages].reverse().find((item) => item.role === 'assistant')
      : undefined
    const userMessage: AgentChatMessage = {
      id: Date.now(),
      role: 'user',
      content: trimmedMessage,
      skills: optimisticSkills.messageSkills,
      createdAt: Date.now()
    }
    let assistantMessageId = reusableAssistantMessage?.id || Date.now() + 1
    const assistantMessage: AgentChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      agentPhase: messageAgentPhase,
      createdAt: Date.now()
    }
    const nextMessages = reusableAssistantMessage
      ? previousMessages
      : options?.suppressUserMessage
        ? [...previousMessages, assistantMessage]
        : [...previousMessages, userMessage, assistantMessage]

    runningSessionsRef.current.set(identity.key, identity)
    setRunStates((current) => ({
      ...current,
      [identity.key]: {
        identity,
        status: 'running',
        directModification: Boolean(options?.directModification)
      }
    }))
    setErrors((current) => ({ ...current, [identity.key]: undefined }))
    if (!options?.planControlAction) {
      setLiveWorkflows((current) => omitKey(current, identity.key))
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
    let streamedWorkflowSegmentKey = ''
    let streamedToolCalls: ToolCallRecord[] = []
    let streamedProcessSteps: ProcessStepRecord[] = reusableAssistantMessage?.processSteps || []
    let latestMessages = nextMessages
    const updateAssistantMessage = (
      content: string,
      workflow?: WorkflowRunPayload,
      toolCalls?: ToolCallRecord[],
      processSteps?: ProcessStepRecord[]
    ): AgentChatMessage[] => {
      const nextCodeChanges = workflowCodeChanges(workflow)
      const updateMessages = (currentMessages: AgentChatMessage[]): AgentChatMessage[] =>
        currentMessages.map((currentMessage) =>
          currentMessage.id === assistantMessageId
            ? {
                ...currentMessage,
                content,
                workflow: workflow ?? currentMessage.workflow,
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
      const nextSegmentKey = workflowSegmentKey(nextWorkflow)
      if (
        identity.sessionKind === 'testing' &&
        streamedWorkflow &&
        nextSegmentKey &&
        nextSegmentKey !== streamedWorkflowSegmentKey
      ) {
        // 测试阶段主对话固定不变，同一用例继续更新原消息，切换下一用例时才追加消息。
        const nextMessageId =
          Math.max(Date.now(), ...latestMessages.map((message) => message.id)) + 1
        const nextAssistantMessage: AgentChatMessage = {
          id: nextMessageId,
          role: 'assistant',
          content: '',
          agentPhase: 'testing',
          createdAt: Date.now()
        }
        latestMessages = [...latestMessages, nextAssistantMessage]
        setSessionMessages(identity.key, latestMessages)
        assistantMessageId = nextMessageId
        streamedContent = ''
        streamedToolCalls = []
        streamedProcessSteps = []
      }
      streamedWorkflowSegmentKey = nextSegmentKey
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
        pageId: identity.pageId,
        sessionKind: identity.sessionKind,
        titleFrom: options?.titleFrom || trimmedMessage,
        materialize: false
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
        originalRequest: options?.originalRequest,
        onApplicationLifecycle: onApplicationLifecycleChange,
        selectedSkillNames: selectedSkillNames(options?.selectedSkills),
        selectedFilePaths: options?.selectedFilePaths,
        selectedPageId:
          options && 'selectedPageId' in options ? options.selectedPageId : identity.pageId,
        selectedApiContractId: options?.selectedApiContractId,
        selectedEndpointId: options?.selectedEndpointId,
        detailTargetType: options?.detailTargetType,
        buildExecutionScope: options?.buildExecutionScope,
        workflowDebug: options?.workflowDebug,
        planControlAction: options?.planControlAction,
        planControlRunId: options?.planControlRunId,
        resumeExecutionRunId: options?.resumeExecutionRunId,
        acceptanceFeedback: options?.acceptanceFeedback,
        resumeState: options?.resumeState,
        pageTemplate: options?.pageTemplate,
        directModification: options?.directModification,
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
          streamedProcessSteps = reusableAssistantMessage
            ? mergeProcessSteps(streamedProcessSteps, nextProcessSteps)
            : nextProcessSteps
          updateAssistantMessage(
            streamedContent,
            streamedWorkflow,
            streamedToolCalls,
            streamedProcessSteps
          )
        }
      })
      const stopped = Boolean(stopRequestedRef.current[identity.key])
      const answer = stopped ? stoppedAnswer(streamedContent || rawAnswer) : rawAnswer.trim()
      const finalWorkflow = stopped
        ? withWorkflowExecutionStatus(workflow ?? streamedWorkflow, 'stopped')
        : (workflow ?? streamedWorkflow)
      // 没有正式回复时保持正文为空：执行状态由 Agent 头像后的加载动画、节点过程或交互卡承载，
      // 不把内部 Workflow 状态词写回用户对话。
      const completedMessages = updateAssistantMessage(
        answer,
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
        pageId: identity.pageId,
        sessionKind: identity.sessionKind,
        titleFrom: options?.titleFrom || trimmedMessage
      })
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
          pageId: identity.pageId,
          sessionKind: identity.sessionKind,
          materialize: false
        })
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
          pageId: identity.pageId,
          sessionKind: identity.sessionKind,
          titleFrom: message
        })
        publishAiMessage(identity.editorMode, answer)
        return false
      }
      setErrors((current) => ({
        ...current,
        [identity.key]: caughtError instanceof Error ? caughtError.message : '调用 Workflow 失败。'
      }))
      return false
    } finally {
      runningSessionsRef.current.delete(identity.key)
      setRunStates((current) => omitKey(current, identity.key))
      stopRequestedRef.current[identity.key] = false
    }
  }

  /** 将结构化确认转换为 Workflow 续跑参数，不把已在卡片确认的答案重复写入对话历史。 */
  const handleSubmitClarification = async (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ): Promise<boolean> => {
    const directModification = isDirectModificationWorkflow(workflow)
    if (
      !directModification &&
      workflowInteractionAvailability(workflow, applicationLifecycle) !== 'active'
    )
      return false
    const continuationMessage = buildClarificationContinuationMessage(workflow, answers)
    if (!continuationMessage || loading || workspaceBusy) return false
    const originalRequest = workflowOriginalRequest(workflow)
    const clarificationMode = workflowClarificationMode(workflow)
    // 开发/审查 Diff 与测试用例确认均复用原工作流消息，避免确认动作凭空拆出新的工作流卡片。
    const fileAcceptanceContinuation =
      ['build', 'code_review'].includes(String(workflow.summary?.phase || '')) &&
      (clarificationMode === 'file_acceptance' || answers.file_acceptance !== undefined)
    // 产物验收/后台执行方式选择的续跑复用原工作流消息，交互卡在原地落为完成态。
    const acceptanceContinuation =
      clarificationMode === 'page_acceptance' && answers.page_acceptance !== undefined
    // 页面轮与接口轮分别提交（background_dispatch / background_dispatch_endpoint），两键任一出现即复用原工作流消息。
    const backgroundDispatchContinuation =
      clarificationMode === 'background_dispatch' &&
      (answers.background_dispatch !== undefined ||
        answers.background_dispatch_endpoint !== undefined)
    const requirementConfirmation = clarificationMode === 'requirement_spec_confirmation'
    const requirementNeedsRevision =
      requirementConfirmation &&
      answers.confirm_requirement_spec !== undefined &&
      !clarificationAnswerIsYes(answers, 'confirm_requirement_spec')
    // 需求确认完成后立即切换到项目 Agent 的独立默认对话，后续项目计划不再混入产品会话。
    const planningDesignContinuation =
      Boolean(designPhase) &&
      !requirementNeedsRevision &&
      (requirementConfirmation ||
        Boolean(planningPhase) ||
        activeSession?.sessionKind === 'planning')
    const sessionIdentity = designPhase
      ? requirementNeedsRevision
        ? await ensureAnalysisSession()
        : planningDesignContinuation
          ? await ensurePlanningSession()
          : await ensureAnalysisSession()
      : undefined
    // 应用验收、测试用例检查与审查确认必须续传到各自的应用级剧本，避免落回页面/API工作流。
    const applicationAcceptanceResume =
      workflow.summary?.phase === 'acceptance' &&
      !workflowSelectedPageId(workflow) &&
      !workflowEndpointExecutionScope(workflow)
    const reviewResume = workflow.summary?.phase === 'code_review'
    const testingResume =
      activeSession?.sessionKind === 'testing' ||
      ['application_test', 'business_test'].includes(String(workflow.summary?.phase || ''))
    return sendWorkflowMessage(continuationMessage, {
      clarificationAnswers: answers,
      originalRequest,
      resumeState: workflow,
      selectedPageId: workflowSelectedPageId(workflow) || activeSession?.pageId || selectedPageId,
      buildExecutionScope: workflowEndpointExecutionScope(workflow),
      titleFrom: planningDesignContinuation ? '项目计划' : originalRequest || '需求分析',
      sessionIdentity,
      directModification,
      suppressUserMessage: true,
      reuseAssistantMessage:
        fileAcceptanceContinuation ||
        testingResume ||
        acceptanceContinuation ||
        backgroundDispatchContinuation,
      // 分析/计划阶段确认必须继续走对应的工作台规划剧本，否则会被默认路由到
      // replayWorkbench，导致提交确认后不推进规划节点。
      workflowScope:
        designPhase && !directModification
          ? requirementNeedsRevision
            ? 'application_analysis'
            : requirementConfirmation || planningPhase || activeSession?.sessionKind === 'planning'
              ? 'application_workbench_planning'
              : 'application_analysis'
          : applicationAcceptanceResume
            ? 'application_acceptance'
            : reviewResume
              ? 'application_review'
              : testingResume
                ? 'application_testing'
                : undefined
    })
  }

  /** 以用户选择的 RequirementSpec 页面作为主 Workflow 细节设计起点。 */
  const handleStartDetailConfirmation = async (
    selectedPageId: string,
    pageLabel: string,
    hasDetailPlan?: boolean,
    templateParams?: {
      templateId?: string
      templateName?: string
      templateSourcePath?: string
    },
    relatedEndpoint?: RelatedEndpointContext
  ): Promise<boolean> => {
    if (!selectedPageId || loading || workspaceBusy) return false
    // 开发阶段统一复用阶段主对话；页面只作为本轮 Workflow 的目标，不再创建页面专属会话。
    const identity = await ensureDevelopmentSession()
    return sendWorkflowMessage(
      `${hasDetailPlan ? '继续实现' : '开始实现'}：${pageLabel}${
        relatedEndpoint ? `（包含 ${relatedEndpoint.endpointLabel}）` : ''
      }${templateParams?.templateName ? `，使用模板「${templateParams.templateName}」` : ''}`,
      {
        selectedPageId,
        selectedApiContractId: relatedEndpoint?.apiContractId,
        selectedEndpointId: relatedEndpoint?.endpointId,
        detailTargetType: 'page',
        sessionIdentity: identity,
        titleFrom: `实现${pageLabel}`,
        ...(templateParams?.templateSourcePath
          ? {
              pageTemplate: {
                id: templateParams.templateId,
                name: templateParams.templateName,
                sourcePath: templateParams.templateSourcePath
              }
            }
          : {}),
        // 复用模板选择节点所在的 assistant 消息，保持详细设计、Diff 和预览在同一条 Workflow 轨迹。
        reuseAssistantMessage: true,
        // 模板选择已经由卡片表达，不能再把“开始实现 + 模板名”重复渲染成用户消息。
        suppressUserMessage: true
      }
    )
  }

  /** 以用户选择的具体 endpoint 作为主 Workflow 细节设计起点。 */
  const handleStartEndpointDetailConfirmation = async (target: {
    apiContractId?: string
    endpointId: string
    endpointLabel: string
    hasDetailPlan?: boolean
  }): Promise<boolean> => {
    if (!target.apiContractId || !target.endpointId || loading || workspaceBusy) return false
    // 开发阶段统一复用阶段主对话；接口身份仅通过本轮 Workflow 参数传递。
    const identity = await ensureDevelopmentSession()
    return sendWorkflowMessage(
      `${target.hasDetailPlan ? '继续实现接口' : '开始实现接口'}：${target.endpointLabel}`,
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
        titleFrom: `实现${target.endpointLabel}`,
        reuseAssistantMessage: true,
        // 接口目标选择同样由产物卡片承载，历史只保留 Agent 的执行承接与节点过程。
        suppressUserMessage: true
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
        directModification: Boolean(current[runningIdentity.key]?.directModification)
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
    const resumeFrom = workflowResumeNode(activeWorkflow, execution?.phase)
    await sendWorkflowMessage('重试当前计划任务。', {
      resumeState: activeWorkflow,
      resumeExecutionRunId: execution?.runId,
      selectedPageId: workflowSelectedPageId(activeWorkflow) || activeSession?.pageId,
      titleFrom: '重试计划任务',
      workflowDebug: { enabled: true, resumeFrom },
      // 重试是工作流控制动作，按钮本身已经表达了用户意图，不再追加一条用户消息。
      suppressUserMessage: true
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
      resumeExecutionRunId: execution?.runId,
      selectedPageId: workflowSelectedPageId(activeWorkflow) || activeSession?.pageId,
      titleFrom: '从指定节点继续执行',
      workflowDebug,
      // 调试面板的节点选择属于结构化控制，不重复写入对话正文。
      suppressUserMessage: true
    })
  }

  /** 使用受控反馈重新生成执行任务，输入仅在用户主动调整计划时开放。 */
  const handleAdjustPlan = async (feedback: string): Promise<void> => {
    const normalizedFeedback = feedback.trim()
    if (!activeWorkflow || !normalizedFeedback || loading || workspaceBusy) return
    const execution = planExecutionForPage(
      activeWorkflow.summary.lifecycle,
      activeSession?.pageId || selectedPageId,
      { runId: activeWorkflow.runId, threadId: activeWorkflow.threadId }
    )
    await sendWorkflowMessage(`调整执行计划：${normalizedFeedback}`, {
      resumeState: activeWorkflow,
      resumeExecutionRunId: execution?.runId,
      selectedPageId: workflowSelectedPageId(activeWorkflow) || activeSession?.pageId,
      titleFrom: '调整执行计划',
      workflowDebug: { enabled: true, resumeFrom: 'prepare_build_tasks' }
    })
  }

  /** 通过同一 AG-UI 端点结束计划并释放生命周期中的工作区锁。 */
  const handleEndPlan = async (runId?: string): Promise<void> => {
    if (loading || workspaceBusy) return
    const execution = planExecutionForPage(
      activeWorkflow?.summary.lifecycle,
      activeSession?.pageId || selectedPageId,
      { runId: activeWorkflow?.runId, threadId: activeWorkflow?.threadId }
    )
    const targetRunId = runId || execution?.runId
    if (!targetRunId) return
    await sendWorkflowMessage('结束当前计划。', {
      planControlAction: 'end',
      planControlRunId: targetRunId,
      selectedPageId: activeSession?.pageId || selectedPageId,
      titleFrom: '结束计划',
      // 结束按钮本身就是确认，不再制造一条“结束当前计划”的用户消息。
      suppressUserMessage: true
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
    const targetRunId = runId || execution?.runId
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
      titleFrom: '暂停计划',
      // 暂停属于工作流控制动作，保留状态变化即可。
      suppressUserMessage: true
    })
    if (stopped && activeRuntimeKey && resumeWorkflow) {
      setLiveWorkflows((current) => ({
        ...current,
        [activeRuntimeKey]:
          withWorkflowExecutionStatus(resumeWorkflow, 'stopped', targetRunId) || resumeWorkflow
      }))
    }
  }

  // 分析/计划阶段：新应用或阶段回退后，默认 Agent 主动开启当前阶段对话。
  // 注意：ref 只在 timer 真正 fire（已发起发送）后置位，而非 effect body 里提前置位——
  // 否则 React.StrictMode 双调（mount→cleanup 清 timer→重 mount）会因 ref 已 true 而不再
  // 调度，handleSend 被 cleanup 吞掉，表现为“有时自动开始、有时不开始”。
  const autoStartDesignRef = useRef(false)
  useEffect(() => {
    if (!autoStartDesign || autoStartDesignRef.current) return
    if (loading || workspaceBusy) return // 等待就绪后再自动开始，避免发送被 gate 拦掉
    // 稍等让会话列表/运行态稳定，避免新建会话与发送竞态导致内容不进展示。
    const timer = window.setTimeout(() => {
      autoStartDesignRef.current = true // 只在真正发起后置位，cleanup 清 timer 时保持可重试
      void handleSend(undefined, planningPhase ? '开始项目计划' : '开始需求分析')
    }, 600)
    return () => window.clearTimeout(timer)
  }, [autoStartDesign, loading, planningPhase, workspaceBusy])

  // 用户确认开发完成后进入测试阶段，测试 Agent 负责执行完整用例集。
  const autoStartTestingRef = useRef(false)
  useEffect(() => {
    if (!autoStartTesting) autoStartTestingRef.current = false
  }, [autoStartTesting])
  useEffect(() => {
    if (!autoStartTesting || autoStartTestingRef.current) return
    if (loading || workspaceBusy) return
    const timer = window.setTimeout(() => {
      autoStartTestingRef.current = true
      void handleSend(undefined, '开始应用测试', { suppressUserMessage: true })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [autoStartTesting, loading, workspaceBusy])

  // 进入审查阶段后直接创建审查 Agent 的应用级会话，不再追加“是否启动审查”的用户消息。
  // 用 ref 防 StrictMode 双调；离开触发态后复位，以支持下一版本再次进入审查。
  const autoStartReviewRef = useRef(false)
  // 确认信号撤销时重置一次性门闩，下一版本仍可按同一流程进入审查。
  useEffect(() => {
    if (!autoStartReview) autoStartReviewRef.current = false
  }, [autoStartReview])
  useEffect(() => {
    if (!autoStartReview || autoStartReviewRef.current) return
    if (loading || workspaceBusy) return
    const timer = window.setTimeout(() => {
      autoStartReviewRef.current = true
      void handleSend(undefined, '开始代码审查', { suppressUserMessage: true })
    }, 0)
    return () => window.clearTimeout(timer)
  }, [autoStartReview, loading, workspaceBusy])

  return {
    activeWorkflow,
    directModificationRunning,
    error,
    handleAcceptPreview,
    handleAdjustPlan,
    handleEndPlan,
    handleResumePlan,
    handleRetryPlan,
    handleStopPlan,
    handleSend,
    handleStartEndpointDetailConfirmation,
    handleStartDetailConfirmation,
    handleStopGenerating,
    handleSubmitClarification,
    loading,
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

/** 合并同一研发工作流的续跑节点：已有节点更新状态，新增文件节点顺序追加。 */
function mergeProcessSteps(
  previousSteps: ProcessStepRecord[],
  nextSteps: ProcessStepRecord[]
): ProcessStepRecord[] {
  const merged = [...previousSteps]
  nextSteps.forEach((step) => {
    const existingIndex = merged.findIndex((item) => item.id === step.id)
    if (existingIndex >= 0) {
      merged[existingIndex] = {
        ...merged[existingIndex],
        ...step,
        sequence: merged[existingIndex].sequence,
        ...(Math.max(merged[existingIndex].total || 0, step.total || 0) > 0
          ? { total: Math.max(merged[existingIndex].total || 0, step.total || 0) }
          : {})
      }
      return
    }
    merged.push({ ...step, sequence: merged.length + 1 })
  })
  return merged
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
