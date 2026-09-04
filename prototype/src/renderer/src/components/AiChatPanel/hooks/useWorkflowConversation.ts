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
  workflowClarification,
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
  withWorkflowExecutionStatus,
  workflowInteractionAvailability
} from '../planExecutionMode'

type SessionRunEntry = {
  identity: SessionIdentity
  status: SessionRunStatus
  directModification: boolean
}

type UseWorkflowConversationParams = {
  activeSession?: SessionIdentity
  /** 用户显式持有编辑权限的常规任务；其它常规任务只能查看历史。 */
  authorizedEditingSessionId?: string
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
  /** 当前查看阶段是否为项目规划阶段，用于选择项目 Agent 的默认会话。 */
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
  /** 创建/复用产品 Agent 的需求分析阶段默认会话。 */
  ensureAnalysisSession: () => Promise<SessionIdentity>
  /** 创建/复用项目 Agent 的项目规划阶段默认会话。 */
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
  error?: string
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
  editingSessionId?: string
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
  authorizedEditingSessionId,
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
  // 多开发对话并存时运行态必须归属当前会话（按 sessionId 收敛），禁止对话 A 的运行进度泄漏到对话 B。
  const activeRun =
    (matchingActiveSession ? runStates[matchingActiveSession.key] : undefined) ||
    Object.values(runStates).find(
      (entry) =>
        entry.identity.workspaceRoot === application.workspaceRoot &&
        entry.identity.editorMode === editorMode &&
        sessionIdentityMatchesTarget(entry.identity, selectedTarget) &&
        (!activeSession || entry.identity.sessionId === activeSession.sessionId)
    )
  const activeRuntimeKey = matchingActiveSession?.key || activeRun?.identity.key
  const loading = activeRun?.status === 'running' || activeRun?.status === 'stopping'
  const stopping = activeRun?.status === 'stopping'
  const error = activeRuntimeKey ? errors[activeRuntimeKey] : undefined
  const activeWorkflow = activeRuntimeKey
    ? activeRun
      ? liveWorkflows[activeRuntimeKey]
      : (liveWorkflows[activeRuntimeKey] ?? latestWorkflow(getSessionMessages(activeRuntimeKey)))
    : undefined
  // 一个应用工作区同一时刻只保留一个正式编辑席位：运行、停止中和等待用户确认都继续占位。
  // 用户仍可切换其它对话查看历史，但只有占位会话能够继续输入或提交交互卡。
  const editingRun = Object.values(runStates).find(
    (entry) =>
      entry.identity.workspaceRoot === application.workspaceRoot &&
      entry.identity.editorMode === editorMode &&
      entry.identity.sessionKind === activeSession?.sessionKind
  )
  const editingSessionId = authorizedEditingSessionId || editingRun?.identity.sessionId
  const workspaceBusy = Boolean(editingSessionId && editingSessionId !== activeSession?.sessionId)
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
    // 产物验收入口（artifact_acceptance）属于开发对话的结构化动作，不按应用验收反馈分流，
    // 否则在验收查看阶段从任务抽屉点「验收」会被误判为验收意见而拒绝启动。
    const acceptanceFeedback =
      Boolean(acceptancePhase) && options?.workflowScope !== 'artifact_acceptance'
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
      Boolean(
        (options?.sessionIdentity || activeSession)?.pageId ||
          (options?.sessionIdentity || activeSession)?.endpointId
      )
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
          // 必须让 autoStartReview 优先命中审查会话，否则审查开启消息会被误投到测试会话。
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
    // 显式目标必须整体覆盖：未指明的维度清空，防止右侧面板残留的其它产物选中
    // （例如刚验收完接口后残留的接口上下文）把页面验收误路由到接口。
    const hasExplicitTarget = Boolean(
      options &&
        (options.selectedPageId !== undefined ||
          options.selectedApiContractId !== undefined ||
          options.selectedEndpointId !== undefined)
    )
    const targetApiContractId = hasExplicitTarget
      ? options?.selectedApiContractId ?? ''
      : selectedApiContractId
    const targetEndpointId = hasExplicitTarget
      ? options?.selectedEndpointId ?? ''
      : selectedEndpointId
    const targetPageId = hasExplicitTarget ? options?.selectedPageId ?? '' : selectedPageId
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
    const occupiedSession = Object.values(runStates).find(
      (entry) =>
        entry.identity.workspaceRoot === identity.workspaceRoot &&
        entry.identity.editorMode === identity.editorMode &&
        entry.identity.sessionKind === identity.sessionKind &&
        entry.identity.key !== identity.key
    )
    if (occupiedSession) {
      setErrors((current) => ({
        ...current,
        [identity.key]: '另一条任务正在执行或等待确认，请先完成该工作流。'
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
      // Workflow 暂停等待用户确认时仍占用唯一编辑席位；完成、失败或停止后才释放。
      setRunStates((current) =>
        streamedWorkflow?.summary?.status === 'requires_user_input'
          ? {
              ...current,
              [identity.key]: {
                identity,
                status: 'awaiting_user',
                directModification: Boolean(options?.directModification)
              }
            }
          : omitKey(current, identity.key)
      )
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
    // 阶段准入门（规划/开发）是跨会话续跑：目标会话由剧本决定，与当前推进任务无关。
    // 推进权已交给新建任务时仍必须能确认门禁，否则弹框会变成点了没反应的静默失效。
    const clarificationMode = workflowClarificationMode(workflow)
    const stageEntryGate =
      clarificationMode === 'planning_stage_entry' ||
      clarificationMode === 'development_entry_confirmation'
    if (!continuationMessage || loading || (workspaceBusy && !stageEntryGate)) return false
    // 确认完成后把每题答案回写到原消息的 Workflow 快照；阶段切换后这张卡仍可只读回看。
    if (activeSession) {
      const historicalClarification = workflowClarification(workflow)
      const nextMessages = getSessionMessages(activeSession.key).map((message) => {
        if (message.workflow !== workflow) return message
        const submittedClarification = historicalClarification
          ? { ...historicalClarification, status: 'submitted' }
          : undefined
        return {
          ...message,
          // 已提交门禁对应的待输入节点同步落为完成态：跨阶段续跑不会再来更新原消息轨迹，
          // 不落定的话历史回看会一直显示“等待确认”节点。
          processSteps: (message.processSteps || []).map((step) =>
            step.status === 'requires_user_input'
              ? { ...step, status: 'completed' as const }
              : step
          ),
          workflow: {
            ...workflow,
            summary: {
              ...workflow.summary,
              clarification: submittedClarification
            },
            state: {
              ...workflow.state,
              clarification: submittedClarification,
              clarificationAnswers: answers
            }
          }
        }
      })
      if (nextMessages.some((message, index) => message !== getSessionMessages(activeSession.key)[index])) {
        setSessionMessages(activeSession.key, nextMessages)
        await persistSession({
          editorMode: activeSession.editorMode,
          messages: nextMessages,
          sessionId: activeSession.sessionId,
          threadId: activeSession.threadId,
          apiContractId: activeSession.apiContractId,
          endpointId: activeSession.endpointId,
          endpointLabel: activeSession.endpointLabel,
          pageId: activeSession.pageId,
          sessionKind: activeSession.sessionKind,
          materialize: true
        })
      }
      // 已提交的确认卡立即退出运行占位；后续续跑会按目标阶段/会话重新写入 running 状态。
      // 不能让历史卡残留的 awaiting_user 把本阶段的新建任务入口永久锁住。
      setRunStates((current) => omitKey(current, activeSession.key))
      setLiveWorkflows((current) => omitKey(current, activeSession.key))
    }
    const originalRequest = workflowOriginalRequest(workflow)
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
    // 项目规划准入门：需求确认接受后，工作流停在分析会话的「进入项目规划阶段」节点上；
    // Diff 确认是节点动作，不直接触发阶段切换，门禁确认才切到规划会话。
    const planningStageEntry = clarificationMode === 'planning_stage_entry'
    // 每个阶段都有独立默认对话；准入门确认后必须在计划对话继续，不能混入分析消息。
    const planningDesignContinuation =
      Boolean(designPhase) &&
      !requirementNeedsRevision &&
      !requirementConfirmation &&
      (planningStageEntry ||
        Boolean(planningPhase) ||
        activeSession?.sessionKind === 'planning')
    const sessionIdentity = designPhase
      ? requirementNeedsRevision || requirementConfirmation
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
      titleFrom: planningStageEntry || planningDesignContinuation ? '项目计划' : originalRequest || '需求分析',
      sessionIdentity,
      directModification,
      suppressUserMessage: true,
      reuseAssistantMessage:
        fileAcceptanceContinuation ||
        testingResume ||
        acceptanceContinuation ||
        backgroundDispatchContinuation ||
        // 需求分析/项目规划阶段同会话续跑复用原工作流消息：整阶段保持一条连续工作流轨迹。
        // 跨阶段续跑（规划准入门 → 计划会话）目标会话尚无 assistant 消息，复用自然落空、另起新轨迹。
        (Boolean(designPhase) && !directModification),
      // 需求分析/项目规划阶段确认必须继续走对应的工作台规划剧本，否则会被默认路由到
      // replayWorkbench，导致提交确认后不推进规划节点。
      workflowScope:
        designPhase && !directModification
          ? requirementNeedsRevision || requirementConfirmation
            ? // 需求确认接受后回到分析剧本，推进到「进入项目规划阶段」准入门，不直接切阶段。
              'application_analysis'
            : planningStageEntry || planningPhase || activeSession?.sessionKind === 'planning'
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
    // 开发阶段复用当前打开的开发对话（主对话或按边界拆分的对话）；页面只作为本轮 Workflow 的目标。
    const identity =
      activeSession?.sessionKind === 'development'
        ? activeSession
        : await ensureDevelopmentSession()
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
    // 开发阶段复用当前打开的开发对话；接口身份仅通过本轮 Workflow 参数传递。
    const identity =
      activeSession?.sessionKind === 'development'
        ? activeSession
        : await ensureDevelopmentSession()
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

    // 需求分析/项目规划阶段：新应用或阶段回退后，默认 Agent 主动开启当前阶段对话。
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
    editingSessionId,
    error,
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
