import type { EditorMode } from '../../../typings'
import type {
  AgentStage,
  ChatSessionTargetType,
  ChatSessionRevisionContext,
  ChatSessionSummary
} from '../../../service/chatSessions'

export type SessionIdentity = {
  key: string
  sessionId: string
  threadId: string
  workflowId: string
  targetType: ChatSessionTargetType
  stage?: AgentStage
  sequence?: number
  entryKey?: string
  apiContractId?: string
  endpointId?: string
  endpointLabel?: string
  entityId?: string
  entityLabel?: string
  pageId?: string
  revisionContext?: ChatSessionRevisionContext
  editorMode: EditorMode
  workspaceRoot: string
}

export type SessionTargetBinding = {
  pageId?: string
  endpointContext?: {
    apiContractId: string
    endpointId: string
    endpointLabel: string
  }
  entityContext?: {
    entityId: string
    entityLabel: string
  }
}

type SessionTargetIdentity = Pick<
  SessionIdentity,
  'targetType' | 'pageId' | 'apiContractId' | 'endpointId' | 'entityId'
>

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
  targetType: ChatSessionTargetType
  stage?: AgentStage
  sequence?: number
  entryKey?: string
  apiContractId?: string
  endpointId?: string
  endpointLabel?: string
  entityId?: string
  entityLabel?: string
  pageId?: string
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
    targetType: summary.targetType,
    stage: summary.stage,
    sequence: summary.sequence,
    entryKey: summary.entryKey,
    apiContractId: summary.apiContractId,
    endpointId: summary.endpointId,
    endpointLabel: summary.endpointLabel,
    entityId: summary.entityId,
    entityLabel: summary.entityLabel,
    pageId: summary.pageId,
    revisionContext: summary.revisionContext
  })
}

/** 提取会话的业务目标绑定，供阶段转接创建新会话时继承页面、接口或实体归属。 */
export function inheritedSessionTargetBinding(
  identity: SessionIdentity | undefined
): SessionTargetBinding {
  if (!identity || identity.targetType === 'workflow') return {}
  if (identity.targetType === 'page') {
    if (!identity.pageId) throw new Error('页面会话缺少页面标识，无法转接。')
    return { pageId: identity.pageId }
  }
  if (identity.targetType === 'api') {
    if (!identity.apiContractId || !identity.endpointId) {
      throw new Error('接口会话缺少接口标识，无法转接。')
    }
    return {
      endpointContext: {
        apiContractId: identity.apiContractId,
        endpointId: identity.endpointId,
        endpointLabel: identity.endpointLabel || identity.endpointId
      }
    }
  }
  if (!identity.entityId) throw new Error('实体会话缺少实体标识，无法转接。')
  return {
    entityContext: {
      entityId: identity.entityId,
      entityLabel: identity.entityLabel || identity.entityId
    }
  }
}

/** 判断两个会话是否绑定同一个工作流、页面、接口或实体目标。 */
export function hasSameSessionTargetBinding(
  left: SessionTargetIdentity,
  right: SessionTargetIdentity
): boolean {
  return (
    left.targetType === right.targetType &&
    left.pageId === right.pageId &&
    left.apiContractId === right.apiContractId &&
    left.endpointId === right.endpointId &&
    left.entityId === right.entityId
  )
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
