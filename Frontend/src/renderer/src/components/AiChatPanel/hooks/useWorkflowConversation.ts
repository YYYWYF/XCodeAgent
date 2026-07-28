import { useRef, useState } from 'react'
import type { MutableRefObject, SetStateAction } from 'react'
import { AgUiChatSession } from '../../../service/agUiAgent'
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
import {
  buildClarificationContinuationMessage,
  workflowOriginalRequest,
  type ClarificationAnswers
} from '../components/WorkflowRunCard'
import type { AgentChatMessage } from '../types'
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
  workflowResumeNode
} from '../planExecutionMode'

type SessionRunEntry = {
  identity: SessionIdentity
  status: SessionRunStatus
}

type UseWorkflowConversationParams = {
  activeSession?: SessionIdentity
  agUiSessionsRef: MutableRefObject<Record<string, AgUiChatSession>>
  application: ApplicationConfig
  draft: string
  draftKey: string
  selectedSkills: ChatMessageSkill[]
  selectedApiContractId?: string
  selectedEndpointId?: string
  selectedEndpointLabel?: string
  selectedPageId?: string
  selectedPageLabel?: string
  editorMode: EditorMode
  ensureActiveSession: () => Promise<SessionIdentity>
  ensureEndpointSession: (
    apiContractId: string,
    endpointId: string,
    endpointLabel: string
  ) => Promise<SessionIdentity>
  ensurePageSession: (pageId: string, pageLabel: string) => Promise<SessionIdentity>
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
  handleAcceptPreview: () => Promise<boolean>
  handleAdjustPlan: (feedback: string) => Promise<void>
  handleEndPlan: (runId?: string) => Promise<void>
  handleResumePlan: (workflowDebug?: WorkflowDebugOptions) => Promise<void>
  handleRetryPlan: () => Promise<void>
  handleStopPlan: (runId?: string) => Promise<void>
  handleSend: (workflowDebug?: WorkflowDebugOptions) => Promise<void>
  handleStartDetailConfirmation: (
    selectedPageId: string,
    pageLabel: string,
    hasDetailPlan?: boolean,
    templateParams?: {
      templateId?: string
      templateName?: string
      templateSourcePath?: string
    },
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
  const stateApiContractId = workflow.state?.selected_api_contract_id || workflow.state?.selectedApiContractId
  const resultApiContractId = workflow.result?.selected_api_contract_id || workflow.result?.selectedApiContractId
  const stateEndpointId = workflow.state?.selected_endpoint_id || workflow.state?.selectedEndpointId
  const resultEndpointId = workflow.result?.selected_endpoint_id || workflow.result?.selectedEndpointId
  const apiContractId = String(stateApiContractId || resultApiContractId || '').trim()
  const endpointId = String(stateEndpointId || resultEndpointId || '').trim()
  return apiContractId && endpointId
    ? { type: 'endpoint', targetId: endpointId, apiContractId }
    : undefined
}

export function useWorkflowConversation({
  activeSession,
  agUiSessionsRef,
  application,
  draft,
  draftKey,
  selectedSkills,
  selectedApiContractId,
  selectedEndpointId,
  selectedEndpointLabel,
  selectedPageId,
  selectedPageLabel,
  editorMode,
  ensureActiveSession,
  ensureEndpointSession,
  ensurePageSession,
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
  const [runStates, setRunStates] = useState<Record<string, SessionRunEntry>>({})
  const [errors, setErrors] = useState<Record<string, string | undefined>>({})
  const [liveWorkflows, setLiveWorkflows] = useState<Record<string, WorkflowRunPayload>>({})

  const selectedTarget = {
    apiContractId: selectedApiContractId,
    endpointId: selectedEndpointId,
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

  /** 首次发送目标消息时再创建对应会话，并复用已有页面或 API 会话继续 Workflow。 */
  const handleSend = async (workflowDebug?: WorkflowDebugOptions): Promise<void> => {
    const message = draft.trim() || workflowDebugMessage(workflowDebug)
    if (!message || loading || workspaceBusy) return
    const sessionIdentity =
      selectedApiContractId && selectedEndpointId
        ? await ensureEndpointSession(
            selectedApiContractId,
            selectedEndpointId,
            selectedEndpointLabel || selectedEndpointId
          )
        : selectedPageId
          ? await ensurePageSession(selectedPageId, selectedPageLabel || selectedPageId)
          : await ensureActiveSession()
    await sendWorkflowMessage(message, {
      clearDraft: true,
      detailTargetType: selectedApiContractId && selectedEndpointId ? 'endpoint' : undefined,
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
      selectedPageId: selectedApiContractId && selectedEndpointId ? '' : selectedPageId,
      sessionIdentity,
      titleFrom: message,
      workflowDebug
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
      resumeState?: WorkflowRunPayload
      titleFrom?: string
      workflowDebug?: WorkflowDebugOptions
      buildExecutionScope?: WorkflowBuildExecutionScope
      planControlAction?: 'stop' | 'end'
      planControlRunId?: string
      resumeExecutionRunId?: string
      selectedPageId?: string
      selectedApiContractId?: string
      selectedEndpointId?: string
      endpointLabel?: string
      detailTargetType?: 'page' | 'endpoint'
      sessionIdentity?: SessionIdentity
      pageTemplate?: {
        id?: string
        name?: string
        sourcePath?: string
      }
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

    const agUiSession =
      agUiSessionsRef.current[identity.key] ||
      (agUiSessionsRef.current[identity.key] = new AgUiChatSession(identity.threadId))
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
      [identity.key]: { identity, status: 'running' }
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
    let streamedToolCalls: ToolCallRecord[] = []
    let streamedProcessSteps: ProcessStepRecord[] = []
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
        originalRequest: options?.originalRequest,
        onApplicationLifecycle: onApplicationLifecycleChange,
        selectedSkillNames: selectedSkillNames(options?.selectedSkills),
        selectedPageId:
          options && 'selectedPageId' in options
            ? options.selectedPageId
            : identity.pageId,
        selectedApiContractId: options?.selectedApiContractId,
        selectedEndpointId: options?.selectedEndpointId,
        detailTargetType: options?.detailTargetType,
        buildExecutionScope: options?.buildExecutionScope,
        workflowDebug: options?.workflowDebug,
        planControlAction: options?.planControlAction,
        planControlRunId: options?.planControlRunId,
        resumeExecutionRunId: options?.resumeExecutionRunId,
        resumeState: options?.resumeState,
        pageTemplate: options?.pageTemplate,
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
        : workflow ?? streamedWorkflow
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
        pageId: identity.pageId,
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
          pageId: identity.pageId
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

  /** 将结构化确认转换为可追踪的用户消息，并通过当前 AG-UI 会话恢复 Workflow。 */
  const handleSubmitClarification = async (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ): Promise<boolean> => {
    const continuationMessage = buildClarificationContinuationMessage(workflow, answers)
    if (!continuationMessage || loading || workspaceBusy) return false
    const originalRequest = workflowOriginalRequest(workflow)
    return sendWorkflowMessage(continuationMessage, {
      clarificationAnswers: answers,
      originalRequest,
      resumeState: workflow,
      selectedPageId: workflowSelectedPageId(workflow) || activeSession?.pageId || selectedPageId,
      buildExecutionScope: workflowEndpointExecutionScope(workflow),
      titleFrom: originalRequest || '补充需求确认'
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
  ): Promise<boolean> => {
    if (!selectedPageId || loading || workspaceBusy) return false
    const identity = await ensurePageSession(selectedPageId, pageLabel)
    return sendWorkflowMessage(
      `${hasDetailPlan ? '查看已生成页面计划' : '开始设计页面'}：${pageLabel}`,
      {
        selectedPageId,
        detailTargetType: 'page',
        sessionIdentity: identity,
        titleFrom: `${hasDetailPlan ? '确认页面' : '设计页面'}：${pageLabel}`,
        ...(templateParams?.templateSourcePath
          ? {
              pageTemplate: {
                id: templateParams.templateId,
                name: templateParams.templateName,
                sourcePath: templateParams.templateSourcePath,
              },
            }
          : {}),
      },
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
    const identity = await ensureEndpointSession(
      target.apiContractId,
      target.endpointId,
      target.endpointLabel
    )
    return sendWorkflowMessage(
      `${target.hasDetailPlan ? '查看已生成接口计划' : '开始设计接口'}：${target.endpointLabel}`,
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
        titleFrom: `${target.hasDetailPlan ? '确认接口' : '设计接口'}：${target.endpointLabel}`
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
      [runningIdentity.key]: { identity: runningIdentity, status: 'stopping' }
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
      workflowDebug: { enabled: true, resumeFrom }
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
      workflowDebug
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
