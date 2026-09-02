import type { EditorMode } from '../../../typings'
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
  stage?: AgentStage
  sequence?: number
  entryKey?: string
  revisionContext?: ChatSessionRevisionContext
  editorMode: EditorMode
  workspaceRoot: string
}

export type SessionRunStatus = 'running' | 'stopping'

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
    stage: summary.stage,
    sequence: summary.sequence,
    entryKey: summary.entryKey,
    revisionContext: summary.revisionContext
  })
}
