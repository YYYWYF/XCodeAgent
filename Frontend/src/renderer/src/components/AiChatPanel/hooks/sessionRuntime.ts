import type { EditorMode } from '../../../typings'
import type { WorkbenchPhase } from '../../../workbenchPhase'
import type {
  AgentStage,
  ChatSessionDevelopmentTarget,
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
  developmentTarget?: ChatSessionDevelopmentTarget
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
  developmentTarget?: ChatSessionDevelopmentTarget
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

/** 比较两个规范化的页面或 Endpoint 目标是否相同。 */
export function sameDevelopmentTarget(
  left: ChatSessionDevelopmentTarget | undefined,
  right: ChatSessionDevelopmentTarget | undefined
): boolean {
  if (!left || !right) return !left && !right
  if (left.type !== right.type) return false
  return left.type === 'page'
    ? right.type === 'page' && left.pageId === right.pageId
    : right.type === 'endpoint' &&
        left.apiContractId === right.apiContractId &&
        left.endpointId === right.endpointId
}

/** 将会话目标投影为 Workflow 请求所需的显式选择和构建范围。 */
export function developmentTargetWorkflowFields(
  target: ChatSessionDevelopmentTarget | undefined
): {
  selectedPageId?: string
  selectedApiContractId?: string
  selectedEndpointId?: string
  detailTargetType?: 'page' | 'endpoint'
  buildExecutionScope?: {
    type: 'page' | 'endpoint'
    targetId: string
    apiContractId?: string
  }
} {
  if (!target) return {}
  if (target.type === 'page') {
    return {
      selectedPageId: target.pageId,
      detailTargetType: 'page',
      buildExecutionScope: { type: 'page', targetId: target.pageId }
    }
  }
  return {
    selectedApiContractId: target.apiContractId,
    selectedEndpointId: target.endpointId,
    detailTargetType: 'endpoint',
    buildExecutionScope: {
      type: 'endpoint',
      targetId: target.endpointId,
      apiContractId: target.apiContractId
    }
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
    workbenchPhase: summary.workbenchPhase,
    stage: summary.stage,
    sequence: summary.sequence,
    entryKey: summary.entryKey,
    developmentTarget: summary.developmentTarget,
    revisionContext: summary.revisionContext
  })
}
