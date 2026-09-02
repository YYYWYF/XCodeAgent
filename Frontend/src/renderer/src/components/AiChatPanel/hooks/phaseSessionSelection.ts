import type { EditorMode } from '../../../typings'
import type { WorkbenchPhase } from '../../../workbenchPhase'

export type PhaseSessionSelection = Partial<
  Record<EditorMode, Partial<Record<WorkbenchPhase, string>>>
>

/** 只返回归属于指定工作台阶段的会话，避免历史确认卡跨阶段显示。 */
export function sessionsForWorkbenchPhase<
  Session extends { workbenchPhase: WorkbenchPhase }
>(sessions: Session[], phase: WorkbenchPhase): Session[] {
  return sessions.filter((session) => session.workbenchPhase === phase)
}

type RestorablePhaseSession = {
  id: string
  workbenchPhase: WorkbenchPhase
  messageCount: number
}

/** 按显式交接、持久化选择、阶段默认值的顺序解析唯一恢复会话。 */
export function sessionToRestoreForPhase<Session extends RestorablePhaseSession>(
  sessions: Session[],
  phase: WorkbenchPhase,
  explicitSessionId?: string,
  persistedSessionId?: string
): Session | undefined {
  const phaseSessions = sessionsForWorkbenchPhase(sessions, phase)
  if (explicitSessionId) {
    // 显式阶段交接必须 fail closed；目标缺失时不能退回旧 persisted session。
    return phaseSessions.find((session) => session.id === explicitSessionId)
  }
  const persistedSession = persistedSessionId
    ? phaseSessions.find((session) => session.id === persistedSessionId)
    : undefined
  if (persistedSession) return persistedSession

  return phaseSessions.find((session) => session.messageCount > 0) || phaseSessions[0]
}

/** 读取指定编辑模式和工作台阶段各自选中的会话，避免跨阶段复用最后会话。 */
export function selectedSessionIdForPhase(
  selection: PhaseSessionSelection,
  editorMode: EditorMode,
  phase: WorkbenchPhase
): string | undefined {
  return selection[editorMode]?.[phase]
}

/** 仅更新指定阶段的当前会话，保留其他阶段的会话历史选择。 */
export function withSelectedSessionForPhase(
  selection: PhaseSessionSelection,
  editorMode: EditorMode,
  phase: WorkbenchPhase,
  sessionId: string | undefined
): PhaseSessionSelection {
  const nextModeSelection = { ...selection[editorMode] }
  if (sessionId) nextModeSelection[phase] = sessionId
  else delete nextModeSelection[phase]
  return { ...selection, [editorMode]: nextModeSelection }
}

/** 清空指定编辑模式的全部阶段选择，供工作区切换或无会话场景使用。 */
export function withoutEditorModeSessionSelection(
  selection: PhaseSessionSelection,
  editorMode: EditorMode
): PhaseSessionSelection {
  return { ...selection, [editorMode]: {} }
}

/** 从全部阶段选择中移除已删除会话，避免切回阶段后引用不存在的历史。 */
export function withoutDeletedSessionSelection(
  selection: PhaseSessionSelection,
  editorMode: EditorMode,
  sessionId: string
): PhaseSessionSelection {
  const nextModeSelection = Object.fromEntries(
    Object.entries(selection[editorMode] || {}).filter(([, value]) => value !== sessionId)
  ) as Partial<Record<WorkbenchPhase, string>>
  return { ...selection, [editorMode]: nextModeSelection }
}
