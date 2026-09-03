import { message as antdMessage } from 'antd'
import type { MutableRefObject, SetStateAction } from 'react'
import { useEffect, useRef, useState } from 'react'
import { AgUiChatSession } from '../../../service/agUiAgent'
import {
  createChatSession,
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
  ApplicationLifecycle,
  ChatMessageSkill,
  EditorMode,
  WorkbenchExecution,
  WorkflowRevisionContinuation
} from '../../../typings'
import type { WorkbenchPhase } from '../../../workbenchPhase'
import type { AgentChatMessage } from '../types'
import {
  createSessionIdentity,
  pendingDraftKey,
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
  sessionToRestoreForPhase,
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

/** 创建只归属于审查阶段的新会话和 AG-UI thread。 */
export type ReviewPhaseSessionTarget = TestPhaseSessionTarget

/** 创建只归属于验收阶段的新会话和 AG-UI thread。 */
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
  createTestSession: (target: TestPhaseSessionTarget) => Promise<SessionIdentity>
  createReviewSession: (target: ReviewPhaseSessionTarget) => Promise<SessionIdentity>
  createAcceptanceSession: (target: AcceptancePhaseSessionTarget) => Promise<SessionIdentity>
  discardPreparedSession: (identity: SessionIdentity) => Promise<void>
  ensureActiveSession: () => Promise<SessionIdentity>
  ensurePlanningSession: (
    threadId: string,
    phase?: WorkbenchPhase,
    revisionContext?: ChatSessionRevisionContext,
    sourceIdentity?: SessionIdentity
  ) => Promise<SessionIdentity>
  ensureRevisionDevelopmentSession: (
    source: SessionIdentity,
    continuation: WorkflowRevisionContinuation
  ) => Promise<SessionIdentity>
  recoverRevisionDevelopmentSession: (
    source: SessionIdentity,
    lifecycle: ApplicationLifecycle,
    execution: WorkbenchExecution
  ) => Promise<SessionIdentity>
  activateRevisionDevelopmentSession: (identity: SessionIdentity) => Promise<void>
  clearActiveSession: () => void
  getSessionMessages: (sessionKey: string) => AgentChatMessage[]
  loadSessionIdentity: (sessionId: string) => Promise<SessionIdentity>
  handleCreateSessionFromList: () => void
  handleDeleteSession: (sessionId: string) => Promise<void>
  handleOpenSession: (sessionId: string) => Promise<void>
  openSessionForPhase: (handoff: ChatSessionRevisionHandoff, phase: WorkbenchPhase) => Promise<void>
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
  // 显式阶段交接目标优先于 localStorage 恢复值，直到目标阶段完成一次权威加载。
  const explicitPhaseSessionTargetsRef = useRef<
    Partial<Record<EditorMode, Partial<Record<WorkbenchPhase, string>>>>
  >({})
  // 同一编辑模式只允许最后一次阶段加载写回，避免旧阶段的迟到请求覆盖新会话。
  const sessionLoadGenerationRef = useRef<Partial<Record<EditorMode, number>>>({})
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
    void loadSessionsForMode(editorMode, workbenchPhase)
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
  const loadSessionsForMode = async (mode: EditorMode, phase: WorkbenchPhase): Promise<void> => {
    const generation = (sessionLoadGenerationRef.current[mode] || 0) + 1
    sessionLoadGenerationRef.current[mode] = generation
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
      // 阶段或工作区已经触发了更新加载时，旧请求不得再写 session/activeSessionId。
      if (sessionLoadGenerationRef.current[mode] !== generation) return
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
      // 显式交接目标拥有最高优先级；只有普通恢复才读取 localStorage 和阶段默认值。
      const explicitSessionId = explicitPhaseSessionTargetsRef.current[mode]?.[phase]
      const persistedSessionId = getPersistedActiveSessionId(application.id, mode, phase)
      const sessionToOpen = sessionToRestoreForPhase(
        nextSessions,
        phase,
        explicitSessionId,
        persistedSessionId
      )
      if (sessionToOpen) {
        await openChatSession(mode, sessionToOpen.id, phase, true)
        if (explicitPhaseSessionTargetsRef.current[mode]?.[phase] === sessionToOpen.id) {
          delete explicitPhaseSessionTargetsRef.current[mode]?.[phase]
        }
      } else {
        setActiveSessionIds((current) =>
          withSelectedSessionForPhase(current, mode, phase, undefined)
        )
        if (explicitSessionId) {
          setSessionErrors((current) => ({
            ...current,
            [mode]: '显式交接的目标会话不存在或已被删除，已停止旧会话恢复。'
          }))
        }
      }
    } catch (caughtError) {
      setSessionErrors((current) => ({
        ...current,
        [mode]: caughtError instanceof Error ? caughtError.message : '读取本地会话失败。'
      }))
    } finally {
      if (sessionLoadGenerationRef.current[mode] === generation) {
        setSessionLoadingModes((current) => ({ ...current, [mode]: false }))
      }
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
      workflowId: session.workflowId,
      workbenchPhase: session.workbenchPhase,
      stage: session.stage,
      sequence: session.sequence,
      entryKey: session.entryKey,
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
    const summary = sessionSummariesRef.current[editorMode].find(
      (session) =>
        session.id === handoff.targetSessionId &&
        session.threadId === handoff.targetConversationThreadId &&
        session.workbenchPhase === phase &&
        session.revisionContext?.impactInteractionId === handoff.impactInteractionId &&
        (handoff.kind === 'formal_revision' ||
          (handoff.kind === 'revision_planning' &&
            session.stage === 'PLAN' &&
            session.revisionContext.sessionRole === 'design' &&
            session.revisionContext.changeId === handoff.changeId) ||
          (handoff.kind === 'revision_development' &&
            session.stage === 'DEVELOPMENT' &&
            session.revisionContext.sessionRole === 'development' &&
            session.revisionContext.changeId === handoff.changeId))
    )
    if (!summary) throw new Error('目标二次修改会话不存在、身份不匹配或已被删除。')
    explicitPhaseSessionTargetsRef.current[editorMode] = {
      ...explicitPhaseSessionTargetsRef.current[editorMode],
      [phase]: summary.id
    }
    try {
      await openChatSession(editorMode, summary.id, phase, true)
    } catch (error) {
      if (explicitPhaseSessionTargetsRef.current[editorMode]?.[phase] === summary.id) {
        delete explicitPhaseSessionTargetsRef.current[editorMode]?.[phase]
      }
      throw error
    }
  }

  /** 清空当前会话选择，用于实体设计确认后回到实体信息展示界面。 */
  const clearActiveSession = (): void => {
    setActiveSessionIds((current) =>
      withSelectedSessionForPhase(current, editorMode, workbenchPhase, undefined)
    )
    setPersistedActiveSessionId(application.id, editorMode, workbenchPhase, undefined)
    onCloseRightPanel()
  }

  /** 创建只归属于工作台阶段的独立会话。 */
  const createNewSession = async (options?: {
    activate?: boolean
    entryKey?: string
    title?: string
    workbenchPhase?: WorkbenchPhase
    revisionContext?: ChatSessionRevisionContext
    recoveryExecutionRunId?: string
  }): Promise<SessionIdentity> => {
    if (!application.workspaceRoot) {
      throw new Error('创建会话前需要选择工作目录。')
    }

    const sessionWorkbenchPhase = options?.workbenchPhase || workbenchPhase
    const session = await createChatSession({
      workspaceRoot: application.workspaceRoot,
      workflowId: application.id,
      editorMode,
      workbenchPhase: sessionWorkbenchPhase,
      entryKey: options?.entryKey,
      title: options?.title || '新对话',
      revisionContext: options?.revisionContext,
      recoveryExecutionRunId: options?.recoveryExecutionRunId
    })
    if (session.editorMode !== editorMode) {
      throw new Error('同一阶段入口已由另一个编辑模式创建，不能重复接管。')
    }
    const identity = createSessionIdentity({
      workspaceRoot: application.workspaceRoot,
      editorMode,
      sessionId: session.id,
      threadId: session.threadId,
      workflowId: session.workflowId,
      workbenchPhase: session.workbenchPhase,
      stage: session.stage,
      sequence: session.sequence,
      entryKey: session.entryKey,
      revisionContext: session.revisionContext
    })
    const agUiSession = new AgUiChatSession(session.threadId)

    registerSession(identity, session.messages, agUiSession)
    setDraftByKey(identity.key, '')
    if (options?.activate !== false) {
      setActiveSessionIds((current) =>
        withSelectedSessionForPhase(current, editorMode, sessionWorkbenchPhase, session.id)
      )
      setPersistedActiveSessionId(application.id, editorMode, sessionWorkbenchPhase, session.id)
      onCloseRightPanel()
    }

    try {
      replaceSessionSummary(editorMode, {
        ...session,
        messageCount: session.messages.length
      })
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

  /** 创建或恢复同一阶段入口的可见会话；Graph checkpoint 与该会话 Thread 始终分离。 */
  const ensurePlanningSession = async (
    entryKey: string,
    phase: WorkbenchPhase = 'product',
    revisionContext?: ChatSessionRevisionContext,
    _sourceIdentity?: SessionIdentity
  ): Promise<SessionIdentity> => {
    void _sourceIdentity
    const normalizedEntryKey = entryKey.trim()
    if (!normalizedEntryKey) throw new Error('阶段入口标识不能为空。')
    const sameThreadSessions = sessionSummaries[editorMode].filter(
      (session) =>
        session.workflowId === application.id &&
        session.workbenchPhase === phase &&
        (session.entryKey === normalizedEntryKey || session.threadId === normalizedEntryKey)
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
        const pickedKey = sessionRuntimeKey(application.workspaceRoot || '', editorMode, picked.id)
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
        return identity
      }
    }
    const identity = await createNewSession({
      entryKey: normalizedEntryKey,
      title: phase === 'planning' ? '规划 Agent' : '产品 Agent',
      workbenchPhase: phase,
      revisionContext
    })
    planningSessionActivatedRef.current = true
    return identity
  }

  /** 为同一 change 创建或复用唯一的独立开发会话；准备阶段不切换当前规划会话。 */
  const ensureRevisionDevelopmentSession = async (
    source: SessionIdentity,
    continuation: WorkflowRevisionContinuation
  ): Promise<SessionIdentity> => {
    const existing = revisionDevelopmentSessionForContinuation(
      sessionSummariesRef.current[editorMode],
      source,
      continuation
    )
    if (existing) return loadChatSessionIdentity(editorMode, existing.id)
    const revisionContext = createRevisionDevelopmentSessionContext(source, continuation)
    return createNewSession({
      activate: false,
      entryKey: `revision-development:${continuation.changeId}:${continuation.technicalPlanSha256}`,
      title: '二次修改 · 开发 Agent',
      workbenchPhase: 'development',
      revisionContext
    })
  }

  /** 为已消费 continuation 但本地记录缺失的 execution 恢复同 thread 开发会话。 */
  const recoverRevisionDevelopmentSession = async (
    source: SessionIdentity,
    lifecycle: ApplicationLifecycle,
    execution: WorkbenchExecution
  ): Promise<SessionIdentity> => {
    const active = lifecycle.activeFormalRevision
    const technicalPlanSha256 = String(active?.technicalPlanSha256 || '').trim()
    if (!active || !technicalPlanSha256 || !execution.runId || !execution.threadId) {
      throw new Error('当前 lifecycle 缺少可恢复的 revision development execution。')
    }
    const continuation: WorkflowRevisionContinuation = {
      changeId: active.changeId,
      formalBranch: active.formalBranch,
      action: 'continue_revision_build',
      token: 'recovery-only',
      technicalPlanSha256
    }
    const existing = revisionDevelopmentSessionForContinuation(
      sessionSummariesRef.current[editorMode],
      source,
      continuation
    )
    if (existing) return loadChatSessionIdentity(editorMode, existing.id)
    const revisionContext = createRevisionDevelopmentSessionContext(source, continuation)
    const identity = await createNewSession({
      activate: false,
      entryKey: `revision-development:${active.changeId}:${technicalPlanSha256}`,
      title: '二次修改 · 开发 Agent',
      workbenchPhase: 'development',
      revisionContext,
      recoveryExecutionRunId: execution.runId
    })
    if (identity.threadId !== execution.threadId) {
      throw new Error('恢复后的开发会话 thread 与 lifecycle execution 不匹配。')
    }
    return identity
  }

  /** 仅在开发 Workflow 已成功接管后激活本次 revision 的独立开发会话。 */
  const activateRevisionDevelopmentSession = async (identity: SessionIdentity): Promise<void> => {
    const summary = sessionSummariesRef.current[editorMode].find(
      (session) =>
        session.id === identity.sessionId &&
        session.threadId === identity.threadId &&
        session.workbenchPhase === 'development' &&
        session.stage === 'DEVELOPMENT' &&
        session.revisionContext?.sessionRole === 'development' &&
        session.revisionContext.changeId === identity.revisionContext?.changeId
    )
    if (!summary) throw new Error('二次修改开发会话不存在或身份不匹配。')
    planningSessionActivatedRef.current = false
    // 自动 continuation 与回执按钮共用同一显式目标，切阶段后的恢复不得降级到旧开发会话。
    explicitPhaseSessionTargetsRef.current[editorMode] = {
      ...explicitPhaseSessionTargetsRef.current[editorMode],
      development: summary.id
    }
    try {
      await openChatSession(editorMode, summary.id, 'development', true)
    } catch (error) {
      if (explicitPhaseSessionTargetsRef.current[editorMode]?.development === summary.id) {
        delete explicitPhaseSessionTargetsRef.current[editorMode]?.development
      }
      throw error
    }
  }

  /** 只读入指定会话的身份和消息，不改变当前阶段或当前选中会话。 */
  const loadSessionIdentity = (sessionId: string): Promise<SessionIdentity> =>
    loadChatSessionIdentity(editorMode, sessionId)

  /** 为测试阶段创建独立的空白会话和 AG-UI thread，只保留测试阶段归属。 */
  const createTestSession = async (target: TestPhaseSessionTarget): Promise<SessionIdentity> => {
    const targetLabel = target.targetLabel.trim() || '当前应用'
    try {
      return await createNewSession({ title: `测试：${targetLabel}`, workbenchPhase: 'test' })
    } catch (caughtError) {
      reportSessionError(caughtError)
      throw caughtError
    }
  }

  /** 为审查阶段创建独立空白会话和 AG-UI thread，只保留审查阶段归属。 */
  const createReviewSession = async (
    target: ReviewPhaseSessionTarget
  ): Promise<SessionIdentity> => {
    const targetLabel = target.targetLabel.trim() || '当前应用'
    try {
      return await createNewSession({ title: `审查：${targetLabel}`, workbenchPhase: 'review' })
    } catch (caughtError) {
      reportSessionError(caughtError)
      throw caughtError
    }
  }

  /** 为验收阶段创建独立空白会话和 AG-UI thread，只保留验收阶段归属。 */
  const createAcceptanceSession = async (
    target: AcceptancePhaseSessionTarget
  ): Promise<SessionIdentity> => {
    const targetLabel = target.targetLabel.trim() || '当前应用'
    try {
      return await createNewSession({ title: `验收：${targetLabel}`, workbenchPhase: 'acceptance' })
    } catch (caughtError) {
      reportSessionError(caughtError)
      throw caughtError
    }
  }

  /** 新建对话只进入空白草稿态，首轮真实发送时才创建并持久化历史会话。 */
  const handleCreateSessionFromList = (): void => {
    if (!application.workspaceRoot) return
    const pendingKey = pendingDraftKey(application.workspaceRoot, editorMode)
    setDraftByKey(pendingKey, '')
    setSelectedSkillsByKey(pendingKey, [])
    setActiveSessionIds((current) =>
      withSelectedSessionForPhase(current, editorMode, workbenchPhase, undefined)
    )
    setPersistedActiveSessionId(application.id, editorMode, workbenchPhase, undefined)
    setSessionErrors((current) => ({ ...current, [editorMode]: undefined }))
    onCloseRightPanel()
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

  /** 删除尚未成功进入的预创建 StageSession，并清理其运行时与阶段选择。 */
  const discardPreparedSession = async (identity: SessionIdentity): Promise<void> => {
    if (!application.workspaceRoot || identity.workspaceRoot !== application.workspaceRoot) return
    if (runningSessionsRef.current.has(identity.key)) {
      throw new Error('会话仍在运行，不能回滚阶段入口。')
    }
    const summary = sessionSummariesRef.current[identity.editorMode].find(
      (session) =>
        session.id === identity.sessionId &&
        session.threadId === identity.threadId &&
        session.entryKey === identity.entryKey
    )
    if (!summary) return
    await deleteChatSession(application.workspaceRoot, identity.editorMode, identity.sessionId)
    const nextModeSummaries = sessionSummariesRef.current[identity.editorMode].filter(
      (session) => session.id !== identity.sessionId
    )
    sessionSummariesRef.current = {
      ...sessionSummariesRef.current,
      [identity.editorMode]: nextModeSummaries
    }
    setSessionSummaries((current) => ({
      ...current,
      [identity.editorMode]: current[identity.editorMode].filter(
        (session) => session.id !== identity.sessionId
      )
    }))
    setActiveSessionIds((current) =>
      withoutDeletedSessionSelection(current, identity.editorMode, identity.sessionId)
    )
    if (
      getPersistedActiveSessionId(application.id, identity.editorMode, summary.workbenchPhase) ===
      identity.sessionId
    ) {
      setPersistedActiveSessionId(
        application.id,
        identity.editorMode,
        summary.workbenchPhase,
        undefined
      )
    }
    removeSession(identity.key)
  }

  /** 持久化消息时保留会话原有阶段归属，避免异步完成后被当前顶部阶段改写。 */
  const persistSession = async (input: PersistSessionInput): Promise<void> => {
    if (!application.workspaceRoot) return
    const existingSummary = sessionSummariesRef.current[input.editorMode].find(
      (summary) => summary.id === input.sessionId
    )
    if (!existingSummary) throw new Error('会话尚未通过统一入口创建，不能直接保存。')
    const now = Date.now()
    const session: ChatSessionRecord = {
      id: input.sessionId,
      title:
        input.titleFrom && existingSummary.title === '新对话'
          ? createChatSessionTitle(input.titleFrom)
          : existingSummary?.title || '新对话',
      editorMode: input.editorMode,
      workbenchPhase: existingSummary.workbenchPhase,
      workflowId: existingSummary.workflowId,
      stage: existingSummary.stage,
      sequence: existingSummary.sequence,
      entryKey: existingSummary.entryKey,
      threadId: input.threadId,
      revisionContext: mergeRevisionSessionContext(
        existingSummary?.revisionContext,
        input.revisionContext
      ),
      workspaceRoot: application.workspaceRoot,
      messages: input.messages,
      createdAt: existingSummary.createdAt,
      updatedAt: now
    }
    const summary = await saveChatSession(session)
    replaceSessionSummary(input.editorMode, summary)
  }

  return {
    activeSession,
    activeSessionId,
    agUiSessionsRef,
    createTestSession,
    createReviewSession,
    createAcceptanceSession,
    discardPreparedSession,
    deletingSessionId,
    draft,
    draftKey,
    ensureActiveSession,
    ensurePlanningSession,
    ensureRevisionDevelopmentSession,
    recoverRevisionDevelopmentSession,
    activateRevisionDevelopmentSession,
    getSessionMessages,
    loadSessionIdentity,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    openSessionForPhase,
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
