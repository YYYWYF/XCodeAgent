import { useRef, useState } from 'react'
import type { MutableRefObject, SetStateAction } from 'react'
import { AgUiChatSession } from '../../../service/agUiAgent'
import type { ProcessStepRecord, ToolCallRecord } from '../../../service/agUiAgent'
import { isAuthenticationFailure } from '../../../service/authentication'
import type {
  ApplicationConfig,
  ChatMessageSkill,
  EditorMode,
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
import { stoppedAnswer, workflowCodeChanges } from '../utils'
import type { PersistSessionInput } from './useChatSessions'
import type { SessionIdentity, SessionRunStatus } from './sessionRuntime'

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
  editorMode: EditorMode
  ensureActiveSession: () => Promise<SessionIdentity>
  getSessionMessages: (sessionKey: string) => AgentChatMessage[]
  persistSession: (input: PersistSessionInput) => Promise<void>
  publishAiMessage: (mode: EditorMode, content: string) => void
  runningSessionsRef: MutableRefObject<Map<string, SessionIdentity>>
  setDraftByKey: (sessionKey: string, value: string) => void
  setSelectedSkillsByKey: (sessionKey: string, value: ChatMessageSkill[]) => void
  setSessionMessages: (sessionKey: string, value: SetStateAction<AgentChatMessage[]>) => void
}

type UseWorkflowConversationResult = {
  activeWorkflow?: WorkflowRunPayload
  error?: string
  handleSend: (workflowDebug?: WorkflowDebugOptions) => Promise<void>
  handleStartDetailConfirmation: (
    selectedPageId: string,
    pageLabel: string,
    hasDetailPlan?: boolean
  ) => Promise<boolean>
  handleStopGenerating: () => void
  handleSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => Promise<void>
  loading: boolean
  sessionRunStates: Record<string, SessionRunStatus>
  stopping: boolean
  workspaceBusy: boolean
}

export function useWorkflowConversation({
  activeSession,
  agUiSessionsRef,
  application,
  draft,
  draftKey,
  selectedSkills,
  editorMode,
  ensureActiveSession,
  getSessionMessages,
  persistSession,
  publishAiMessage,
  runningSessionsRef,
  setDraftByKey,
  setSelectedSkillsByKey,
  setSessionMessages
}: UseWorkflowConversationParams): UseWorkflowConversationResult {
  const stopRequestedRef = useRef<Record<string, boolean>>({})
  const [runStates, setRunStates] = useState<Record<string, SessionRunEntry>>({})
  const [errors, setErrors] = useState<Record<string, string | undefined>>({})
  const [liveWorkflows, setLiveWorkflows] = useState<Record<string, WorkflowRunPayload>>({})

  // 首次从页面设计入口创建会话时，React 还未提交 activeSession；先按工作区接住刚启动的运行态，避免进度页闪退。
  const activeRun = activeSession
    ? runStates[activeSession.key]
    : Object.values(runStates).find(
        (entry) =>
          entry.identity.workspaceRoot === application.workspaceRoot &&
          entry.identity.editorMode === editorMode
      )
  const activeRuntimeKey = activeSession?.key || activeRun?.identity.key
  const loading = activeRun?.status === 'running' || activeRun?.status === 'stopping'
  const stopping = activeRun?.status === 'stopping'
  const error = activeRuntimeKey ? errors[activeRuntimeKey] : undefined
  const activeWorkflow = activeRuntimeKey
    ? activeRun
      ? liveWorkflows[activeRuntimeKey]
      : liveWorkflows[activeRuntimeKey] ?? latestWorkflow(getSessionMessages(activeRuntimeKey))
    : undefined
  const workspaceBusy = Object.values(runStates).some(
    (entry) =>
      entry.identity.workspaceRoot === application.workspaceRoot &&
      entry.identity.key !== activeSession?.key
  )
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

  const handleSend = async (workflowDebug?: WorkflowDebugOptions): Promise<void> => {
    const message = draft.trim() || workflowDebugMessage(workflowDebug)
    if (!message || loading || workspaceBusy) return
    await sendWorkflowMessage(message, {
      clearDraft: true,
      selectedSkills,
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
      selectedPageId?: string
    }
  ): Promise<boolean> => {
    const trimmedMessage = message.trim()
    if (!trimmedMessage) return false

    const identity = await ensureActiveSession()
    const competingSession = findRunningSession(
      runningSessionsRef.current,
      identity.workspaceRoot,
      identity.key
    )
    if (competingSession || runningSessionsRef.current.has(identity.key)) {
      setErrors((current) => ({
        ...current,
        [identity.key]: competingSession ? '工作区中另一个会话正在执行。' : '当前会话正在执行。'
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
    setLiveWorkflows((current) => omitKey(current, identity.key))
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

    try {
      await persistSession({
        editorMode: identity.editorMode,
        messages: nextMessages,
        sessionId: identity.sessionId,
        threadId: identity.threadId,
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
        selectedSkillNames: selectedSkillNames(options?.selectedSkills),
        selectedPageId: options?.selectedPageId,
        workflowDebug: options?.workflowDebug,
        resumeState: options?.resumeState,
        onContent: (content) => {
          streamedContent = content
          updateAssistantMessage(content, streamedWorkflow, streamedToolCalls)
        },
        onWorkflow: (nextWorkflow) => {
          streamedWorkflow = nextWorkflow
          setLiveWorkflows((current) => ({ ...current, [identity.key]: nextWorkflow }))
          updateAssistantMessage(streamedContent, nextWorkflow, streamedToolCalls)
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
      const completedMessages = updateAssistantMessage(
        answer || 'Workflow 已返回，但内容为空。',
        workflow ?? streamedWorkflow,
        rawToolCalls.length > 0 ? rawToolCalls : streamedToolCalls,
        streamedProcessSteps
      )
      const finalWorkflow = workflow ?? streamedWorkflow
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
        titleFrom: options?.titleFrom || trimmedMessage
      })
      publishAiMessage(identity.editorMode, answer)
      return true
    } catch (caughtError) {
      if (isAuthenticationFailure(caughtError)) {
        setSessionMessages(identity.key, previousMessages)
        if (options?.clearDraft) setDraftByKey(identity.key, trimmedMessage)
        if (options?.clearDraft) {
          setSelectedSkillsByKey(
            identity.key,
            rollbackSkillSelection(options.selectedSkills)
          )
        }
        await persistSession({
          editorMode: identity.editorMode,
          messages: previousMessages,
          sessionId: identity.sessionId,
          threadId: identity.threadId
        })
        return false
      }
      if (stopRequestedRef.current[identity.key] || isAbortedStreamError(caughtError)) {
        const answer = stoppedAnswer(streamedContent)
        const completedMessages = updateAssistantMessage(
          answer,
          streamedWorkflow,
          streamedToolCalls,
          streamedProcessSteps
        )
        await persistSession({
          editorMode: identity.editorMode,
          messages: completedMessages,
          sessionId: identity.sessionId,
          threadId: identity.threadId,
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

  const handleSubmitClarification = async (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ): Promise<void> => {
    const continuationMessage = buildClarificationContinuationMessage(workflow, answers)
    if (!continuationMessage || loading || workspaceBusy) return
    const originalRequest = workflowOriginalRequest(workflow)
    await sendWorkflowMessage(continuationMessage, {
      clarificationAnswers: answers,
      originalRequest,
      resumeState: workflow,
      titleFrom: originalRequest || '补充需求确认'
    })
  }

  /** 以用户选择的 RequirementSpec 页面作为主 Workflow 细节设计起点。 */
  const handleStartDetailConfirmation = async (
    selectedPageId: string,
    pageLabel: string,
    hasDetailPlan?: boolean
  ): Promise<boolean> => {
    if (!selectedPageId || loading || workspaceBusy) return false
    return sendWorkflowMessage(
      `${hasDetailPlan ? '查看已生成页面计划' : '开始设计页面'}：${pageLabel}`,
      {
        selectedPageId,
        titleFrom: `${hasDetailPlan ? '确认页面' : '设计页面'}：${pageLabel}`
      }
    )
  }

  const handleStopGenerating = (): void => {
    if (!activeSession || !loading || stopping) return
    const agUiSession = agUiSessionsRef.current[activeSession.key]
    if (!agUiSession) return

    stopRequestedRef.current[activeSession.key] = true
    setRunStates((current) => ({
      ...current,
      [activeSession.key]: { identity: activeSession, status: 'stopping' }
    }))
    agUiSession.stop()
  }

  return {
    activeWorkflow,
    error,
    handleSend,
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

function findRunningSession(
  sessions: Map<string, SessionIdentity>,
  workspaceRoot: string,
  excludedKey: string
): SessionIdentity | undefined {
  return Array.from(sessions.values()).find(
    (identity) => identity.workspaceRoot === workspaceRoot && identity.key !== excludedKey
  )
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
