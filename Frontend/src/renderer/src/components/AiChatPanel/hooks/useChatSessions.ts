import { message as antdMessage } from 'antd'
import type { KeyboardEvent, MutableRefObject, SetStateAction } from 'react'
import { useEffect, useRef, useState } from 'react'
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
import {
  createSessionIdentity,
  pendingDraftKey,
  sessionIdentityFromSummary,
  sessionRuntimeKey,
  type SessionIdentity
} from './sessionRuntime'
import { useSessionRuntimeStore } from './useSessionRuntimeStore'

export type PersistSessionInput = {
  editorMode: EditorMode
  messages: ChatSessionMessage[]
  sessionId: string
  threadId: string
  titleFrom?: string
}

type UseChatSessionsParams = {
  application: ApplicationConfig
  editorMode: EditorMode
  onCloseRightPanel: () => void
  runningSessionsRef: MutableRefObject<Map<string, SessionIdentity>>
}

type UseChatSessionsResult = {
  activeSession?: SessionIdentity
  activeSessionId?: string
  agUiSessionsRef: MutableRefObject<Record<string, AgUiChatSession>>
  deletingSessionId?: string
  draft: string
  draftKey: string
  ensureActiveSession: () => Promise<SessionIdentity>
  getSessionMessages: (sessionKey: string) => AgentChatMessage[]
  handleCreateSessionFromList: () => void
  handleCreateSessionKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void
  handleDeleteSession: (sessionId: string) => Promise<void>
  handleOpenSession: (sessionId: string) => Promise<void>
  handleOpenSessionKeyDown: (event: KeyboardEvent<HTMLDivElement>, sessionId: string) => void
  loadingSessions: boolean
  messages: AgentChatMessage[]
  persistSession: (input: PersistSessionInput) => Promise<void>
  sessionError?: string
  sessions: ChatSessionSummary[]
  setDraftByKey: (sessionKey: string, value: string) => void
  setSessionMessages: (sessionKey: string, value: SetStateAction<AgentChatMessage[]>) => void
}

export function useChatSessions({
  application,
  editorMode,
  onCloseRightPanel,
  runningSessionsRef
}: UseChatSessionsParams): UseChatSessionsResult {
  const [sessionSummaries, setSessionSummaries] = useState<
    Record<EditorMode, ChatSessionSummary[]>
  >({ frontend: [], backend: [] })
  const [activeSessionIds, setActiveSessionIds] = useState<Partial<Record<EditorMode, string>>>({})
  const [sessionLoadingModes, setSessionLoadingModes] = useState<
    Partial<Record<EditorMode, boolean>>
  >({})
  const [sessionErrors, setSessionErrors] = useState<Partial<Record<EditorMode, string>>>({})
  const [deletingSessionIds, setDeletingSessionIds] = useState<Partial<Record<EditorMode, string>>>(
    {}
  )
  const sessionSummariesRef = useRef(sessionSummaries)
  const {
    agUiSessionsRef,
    draftForKey,
    ensureAgent,
    getIdentity,
    getSessionMessages,
    messagesForKey,
    registerSession,
    removeSession,
    setDraftByKey,
    setSessionMessages
  } = useSessionRuntimeStore()

  useEffect(() => {
    sessionSummariesRef.current = sessionSummaries
  }, [sessionSummaries])

  useEffect(() => {
    loadSessionsForMode(editorMode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application.workspaceRoot, editorMode])

  const workspaceRoot = application.workspaceRoot || ''
  const sessions = sessionSummaries[editorMode]
  const activeSessionId = activeSessionIds[editorMode]
  const activeKey = activeSessionId
    ? sessionRuntimeKey(workspaceRoot, editorMode, activeSessionId)
    : undefined
  const activeSession = activeKey
    ? getIdentity(activeKey) ||
      sessionIdentityFromSummary(
        sessions.find((session) => session.id === activeSessionId),
        editorMode,
        workspaceRoot
      )
    : undefined
  const draftKey = activeSession?.key || pendingDraftKey(workspaceRoot, editorMode)
  const draft = draftForKey(draftKey)
  const messages = activeSession ? messagesForKey(activeSession.key) : []
  const loadingSessions = Boolean(sessionLoadingModes[editorMode])
  const deletingSessionId = deletingSessionIds[editorMode]
  const sessionError = sessionErrors[editorMode]

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
      setSessionSummaries((current) => ({ ...current, [mode]: [] }))
      setActiveSessionIds((current) => ({ ...current, [mode]: undefined }))
      return
    }

    setSessionLoadingModes((current) => ({ ...current, [mode]: true }))
    setSessionErrors((current) => ({ ...current, [mode]: undefined }))
    try {
      const nextSessions = await listChatSessions(application.workspaceRoot, mode)
      setSessionSummaries((current) => ({ ...current, [mode]: nextSessions }))
      if (nextSessions.length === 0) {
        setActiveSessionIds((current) => ({ ...current, [mode]: undefined }))
        return
      }
      await openChatSession(mode, nextSessions[0].id)
    } catch (caughtError) {
      setSessionErrors((current) => ({
        ...current,
        [mode]: caughtError instanceof Error ? caughtError.message : '读取本地会话失败。'
      }))
    } finally {
      setSessionLoadingModes((current) => ({ ...current, [mode]: false }))
    }
  }

  const openChatSession = async (mode: EditorMode, sessionId: string): Promise<void> => {
    if (!application.workspaceRoot) return

    const key = sessionRuntimeKey(application.workspaceRoot, mode, sessionId)
    if (!agUiSessionsRef.current[key]) {
      const session = await readChatSession(application.workspaceRoot, mode, sessionId)
      const identity = createSessionIdentity({
        workspaceRoot: application.workspaceRoot,
        editorMode: mode,
        sessionId: session.id,
        threadId: session.threadId
      })
      registerSession(identity, session.messages)
    }

    setActiveSessionIds((current) => ({ ...current, [mode]: sessionId }))
    onCloseRightPanel()
  }

  const handleOpenSession = async (sessionId: string): Promise<void> => {
    if (sessionId === activeSessionId || loadingSessions) return
    setSessionLoadingModes((current) => ({ ...current, [editorMode]: true }))
    setSessionErrors((current) => ({ ...current, [editorMode]: undefined }))
    try {
      await openChatSession(editorMode, sessionId)
    } catch (caughtError) {
      setSessionErrors((current) => ({
        ...current,
        [editorMode]: caughtError instanceof Error ? caughtError.message : '打开本地会话失败。'
      }))
    } finally {
      setSessionLoadingModes((current) => ({ ...current, [editorMode]: false }))
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

  const createNewSession = async (): Promise<SessionIdentity> => {
    if (!application.workspaceRoot) {
      throw new Error('创建会话前需要选择工作目录。')
    }

    const now = Date.now()
    const sessionId = createChatSessionId()
    const agUiSession = new AgUiChatSession()
    const identity = createSessionIdentity({
      workspaceRoot: application.workspaceRoot,
      editorMode,
      sessionId,
      threadId: agUiSession.threadId
    })
    const session: ChatSessionRecord = {
      id: sessionId,
      title: '新对话',
      editorMode,
      threadId: identity.threadId,
      workspaceRoot: application.workspaceRoot,
      messages: [],
      createdAt: now,
      updatedAt: now
    }

    registerSession(identity, [], agUiSession)
    setDraftByKey(identity.key, '')
    setActiveSessionIds((current) => ({ ...current, [editorMode]: session.id }))
    onCloseRightPanel()

    const summary = await saveChatSession(session)
    replaceSessionSummary(editorMode, summary)
    return identity
  }

  const ensureActiveSession = async (): Promise<SessionIdentity> => {
    if (activeSession) {
      ensureAgent(activeSession)
      return activeSession
    }
    return createNewSession()
  }

  const handleCreateSessionFromList = (): void => {
    if (!application.workspaceRoot) return
    createNewSession().catch(reportSessionError)
  }

  const handleCreateSessionKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (!application.workspaceRoot || (event.key !== 'Enter' && event.key !== ' ')) return
    event.preventDefault()
    createNewSession().catch(reportSessionError)
  }

  const reportSessionError = (caughtError: unknown): void => {
    setSessionErrors((current) => ({
      ...current,
      [editorMode]: caughtError instanceof Error ? caughtError.message : '创建本地会话失败。'
    }))
  }

  const handleDeleteSession = async (sessionId: string): Promise<void> => {
    if (!application.workspaceRoot || deletingSessionId) return
    const key = sessionRuntimeKey(application.workspaceRoot, editorMode, sessionId)
    if (runningSessionsRef.current.has(key)) return

    const nextSession = sessions.find((session) => session.id !== sessionId)
    setDeletingSessionIds((current) => ({ ...current, [editorMode]: sessionId }))
    setSessionErrors((current) => ({ ...current, [editorMode]: undefined }))

    try {
      await deleteChatSession(application.workspaceRoot, editorMode, sessionId)
      setSessionSummaries((current) => ({
        ...current,
        [editorMode]: current[editorMode].filter((session) => session.id !== sessionId)
      }))
      removeSession(key)

      if (activeSessionId === sessionId) {
        if (nextSession) {
          await openChatSession(editorMode, nextSession.id)
        } else {
          setActiveSessionIds((current) => ({ ...current, [editorMode]: undefined }))
        }
      }

      antdMessage.success('已删除会话')
    } catch (caughtError) {
      setSessionErrors((current) => ({
        ...current,
        [editorMode]: caughtError instanceof Error ? caughtError.message : '删除本地会话失败。'
      }))
    } finally {
      setDeletingSessionIds((current) => ({ ...current, [editorMode]: undefined }))
    }
  }

  const persistSession = async (input: PersistSessionInput): Promise<void> => {
    if (!application.workspaceRoot) return
    const existingSummary = sessionSummariesRef.current[input.editorMode].find(
      (summary) => summary.id === input.sessionId
    )
    const now = Date.now()
    const session: ChatSessionRecord = {
      id: input.sessionId,
      title:
        input.titleFrom && (!existingSummary || existingSummary.title === '新对话')
          ? createChatSessionTitle(input.titleFrom)
          : existingSummary?.title || '新对话',
      editorMode: input.editorMode,
      threadId: input.threadId,
      workspaceRoot: application.workspaceRoot,
      messages: input.messages,
      createdAt: existingSummary?.createdAt || now,
      updatedAt: now
    }
    const summary = await saveChatSession(session)
    replaceSessionSummary(input.editorMode, summary)
  }

  return {
    activeSession,
    activeSessionId,
    agUiSessionsRef,
    deletingSessionId,
    draft,
    draftKey,
    ensureActiveSession,
    getSessionMessages,
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
    setDraftByKey,
    setSessionMessages
  }
}
