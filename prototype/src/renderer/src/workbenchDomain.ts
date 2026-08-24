import {
  compareWorkbenchPhases,
  WORKBENCH_PHASE_AGENTS,
  WORKBENCH_PHASE_ORDER,
  type WorkbenchPhase
} from './workbenchPhase'
import { agentArtifactId } from './agentDevelopment'

export type WorkbenchArtifactType =
  | 'document'
  | 'page'
  | 'endpoint'
  | 'entity'
  | 'agent'
  | 'model'
/**
 * 开发产物扩展状态：文档类沿用三档；开发对象在后台实现任务接入后增加
 * 实现排队/实现中/待验收/失败四档，由统一后台任务流水推导。
 */
export type WorkbenchArtifactStatus =
  | 'not-started'
  | 'in-progress'
  | 'impl-queued'
  | 'implementing'
  | 'awaiting-review'
  | 'completed'
  | 'failed'
export type WorkbenchArtifactProgress = {
  completed: number
  total: number
}
export type WorkbenchSessionKind =
  | 'analysis'
  | 'planning'
  | 'development'
  | 'testing'
  | 'review'
  | 'acceptance'
  | 'general'
export type WorkbenchArtifactAccessMode = 'unavailable' | 'read' | 'write'
export type WorkbenchArtifactLockReason =
  | 'future-phase'
  | 'version-locked'
  | 'phase-locked'
  | 'editable'

export type WorkbenchArtifact = {
  id: string
  name: string
  path: string
  phase: WorkbenchPhase
  status: WorkbenchArtifactStatus
  type: WorkbenchArtifactType
  available: boolean
}

export type WorkbenchSessionArtifactIdentity = {
  artifactIds?: readonly string[]
  apiContractId?: string
  endpointId?: string
  agentId?: string
  entityIds?: readonly string[]
  pageId?: string
  sessionKind?: WorkbenchSessionKind
  title?: string
}

export type WorkbenchArtifactAccess = {
  mode: WorkbenchArtifactAccessMode
  reason: WorkbenchArtifactLockReason
  message: string
}

/** 生成设计文档产物的稳定领域标识。 */
export function documentArtifactId(
  key: 'requirement-spec' | 'project-plan' | 'code-review'
): string {
  return `document:${key}`
}

/** 生成页面产物的稳定领域标识。 */
export function pageArtifactId(pageId: string): string {
  return `page:${pageId.trim()}`
}

/** 生成接口产物的稳定领域标识，契约和 endpoint 共同确定唯一性。 */
export function endpointArtifactId(apiContractId: string, endpointId: string): string {
  return `endpoint:${apiContractId.trim()}:${endpointId.trim()}`
}

/** 生成实体占位产物的稳定领域标识。 */
export function entityArtifactId(entityId: string): string {
  return `entity:${entityId.trim()}`
}

/** 把会话的显式目标转换为统一产物集合；当前单会话工作流只用它恢复上下文，不据此加写锁。 */
export function artifactIdsForSession(session: WorkbenchSessionArtifactIdentity): string[] {
  const artifactIds = new Set((session.artifactIds || []).filter(Boolean))
  if (session.pageId) artifactIds.add(pageArtifactId(session.pageId))
  if (session.apiContractId && session.endpointId) {
    artifactIds.add(endpointArtifactId(session.apiContractId, session.endpointId))
  }
  if (session.agentId) artifactIds.add(agentArtifactId(session.agentId))
  ;(session.entityIds || []).forEach((entityId) => {
    if (entityId.trim()) artifactIds.add(entityArtifactId(entityId))
  })
  if (session.sessionKind === 'analysis') artifactIds.add(documentArtifactId('requirement-spec'))
  if (session.sessionKind === 'planning') artifactIds.add(documentArtifactId('project-plan'))
  if ((session.title || '').includes('代码审查')) artifactIds.add(documentArtifactId('code-review'))
  return [...artifactIds]
}


/**
 * 统一判定产物可用性、版本锁和阶段锁。
 * 产物写入由活动 Workflow 的 DAG/Task 范围控制，不再由对话身份控制。
 */
export function resolveArtifactAccess(input: {
  artifact: WorkbenchArtifact
  currentPhase: WorkbenchPhase
  reachedPhase: WorkbenchPhase
  versionLocked: boolean
}): WorkbenchArtifactAccess {
  const { artifact, currentPhase, reachedPhase, versionLocked } = input
  if (!artifact.available || compareWorkbenchPhases(artifact.phase, reachedPhase) > 0) {
    return {
      mode: 'unavailable',
      reason: 'future-phase',
      message: '当前阶段尚未生成该产物'
    }
  }
  if (versionLocked) {
    return {
      mode: 'read',
      reason: 'version-locked',
      message: '已生成版本中的产物只读'
    }
  }
  if (artifact.phase !== currentPhase) {
    return {
      mode: 'read',
      reason: 'phase-locked',
      message: `请先切换到${phaseLabel(artifact.phase)}后再编辑`
    }
  }
  return {
    mode: 'write',
    reason: 'editable',
    message: '正式写入由当前 Workflow 的任务范围控制'
  }
}

/** 返回阶段的界面名称，供权限原因统一复用。 */
export function phaseLabel(phase: WorkbenchPhase): string {
  return `${WORKBENCH_PHASE_AGENTS[phase].label}阶段`
}

// 显式引用固定顺序，确保领域模块在阶段枚举扩展时仍保持穷尽性检查。
export { compareWorkbenchPhases, WORKBENCH_PHASE_ORDER }
