import type { EditorMode } from '../../../typings'
import type { ChatSessionSummary } from '../../../service/chatSessions'

export type SessionIdentity = {
  key: string
  sessionId: string
  threadId: string
  apiContractId?: string
  endpointId?: string
  endpointLabel?: string
  entityId?: string
  entityLabel?: string
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
  apiContractId?: string
  endpointId?: string
  endpointLabel?: string
  entityId?: string
  entityLabel?: string
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
    apiContractId: summary.apiContractId,
    endpointId: summary.endpointId,
    endpointLabel: summary.endpointLabel,
    entityId: summary.entityId,
    entityLabel: summary.entityLabel,
    pageId: summary.pageId
  })
}

/** 查找可直接打开的接口会话；空白历史不应阻止目标切换后显示详细设计挡板。 */
export function selectableEndpointSessionId(
  sessions: ChatSessionSummary[],
  apiContractId: string,
  endpointId: string
): string | undefined {
  const normalizedApiContractId = apiContractId.trim()
  const normalizedEndpointId = endpointId.trim()
  if (!normalizedApiContractId || !normalizedEndpointId) return undefined
  return sessions.find(
    (session) =>
      session.apiContractId === normalizedApiContractId &&
      session.endpointId === normalizedEndpointId &&
      session.messageCount > 0
  )?.id
}

/** 查找可直接打开的实体会话；空白历史不应阻止目标切换后显示实体信息挡板。 */
export function selectableEntitySessionId(
  sessions: ChatSessionSummary[],
  entityId: string
): string | undefined {
  const normalizedEntityId = entityId.trim()
  if (!normalizedEntityId) return undefined
  return sessions.find(
    (session) =>
      session.entityId === normalizedEntityId &&
      session.messageCount > 0
  )?.id
}

/** 判断运行会话是否属于当前页面、接口或无目标自由对话，禁止跨目标复用运行进度。 */
export function sessionIdentityMatchesTarget(
  identity: SessionIdentity,
  target: {
    apiContractId?: string
    endpointId?: string
    entityId?: string
    pageId?: string
  }
): boolean {
  const apiContractId = target.apiContractId?.trim() || ''
  const endpointId = target.endpointId?.trim() || ''
  const entityId = target.entityId?.trim() || ''
  const pageId = target.pageId?.trim() || ''
  if (apiContractId || endpointId) {
    return identity.apiContractId === apiContractId && identity.endpointId === endpointId
  }
  if (entityId) return identity.entityId === entityId
  if (pageId) return identity.pageId === pageId
  return !identity.pageId && !identity.apiContractId && !identity.endpointId && !identity.entityId
}
