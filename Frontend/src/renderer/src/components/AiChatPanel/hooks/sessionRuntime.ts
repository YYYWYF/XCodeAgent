import type { EditorMode } from '../../../typings'
import type { WorkbenchPhase } from '../../../workbenchPhase'
import type {
  AgentStage,
  ChatSessionRevisionContext,
  ChatSessionSummary
} from '../../../service/chatSessions'

export type SessionIdentity = {
  key: string
  sessionId: string
  threadId: string
  workflowId: string
  workbenchPhase: WorkbenchPhase
  stage?: AgentStage
  sequence?: number
  entryKey?: string
  revisionContext?: ChatSessionRevisionContext
  editorMode: EditorMode
  workspaceRoot: string
}

export type SessionRunStatus = 'starting' | 'running' | 'stopping'

export type SessionExecutionEntry = {
  identity: SessionIdentity
  status: SessionRunStatus
  conversation: boolean
}

export function sessionRuntimeKey(
  workspaceRoot: string,
  editorMode: EditorMode,
  sessionId: string
): string {
  return JSON.stringify([workspaceRoot, editorMode, sessionId])
}

/** 判断运行态键是否属于指定工作区，用于项目移入回收站时清理全部会话内存。 */
export function sessionRuntimeKeyBelongsToWorkspace(
  sessionKey: string,
  workspaceRoot: string
): boolean {
  try {
    const value: unknown = JSON.parse(sessionKey)
    return Array.isArray(value) && value[0] === workspaceRoot
  } catch {
    return false
  }
}

export function createSessionIdentity(input: {
  workspaceRoot: string
  editorMode: EditorMode
  sessionId: string
  threadId: string
  workflowId: string
  workbenchPhase: WorkbenchPhase
  stage?: AgentStage
  sequence?: number
  entryKey?: string
  revisionContext?: ChatSessionRevisionContext
}): SessionIdentity {
  return {
    ...input,
    key: sessionRuntimeKey(input.workspaceRoot, input.editorMode, input.sessionId)
  }
}

/** 判断两个会话是否竞争同一应用阶段的单会话执行权，不按编辑模式拆锁。 */
export function isSameSessionExecutionScope(
  left: SessionIdentity,
  right: SessionIdentity
): boolean {
  return (
    left.workspaceRoot === right.workspaceRoot &&
    left.workflowId === right.workflowId &&
    left.workbenchPhase === right.workbenchPhase
  )
}

/** 判断当前选中会话是否就是阶段执行权持有者，避免用局部渲染状态推断所有权。 */
export function isSessionExecutionOwner(
  execution: SessionExecutionEntry | undefined,
  identity: SessionIdentity | undefined
): boolean {
  return Boolean(execution && identity && execution.identity.key === identity.key)
}

export function pendingDraftKey(workspaceRoot: string, editorMode: EditorMode): string {
  return sessionRuntimeKey(workspaceRoot, editorMode, '__new__')
}

export function sessionIdentityFromSummary(
  summary: ChatSessionSummary | undefined,
  editorMode: EditorMode,
  workspaceRoot: string
): SessionIdentity | undefined {
  if (!summary || !workspaceRoot) return undefined
  return createSessionIdentity({
    workspaceRoot,
    editorMode,
    sessionId: summary.id,
    threadId: summary.threadId,
    workflowId: summary.workflowId,
    workbenchPhase: summary.workbenchPhase,
    stage: summary.stage,
    sequence: summary.sequence,
    entryKey: summary.entryKey,
    revisionContext: summary.revisionContext
  })
}
