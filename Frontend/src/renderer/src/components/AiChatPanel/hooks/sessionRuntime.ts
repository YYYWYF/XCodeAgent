import type { EditorMode } from '../../../typings'
import type { ChatSessionSummary } from '../../../service/chatSessions'

export type SessionIdentity = {
  key: string
  sessionId: string
  threadId: string
  pageId?: string
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

export function createSessionIdentity(input: {
  workspaceRoot: string
  editorMode: EditorMode
  sessionId: string
  threadId: string
  pageId?: string
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
    pageId: summary.pageId
  })
}
