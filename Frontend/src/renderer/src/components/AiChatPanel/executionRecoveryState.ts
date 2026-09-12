import type {
  ApplicationLifecycle,
  ExecutionRecoveryAvailability,
  ExecutionRecoveryCandidate,
  ExecutionRecoveryProjection
} from '../../typings'

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

/** 校验单条恢复候选只包含当前 UI 需要的公开字段。 */
function parseExecutionRecoveryCandidate(value: unknown): ExecutionRecoveryCandidate | undefined {
  const candidate = recordValue(value)
  if (!candidate) return undefined
  const sourceRunId = String(candidate.sourceRunId || '').trim()
  const threadId = String(candidate.threadId || '').trim()
  const executionKind = String(candidate.executionKind || '')
  const executionStatus = String(candidate.executionStatus || '')
  const availability = String(candidate.availability || '')
  const reasonCode = String(candidate.reasonCode || '').trim()
  const message = String(candidate.message || '').trim()
  const updatedAt = String(candidate.updatedAt || '').trim()
  if (
    !sourceRunId ||
    !threadId ||
    !['application_planning', 'workbench'].includes(executionKind) ||
    executionStatus !== 'interrupted' ||
    !EXECUTION_RECOVERY_AVAILABILITIES.has(availability) ||
    typeof candidate.canContinue !== 'boolean' ||
    !reasonCode ||
    !message ||
    !updatedAt
  ) {
    return undefined
  }
  const workflowScope =
    typeof candidate.workflowScope === 'string' && candidate.workflowScope.trim()
      ? candidate.workflowScope
      : undefined
  const currentNode =
    typeof candidate.currentNode === 'string' && candidate.currentNode.trim()
      ? candidate.currentNode
      : undefined
  return {
    sourceRunId,
    threadId,
    executionKind: executionKind as ExecutionRecoveryCandidate['executionKind'],
    ...(workflowScope ? { workflowScope } : {}),
    executionStatus: 'interrupted',
    ...(currentNode ? { currentNode } : {}),
    availability: availability as ExecutionRecoveryAvailability,
    canContinue: candidate.canContinue,
    reasonCode,
    message,
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
    candidates
  }
}

/** 只按当前 stage session 的 threadId 选择最新候选，拒绝跨会话串卡。 */
export function executionRecoveryForSession(
  lifecycle: ApplicationLifecycle | undefined,
  threadId: string | undefined
): ExecutionRecoveryCandidate | undefined {
  const normalizedThreadId = String(threadId || '').trim()
  if (!normalizedThreadId) return undefined
  return executionRecoveryProjection(lifecycle)?.candidates
    .filter((candidate) => candidate.threadId === normalizedThreadId)
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))[0]
}
