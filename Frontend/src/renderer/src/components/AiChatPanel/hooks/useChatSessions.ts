import type { Dispatch, KeyboardEvent, MutableRefObject, SetStateAction } from 'react'
import { useEffect, useRef, useState } from 'react'
import { message as antdMessage } from 'antd'
import { AgUiChatSession } from '../../../service/agUiAgent'
import {
  createChatSessionId,
  createChatSessionTitle,
  deleteChatSession,
  listChatSessions,
  readChatSession,
  saveChatSession,
  type ChatSessionMessage,
  type ChatSessionRecord,
  type ChatSessionSummary
} from '../../../service/chatSessions'
import type { ApplicationConfig, EditorMode } from '../../../typings'
import type { AgentChatMessage } from '../types'

type UseChatSessionsParams = {
  application: ApplicationConfig
  editorMode: EditorMode
  loadingRef: MutableRefObject<boolean>
  onCloseRightPanel: () => void
}

type UseChatSessionsResult = {
  activeSessionId?: string
  agUiSessionsRef: MutableRefObject<Partial<Record<EditorMode, AgUiChatSession>>>
  deletingSessionId?: string
  draft: string
  handleCreateSessionFromList: () => void
  handleCreateSessionKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void
  handleDeleteSession: (sessionId: string) => Promise<void>
  handleOpenSession: (sessionId: string) => Promise<void>
  handleOpenSessionKeyDown: (event: KeyboardEvent<HTMLDivElement>, sessionId: string) => void
  loadingSessions: boolean
  messages: AgentChatMessage[]
  persistSession: (
    mode: EditorMode,
    nextMessages: ChatSessionMessage[],
    options?: { titleFrom?: string; sessionId?: string; threadId?: string }
  ) => Promise<void>
  sessionError?: string
  sessions: ChatSessionSummary[]
  setAgentMessages: Dispatch<SetStateAction<Record<EditorMode, AgentChatMessage[]>>>
  setDraftForMode: (mode: EditorMode, value: string) => void
}

export function useChatSessions({
  application,
  editorMode,
  loadingRef,
  onCloseRightPanel
}: UseChatSessionsParams): UseChatSessionsResult {
  const [drafts, setDrafts] = useState<Record<EditorMode, string>>({
    frontend: '',
    backend: ''
  })
  const [agentMessages, setAgentMessages] = useState<Record<EditorMode, AgentChatMessage[]>>({
    frontend: [],
    backend: []
  })
  const [sessionSummaries, setSessionSummaries] = useState<
    Record<EditorMode, ChatSessionSummary[]>
  >({
    frontend: [],
    backend: []
  })
  const [activeSessionIds, setActiveSessionIds] = useState<Partial<Record<EditorMode, string>>>({})
  const [sessionLoadingModes, setSessionLoadingModes] = useState<
    Partial<Record<EditorMode, boolean>>
  >({})
  const [sessionErrors, setSessionErrors] = useState<Partial<Record<EditorMode, string>>>({})
  const [deletingSessionIds, setDeletingSessionIds] = useState<Partial<Record<EditorMode, string>>>(
    {}
  )
  const agUiSessionsRef = useRef<Partial<Record<EditorMode, AgUiChatSession>>>({})

  const messages = agentMessages[editorMode]
  const sessions = sessionSummaries[editorMode]
  const activeSessionId = activeSessionIds[editorMode]
  const draft = drafts[editorMode]
  const loadingSessions = Boolean(sessionLoadingModes[editorMode])
  const deletingSessionId = deletingSessionIds[editorMode]
  const sessionError = sessionErrors[editorMode]

  useEffect(() => {
    loadSessionsForMode(editorMode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application.workspaceRoot, editorMode])

  const setDraftForMode = (mode: EditorMode, value: string): void => {
    setDrafts((currentDrafts) => ({ ...currentDrafts, [mode]: value }))
  }

  const replaceSessionSummary = (mode: EditorMode, summary: ChatSessionSummary): void => {
    setSessionSummaries((currentSummaries) => ({
      ...currentSummaries,
      [mode]: [summary, ...currentSummaries[mode].filter((item) => item.id !== summary.id)].sort(
        (a, b) => b.updatedAt - a.updatedAt
      )
    }))
  }

  const loadSessionsForMode = async (mode: EditorMode): Promise<void> => {
    if (!application.workspaceRoot) {
      setSessionSummaries((currentSummaries) => ({ ...currentSummaries, [mode]: [] }))
      setAgentMessages((currentMessages) => ({ ...currentMessages, [mode]: [] }))
      setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: undefined }))
      return
    }

    setSessionLoadingModes((currentLoadingModes) => ({ ...currentLoadingModes, [mode]: true }))
    setSessionErrors((currentErrors) => ({ ...currentErrors, [mode]: undefined }))
    try {
      const nextSessions = await listChatSessions(application.workspaceRoot, mode)
      setSessionSummaries((currentSummaries) => ({ ...currentSummaries, [mode]: nextSessions }))
      if (nextSessions.length === 0) {
        setAgentMessages((currentMessages) => ({ ...currentMessages, [mode]: [] }))
        setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: undefined }))
        agUiSessionsRef.current[mode] = undefined
        return
      }
      await openChatSession(mode, nextSessions[0].id)
    } catch (caughtError) {
      setSessionErrors((currentErrors) => ({
        ...currentErrors,
        [mode]: caughtError instanceof Error ? caughtError.message : '读取本地会话失败。'
      }))
    } finally {
      setSessionLoadingModes((currentLoadingModes) => ({ ...currentLoadingModes, [mode]: false }))
    }
  }

  const openChatSession = async (mode: EditorMode, sessionId: string): Promise<void> => {
    if (!application.workspaceRoot) return

    const session = await readChatSession(application.workspaceRoot, mode, sessionId)
    setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: session.id }))
    setAgentMessages((currentMessages) => ({ ...currentMessages, [mode]: session.messages }))
    setDraftForMode(mode, '')
    onCloseRightPanel()
    agUiSessionsRef.current[mode] = new AgUiChatSession(session.threadId)
  }

  const handleOpenSession = async (sessionId: string): Promise<void> => {
    if (sessionId === activeSessionId || loadingSessions) return
    setSessionLoadingModes((currentLoadingModes) => ({
      ...currentLoadingModes,
      [editorMode]: true
    }))
    setSessionErrors((currentErrors) => ({ ...currentErrors, [editorMode]: undefined }))
    try {
      await openChatSession(editorMode, sessionId)
    } catch (caughtError) {
      setSessionErrors((currentErrors) => ({
        ...currentErrors,
        [editorMode]: caughtError instanceof Error ? caughtError.message : '打开本地会话失败。'
      }))
    } finally {
      setSessionLoadingModes((currentLoadingModes) => ({
        ...currentLoadingModes,
        [editorMode]: false
      }))
    }
  }

  const handleOpenSessionKeyDown = (
    event: KeyboardEvent<HTMLDivElement>,
    sessionId: string
  ): void => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    handleOpenSession(sessionId)
  }

  const createNewSession = async (): Promise<void> => {
    const agUiSession = new AgUiChatSession()
    agUiSessionsRef.current[editorMode] = agUiSession
    setAgentMessages((currentMessages) => ({ ...currentMessages, [editorMode]: [] }))
    setDraftForMode(editorMode, '')
    onCloseRightPanel()

    if (!application.workspaceRoot) {
      setActiveSessionIds((currentSessionIds) => ({
        ...currentSessionIds,
        [editorMode]: undefined
      }))
      return
    }

    const now = Date.now()
    const session: ChatSessionRecord = {
      id: createChatSessionId(),
      title: '新对话',
      editorMode,
      threadId: agUiSession.threadId,
      workspaceRoot: application.workspaceRoot,
      messages: [],
      createdAt: now,
      updatedAt: now
    }
    setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [editorMode]: session.id }))
    try {
      const summary = await saveChatSession(session)
      replaceSessionSummary(editorMode, summary)
    } catch (caughtError) {
      setSessionErrors((currentErrors) => ({
        ...currentErrors,
        [editorMode]: caughtError instanceof Error ? caughtError.message : '创建本地会话失败。'
      }))
    }
  }

  const handleCreateSessionFromList = (): void => {
    if (!application.workspaceRoot) return
    createNewSession()
  }

  const handleCreateSessionKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (!application.workspaceRoot || (event.key !== 'Enter' && event.key !== ' ')) return
    event.preventDefault()
    createNewSession()
  }

  const handleDeleteSession = async (sessionId: string): Promise<void> => {
    if (
      !application.workspaceRoot ||
      deletingSessionId ||
      (loadingRef.current && activeSessionId === sessionId)
    )
      return

    const nextSession = sessions.find((session) => session.id !== sessionId)
    setDeletingSessionIds((currentDeletingIds) => ({
      ...currentDeletingIds,
      [editorMode]: sessionId
    }))
    setSessionErrors((currentErrors) => ({ ...currentErrors, [editorMode]: undefined }))

    try {
      await deleteChatSession(application.workspaceRoot, editorMode, sessionId)
      setSessionSummaries((currentSummaries) => ({
        ...currentSummaries,
        [editorMode]: currentSummaries[editorMode].filter((session) => session.id !== sessionId)
      }))

      if (activeSessionId === sessionId) {
        if (nextSession) {
          await openChatSession(editorMode, nextSession.id)
        } else {
          setAgentMessages((currentMessages) => ({ ...currentMessages, [editorMode]: [] }))
          setActiveSessionIds((currentSessionIds) => ({
            ...currentSessionIds,
            [editorMode]: undefined
          }))
          setDraftForMode(editorMode, '')
          agUiSessionsRef.current[editorMode] = undefined
        }
      }

      antdMessage.success('已删除会话')
    } catch (caughtError) {
      setSessionErrors((currentErrors) => ({
        ...currentErrors,
        [editorMode]: caughtError instanceof Error ? caughtError.message : '删除本地会话失败。'
      }))
    } finally {
      setDeletingSessionIds((currentDeletingIds) => ({
        ...currentDeletingIds,
        [editorMode]: undefined
      }))
    }
  }

  const persistSession = async (
    mode: EditorMode,
    nextMessages: ChatSessionMessage[],
    options?: { titleFrom?: string; sessionId?: string; threadId?: string }
  ): Promise<void> => {
    if (!application.workspaceRoot) return
    const existingSummary = sessionSummaries[mode].find(
      (summary) => summary.id === (options?.sessionId || activeSessionIds[mode])
    )
    const now = Date.now()
    const session: ChatSessionRecord = {
      id: options?.sessionId || existingSummary?.id || createChatSessionId(),
      title:
        options?.titleFrom && (!existingSummary || existingSummary.title === '新对话')
          ? createChatSessionTitle(options.titleFrom)
          : existingSummary?.title || '新对话',
      editorMode: mode,
      threadId:
        options?.threadId ||
        existingSummary?.threadId ||
        agUiSessionsRef.current[mode]?.threadId ||
        createChatSessionId(),
      workspaceRoot: application.workspaceRoot,
      messages: nextMessages,
      createdAt: existingSummary?.createdAt || now,
      updatedAt: now
    }
    setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: session.id }))
    const summary = await saveChatSession(session)
    replaceSessionSummary(mode, summary)
  }

  return {
    activeSessionId,
    agUiSessionsRef,
    deletingSessionId,
    draft,
    handleCreateSessionFromList,
    handleCreateSessionKeyDown,
    handleDeleteSession,
    handleOpenSession,
    handleOpenSessionKeyDown,
    loadingSessions,
    messages,
    persistSession,
    sessionError,
    sessions,
    setAgentMessages,
    setDraftForMode
  }
}
