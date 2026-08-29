import { message as antdMessage } from 'antd'
import type { MutableRefObject, SetStateAction } from 'react'
import { useEffect, useRef, useState } from 'react'
import { AgUiChatSession } from '../../../service/agUiAgent'
import {
  createChatSessionId,
  createChatSessionTitle,
  deleteChatSession,
  getPersistedActiveSessionId,
  listChatSessions,
  readChatSession,
  saveChatSession,
  setPersistedActiveSessionId,
  type ChatSessionMessage,
  type ChatSessionRecord,
  type ChatSessionRevisionContext,
  type ChatSessionRevisionHandoff,
  type ChatSessionSummary
} from '../../../service/chatSessions'
import type {
  ApplicationConfig,
  ChatMessageSkill,
  EditorMode,
  WorkflowRevisionContinuation
} from '../../../typings'
import type { WorkbenchPhase } from '../../../workbenchPhase'
import type { AgentChatMessage } from '../types'
import {
  createSessionIdentity,
  pendingDraftKey,
  selectableEndpointSessionId,
  sessionIdentityFromSummary,
  sessionRuntimeKey,
  type SessionIdentity
} from './sessionRuntime'
import { clearEntityDesignDraftStore } from '../components/WorkflowRunCard/EntityDesignPanels'
import { useSessionRuntimeStore } from './useSessionRuntimeStore'
import {
  createRevisionDevelopmentSessionContext,
  revisionDevelopmentSessionForContinuation
} from './revisionSession'
import {
  sessionsForPlanningThread,
  sessionsForWorkbenchPhase,
  selectedSessionIdForPhase,
  withSelectedSessionForPhase,
  withoutDeletedSessionSelection,
  withoutEditorModeSessionSelection,
  type PhaseSessionSelection
} from './phaseSessionSelection'

export type PersistSessionInput = {
  editorMode: EditorMode
  messages: ChatSessionMessage[]
  sessionId: string
  threadId: string
  apiContractId?: string
  endpointId?: string
  endpointLabel?: string
  entityId?: string
  entityLabel?: string
  pageId?: string
  revisionContext?: ChatSessionRevisionContext
  titleFrom?: string
}

export type TestPhaseSessionTarget = {
  targetLabel: string
  pageId?: string
  apiContractId?: string
  endpointId?: string
  endpointLabel?: string
  entityId?: string
  entityLabel?: string
}

/** 审查阶段沿用测试目标归属，但使用全新的会话和 AG-UI thread。 */
export type ReviewPhaseSessionTarget = TestPhaseSessionTarget

/** 验收阶段沿用目标归属，但使用全新的会话和 AG-UI thread。 */
export type AcceptancePhaseSessionTarget = TestPhaseSessionTarget

/** 合并会话 revision 身份时保留已经由后端签发的 changeId。 */
function mergeRevisionSessionContext(
  current?: ChatSessionRevisionContext,
  incoming?: ChatSessionRevisionContext
): ChatSessionRevisionContext | undefined {
  if (!current) return incoming
  if (!incoming) return current
  return {
    ...current,
    ...incoming,
    changeId: incoming.changeId || current.changeId
  }
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

/** 从待保存消息中的 Workflow 快照推断实体会话归属。 */
function inferEntityContextFromMessages(messages: ChatSessionMessage[]): {
  entityId?: string
  entityLabel?: string
} {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const workflow = messages[index]?.workflow
    const state = workflow?.state || {}
    const result = workflow?.result || {}
    const reviewSummary = workflow?.summary.clarification?.review?.summary || {}
    const entityId = String(
      state.selectedEntityId ||
      result.selectedEntityId ||
      reviewSummary.selectedEntityId ||
      ''
    ).trim()
    if (entityId) return { entityId }
  }
  return {}
}

/** 判断会话是否为不绑定页面或 API 的自由对话。 */
function isFreeChatSession(session: ChatSessionSummary): boolean {
  return !session.pageId && !session.apiContractId && !session.endpointId && !session.entityId
}

type UseChatSessionsParams = {
  application: ApplicationConfig
  editorMode: EditorMode
  workbenchPhase: WorkbenchPhase
  onCloseRightPanel: () => void
  /** 设计阶段：规划 session 由 ensurePlanningSession 激活，loadSessionsForMode
   *  只加载会话列表不自动 openChatSession，避免覆盖规划 session 的 activeSessionId。 */
  designPhasePlanning?: boolean
}

type UseChatSessionsResult = {
  activeSession?: SessionIdentity
  activeSessionId?: string
  agUiSessionsRef: MutableRefObject<Record<string, AgUiChatSession>>
  deletingSessionId?: string
  draft: string
  draftKey: string
  createEndpointSession: (
    apiContractId: string,
    endpointId: string,
    endpointLabel: string
  ) => Promise<SessionIdentity>
  createEntitySession: (entityId: string, entityLabel: string) => Promise<SessionIdentity>
  createPageSession: (pageId: string, pageLabel: string) => Promise<SessionIdentity>
  createTestSession: (target: TestPhaseSessionTarget) => Promise<SessionIdentity>
  createReviewSession: (target: ReviewPhaseSessionTarget) => Promise<SessionIdentity>
  createAcceptanceSession: (target: AcceptancePhaseSessionTarget) => Promise<SessionIdentity>
  ensureActiveSession: () => Promise<SessionIdentity>
  ensurePlanningSession: (
    threadId: string,
    phase?: WorkbenchPhase,
    revisionContext?: ChatSessionRevisionContext
  ) => Promise<SessionIdentity>
  ensureRevisionDevelopmentSession: (
    source: SessionIdentity,
    continuation: WorkflowRevisionContinuation
  ) => Promise<SessionIdentity>
  activateRevisionDevelopmentSession: (identity: SessionIdentity) => Promise<void>
  ensureEndpointSession: (
    apiContractId: string,
    endpointId: string,
    endpointLabel: string
  ) => Promise<SessionIdentity>
  ensureEntitySession: (entityId: string, entityLabel: string) => Promise<SessionIdentity>
  ensurePageSession: (pageId: string, pageLabel: string) => Promise<SessionIdentity>
  clearActiveSession: () => void
  getSessionMessages: (sessionKey: string) => AgentChatMessage[]
  handleCreateSessionFromList: () => void
  handleDeleteSession: (sessionId: string) => Promise<void>
  handleOpenSession: (sessionId: string) => Promise<void>
  openSessionForPhase: (
    handoff: ChatSessionRevisionHandoff,
    phase: WorkbenchPhase
  ) => Promise<void>
  handleSelectEndpoint: (apiContractId: string, endpointId: string) => Promise<void>
  handleSelectEntity: (entityId: string) => Promise<void>
  handleSelectFreeChat: () => Promise<void>
  handleSelectPage: (pageId: string) => Promise<void>
  loadingSessions: boolean
  messages: AgentChatMessage[]
  selectedSkills: ChatMessageSkill[]
  persistSession: (input: PersistSessionInput) => Promise<void>
  runningSessionsRef: MutableRefObject<Map<string, SessionIdentity>>
  sessionError?: string
  sessions: ChatSessionSummary[]
  /** 当前编辑模式下的完整会话列表，仅供跨阶段恢复和身份匹配使用。 */
  allSessions: ChatSessionSummary[]
  setDraftByKey: (sessionKey: string, value: string) => void
  setSelectedSkillsByKey: (sessionKey: string, value: ChatMessageSkill[]) => void
  setSessionMessages: (sessionKey: string, value: SetStateAction<AgentChatMessage[]>) => void
}

export function useChatSessions({
  application,
  editorMode,
  workbenchPhase,
  onCloseRightPanel,
  designPhasePlanning = false
}: UseChatSessionsParams): UseChatSessionsResult {
  const [sessionSummaries, setSessionSummaries] = useState<
    Record<EditorMode, ChatSessionSummary[]>
  >({ frontend: [], backend: [] })
  const [activeSessionIds, setActiveSessionIds] = useState<PhaseSessionSelection>({})
  // 初始即标记为加载中：loadSessionsForMode 在 effect 中异步执行，
  // 首次渲染时 loadingSessions 需为 true，否则 ensurePlanningSession effect
  // 会在 sessionSummaries 为空时抢先创建重复 session，导致历史对话丢失。
  const [sessionLoadingModes, setSessionLoadingModes] = useState<
    Partial<Record<EditorMode, boolean>>
  >({ frontend: true, backend: true })
  const [sessionErrors, setSessionErrors] = useState<Partial<Record<EditorMode, string>>>({})
  const [deletingSessionIds, setDeletingSessionIds] = useState<Partial<Record<EditorMode, string>>>(
    {}
  )
  const sessionSummariesRef = useRef(sessionSummaries)
  const pageSessionPromisesRef = useRef<Record<string, Promise<SessionIdentity>>>({})
  // 设计阶段规划 session 已激活标志：ensurePlanningSession 完成后置 true，
  // 阻止 loadSessionsForMode 的异步 openChatSession 覆盖规划 session 的 activeSessionId。
  const planningSessionActivatedRef = useRef(false)
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

  // 退出设计阶段时重置规划 session 激活标志，允许后续正常 openChatSession。
  useEffect(() => {
    if (!designPhasePlanning) {
      planningSessionActivatedRef.current = false
    }
  }, [designPhasePlanning])

  useEffect(() => {
    loadSessionsForMode(editorMode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application.workspaceRoot, editorMode, workbenchPhase])

  const workspaceRoot = application.workspaceRoot || ''
  // 左侧会话列表仍按当前阶段展示；allSessions 只供跨阶段恢复和身份校验使用。
  const sessions = sessionsForWorkbenchPhase(sessionSummaries[editorMode], workbenchPhase)
  const allSessions = sessionSummaries[editorMode]
  const activeSessionId = selectedSessionIdForPhase(activeSessionIds, editorMode, workbenchPhase)
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

  /** 更新指定编辑模式下的会话摘要，同时保持更新时间倒序。 */
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

  /** 加载会话并只为当前工作台阶段恢复最近的目标会话。 */
  const loadSessionsForMode = async (mode: EditorMode): Promise<void> => {
    if (!application.workspaceRoot) {
      setSessionSummaries((current) => ({ ...current, [mode]: [] }))
      setActiveSessionIds((current) => withoutEditorModeSessionSelection(current, mode))
      setSessionLoadingModes((current) => ({ ...current, [mode]: false }))
      return
    }

    setSessionLoadingModes((current) => ({ ...current, [mode]: true }))
    setSessionErrors((current) => ({ ...current, [mode]: undefined }))
    try {
      const nextSessions = await listChatSessions(application.workspaceRoot, mode)
      sessionSummariesRef.current = {
        ...sessionSummariesRef.current,
        [mode]: nextSessions
      }
      setSessionSummaries((current) => ({ ...current, [mode]: nextSessions }))
      if (nextSessions.length === 0) {
        setActiveSessionIds((current) => withoutEditorModeSessionSelection(current, mode))
        return
      }
      // 设计阶段规划 session 由 ensurePlanningSession 激活，这里只加载完整列表，
      // 不自动打开其他会话抢占规划对话。
      if (designPhasePlanning) return
      // 当前阶段列表负责界面恢复；跨阶段的历史会话由 allSessions 供上层身份恢复使用。
      // 优先使用用户退出前保存的当前阶段会话，找不到时再恢复该阶段最近的目标会话。
      const persistedSessionId = getPersistedActiveSessionId(application.id, mode, workbenchPhase)
      const persistedSession = persistedSessionId
        ? nextSessions.find(
            (session) =>
              session.id === persistedSessionId && session.workbenchPhase === workbenchPhase
          )
        : undefined
      const phaseSessions = sessionsForWorkbenchPhase(nextSessions, workbenchPhase)
      const shouldRestoreLatestPhaseSession = ['test', 'review', 'acceptance'].includes(
        workbenchPhase
      )
      const fallbackSession =
        phaseSessions.find(
          (session) => (session.pageId || session.endpointId) && session.messageCount > 0
        ) ||
        phaseSessions.find((session) => session.pageId || session.endpointId) ||
        (shouldRestoreLatestPhaseSession
          ? phaseSessions.find((session) => session.messageCount > 0) || phaseSessions[0]
          : undefined)
      const sessionToOpen = persistedSession || fallbackSession
      if (sessionToOpen) {
        await openChatSession(mode, sessionToOpen.id, sessionToOpen.workbenchPhase, true)
      } else {
        setActiveSessionIds((current) =>
          withSelectedSessionForPhase(current, mode, workbenchPhase, undefined)
        )
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

  /** 将指定会话加载到运行时，并登记到其明确所属的工作台阶段。 */
  const openChatSession = async (
    mode: EditorMode,
    sessionId: string,
    phase: WorkbenchPhase = workbenchPhase,
    forceActivate = false
  ): Promise<void> => {
    if (!application.workspaceRoot) return

    await loadChatSessionIdentity(mode, sessionId)

    // 设计阶段规划 session 已激活时，不抢 activeSessionId，避免覆盖规划对话。
    if (planningSessionActivatedRef.current && !forceActivate) return

    setActiveSessionIds((current) => withSelectedSessionForPhase(current, mode, phase, sessionId))
    setPersistedActiveSessionId(application.id, mode, phase, sessionId)
    onCloseRightPanel()
  }

  /** 只把持久化会话装入运行时，不改变当前阶段和当前选中会话。 */
  const loadChatSessionIdentity = async (
    mode: EditorMode,
    sessionId: string
  ): Promise<SessionIdentity> => {
    if (!application.workspaceRoot) throw new Error('加载会话前需要选择工作目录。')
    const key = sessionRuntimeKey(application.workspaceRoot, mode, sessionId)
    const existingIdentity = getIdentity(key)
    if (existingIdentity && agUiSessionsRef.current[key]) return existingIdentity
    const session = await readChatSession(application.workspaceRoot, mode, sessionId)
    const identity = createSessionIdentity({
      workspaceRoot: application.workspaceRoot,
      editorMode: mode,
      sessionId: session.id,
      threadId: session.threadId,
      apiContractId: session.apiContractId,
      endpointId: session.endpointId,
      endpointLabel: session.endpointLabel,
      entityId: session.entityId,
      entityLabel: session.entityLabel,
      pageId: session.pageId,
      revisionContext: session.revisionContext
    })
    registerSession(identity, session.messages)
    return identity
  }

  /** 打开当前阶段侧栏中的会话，禁止通过旧列表跨阶段切换。 */
  const handleOpenSession = async (sessionId: string): Promise<void> => {
    if (sessionId === activeSessionId || loadingSessions) return
    setSessionLoadingModes((current) => ({ ...current, [editorMode]: true }))
    setSessionErrors((current) => ({ ...current, [editorMode]: undefined }))
    try {
      const summary = sessions.find((session) => session.id === sessionId)
      if (!summary) throw new Error('当前阶段不存在该会话。')
      await openChatSession(editorMode, sessionId, summary.workbenchPhase)
    } catch (caughtError) {
      setSessionErrors((current) => ({
        ...current,
        [editorMode]: caughtError instanceof Error ? caughtError.message : '打开本地会话失败。'
      }))
    } finally {
      setSessionLoadingModes((current) => ({ ...current, [editorMode]: false }))
    }
  }

  /** 显式打开另一个阶段中的会话，用于来源会话跳转到独立的正式二次修改会话。 */
  const openSessionForPhase = async (
    handoff: ChatSessionRevisionHandoff,
    phase: WorkbenchPhase
  ): Promise<void> => {
    if (loadingSessions) return
    const summary = sessionSummariesRef.current[editorMode].find(
      (session) =>
        session.id === handoff.targetSessionId &&
        session.threadId === handoff.targetConversationThreadId &&
        session.workbenchPhase === phase &&
        session.revisionContext?.impactInteractionId === handoff.impactInteractionId &&
        (handoff.kind !== 'revision_development' ||
          (session.revisionContext.sessionRole === 'development' &&
            session.revisionContext.changeId === handoff.changeId))
    )
    if (!summary) throw new Error('目标二次修改会话不存在、身份不匹配或已被删除。')
    await openChatSession(editorMode, summary.id, phase, true)
  }

  /** 切换页面时恢复该页面的已有会话（含仅含挡板消息的空会话），无会话则清空等待首次创建。 */
  const handleSelectPage = async (pageId: string): Promise<void> => {
    const normalizedPageId = pageId.trim()
    if (!normalizedPageId || loadingSessions) return

    const existingSession = sessionSummariesRef.current[editorMode].find(
      (session) =>
        session.workbenchPhase === workbenchPhase && session.pageId === normalizedPageId
    )
    if (existingSession) {
      await handleOpenSession(existingSession.id)
      return
    }

    setActiveSessionIds((current) =>
      withSelectedSessionForPhase(current, editorMode, workbenchPhase, undefined)
    )
    setPersistedActiveSessionId(application.id, editorMode, workbenchPhase, undefined)
    onCloseRightPanel()
  }

  /** 切换接口时仅恢复已有且有消息的会话，否则清空旧页面会话以显示当前接口挡板。 */
  const handleSelectEndpoint = async (apiContractId: string, endpointId: string): Promise<void> => {
    if (loadingSessions) return
    const existingSessionId = selectableEndpointSessionId(
      sessionsForWorkbenchPhase(sessionSummariesRef.current[editorMode], workbenchPhase),
      apiContractId,
      endpointId
    )
    if (existingSessionId) {
      await handleOpenSession(existingSessionId)
      return
    }

    setActiveSessionIds((current) =>
      withSelectedSessionForPhase(current, editorMode, workbenchPhase, undefined)
    )
    setPersistedActiveSessionId(application.id, editorMode, workbenchPhase, undefined)
    onCloseRightPanel()
  }

  /** 切换实体时只定位到实体信息展示界面（已设计）或锁定引导卡片（未设计），
   *  不自动打开历史会话；设计会话保留在左侧大纲历史中可再次打开。 */
  const handleSelectEntity = async (_entityId: string): Promise<void> => {
    if (loadingSessions) return
    void _entityId
    setActiveSessionIds((current) =>
      withSelectedSessionForPhase(current, editorMode, workbenchPhase, undefined)
    )
    setPersistedActiveSessionId(application.id, editorMode, workbenchPhase, undefined)
    onCloseRightPanel()
  }

  /** 清空当前会话选择，用于实体设计确认后回到实体信息展示界面。 */
  const clearActiveSession = (): void => {
    setActiveSessionIds((current) =>
      withSelectedSessionForPhase(current, editorMode, workbenchPhase, undefined)
    )
    setPersistedActiveSessionId(application.id, editorMode, workbenchPhase, undefined)
    onCloseRightPanel()
  }

  /** 进入自由对话时恢复按更新时间排序的最近会话，不因点击入口重复新建。 */
  const handleSelectFreeChat = async (): Promise<void> => {
    if (loadingSessions) return
    const freeSessions = sessions.filter(isFreeChatSession)
    const sessionToOpen = freeSessions[0]
    if (sessionToOpen) {
      await handleOpenSession(sessionToOpen.id)
      return
    }

    setActiveSessionIds((current) =>
      withSelectedSessionForPhase(current, editorMode, workbenchPhase, undefined)
    )
    setPersistedActiveSessionId(application.id, editorMode, workbenchPhase, undefined)
    onCloseRightPanel()
  }

  /** 创建普通会话或带页面归属的独立设计会话。 */
  const createNewSession = async (
    pageId?: string,
    pageLabel?: string,
    endpointContext?: {
      apiContractId: string
      endpointId: string
      endpointLabel: string
    },
    entityContext?: {
      entityId: string
      entityLabel: string
    },
    options?: {
      activate?: boolean
      threadId?: string
      title?: string
      workbenchPhase?: WorkbenchPhase
      revisionContext?: ChatSessionRevisionContext
    }
  ): Promise<SessionIdentity> => {
    if (!application.workspaceRoot) {
      throw new Error('创建会话前需要选择工作目录。')
    }

    const now = Date.now()
    const sessionWorkbenchPhase = options?.workbenchPhase || workbenchPhase
    const sessionId = createChatSessionId()
    const agUiSession = new AgUiChatSession(options?.threadId)
    const identity = createSessionIdentity({
      workspaceRoot: application.workspaceRoot,
      editorMode,
      sessionId,
      threadId: agUiSession.threadId,
      apiContractId: endpointContext?.apiContractId,
      endpointId: endpointContext?.endpointId,
      endpointLabel: endpointContext?.endpointLabel,
      entityId: entityContext?.entityId,
      entityLabel: entityContext?.entityLabel,
      pageId,
      revisionContext: options?.revisionContext
    })
    const session: ChatSessionRecord = {
      id: sessionId,
      title: options?.title
        ? options.title
        : entityContext
        ? `实体新会话：${entityContext.entityLabel}`
        : endpointContext
          ? `接口新会话：${endpointContext.endpointLabel}`
          : pageLabel
            ? `页面新会话：${pageLabel}`
            : '新对话',
      editorMode,
      workbenchPhase: sessionWorkbenchPhase,
      threadId: identity.threadId,
      apiContractId: identity.apiContractId,
      endpointId: identity.endpointId,
      endpointLabel: identity.endpointLabel,
      entityId: identity.entityId,
      entityLabel: identity.entityLabel,
      pageId: identity.pageId,
      revisionContext: identity.revisionContext,
      workspaceRoot: application.workspaceRoot,
      messages: [],
      createdAt: now,
      updatedAt: now
    }

    registerSession(identity, [], agUiSession)
    setDraftByKey(identity.key, '')
    if (options?.activate !== false) {
      setActiveSessionIds((current) =>
        withSelectedSessionForPhase(current, editorMode, sessionWorkbenchPhase, session.id)
      )
      setPersistedActiveSessionId(application.id, editorMode, sessionWorkbenchPhase, session.id)
      onCloseRightPanel()
    }

    try {
      const summary = await saveChatSession(session)
      replaceSessionSummary(editorMode, summary)
      return identity
    } catch (error) {
      removeSession(identity.key)
      throw error
    }
  }

  const ensureActiveSession = async (): Promise<SessionIdentity> => {
    if (activeSession) {
      ensureAgent(activeSession)
      return activeSession
    }
    return createNewSession()
  }

  /** 创建规划会话：产品阶段可绑定 Graph thread，规划阶段则绑定新的独立聊天 thread。
   *  Graph 仍由 ApplicationPagePlanningModal 的原 checkpoint session 跑，本会话只承接转发流。 */
  const ensurePlanningSession = async (
    threadId: string,
    phase: WorkbenchPhase = 'product',
    revisionContext?: ChatSessionRevisionContext
  ): Promise<SessionIdentity> => {
    const normalizedThreadId = threadId.trim()
    if (!normalizedThreadId) throw new Error('规划线程标识不能为空。')
    // 已存在同 threadId 的会话则复用并激活。
    // 可能有多个同 threadId 的重复 session（历史 bug 产生），优先选消息最多的；
    // 同时检查内存中 messagesRef 是否有历史消息（未落盘的规划对话）。
    const sameThreadSessions = sessionsForPlanningThread(
      sessionSummaries[editorMode],
      phase,
      normalizedThreadId
    )
    if (sameThreadSessions.length > 0) {
      // 选消息最多或内存中有消息的 session，避免选中空壳重复 session 丢失历史对话。
      const best = sameThreadSessions.reduce((picked, candidate) => {
        const candidateKey = sessionRuntimeKey(
          application.workspaceRoot || '',
          editorMode,
          candidate.id
        )
        const candidateMemCount = getSessionMessages(candidateKey).length
        const pickedKey = sessionRuntimeKey(
          application.workspaceRoot || '',
          editorMode,
          picked.id
        )
        const pickedMemCount = getSessionMessages(pickedKey).length
        // 优先比内存消息数（未落盘的规划对话），再比磁盘 messageCount。
        const candidateScore = candidateMemCount * 1000 + candidate.messageCount
        const pickedScore = pickedMemCount * 1000 + picked.messageCount
        return candidateScore > pickedScore ? candidate : picked
      })
      const identity = sessionIdentityFromSummary(best, editorMode, application.workspaceRoot || '')
      if (identity) {
        ensureAgent(identity)
        // 内存中没有历史消息但磁盘有（应用重启后重新进入）：从磁盘加载消息，
        // 避免规划对话历史丢失。内存已有消息（同一进程内切换工作区）则直接复用。
        const memMessages = getSessionMessages(identity.key)
        if (memMessages.length === 0 && best.messageCount > 0) {
          try {
            const session = await readChatSession(
              application.workspaceRoot || '',
              editorMode,
              best.id
            )
            registerSession(identity, session.messages)
          } catch {
            // 读取失败不阻塞，后续流式 chunk 仍可注入。
          }
        }
        planningSessionActivatedRef.current = true
        setActiveSessionIds((current) =>
          withSelectedSessionForPhase(current, editorMode, phase, best.id)
        )
        setPersistedActiveSessionId(application.id, editorMode, phase, best.id)
        // 清理同 threadId 的重复空壳 session（消息数 0 且非选中），避免再次串用。
        for (const duplicate of sameThreadSessions) {
          if (duplicate.id === best.id) continue
          const dupKey = sessionRuntimeKey(application.workspaceRoot || '', editorMode, duplicate.id)
          const dupMemCount = getSessionMessages(dupKey).length
          if (duplicate.messageCount === 0 && dupMemCount === 0) {
            try {
              await deleteChatSession(application.workspaceRoot || '', editorMode, duplicate.id)
              removeSession(dupKey)
              setSessionSummaries((current) => ({
                ...current,
                [editorMode]: current[editorMode].filter((s) => s.id !== duplicate.id)
              }))
            } catch {
              // 删除失败不阻塞，下次再清理。
            }
          }
        }
        return identity
      }
    }
    const identity = await createNewSession(
      undefined,
      undefined,
      undefined,
      undefined,
      {
        threadId: normalizedThreadId,
        title: phase === 'planning'
          ? '规划 Agent'
          : '产品 Agent',
        workbenchPhase: phase,
        revisionContext
      }
    )
    planningSessionActivatedRef.current = true
    return identity
  }

  /** 为同一 change 创建或复用唯一开发会话；准备阶段不切换当前设计会话。 */
  const ensureRevisionDevelopmentSession = async (
    source: SessionIdentity,
    continuation: WorkflowRevisionContinuation
  ): Promise<SessionIdentity> => {
    const existing = revisionDevelopmentSessionForContinuation(
      sessionSummariesRef.current[editorMode],
      continuation
    )
    if (existing) return loadChatSessionIdentity(editorMode, existing.id)
    const revisionContext = createRevisionDevelopmentSessionContext(source, continuation)
    return createNewSession(undefined, undefined, undefined, undefined, {
      activate: false,
      title: '二次修改 · 开发 Agent',
      workbenchPhase: 'development',
      revisionContext
    })
  }

  /** 仅在开发 Workflow 已成功接管后激活目标会话。 */
  const activateRevisionDevelopmentSession = async (
    identity: SessionIdentity
  ): Promise<void> => {
    const summary = sessionSummariesRef.current[editorMode].find(
      (session) =>
        session.id === identity.sessionId &&
        session.threadId === identity.threadId &&
        session.workbenchPhase === 'development' &&
        session.revisionContext?.sessionRole === 'development' &&
        session.revisionContext.changeId === identity.revisionContext?.changeId
    )
    if (!summary) throw new Error('二次修改开发会话不存在或身份不匹配。')
    planningSessionActivatedRef.current = false
    await openChatSession(editorMode, summary.id, 'development', true)
  }

  /** 为指定页面显式创建一个新的独立会话和 AG-UI thread。 */
  const createPageSession = async (pageId: string, pageLabel: string): Promise<SessionIdentity> => {
    const normalizedPageId = pageId.trim()
    if (!normalizedPageId) throw new Error('页面标识不能为空。')
    try {
      return await createNewSession(normalizedPageId, pageLabel)
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

  /** 为指定实体显式创建一个新的独立会话和 AG-UI thread。 */
  const createEntitySession = async (
    entityId: string,
    entityLabel: string
  ): Promise<SessionIdentity> => {
    const normalizedEntityId = entityId.trim()
    if (!normalizedEntityId) {
      throw new Error('实体标识不能为空。')
    }
    try {
      return await createNewSession(undefined, undefined, undefined, {
        entityId: normalizedEntityId,
        entityLabel
      })
    } catch (caughtError) {
      reportSessionError(caughtError)
      throw caughtError
    }
  }

  /** 为测试阶段创建独立的空白会话和 AG-UI thread，同时保留开发目标归属。 */
  const createTestSession = async (target: TestPhaseSessionTarget): Promise<SessionIdentity> => {
    const targetLabel = target.targetLabel.trim() || '当前应用'
    const apiContractId = String(target.apiContractId || '').trim()
    const endpointId = String(target.endpointId || '').trim()
    const entityId = String(target.entityId || '').trim()
    try {
      return await createNewSession(
        String(target.pageId || '').trim() || undefined,
        undefined,
        apiContractId && endpointId
          ? {
              apiContractId,
              endpointId,
              endpointLabel: String(target.endpointLabel || targetLabel).trim() || targetLabel
            }
          : undefined,
        entityId
          ? {
              entityId,
              entityLabel: String(target.entityLabel || targetLabel).trim() || targetLabel
            }
          : undefined,
        { title: `测试：${targetLabel}`, workbenchPhase: 'test' }
      )
    } catch (caughtError) {
      reportSessionError(caughtError)
      throw caughtError
    }
  }

  /** 为审查阶段创建独立空白会话和 AG-UI thread，保留当前目标归属但不复制测试消息。 */
  const createReviewSession = async (
    target: ReviewPhaseSessionTarget
  ): Promise<SessionIdentity> => {
    const targetLabel = target.targetLabel.trim() || '当前应用'
    const apiContractId = String(target.apiContractId || '').trim()
    const endpointId = String(target.endpointId || '').trim()
    const entityId = String(target.entityId || '').trim()
    try {
      return await createNewSession(
        String(target.pageId || '').trim() || undefined,
        undefined,
        apiContractId && endpointId
          ? {
              apiContractId,
              endpointId,
              endpointLabel: String(target.endpointLabel || targetLabel).trim() || targetLabel
            }
          : undefined,
        entityId
          ? {
              entityId,
              entityLabel: String(target.entityLabel || targetLabel).trim() || targetLabel
            }
          : undefined,
        { title: `审查：${targetLabel}`, workbenchPhase: 'review' }
      )
    } catch (caughtError) {
      reportSessionError(caughtError)
      throw caughtError
    }
  }

  /** 为验收阶段创建独立空白会话和 AG-UI thread，保留当前目标归属。 */
  const createAcceptanceSession = async (
    target: AcceptancePhaseSessionTarget
  ): Promise<SessionIdentity> => {
    const targetLabel = target.targetLabel.trim() || '当前应用'
    const apiContractId = String(target.apiContractId || '').trim()
    const endpointId = String(target.endpointId || '').trim()
    const entityId = String(target.entityId || '').trim()
    try {
      return await createNewSession(
        String(target.pageId || '').trim() || undefined,
        undefined,
        apiContractId && endpointId
          ? {
              apiContractId,
              endpointId,
              endpointLabel: String(target.endpointLabel || targetLabel).trim() || targetLabel
            }
          : undefined,
        entityId
          ? {
              entityId,
              entityLabel: String(target.entityLabel || targetLabel).trim() || targetLabel
            }
          : undefined,
        { title: `验收：${targetLabel}`, workbenchPhase: 'acceptance' }
      )
    } catch (caughtError) {
      reportSessionError(caughtError)
      throw caughtError
    }
  }

  /** 按页面恢复既有会话，首次进入该页面时创建独立 session 与 thread。 */
  const ensurePageSession = async (pageId: string, pageLabel: string): Promise<SessionIdentity> => {
    const normalizedPageId = pageId.trim()
    if (!normalizedPageId) throw new Error('页面标识不能为空。')
    const promiseKey = `${editorMode}:${normalizedPageId}`
    const pendingPromise = pageSessionPromisesRef.current[promiseKey]
    if (pendingPromise) return pendingPromise

    const sessionPromise = (async (): Promise<SessionIdentity> => {
      const existingSession = sessionSummariesRef.current[editorMode].find(
        (session) =>
          session.workbenchPhase === workbenchPhase && session.pageId === normalizedPageId
      )
      if (existingSession) {
        await openChatSession(editorMode, existingSession.id)
        const key = sessionRuntimeKey(workspaceRoot, editorMode, existingSession.id)
        const identity =
          getIdentity(key) || sessionIdentityFromSummary(existingSession, editorMode, workspaceRoot)
        if (identity) return identity
      }
      return createNewSession(normalizedPageId, pageLabel)
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
        session.workbenchPhase === workbenchPhase &&
        session.apiContractId === normalizedApiContractId &&
        session.endpointId === normalizedEndpointId
    )
    if (existingSession) {
      await openChatSession(editorMode, existingSession.id)
      const key = sessionRuntimeKey(workspaceRoot, editorMode, existingSession.id)
      const identity = getIdentity(key)
        || sessionIdentityFromSummary(existingSession, editorMode, workspaceRoot)
      if (identity) return identity
    }
    return createEndpointSession(
      normalizedApiContractId,
      normalizedEndpointId,
      endpointLabel
    )
  }

  /** 按实体恢复既有会话，首次进入该实体时创建独立 session 与 thread。 */
  const ensureEntitySession = async (
    entityId: string,
    entityLabel: string
  ): Promise<SessionIdentity> => {
    const normalizedEntityId = entityId.trim()
    if (!normalizedEntityId) {
      throw new Error('实体标识不能为空。')
    }
    const existingSession = sessionSummariesRef.current[editorMode].find(
      (session) =>
        session.workbenchPhase === workbenchPhase && session.entityId === normalizedEntityId
    )
    if (existingSession) {
      await openChatSession(editorMode, existingSession.id)
      const key = sessionRuntimeKey(workspaceRoot, editorMode, existingSession.id)
      const identity = getIdentity(key)
        || sessionIdentityFromSummary(existingSession, editorMode, workspaceRoot)
      if (identity) return identity
    }
    return createEntitySession(normalizedEntityId, entityLabel)
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
      setActiveSessionIds((current) =>
        withoutDeletedSessionSelection(current, editorMode, sessionId)
      )
      if (getPersistedActiveSessionId(application.id, editorMode, workbenchPhase) === sessionId) {
        setPersistedActiveSessionId(application.id, editorMode, workbenchPhase, undefined)
      }
      // 删除会话后清空该工作区的实体设计草稿缓存，避免新会话继承旧状态。
      clearEntityDesignDraftStore(application.workspaceRoot)

      if (activeSessionId === sessionId) {
        if (nextSession) {
          await openChatSession(editorMode, nextSession.id, nextSession.workbenchPhase)
        } else {
          setActiveSessionIds((current) =>
            withSelectedSessionForPhase(current, editorMode, workbenchPhase, undefined)
          )
          setPersistedActiveSessionId(application.id, editorMode, workbenchPhase, undefined)
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

  /** 持久化消息时保留会话原有阶段归属，避免异步完成后被当前顶部阶段改写。 */
  const persistSession = async (input: PersistSessionInput): Promise<void> => {
    if (!application.workspaceRoot) return
    const existingSummary = sessionSummariesRef.current[input.editorMode].find(
      (summary) => summary.id === input.sessionId
    )
    const inferredEndpoint = inferEndpointContextFromMessages(input.messages)
    const inferredEntity = inferEntityContextFromMessages(input.messages)
    const now = Date.now()
    const session: ChatSessionRecord = {
      id: input.sessionId,
      title:
        input.titleFrom &&
        (!existingSummary ||
          existingSummary.title === '新对话' ||
          existingSummary.title.startsWith('页面新会话：') ||
          existingSummary.title.startsWith('接口新会话：') ||
          existingSummary.title.startsWith('实体新会话：'))
          ? createChatSessionTitle(input.titleFrom)
          : existingSummary?.title || '新对话',
      editorMode: input.editorMode,
      workbenchPhase: existingSummary?.workbenchPhase || workbenchPhase,
      threadId: input.threadId,
      apiContractId:
        input.apiContractId || existingSummary?.apiContractId || inferredEndpoint.apiContractId,
      endpointId:
        input.endpointId || existingSummary?.endpointId || inferredEndpoint.endpointId,
      endpointLabel:
        input.endpointLabel || existingSummary?.endpointLabel || inferredEndpoint.endpointLabel,
      entityId: input.entityId || existingSummary?.entityId || inferredEntity.entityId,
      entityLabel: input.entityLabel || existingSummary?.entityLabel || inferredEntity.entityLabel,
      pageId: input.pageId || existingSummary?.pageId,
      revisionContext: mergeRevisionSessionContext(
        existingSummary?.revisionContext,
        input.revisionContext
      ),
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
    createEndpointSession,
    createEntitySession,
    createPageSession,
    createTestSession,
    createReviewSession,
    createAcceptanceSession,
    deletingSessionId,
    draft,
    draftKey,
    ensureActiveSession,
    ensurePlanningSession,
    ensureRevisionDevelopmentSession,
    activateRevisionDevelopmentSession,
    ensureEndpointSession,
    ensureEntitySession,
    ensurePageSession,
    getSessionMessages,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    openSessionForPhase,
    handleSelectEndpoint,
    handleSelectEntity,
    handleSelectFreeChat,
    handleSelectPage,
    clearActiveSession,
    loadingSessions,
    messages,
    selectedSkills,
    persistSession,
    runningSessionsRef,
    sessionError,
    sessions,
    allSessions,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  }
}
