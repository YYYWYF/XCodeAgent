import type {
  ApplicationLifecycle,
  ExecutionRecoveryAvailability,
  ExecutionRecoveryCandidate,
  ExecutionRecoveryProjection
} from '../../typings'
import {
  parseRecoveryFailureDiagnostic,
  parseRecoveryActionPlan,
  type RecoveryExecutionKind
} from '../../service/recoveryActionPlan'

const EXECUTION_RECOVERY_AVAILABILITIES: ReadonlySet<string> = new Set([
  'ready',
  'requires_handler',
  'blocked',
  'awaiting_user'
])

/** 把未知 lifecycle 扩展安全收窄为普通对象，避免 UI 直接信任后端任意字段。 */
function recordValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined
}

/** 严格读取恢复投影中的非空文本，拒绝对象与隐式字符串化。 */
function requiredText(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const normalized = value.trim()
  return normalized || undefined
}

/** 校验单条 Workbench Recovery 候选，并强制要求 Backend-authoritative ActionPlan。 */
function parseExecutionRecoveryCandidate(value: unknown): ExecutionRecoveryCandidate | undefined {
  const candidate = recordValue(value)
  if (!candidate) return undefined
  const sourceRunId = requiredText(candidate.sourceRunId)
  const ownerSessionId = requiredText(candidate.ownerSessionId)
  const threadId = requiredText(candidate.threadId)
  const executionKind = requiredText(candidate.executionKind)
  const executionStatus = requiredText(candidate.executionStatus)
  const availability = requiredText(candidate.availability)
  const reasonCode = requiredText(candidate.reasonCode)
  const message = requiredText(candidate.message)
  const updatedAt = requiredText(candidate.updatedAt)
  if (
    !sourceRunId ||
    !ownerSessionId ||
    !threadId ||
    !executionKind ||
    !['application_planning', 'workbench'].includes(executionKind) ||
    !executionStatus ||
    !['interrupted', 'failed'].includes(executionStatus) ||
    !availability ||
    !EXECUTION_RECOVERY_AVAILABILITIES.has(availability) ||
    typeof candidate.canContinue !== 'boolean' ||
    !reasonCode ||
    !message ||
    !updatedAt
  ) {
    return undefined
  }
  const recoveryActionPlan = parseRecoveryActionPlan(candidate.recoveryActionPlan, {
    threadId,
    sourceRunId,
    executionKind: executionKind as RecoveryExecutionKind
  })
  if (!recoveryActionPlan) return undefined
  const failureDiagnostic = parseRecoveryFailureDiagnostic(candidate.failureDiagnostic)
  if (
    failureDiagnostic === undefined &&
    Object.prototype.hasOwnProperty.call(candidate, 'failureDiagnostic')
  ) {
    return undefined
  }
  const workflowScope = requiredText(candidate.workflowScope)
  const currentNode = requiredText(candidate.currentNode)
  return {
    sourceRunId,
    ownerSessionId,
    threadId,
    executionKind: executionKind as ExecutionRecoveryCandidate['executionKind'],
    ...(workflowScope ? { workflowScope } : {}),
    executionStatus: executionStatus as ExecutionRecoveryCandidate['executionStatus'],
    ...(currentNode ? { currentNode } : {}),
    availability: availability as ExecutionRecoveryAvailability,
    canContinue: candidate.canContinue,
    reasonCode,
    message,
    ...(failureDiagnostic !== undefined ? { failureDiagnostic } : {}),
    recoveryActionPlan,
    updatedAt
  }
}

/** 解析 lifecycle GET 的运行时恢复投影；schema 不匹配时 fail closed。 */
export function executionRecoveryProjection(
  lifecycle?: ApplicationLifecycle
): ExecutionRecoveryProjection | undefined {
  const value = recordValue(lifecycle?.extensions?.executionRecovery)
  if (!value || value.schemaVersion !== 'execution-recovery.v1' || !Array.isArray(value.candidates)) {
    return undefined
  }
  const candidates = value.candidates
    .map(parseExecutionRecoveryCandidate)
    .filter((candidate): candidate is ExecutionRecoveryCandidate => Boolean(candidate))
  if (typeof value.generatedAt !== 'string' || !value.generatedAt.trim()) return undefined
  return {
    schemaVersion: 'execution-recovery.v1',
    generatedAt: value.generatedAt,
    candidates,
    error: value.error as ExecutionRecoveryProjection["error"]
  }
}

/** 只按当前 stage session 的 sessionId 选择最新 Workbench 候选，拒绝跨会话串卡。 */
export function executionRecoveryForSession(
  lifecycle: ApplicationLifecycle | undefined,
  sessionId: string | undefined
): ExecutionRecoveryCandidate | undefined {
  if (executionRecoveryReadError(lifecycle)) return undefined
  const normalizedSessionId = String(sessionId || '').trim()
  if (!normalizedSessionId) return undefined
  return executionRecoveryProjection(lifecycle)?.candidates
    .filter((candidate) => candidate.ownerSessionId === normalizedSessionId)
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0]
}

/** 读取失败时保留重试入口，但旧候选仅作展示证据，不能授权执行。 */
export function executionRecoveryReadError(lifecycle?: ApplicationLifecycle): string | undefined {
  const error = recordValue(recordValue(lifecycle?.extensions?.executionRecovery)?.error)
  return error?.code === 'RECOVERY_PROJECTION_READ_FAILED'
    ? requiredText(error.message) || '恢复状态读取失败，请重试同步。' : undefined
}
