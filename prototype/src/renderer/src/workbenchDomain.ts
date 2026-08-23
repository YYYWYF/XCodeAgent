import {
  compareWorkbenchPhases,
  WORKBENCH_PHASE_AGENTS,
  WORKBENCH_PHASE_ORDER,
  type WorkbenchPhase
} from './workbenchPhase'

export type WorkbenchArtifactType = 'document' | 'page' | 'endpoint' | 'entity'
export type WorkbenchArtifactStatus = 'not-started' | 'in-progress' | 'completed'
export type WorkbenchSessionKind =
  | 'analysis'
  | 'planning'
  | 'development'
  | 'testing'
  | 'review'
  | 'general'
export type WorkbenchArtifactAccessMode = 'unavailable' | 'read' | 'write'
export type WorkbenchArtifactLockReason =
  | 'future-phase'
  | 'version-locked'
  | 'phase-locked'
  | 'conversation-locked'
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

export type WorkbenchSessionArtifactClaim = {
  artifactIds: readonly string[]
  createdAt: number
  sessionId: string
}

export type WorkbenchSessionArtifactIdentity = {
  artifactIds?: readonly string[]
  apiContractId?: string
  endpointId?: string
  pageId?: string
  sessionKind?: WorkbenchSessionKind
  title?: string
}

export type WorkbenchArtifactAccess = {
  mode: WorkbenchArtifactAccessMode
  ownerSessionId?: string
  reason: WorkbenchArtifactLockReason
  message: string
}

/** 生成设计文档产物的稳定领域标识。 */
export function documentArtifactId(
  key: 'requirement-spec' | 'project-plan' | 'test-report' | 'code-review'
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


/**
 * 把当前会话显式声明转换为统一产物集合；artifactIds 支持一条对话拥有任意多个产物。
 * pageId/API 字段和标题推断只用于兼容旧会话，不再按静态计划依赖提前扩张编辑范围。
 */
export function artifactIdsForSession(session: WorkbenchSessionArtifactIdentity): string[] {
  const artifactIds = new Set((session.artifactIds || []).filter(Boolean))
  if (session.pageId) {
    artifactIds.add(pageArtifactId(session.pageId))
  }
  if (session.apiContractId && session.endpointId) {
    artifactIds.add(endpointArtifactId(session.apiContractId, session.endpointId))
  }
  if (session.sessionKind === 'analysis') {
    artifactIds.add(documentArtifactId('requirement-spec'))
  }
  if (session.sessionKind === 'planning') {
    artifactIds.add(documentArtifactId('project-plan'))
  }
  if (session.sessionKind === 'testing') artifactIds.add(documentArtifactId('test-report'))
  if ((session.title || '').includes('代码审查')) {
    artifactIds.add(documentArtifactId('code-review'))
  }
  return [...artifactIds]
}

/** 从全部对话声明中为每个产物选出唯一且稳定的默认对话。 */
export function resolveArtifactOwners(
  claims: readonly WorkbenchSessionArtifactClaim[]
): Record<string, string> {
  const owners: Record<string, string> = {}
  const orderedClaims = [...claims].sort(
    (left, right) =>
      left.createdAt - right.createdAt || left.sessionId.localeCompare(right.sessionId)
  )
  orderedClaims.forEach((claim) => {
    claim.artifactIds.forEach((artifactId) => {
      if (artifactId && !owners[artifactId]) owners[artifactId] = claim.sessionId
    })
  })
  return owners
}

/**
 * 统一判定产物可用性、版本锁、阶段锁和对话写锁。
 * 优先级固定为：未来阶段 > 版本锁 > 阶段锁 > 对话写锁 > 可编辑。
 */
export function resolveArtifactAccess(input: {
  artifact: WorkbenchArtifact
  currentPhase: WorkbenchPhase
  currentSessionId?: string
  ownerSessionId?: string
  reachedPhase: WorkbenchPhase
  versionLocked: boolean
}): WorkbenchArtifactAccess {
  const { artifact, currentPhase, currentSessionId, ownerSessionId, reachedPhase, versionLocked } =
    input
  if (!artifact.available || compareWorkbenchPhases(artifact.phase, reachedPhase) > 0) {
    return {
      mode: 'unavailable',
      ownerSessionId,
      reason: 'future-phase',
      message: '当前阶段尚未生成该产物'
    }
  }
  if (versionLocked) {
    return {
      mode: 'read',
      ownerSessionId,
      reason: 'version-locked',
      message: '已生成版本中的产物只读'
    }
  }
  if (artifact.phase !== currentPhase) {
    return {
      mode: 'read',
      ownerSessionId,
      reason: 'phase-locked',
      message: `请先切换到${phaseLabel(artifact.phase)}后再编辑`
    }
  }
  if (ownerSessionId && ownerSessionId !== currentSessionId) {
    return {
      mode: 'read',
      ownerSessionId,
      reason: 'conversation-locked',
      message: '编辑权由该产物的默认对话持有'
    }
  }
  return {
    mode: 'write',
    ownerSessionId,
    reason: 'editable',
    message: ownerSessionId ? '当前对话拥有编辑权' : '尚未分配默认对话，可在当前对话中编辑'
  }
}

/** 返回阶段的界面名称，供权限原因统一复用。 */
export function phaseLabel(phase: WorkbenchPhase): string {
  return `${WORKBENCH_PHASE_AGENTS[phase].label}阶段`
}

// 显式引用固定顺序，确保领域模块在阶段枚举扩展时仍保持穷尽性检查。
export { compareWorkbenchPhases, WORKBENCH_PHASE_ORDER }
