import { message as antdMessage } from 'antd'
import type { MutableRefObject, SetStateAction } from 'react'
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
import type { ApplicationConfig, ChatMessageSkill, EditorMode } from '../../../typings'
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
  apiContractId?: string
  endpointId?: string
  endpointLabel?: string
  pageId?: string
  titleFrom?: string
  materialize?: boolean
}

/** 从待保存消息中的 Workflow 快照推断 API endpoint 会话归属。 */
function inferEndpointContextFromMessages(messages: ChatSessionMessage[]): {
  apiContractId?: string
  endpointId?: string
  endpointLabel?: string
} {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const workflow = messages[index]?.workflow
    const state = workflow?.state || {}
    const result = workflow?.result || {}
    const reviewSummary = workflow?.summary.clarification?.review?.summary || {}
    const apiContractId = String(
      state.selectedApiContractId ||
        result.selectedApiContractId ||
        reviewSummary.selectedApiContractId ||
        ''
    ).trim()
    const endpointId = String(
      state.selectedEndpointId ||
        result.selectedEndpointId ||
        reviewSummary.selectedEndpointId ||
        ''
    ).trim()
    if (apiContractId && endpointId) return { apiContractId, endpointId }
  }
  return {}
}

type UseChatSessionsParams = {
  application: ApplicationConfig
  editorMode: EditorMode
}

export type RelatedEndpointContext = {
  apiContractId: string
  endpointId: string
  endpointLabel: string
}

type UseChatSessionsResult = {
  activeSession?: SessionIdentity
  activeSessionId?: string
  agUiSessionsRef: MutableRefObject<Record<string, AgUiChatSession>>
  deletingSessionId?: string
  draft: string
  draftKey: string
  createReviewSession: () => Promise<SessionIdentity>
  createEndpointSession: (
    apiContractId: string,
    endpointId: string,
    endpointLabel: string
  ) => Promise<SessionIdentity>
  createPageSession: (
    pageId: string,
    pageLabel: string,
    endpointContext?: RelatedEndpointContext
  ) => Promise<SessionIdentity>
  ensureActiveSession: () => Promise<SessionIdentity>
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
  ensurePlannedPageSession: (
    pageId: string,
    pageLabel: string,
    endpointContext?: RelatedEndpointContext
  ) => Promise<SessionIdentity>
  getSessionMessages: (sessionKey: string) => AgentChatMessage[]
  handleCreateSessionFromList: () => void
  handleDeleteSession: (sessionId: string) => Promise<void>
  handleOpenSession: (sessionId: string) => Promise<void>
  handleRenameSession: (sessionId: string, title: string) => Promise<void>
  handleSelectEndpoint: (apiContractId: string, endpointId: string) => Promise<void>
  handleSelectPage: (pageId: string) => Promise<void>
  loadingSessions: boolean
  messages: AgentChatMessage[]
  selectedSkills: ChatMessageSkill[]
  persistSession: (input: PersistSessionInput) => Promise<void>
  runningSessionsRef: MutableRefObject<Map<string, SessionIdentity>>
  sessionError?: string
  sessions: ChatSessionSummary[]
  setDraftByKey: (sessionKey: string, value: string) => void
  setSelectedSkillsByKey: (sessionKey: string, value: ChatMessageSkill[]) => void
  setSessionMessages: (sessionKey: string, value: SetStateAction<AgentChatMessage[]>) => void
}

/** 管理按版本隔离的对话目录、运行时草稿及本地持久化生命周期。 */
export function useChatSessions({
  application,
  editorMode
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
  const pageSessionPromisesRef = useRef<Record<string, Promise<SessionIdentity>>>({})
  const transientSessionIdsRef = useRef<Set<string>>(new Set())
  const {
    agUiSessionsRef,
    draftForKey,
    ensureAgent,
    getIdentity,
    getSessionMessages,
    messagesForKey,
    registerSession,
    removeSession,
    runningSessionsRef,
    selectedSkillsForKey,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  } = useSessionRuntimeStore()

  useEffect(() => {
    sessionSummariesRef.current = sessionSummaries
  }, [sessionSummaries])

  useEffect(() => {
    loadSessionsForMode(editorMode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application.currentVersionId, application.workspaceRoot, editorMode])

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
  const selectedSkills = selectedSkillsForKey(draftKey)
  const loadingSessions = Boolean(sessionLoadingModes[editorMode])
  const deletingSessionId = deletingSessionIds[editorMode]
  const sessionError = sessionErrors[editorMode]

  const replaceSessionSummary = (mode: EditorMode, summary: ChatSessionSummary): void => {
    const nextModeSummaries = [
      summary,
      ...sessionSummariesRef.current[mode].filter((item) => item.id !== summary.id)
    ].sort((a, b) => b.updatedAt - a.updatedAt)
    sessionSummariesRef.current = {
      ...sessionSummariesRef.current,
      [mode]: nextModeSummaries
    }
    setSessionSummaries((currentSummaries) => ({
      ...currentSummaries,
      [mode]: nextModeSummaries
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
      const allSessions = await listChatSessions(application.workspaceRoot, mode, application.id)
      // 新版本只恢复明确属于该版本的会话；无版本字段的旧会话仅供无版本应用兼容使用。
      const nextSessions = allSessions.filter((session) =>
        application.currentVersionId
          ? session.versionId === application.currentVersionId
          : !session.versionId
      )
      sessionSummariesRef.current = {
        ...sessionSummariesRef.current,
        [mode]: nextSessions
      }
      setSessionSummaries((current) => ({ ...current, [mode]: nextSessions }))
      if (nextSessions.length === 0) {
        setActiveSessionIds((current) => ({ ...current, [mode]: undefined }))
        return
      }
      // 优先恢复最近一条有内容的会话，避免空白“新对话”遮住已经落盘的页面设计记录。
      const sessionToOpen =
        nextSessions.find((session) => session.messageCount > 0) || nextSessions[0]
      await openChatSession(mode, sessionToOpen.id)
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
        threadId: session.threadId,
        apiContractId: session.apiContractId,
        endpointId: session.endpointId,
        endpointLabel: session.endpointLabel,
        pageId: session.pageId
      })
      registerSession(identity, session.messages)
    }

    setActiveSessionIds((current) => ({ ...current, [mode]: sessionId }))
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

  /** 切换页面时仅恢复已有且有消息的会话，空白页面等待首次发送后再创建会话。 */
  const handleSelectPage = async (pageId: string): Promise<void> => {
    const normalizedPageId = pageId.trim()
    if (!normalizedPageId || loadingSessions) return

    // 优先恢复该页面的有内容会话：静态 messageCount 在运行时消息写入后可能未刷新
    // （挡板注入/设计会话走 setSessionMessages 不重写 summary），回退到内存消息判断，
    // 避免切换到其它页面再回来时会话历史丢失。
    const pageSessions = sessionSummariesRef.current[editorMode].filter(
      (session) => session.pageId === normalizedPageId
    )
    const existingSession =
      pageSessions.find((session) => session.messageCount > 0) ||
      pageSessions.find((session) => {
        const key = sessionRuntimeKey(workspaceRoot, editorMode, session.id)
        return getSessionMessages(key).length > 0
      })
    if (existingSession) {
      await handleOpenSession(existingSession.id)
      return
    }

    setActiveSessionIds((current) => ({ ...current, [editorMode]: undefined }))
  }

  /** 切换接口时恢复已有会话(优先 messageCount>0，回退内存消息)，避免切回后历史丢失。 */
  const handleSelectEndpoint = async (apiContractId: string, endpointId: string): Promise<void> => {
    if (loadingSessions) return
    const normalizedApi = apiContractId.trim()
    const normalizedEp = endpointId.trim()
    if (!normalizedApi || !normalizedEp) return
    const epSessions = sessionSummariesRef.current[editorMode].filter(
      (session) => session.apiContractId === normalizedApi && session.endpointId === normalizedEp
    )
    // summary messageCount 可能滞后(persist 时机/竞态)，回退内存消息判断，
    // 确保切回开发后接口历史不丢（对齐 handleSelectPage 的回退策略）。
    const existingSession =
      epSessions.find((session) => session.messageCount > 0) ||
      epSessions.find((session) => {
        const runtimeKey = sessionRuntimeKey(workspaceRoot, editorMode, session.id)
        return getSessionMessages(runtimeKey).length > 0
      })
    if (existingSession) {
      await handleOpenSession(existingSession.id)
      return
    }

    setActiveSessionIds((current) => ({ ...current, [editorMode]: undefined }))
  }

  /** 创建普通会话或带页面归属的独立设计会话。 */
  /** 创建尚未进入目录的运行时草稿会话，等待 Agent 回复后再物化。 */
  const createNewSession = async (
    pageId?: string,
    pageLabel?: string,
    endpointContext?: RelatedEndpointContext,
    customTitle?: string,
    materializeImmediately = false
  ): Promise<SessionIdentity> => {
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
      threadId: agUiSession.threadId,
      apiContractId: endpointContext?.apiContractId,
      endpointId: endpointContext?.endpointId,
      endpointLabel: endpointContext?.endpointLabel,
      pageId
    })
    const session: ChatSessionRecord = {
      id: sessionId,
      title:
        customTitle ||
        (endpointContext
          ? `实现${endpointContext.endpointLabel}`
          : pageLabel
            ? `实现${pageLabel}`
            : '新对话'),
      editorMode,
      threadId: identity.threadId,
      apiContractId: identity.apiContractId,
      endpointId: identity.endpointId,
      endpointLabel: identity.endpointLabel,
      pageId: identity.pageId,
      versionId: application.currentVersionId,
      workspaceRoot: application.workspaceRoot,
      messages: [],
      createdAt: now,
      updatedAt: now
    }

    registerSession(identity, [], agUiSession)
    setDraftByKey(identity.key, '')
    setActiveSessionIds((current) => ({ ...current, [editorMode]: session.id }))

    if (materializeImmediately) {
      // 计划指定的首个正式开发对话需要预先进入目录，但仍要等用户发送后才推进产物状态。
      const summary = await saveChatSession(session)
      replaceSessionSummary(editorMode, summary)
    } else {
      // 普通新建入口先创建仅存在于运行时的草稿会话；收到 Agent 回复后才写入目录。
      transientSessionIdsRef.current.add(session.id)
    }
    return identity
  }

  const ensureActiveSession = async (): Promise<SessionIdentity> => {
    if (activeSession) {
      ensureAgent(activeSession)
      return activeSession
    }
    return createNewSession()
  }

  /** 为指定页面显式创建会话，并可一并登记该页面任务负责的依赖接口。 */
  const createPageSession = async (
    pageId: string,
    pageLabel: string,
    endpointContext?: RelatedEndpointContext
  ): Promise<SessionIdentity> => {
    const normalizedPageId = pageId.trim()
    if (!normalizedPageId) throw new Error('页面标识不能为空。')
    try {
      return await createNewSession(
        normalizedPageId,
        pageLabel,
        endpointContext,
        `实现${pageLabel}`
      )
    } catch (caughtError) {
      reportSessionError(caughtError)
      throw caughtError
    }
  }

  /** 为指定 API endpoint 显式创建一个新的独立会话和 AG-UI thread。 */
  const createEndpointSession = async (
    apiContractId: string,
    endpointId: string,
    endpointLabel: string
  ): Promise<SessionIdentity> => {
    const normalizedApiContractId = apiContractId.trim()
    const normalizedEndpointId = endpointId.trim()
    if (!normalizedApiContractId || !normalizedEndpointId) {
      throw new Error('接口标识不能为空。')
    }
    try {
      return await createNewSession(undefined, undefined, {
        apiContractId: normalizedApiContractId,
        endpointId: normalizedEndpointId,
        endpointLabel
      })
    } catch (caughtError) {
      reportSessionError(caughtError)
      throw caughtError
    }
  }

  /** 按页面恢复既有会话，并把同一任务依赖的接口并入同一个 session 身份。 */
  const ensurePageSession = async (
    pageId: string,
    pageLabel: string,
    endpointContext?: RelatedEndpointContext
  ): Promise<SessionIdentity> => {
    const normalizedPageId = pageId.trim()
    if (!normalizedPageId) throw new Error('页面标识不能为空。')
    const promiseKey = `${editorMode}:${normalizedPageId}`
    const pendingPromise = pageSessionPromisesRef.current[promiseKey]
    if (pendingPromise) return pendingPromise

    const sessionPromise = (async (): Promise<SessionIdentity> => {
      const existingSession = sessionSummariesRef.current[editorMode].find(
        (session) => session.pageId === normalizedPageId
      )
      if (existingSession) {
        await openChatSession(editorMode, existingSession.id)
        const key = sessionRuntimeKey(workspaceRoot, editorMode, existingSession.id)
        const identity =
          getIdentity(key) || sessionIdentityFromSummary(existingSession, editorMode, workspaceRoot)
        if (identity) {
          if (!endpointContext) return identity
          const enrichedIdentity = createSessionIdentity({
            workspaceRoot: identity.workspaceRoot,
            editorMode: identity.editorMode,
            sessionId: identity.sessionId,
            threadId: identity.threadId,
            pageId: identity.pageId || normalizedPageId,
            apiContractId: endpointContext.apiContractId,
            endpointId: endpointContext.endpointId,
            endpointLabel: endpointContext.endpointLabel
          })
          registerSession(enrichedIdentity, getSessionMessages(key))
          return enrichedIdentity
        }
      }
      return createNewSession(normalizedPageId, pageLabel, endpointContext, `实现${pageLabel}`)
    })()
    pageSessionPromisesRef.current[promiseKey] = sessionPromise
    try {
      return await sessionPromise
    } catch (caughtError) {
      reportSessionError(caughtError)
      throw caughtError
    } finally {
      delete pageSessionPromisesRef.current[promiseKey]
    }
  }

  /** 为计划中的首个开发产物预建正式默认对话，不发送消息也不提前推进产物状态。 */
  const ensurePlannedPageSession = async (
    pageId: string,
    pageLabel: string,
    endpointContext?: RelatedEndpointContext
  ): Promise<SessionIdentity> => {
    const normalizedPageId = pageId.trim()
    if (!normalizedPageId) throw new Error('页面标识不能为空。')
    const existingSession = sessionSummariesRef.current[editorMode].find(
      (session) => session.pageId === normalizedPageId
    )
    if (existingSession) {
      await openChatSession(editorMode, existingSession.id)
      const key = sessionRuntimeKey(workspaceRoot, editorMode, existingSession.id)
      const identity =
        getIdentity(key) || sessionIdentityFromSummary(existingSession, editorMode, workspaceRoot)
      if (identity) return identity
    }
    return createNewSession(normalizedPageId, pageLabel, endpointContext, `实现${pageLabel}`, true)
  }

  /** 创建或复用唯一的应用级代码审查会话。 */
  const createReviewSession = async (): Promise<SessionIdentity> => {
    const REVIEW_TITLE = '代码审查'
    const existingReview = sessionSummariesRef.current[editorMode].find(
      (session) =>
        !session.pageId &&
        !session.apiContractId &&
        !session.endpointId &&
        (session.title || '').includes(REVIEW_TITLE)
    )
    if (existingReview) {
      await openChatSession(editorMode, existingReview.id)
      const key = sessionRuntimeKey(workspaceRoot, editorMode, existingReview.id)
      const identity =
        getIdentity(key) || sessionIdentityFromSummary(existingReview, editorMode, workspaceRoot)
      if (identity) return identity
    }
    return createNewSession(undefined, undefined, undefined, REVIEW_TITLE)
  }

  /** 按 API endpoint 恢复既有会话，首次进入该接口时创建独立 session 与 thread。 */
  const ensureEndpointSession = async (
    apiContractId: string,
    endpointId: string,
    endpointLabel: string
  ): Promise<SessionIdentity> => {
    const normalizedApiContractId = apiContractId.trim()
    const normalizedEndpointId = endpointId.trim()
    if (!normalizedApiContractId || !normalizedEndpointId) {
      throw new Error('接口标识不能为空。')
    }
    const existingSession = sessionSummariesRef.current[editorMode].find(
      (session) =>
        session.apiContractId === normalizedApiContractId &&
        session.endpointId === normalizedEndpointId
    )
    if (existingSession) {
      await openChatSession(editorMode, existingSession.id)
      const key = sessionRuntimeKey(workspaceRoot, editorMode, existingSession.id)
      const identity =
        getIdentity(key) || sessionIdentityFromSummary(existingSession, editorMode, workspaceRoot)
      if (identity) return identity
    }
    return createEndpointSession(normalizedApiContractId, normalizedEndpointId, endpointLabel)
  }

  const handleCreateSessionFromList = (): void => {
    if (!application.workspaceRoot) return
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

  /** 重命名已落盘的对话，并同步刷新左侧最近对话。 */
  const handleRenameSession = async (sessionId: string, title: string): Promise<void> => {
    if (!application.workspaceRoot) return
    const normalizedTitle = title.trim()
    if (!normalizedTitle) return
    const current = await readChatSession(application.workspaceRoot, editorMode, sessionId)
    const summary = await saveChatSession({
      ...current,
      title: normalizedTitle,
      updatedAt: Date.now()
    })
    replaceSessionSummary(editorMode, summary)
  }

  /** 保存正式对话；草稿会话可通过 materialize=false 延迟首次落盘。 */
  const persistSession = async (input: PersistSessionInput): Promise<void> => {
    if (!application.workspaceRoot) return
    // 草稿会话的用户消息只保存在运行时；首条 Agent 回复完成后才物化为正式对话。
    if (transientSessionIdsRef.current.has(input.sessionId) && input.materialize === false) return
    const existingSummary = sessionSummariesRef.current[input.editorMode].find(
      (summary) => summary.id === input.sessionId
    )
    const inferredEndpoint = inferEndpointContextFromMessages(input.messages)
    const now = Date.now()
    const session: ChatSessionRecord = {
      id: input.sessionId,
      title:
        input.titleFrom &&
        (!existingSummary ||
          existingSummary.title === '新对话' ||
          existingSummary.title.startsWith('页面新会话：') ||
          existingSummary.title.startsWith('接口新会话：') ||
          existingSummary.title.startsWith('实现页面：') ||
          existingSummary.title.startsWith('实现接口：') ||
          existingSummary.title.startsWith('实现GET ') ||
          existingSummary.title.startsWith('实现POST '))
          ? createChatSessionTitle(input.titleFrom)
          : existingSummary?.title || '新对话',
      editorMode: input.editorMode,
      threadId: input.threadId,
      apiContractId:
        input.apiContractId || existingSummary?.apiContractId || inferredEndpoint.apiContractId,
      endpointId: input.endpointId || existingSummary?.endpointId || inferredEndpoint.endpointId,
      endpointLabel:
        input.endpointLabel || existingSummary?.endpointLabel || inferredEndpoint.endpointLabel,
      pageId: input.pageId || existingSummary?.pageId,
      versionId: application.currentVersionId,
      workspaceRoot: application.workspaceRoot,
      messages: input.messages,
      createdAt: existingSummary?.createdAt || now,
      updatedAt: now
    }
    const summary = await saveChatSession(session)
    transientSessionIdsRef.current.delete(input.sessionId)
    replaceSessionSummary(input.editorMode, summary)
  }

  return {
    activeSession,
    activeSessionId,
    agUiSessionsRef,
    createReviewSession,
    createEndpointSession,
    createPageSession,
    deletingSessionId,
    draft,
    draftKey,
    ensureActiveSession,
    ensureEndpointSession,
    ensurePageSession,
    ensurePlannedPageSession,
    getSessionMessages,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    handleRenameSession,
    handleSelectEndpoint,
    handleSelectPage,
    loadingSessions,
    messages,
    selectedSkills,
    persistSession,
    runningSessionsRef,
    sessionError,
    sessions,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  }
}
