import type { WorkbenchPhase } from './workbenchPhase'

export type WorkbenchArtifactType = 'document' | 'page' | 'endpoint' | 'model'
export type WorkbenchArtifactStatus = 'not-started' | 'in-progress' | 'completed'
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
  apiContractId?: string
  endpointId?: string
  pageId?: string
  title?: string
}

export type WorkbenchArtifactAccess = {
  mode: WorkbenchArtifactAccessMode
  ownerSessionId?: string
  reason: WorkbenchArtifactLockReason
  message: string
}

const PHASE_ORDER: WorkbenchPhase[] = ['product', 'development', 'test']

/** 生成设计文档产物的稳定领域标识。 */
export function documentArtifactId(key: 'requirement-spec' | 'project-plan' | 'code-review'): string {
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

/**
 * 把当前会话契约转换为统一的产物集合；页面依赖接口时两个产物属于同一对话。
 * 标题推断只用于兼容应用级设计/审查历史，会话的页面和接口字段仍是权威来源。
 */
export function artifactIdsForSession(
  session: WorkbenchSessionArtifactIdentity,
  pageEndpointRelations: Record<string, string[]> = {}
): string[] {
  const artifactIds = new Set<string>()
  if (session.pageId) {
    artifactIds.add(pageArtifactId(session.pageId))
    pageEndpointRelations[session.pageId]?.forEach((endpointKey) => {
      const separator = endpointKey.indexOf(':')
      if (separator <= 0) return
      artifactIds.add(endpointArtifactId(endpointKey.slice(0, separator), endpointKey.slice(separator + 1)))
    })
  }
  if (session.apiContractId && session.endpointId) {
    artifactIds.add(endpointArtifactId(session.apiContractId, session.endpointId))
  }
  if ((session.title || '').includes('应用设计')) {
    artifactIds.add(documentArtifactId('requirement-spec'))
    artifactIds.add(documentArtifactId('project-plan'))
  }
  if ((session.title || '').includes('代码审查')) {
    artifactIds.add(documentArtifactId('code-review'))
  }
  return [...artifactIds]
}

/** 比较两个阶段在单向旅程中的先后顺序。 */
export function compareWorkbenchPhases(left: WorkbenchPhase, right: WorkbenchPhase): number {
  return PHASE_ORDER.indexOf(left) - PHASE_ORDER.indexOf(right)
}

/** 从全部对话声明中为每个产物选出唯一且稳定的默认对话。 */
export function resolveArtifactOwners(
  claims: readonly WorkbenchSessionArtifactClaim[]
): Record<string, string> {
  const owners: Record<string, string> = {}
  const orderedClaims = [...claims].sort(
    (left, right) => left.createdAt - right.createdAt || left.sessionId.localeCompare(right.sessionId)
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
  const { artifact, currentPhase, currentSessionId, ownerSessionId, reachedPhase, versionLocked } = input
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
      message: `请先切换到${phaseLabel(artifact.phase)}阶段后再编辑`
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
  if (phase === 'product') return '设计'
  if (phase === 'development') return '开发'
  return '审查'
}
