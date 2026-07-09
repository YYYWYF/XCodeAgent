import { useRef, useState } from 'react'
import type { Dispatch, MutableRefObject, SetStateAction } from 'react'
import { AgUiChatSession } from '../../../service/agUiAgent'
import type { ToolCallRecord } from '../../../service/agUiAgent'
import { createChatSessionId, type ChatSessionMessage } from '../../../service/chatSessions'
import type {
  ApplicationConfig,
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
import { stoppedAnswer, workflowCodeChanges } from '../utils'

type UseWorkflowConversationParams = {
  activeSessionId?: string
  agUiSessionsRef: MutableRefObject<Partial<Record<EditorMode, AgUiChatSession>>>
  application: ApplicationConfig
  draft: string
  editorMode: EditorMode
  messages: AgentChatMessage[]
  persistSession: (
    mode: EditorMode,
    nextMessages: ChatSessionMessage[],
    options?: { titleFrom?: string; sessionId?: string; threadId?: string }
  ) => Promise<void>
  publishAiMessage: (mode: EditorMode, content: string) => void
  setAgentMessages: Dispatch<SetStateAction<Record<EditorMode, AgentChatMessage[]>>>
  setDraftForMode: (mode: EditorMode, value: string) => void
}

type UseWorkflowConversationResult = {
  error?: string
  handleSend: (workflowDebug?: WorkflowDebugOptions) => Promise<void>
  handleStopGenerating: () => void
  handleSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => Promise<void>
  loading: boolean
  stopping: boolean
}

export function useWorkflowConversation({
  activeSessionId,
  agUiSessionsRef,
  application,
  draft,
  editorMode,
  messages,
  persistSession,
  publishAiMessage,
  setAgentMessages,
  setDraftForMode
}: UseWorkflowConversationParams): UseWorkflowConversationResult {
  const stopRequestedModesRef = useRef<Partial<Record<EditorMode, boolean>>>({})
  const [loadingModes, setLoadingModes] = useState<Partial<Record<EditorMode, boolean>>>({})
  const [stoppingModes, setStoppingModes] = useState<Partial<Record<EditorMode, boolean>>>({})
  const [errors, setErrors] = useState<Partial<Record<EditorMode, string>>>({})
  const loading = Boolean(loadingModes[editorMode])
  const stopping = Boolean(stoppingModes[editorMode])
  const error = errors[editorMode]

  const handleSend = async (workflowDebug?: WorkflowDebugOptions): Promise<void> => {
    const message = draft.trim() || workflowDebugMessage(workflowDebug)
    if (!message || loading) return
    await sendWorkflowMessage(message, { clearDraft: true, titleFrom: message, workflowDebug })
  }

  const sendWorkflowMessage = async (
    message: string,
    options?: {
      clearDraft?: boolean
      resumeState?: WorkflowRunPayload
      titleFrom?: string
      workflowDebug?: WorkflowDebugOptions
    }
  ): Promise<void> => {
    const trimmedMessage = message.trim()
    if (!trimmedMessage || loading) return

    const userMessage: AgentChatMessage = {
      id: Date.now(),
      role: 'user',
      content: trimmedMessage,
      createdAt: Date.now()
    }
    const agUiSession =
      agUiSessionsRef.current[editorMode] ??
      (agUiSessionsRef.current[editorMode] = new AgUiChatSession())
    const sessionId = activeSessionId || createChatSessionId()
    const assistantMessageId = Date.now() + 1
    const assistantMessage: AgentChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      createdAt: Date.now()
    }
    const nextMessages = [...messages, userMessage, assistantMessage]

    setAgentMessages((currentMessages) => ({
      ...currentMessages,
      [editorMode]: nextMessages
    }))
    if (options?.clearDraft) {
      setDraftForMode(editorMode, '')
    }
    setErrors((currentErrors) => ({ ...currentErrors, [editorMode]: undefined }))
    setLoadingModes((currentLoadingModes) => ({ ...currentLoadingModes, [editorMode]: true }))
    setStoppingModes((currentStoppingModes) => ({ ...currentStoppingModes, [editorMode]: false }))
    stopRequestedModesRef.current[editorMode] = false

    let streamedContent = ''
    let streamedWorkflow: WorkflowRunPayload | undefined
    let streamedToolCalls: ToolCallRecord[] = []
    let latestMessages = nextMessages
    const updateAssistantMessage = (
      content: string,
      workflow?: WorkflowRunPayload,
      toolCalls?: ToolCallRecord[]
    ): AgentChatMessage[] => {
      const nextCodeChanges = workflowCodeChanges(workflow)
      latestMessages = latestMessages.map((currentMessage) =>
        currentMessage.id === assistantMessageId
          ? {
              ...currentMessage,
              content,
              workflow: workflow ?? currentMessage.workflow,
              codeChanges: nextCodeChanges ?? currentMessage.codeChanges,
              toolCalls: toolCalls ?? currentMessage.toolCalls
            }
          : currentMessage
      )
      setAgentMessages((currentMessages) => {
        const updatedMessages = currentMessages[editorMode].map((currentMessage) =>
          currentMessage.id === assistantMessageId
            ? {
                ...currentMessage,
                content,
                workflow: workflow ?? currentMessage.workflow,
                codeChanges: nextCodeChanges ?? currentMessage.codeChanges,
                toolCalls: toolCalls ?? currentMessage.toolCalls
              }
            : currentMessage
        )
        return {
          ...currentMessages,
          [editorMode]: updatedMessages
        }
      })
      return latestMessages
    }

    try {
      await persistSession(editorMode, nextMessages, {
        sessionId,
        threadId: agUiSession.threadId,
        titleFrom: options?.titleFrom || trimmedMessage
      })
      const {
        answer: rawAnswer,
        workflow,
        toolCalls: rawToolCalls
      } = await agUiSession.sendMessage(trimmedMessage, {
        workspaceRoot: application.workspaceRoot,
        application,
        workflowDebug: options?.workflowDebug,
        resumeState: options?.resumeState,
        onContent: (content) => {
          streamedContent = content
          updateAssistantMessage(content, streamedWorkflow, streamedToolCalls)
        },
        onWorkflow: (nextWorkflow) => {
          streamedWorkflow = nextWorkflow
          updateAssistantMessage(streamedContent, nextWorkflow, streamedToolCalls)
        },
        onToolCalls: (nextToolCalls) => {
          streamedToolCalls = nextToolCalls
          updateAssistantMessage(streamedContent, streamedWorkflow, nextToolCalls)
        }
      })
      const stopped = Boolean(stopRequestedModesRef.current[editorMode])
      const answer = stopped ? stoppedAnswer(streamedContent || rawAnswer) : rawAnswer.trim()
      const completedMessages = updateAssistantMessage(
        answer || 'Workflow 已返回，但内容为空。',
        workflow ?? streamedWorkflow,
        rawToolCalls.length > 0 ? rawToolCalls : streamedToolCalls
      )

      await persistSession(editorMode, completedMessages, {
        sessionId,
        threadId: agUiSession.threadId,
        titleFrom: options?.titleFrom || trimmedMessage
      })
      publishAiMessage(editorMode, answer)
    } catch (caughtError) {
      if (stopRequestedModesRef.current[editorMode]) {
        const answer = stoppedAnswer(streamedContent)
        const completedMessages = updateAssistantMessage(
          answer,
          streamedWorkflow,
          streamedToolCalls
        )
        await persistSession(editorMode, completedMessages, {
          sessionId,
          threadId: agUiSession.threadId,
          titleFrom: message
        })
        publishAiMessage(editorMode, answer)
        return
      }
      const errorMessage =
        caughtError instanceof Error ? caughtError.message : '调用 Workflow 失败。'
      setErrors((currentErrors) => ({ ...currentErrors, [editorMode]: errorMessage }))
    } finally {
      setLoadingModes((currentLoadingModes) => ({
        ...currentLoadingModes,
        [editorMode]: false
      }))
      setStoppingModes((currentStoppingModes) => ({
        ...currentStoppingModes,
        [editorMode]: false
      }))
      stopRequestedModesRef.current[editorMode] = false
    }
  }

  const handleSubmitClarification = async (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ): Promise<void> => {
    const continuationMessage = buildClarificationContinuationMessage(workflow, answers)
    if (!continuationMessage) return
    await sendWorkflowMessage(continuationMessage, {
      resumeState: workflow,
      titleFrom: workflowOriginalRequest(workflow) || '补充需求确认'
    })
  }

  const handleStopGenerating = (): void => {
    const agUiSession = agUiSessionsRef.current[editorMode]
    if (!loading || !agUiSession || stopping) return

    stopRequestedModesRef.current[editorMode] = true
    setStoppingModes((currentStoppingModes) => ({ ...currentStoppingModes, [editorMode]: true }))
    agUiSession.stop()
  }

  return {
    error,
    handleSend,
    handleStopGenerating,
    handleSubmitClarification,
    loading,
    stopping
  }
}

function workflowDebugMessage(workflowDebug?: WorkflowDebugOptions): string {
  if (!workflowDebug?.enabled || !workflowDebug.resumeFrom) return ''
  return `从 ${workflowDebug.resumeFrom} 节点继续执行 workflow 调试。`
}
