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
  type ChatSessionSavedFile,
  type ChatSessionSummary
} from '../../../service/chatSessions'
import type { ApplicationConfig, ChatMessageSkill, EditorMode } from '../../../typings'
import type { WorkbenchSessionKind } from '../../../workbenchDomain'
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
  sessionKind?: WorkbenchSessionKind
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
    const detailTargetType = String(
      state.detailTargetType || result.detailTargetType || reviewSummary.detailTargetType || ''
    ).trim()
    // 页面工作流可以携带依赖接口身份，但只有独立接口工作流才能据此建立接口产物关系。
    if (detailTargetType !== 'endpoint') continue
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
  clearActiveSession: () => void
  deletingSessionId?: string
  draft: string
  draftKey: string
  createReviewSession: () => Promise<SessionIdentity>
  createTestingSession: () => Promise<SessionIdentity>
  createAcceptanceSession: () => Promise<SessionIdentity>
  /** 开发阶段按产物边界新建一条开发对话。 */
  createDevelopmentSession: () => Promise<SessionIdentity>
  ensureDevelopmentSession: () => Promise<SessionIdentity>
  ensureAnalysisSession: () => Promise<SessionIdentity>
  ensurePlanningSession: () => Promise<SessionIdentity>
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
  getSessionMessages: (sessionKey: string) => AgentChatMessage[]
  handleCreateSessionFromList: (sessionKind?: WorkbenchSessionKind) => Promise<SessionIdentity>
  handleDeleteSession: (sessionId: string) => Promise<void>
  handleOpenSession: (sessionId: string) => Promise<void>
  handleRenameSession: (sessionId: string, title: string) => Promise<void>
  handleSelectEndpoint: (apiContractId: string, endpointId: string) => Promise<void>
  handleSelectPage: (pageId: string) => Promise<void>
  loadingSessions: boolean
  messages: AgentChatMessage[]
  selectedSkills: ChatMessageSkill[]
  persistSession: (input: PersistSessionInput) => Promise<void>
  recordAcceptedFile: (
    sessionId: string,
    file: Omit<ChatSessionSavedFile, 'savedAt'>
  ) => Promise<void>
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
  // 会话列表异步恢复期间保留当前运行中的临时会话，避免新阶段被旧会话抢回。
  const activeSessionIdsRef = useRef<Partial<Record<EditorMode, string>>>({})
  const pageSessionPromisesRef = useRef<Record<string, Promise<SessionIdentity>>>({})
  // 开发阶段只有一条主会话；并发入口必须共享同一个创建/恢复 Promise，避免生成两条空会话。
  const developmentSessionPromiseRef = useRef<Promise<SessionIdentity> | undefined>()
  // 当前版本的开发主会话身份必须固定在运行期，不能依赖异步会话目录的刷新时机重新查找。
  const developmentSessionRef = useRef<SessionIdentity | undefined>()
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
    activeSessionIdsRef.current = activeSessionIds
  }, [activeSessionIds])

  useEffect(() => {
    developmentSessionPromiseRef.current = undefined
    developmentSessionRef.current = undefined
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
      activeSessionIdsRef.current = { ...activeSessionIdsRef.current, [mode]: undefined }
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
      // 会话列表只负责恢复目录，绝不擅自打开“最近一条”会话；当前会话由阶段入口或用户选择决定。
      // 目录为空不等于没有当前会话：运行期已打开的会话（含尚未物化的草稿）不能被一次
      // 空目录重载踢下线，否则阶段入口会锁死在“正在切换阶段”，任务管理也显示为空。
      if (nextSessions.length === 0 && !activeSessionIdsRef.current[mode]) {
        activeSessionIdsRef.current = { ...activeSessionIdsRef.current, [mode]: undefined }
        setActiveSessionIds((current) => ({ ...current, [mode]: undefined }))
      }
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
        pageId: session.pageId,
        sessionKind: session.sessionKind
      })
      registerSession(identity, session.messages)
    }

    activeSessionIdsRef.current = { ...activeSessionIdsRef.current, [mode]: sessionId }
    setActiveSessionIds((current) => ({ ...current, [mode]: sessionId }))
  }

  const handleOpenSession = async (sessionId: string): Promise<void> => {
    // 阶段切换可能与首轮会话列表读取并行；目标会话必须能抢先接管，不能被加载锁吞掉。
    if (sessionId === activeSessionId) return
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

  /** 清空当前选中的会话，让阶段入口可以展示尚未建立任务上下文的空白态。 */
  const clearActiveSession = (): void => {
    activeSessionIdsRef.current = { ...activeSessionIdsRef.current, [editorMode]: undefined }
    setActiveSessionIds((current) => ({ ...current, [editorMode]: undefined }))
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

    activeSessionIdsRef.current = { ...activeSessionIdsRef.current, [editorMode]: undefined }
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

    activeSessionIdsRef.current = { ...activeSessionIdsRef.current, [editorMode]: undefined }
    setActiveSessionIds((current) => ({ ...current, [editorMode]: undefined }))
  }

/** 创建尚未进入目录的运行时草稿会话，等待 Agent 回复后再物化。 */
  const createNewSession = async (
    pageId?: string,
    pageLabel?: string,
    endpointContext?: RelatedEndpointContext,
    customTitle?: string,
    materializeImmediately = false,
    sessionKind?: WorkbenchSessionKind,
    createdByUser = false
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
      pageId,
      sessionKind
    })
    const session: ChatSessionRecord = {
      createdByUser,
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
      sessionKind: identity.sessionKind,
      versionId: application.currentVersionId,
      workspaceRoot: application.workspaceRoot,
      messages: [],
      createdAt: now,
      updatedAt: now
    }

    registerSession(identity, [], agUiSession)
    setDraftByKey(identity.key, '')
    activeSessionIdsRef.current = { ...activeSessionIdsRef.current, [editorMode]: session.id }
    setActiveSessionIds((current) => ({ ...current, [editorMode]: session.id }))

    if (materializeImmediately) {
      // 阶段主对话需要立即进入目录，确保阶段切换后仍可恢复同一条会话。
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

  /** 为指定页面显式创建正式会话，并可一并登记该页面任务负责的依赖接口。 */
  const createPageSession = async (
    pageId: string,
    pageLabel: string,
    endpointContext?: RelatedEndpointContext
  ): Promise<SessionIdentity> => {
    const normalizedPageId = pageId.trim()
    if (!normalizedPageId) throw new Error('页面标识不能为空。')
    const promiseKey = `${editorMode}:${normalizedPageId}`
    const pendingPromise = pageSessionPromisesRef.current[promiseKey]
    if (pendingPromise) return pendingPromise
    const sessionPromise = createNewSession(
      normalizedPageId,
      pageLabel,
      endpointContext,
      `实现${pageLabel}`,
      true
    )
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

  /** 为指定 API endpoint 显式创建一个新的正式会话和 AG-UI thread。 */
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
      return await createNewSession(
        undefined,
        undefined,
        {
          apiContractId: normalizedApiContractId,
          endpointId: normalizedEndpointId,
          endpointLabel
        },
        undefined,
        true
      )
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

  /** 创建或复用阶段默认的应用级代码审查会话。 */
  const createReviewSession = async (): Promise<SessionIdentity> => {
    const REVIEW_TITLE = '代码审查'
    const existingReview = sessionSummariesRef.current[editorMode].find(
      (session) =>
        !session.pageId &&
        !session.apiContractId &&
        !session.endpointId &&
        session.sessionKind === 'review' && session.title === REVIEW_TITLE
    )
    if (existingReview) {
      await openChatSession(editorMode, existingReview.id)
      const key = sessionRuntimeKey(workspaceRoot, editorMode, existingReview.id)
      const identity =
        getIdentity(key) || sessionIdentityFromSummary(existingReview, editorMode, workspaceRoot)
      if (identity) return identity
    }
    return createNewSession(undefined, undefined, undefined, REVIEW_TITLE, true, 'review')
  }

  /** 创建或复用阶段默认的应用级测试会话。 */
  const createTestingSession = async (): Promise<SessionIdentity> => {
    const TESTING_TITLE = '应用测试'
    const existingTesting = sessionSummariesRef.current[editorMode].find(
      (session) =>
        !session.pageId &&
        !session.apiContractId &&
        !session.endpointId &&
        session.sessionKind === 'testing' && session.title === TESTING_TITLE
    )
    if (existingTesting) {
      await openChatSession(editorMode, existingTesting.id)
      const key = sessionRuntimeKey(workspaceRoot, editorMode, existingTesting.id)
      const identity =
        getIdentity(key) || sessionIdentityFromSummary(existingTesting, editorMode, workspaceRoot)
      if (identity) return identity
    }
    return createNewSession(undefined, undefined, undefined, TESTING_TITLE, true, 'testing')
  }

  /** 创建或复用开发阶段的主对话「应用开发」；产物目标不再决定会话身份。 */
  const ensureDevelopmentSession = async (): Promise<SessionIdentity> => {
    const cachedDevelopment = developmentSessionRef.current
    if (
      cachedDevelopment &&
      cachedDevelopment.workspaceRoot === workspaceRoot &&
      cachedDevelopment.editorMode === editorMode
    ) {
      // 同一阶段后续 Workflow 只激活已固定的主会话，目录刷新不得把它替换成空会话。
      const sanitizedDevelopment =
        cachedDevelopment.pageId || cachedDevelopment.apiContractId || cachedDevelopment.endpointId
          ? createSessionIdentity({
              workspaceRoot: cachedDevelopment.workspaceRoot,
              editorMode: cachedDevelopment.editorMode,
              sessionId: cachedDevelopment.sessionId,
              threadId: cachedDevelopment.threadId,
              sessionKind: 'development'
            })
          : cachedDevelopment
      if (sanitizedDevelopment !== cachedDevelopment) {
        registerSession(sanitizedDevelopment, getSessionMessages(cachedDevelopment.key))
        developmentSessionRef.current = sanitizedDevelopment
      }
      ensureAgent(sanitizedDevelopment)
      activeSessionIdsRef.current = {
        ...activeSessionIdsRef.current,
        [editorMode]: sanitizedDevelopment.sessionId
      }
      setActiveSessionIds((current) => ({
        ...current,
        [editorMode]: sanitizedDevelopment.sessionId
      }))
      return sanitizedDevelopment
    }
    const inFlight = developmentSessionPromiseRef.current
    if (inFlight) return inFlight
    const DEVELOPMENT_TITLE = '应用开发'
    const sessionPromise = (async (): Promise<SessionIdentity> => {
      // 以消息较多的会话优先，兼容并发创建留下的重复空壳，保证历史轨迹不被新空会话遮住。
      const existingDevelopment = sessionSummariesRef.current[editorMode]
        .filter(
          (session) =>
            session.sessionKind === 'development' &&
            session.title === DEVELOPMENT_TITLE
        )
        .sort(
          (left, right) =>
            right.messageCount - left.messageCount || right.updatedAt - left.updatedAt
        )[0]
      if (existingDevelopment) {
        await openChatSession(editorMode, existingDevelopment.id)
        const key = sessionRuntimeKey(workspaceRoot, editorMode, existingDevelopment.id)
        const storedIdentity =
          getIdentity(key) ||
          sessionIdentityFromSummary(existingDevelopment, editorMode, workspaceRoot)
        if (storedIdentity) {
          const identity = createSessionIdentity({
            workspaceRoot,
            editorMode,
            sessionId: storedIdentity.sessionId,
            threadId: storedIdentity.threadId,
            sessionKind: 'development'
          })
          registerSession(identity, getSessionMessages(key))
          developmentSessionRef.current = identity
          return identity
        }
      }
      const identity = await createNewSession(
        undefined,
        undefined,
        undefined,
        DEVELOPMENT_TITLE,
        true,
        'development'
      )
      developmentSessionRef.current = identity
      return identity
    })()
    developmentSessionPromiseRef.current = sessionPromise
    try {
      return await sessionPromise
    } finally {
      if (developmentSessionPromiseRef.current === sessionPromise) {
        developmentSessionPromiseRef.current = undefined
      }
    }
  }

  /** 创建或复用阶段默认的应用级验收会话，保持验收与审查对话完全隔离。 */
  const createAcceptanceSession = async (): Promise<SessionIdentity> => {
    const ACCEPTANCE_TITLE = '应用验收'
    const existingAcceptance = sessionSummariesRef.current[editorMode].find(
      (session) =>
        !session.pageId &&
        !session.apiContractId &&
        !session.endpointId &&
        session.sessionKind === 'acceptance' && session.title === ACCEPTANCE_TITLE
    )
    if (existingAcceptance) {
      await openChatSession(editorMode, existingAcceptance.id)
      const key = sessionRuntimeKey(workspaceRoot, editorMode, existingAcceptance.id)
      const identity =
        getIdentity(key) || sessionIdentityFromSummary(existingAcceptance, editorMode, workspaceRoot)
      if (identity) return identity
    }
    return createNewSession(undefined, undefined, undefined, ACCEPTANCE_TITLE, true, 'acceptance')
  }

  /** 读取当前运行时已创建的设计会话，避免自动首轮再次创建空白会话。 */
  const activeRuntimeDesignSession = (
    sessionKind: 'analysis' | 'planning'
  ): SessionIdentity | undefined => {
    const sessionId = activeSessionIdsRef.current[editorMode]
    if (!sessionId) return undefined
    const key = sessionRuntimeKey(workspaceRoot, editorMode, sessionId)
    const identity = getIdentity(key)
    return identity?.sessionKind === sessionKind ? identity : undefined
  }

  /** 创建或复用产品 Agent 的需求分析阶段默认会话，只持有需求文档。 */
  const ensureAnalysisSession = async (): Promise<SessionIdentity> => {
    const activeRuntimeSession = activeRuntimeDesignSession('analysis')
    if (activeRuntimeSession) return activeRuntimeSession
    const existing = sessionSummariesRef.current[editorMode].find(
      (session) =>
        !session.pageId &&
        !session.apiContractId &&
        !session.endpointId &&
        session.sessionKind === 'analysis' && session.title === '需求分析'
    )
    if (existing) {
      await openChatSession(editorMode, existing.id)
      const key = sessionRuntimeKey(workspaceRoot, editorMode, existing.id)
      const identity =
        getIdentity(key) || sessionIdentityFromSummary(existing, editorMode, workspaceRoot)
      if (identity) return identity
    }
    return createNewSession(undefined, undefined, undefined, '需求分析', true, 'analysis')
  }

  /** 创建或复用项目 Agent 的项目规划阶段默认会话，只持有项目计划。 */
  const ensurePlanningSession = async (): Promise<SessionIdentity> => {
    const activeRuntimeSession = activeRuntimeDesignSession('planning')
    if (activeRuntimeSession) return activeRuntimeSession
    const existing = sessionSummariesRef.current[editorMode].find(
      (session) =>
        !session.pageId &&
        !session.apiContractId &&
        !session.endpointId &&
        session.sessionKind === 'planning' && session.title === '项目计划'
    )
    if (existing) {
      await openChatSession(editorMode, existing.id)
      const key = sessionRuntimeKey(workspaceRoot, editorMode, existing.id)
      const identity =
        getIdentity(key) || sessionIdentityFromSummary(existing, editorMode, workspaceRoot)
      if (identity) return identity
    }
    return createNewSession(undefined, undefined, undefined, '项目计划', true, 'planning')
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

  /** 从对话视图显式创建当前阶段的额外持久会话，创建完成即成为当前会话。 */
  const handleCreateSessionFromList = async (
    sessionKind: WorkbenchSessionKind = 'general'
  ): Promise<SessionIdentity> => {
    if (!application.workspaceRoot) throw new Error('创建会话前需要选择工作目录。')
    // 新任务默认命名「新任务」；首轮操作完成后由 AI 按产物/阶段自动命名，
    // 用户也可在对话区顶部标题重命名——不再用「类型 + 序号」的机械命名。
    return createNewSession(undefined, undefined, undefined, '新任务', true, sessionKind, true)
  }

  /**
   * 开发阶段按产物边界新建一条开发对话：开发者可以把关联页面/接口归入同一条对话推进，
   * 「应用开发」主对话始终保持存在；新增对话以序号命名并立即成为当前会话。
   */
  const createDevelopmentSession = async (): Promise<SessionIdentity> => {
    if (!application.workspaceRoot) {
      throw new Error('创建会话前需要选择工作目录。')
    }
    const existingCount = sessionSummariesRef.current[editorMode].filter(
      (session) => session.sessionKind === 'development'
    ).length
    const title = existingCount === 0 ? '应用开发' : `开发对话 ${existingCount + 1}`
    return createNewSession(undefined, undefined, undefined, title, true, 'development')
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
          activeSessionIdsRef.current = { ...activeSessionIdsRef.current, [editorMode]: undefined }
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

  /** 把 Workflow 已确认的 Diff 固化为正式文件快照；会话仅作为本地历史载体。 */
  const recordAcceptedFile = async (
    sessionId: string,
    file: Omit<ChatSessionSavedFile, 'savedAt'>
  ): Promise<void> => {
    if (!application.workspaceRoot || !sessionId || !file.path.trim()) return
    const current = await readChatSession(application.workspaceRoot, editorMode, sessionId)
    const savedAt = Date.now()
    const savedFiles = [
      ...(current.savedFiles || []).filter((item) => item.path !== file.path),
      { ...file, path: file.path.trim(), savedAt }
    ]
    const summary = await saveChatSession({ ...current, savedFiles, updatedAt: savedAt })
    replaceSessionSummary(editorMode, summary)
  }

  /** 保存正式对话；草稿会话可通过 materialize=false 延迟首次落盘。 */
  const persistSession = async (input: PersistSessionInput): Promise<void> => {
    if (!application.workspaceRoot) return
    // 草稿会话的用户消息只保存在运行时；首条 Agent 回复完成后才物化为正式对话。
    if (transientSessionIdsRef.current.has(input.sessionId) && input.materialize === false) return
    let existingSummary = sessionSummariesRef.current[input.editorMode].find(
      (summary) => summary.id === input.sessionId
    )
    if (!existingSummary) {
      // 目录刷新竞态：刚物化的会话摘要可能还没进入内存目录（异步加载/热切换期间），
      // 从持久层补读一次既有记录，避免后续保存把已命名会话的标题覆盖成“新对话”。
      const persisted = await readChatSession(
        application.workspaceRoot,
        input.editorMode,
        input.sessionId
      ).catch(() => undefined)
      if (persisted) {
        existingSummary = { ...persisted, messageCount: persisted.messages.length }
      }
    }
    // 开发阶段对话（主对话与按边界拆分的对话）一律不绑定产物：产物写入归属由活动 Workflow 承载，
    // 对话只负责交互轨迹，因此摘要上不落 pageId/endpoint 身份。
    const isDevelopmentSession = (input.sessionKind || existingSummary?.sessionKind) === 'development'
    const inferredEndpoint = inferEndpointContextFromMessages(input.messages)
    const now = Date.now()
    const session: ChatSessionRecord = {
      createdByUser: existingSummary?.createdByUser,
      // 继续执行工作流时沿用本次会话已经确认的文件，不能让新消息把正式文件快照覆盖掉。
      savedFiles: existingSummary?.savedFiles,
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
      apiContractId: isDevelopmentSession
        ? undefined
        : input.apiContractId || existingSummary?.apiContractId || inferredEndpoint.apiContractId,
      endpointId: isDevelopmentSession
        ? undefined
        : input.endpointId || existingSummary?.endpointId || inferredEndpoint.endpointId,
      endpointLabel: isDevelopmentSession
        ? undefined
        : input.endpointLabel || existingSummary?.endpointLabel || inferredEndpoint.endpointLabel,
      pageId: isDevelopmentSession ? undefined : input.pageId || existingSummary?.pageId,
      sessionKind: input.sessionKind || existingSummary?.sessionKind,
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
    clearActiveSession,
    createReviewSession,
    createTestingSession,
    createAcceptanceSession,
    createDevelopmentSession,
    ensureDevelopmentSession,
    ensureAnalysisSession,
    ensurePlanningSession,
    createEndpointSession,
    createPageSession,
    deletingSessionId,
    draft,
    draftKey,
    ensureActiveSession,
    ensureEndpointSession,
    ensurePageSession,
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
    recordAcceptedFile,
    runningSessionsRef,
    sessionError,
    sessions,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  }
}
