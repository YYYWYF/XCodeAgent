export type RecoveryExecutionKind = 'application_planning' | 'workbench'

export type RecoveryActionKind =
  | 'continue_checkpoint'
  | 'retry_failed_node'
  | 'retry_business_node'
  | 'retry_operation'
  | 'restart_stage'
  | 'reconcile_state'
  | 'await_user'
  | 'needs_attention'

export type RecoveryIncidentStatus = 'recoverable' | 'awaiting_user' | 'needs_attention'

export type RecoveryFailureDiagnostic = {
  sourceRunId: string
  origin: string
  code: string
  operation?: string | null
  dependency?: string | null
  provider?: string | null
  model?: string | null
  stage?: 'model_setup' | 'model_invoke' | 'unit_session' | null
  httpStatus?: number | null
  providerErrorCode?: string | null
  message?: string | null
}

export type RecoveryAction = {
  actionId: string
  kind: RecoveryActionKind
  /** Backend 选择的恢复目标，仅用于展示，绝不作为 Frontend request authority。 */
  targetNode?: string
  label: string
  description: string
  requiresConfirmation: boolean
}

export type RecoveryActionPlan<
  TKind extends RecoveryExecutionKind = RecoveryExecutionKind
> = {
  schemaVersion: 'recovery-action-plan.v1'
  incidentId: string
  sourceRunId: string
  threadId: string
  executionKind: TKind
  status: RecoveryIncidentStatus
  reasonCode: string
  message: string
  primaryAction?: RecoveryAction | null
  alternateActions: RecoveryAction[]
}

const RECOVERY_ACTION_KINDS = new Set<RecoveryActionKind>([
  'continue_checkpoint',
  'retry_failed_node',
  'retry_business_node',
  'retry_operation',
  'restart_stage',
  'reconcile_state',
  'await_user',
  'needs_attention'
])

const RECOVERY_INCIDENT_STATUSES = new Set<RecoveryIncidentStatus>([
  'recoverable',
  'awaiting_user',
  'needs_attention'
])

/** 从恢复协议中读取非空文本，避免把任意对象转成用户可见字符串。 */
function requiredRecoveryText(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const normalized = value.trim()
  return normalized || undefined
}

/** 按公开字段解析实时和持久化的失败证据，并拒绝不合规来源身份。 */
export function parseRecoveryFailureDiagnostic(value: unknown): RecoveryFailureDiagnostic | null | undefined {
  if (value === null) return null
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const candidate = value as Record<string, unknown>
  const sourceRunId = requiredRecoveryText(candidate.sourceRunId)
  const origin = requiredRecoveryText(candidate.origin)
  const code = requiredRecoveryText(candidate.code)
  if (!sourceRunId || !origin || !code) return undefined
  const optionalText = (field: unknown): string | null | undefined => {
    if (field === null) return null
    return field === undefined ? undefined : requiredRecoveryText(field) ?? null
  }
  const httpStatus =
    candidate.httpStatus === null
      ? null
      : typeof candidate.httpStatus === 'number' &&
          Number.isInteger(candidate.httpStatus) &&
          candidate.httpStatus >= 100 &&
          candidate.httpStatus <= 599
        ? candidate.httpStatus
        : candidate.httpStatus === undefined
          ? undefined
          : null
  const providerErrorCode = optionalText(candidate.providerErrorCode)
  const stage =
    candidate.stage === null
      ? null
      : candidate.stage === 'model_setup' ||
          candidate.stage === 'model_invoke' ||
          candidate.stage === 'unit_session'
        ? candidate.stage
        : undefined
  return {
    sourceRunId,
    origin,
    code,
    operation: optionalText(candidate.operation),
    dependency: optionalText(candidate.dependency),
    provider: optionalText(candidate.provider),
    model: optionalText(candidate.model),
    ...(stage !== undefined ? { stage } : {}),
    ...(httpStatus !== undefined ? { httpStatus } : {}),
    providerErrorCode: typeof providerErrorCode === 'string' &&
      /^[A-Za-z0-9_.-]{1,64}$/.test(providerErrorCode)
      ? providerErrorCode : null,
    message: optionalText(candidate.message)
  }
}

/** 严格解析一个 Backend-authoritative 恢复动作。 */
export function parseRecoveryAction(value: unknown): RecoveryAction | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const candidate = value as Record<string, unknown>
  const actionId = requiredRecoveryText(candidate.actionId)
  const kind = requiredRecoveryText(candidate.kind)
  const label = requiredRecoveryText(candidate.label)
  const description = requiredRecoveryText(candidate.description)
  const targetNode = requiredRecoveryText(candidate.targetNode)
  if (
    !actionId ||
    !kind ||
    !RECOVERY_ACTION_KINDS.has(kind as RecoveryActionKind) ||
    !label ||
    !description ||
    typeof candidate.requiresConfirmation !== 'boolean'
  ) {
    return undefined
  }
  return {
    actionId,
    kind: kind as RecoveryActionKind,
    ...(targetNode ? { targetNode } : {}),
    label,
    description,
    requiresConfirmation: candidate.requiresConfirmation
  }
}

/** 严格解析通用 RecoveryActionPlan，并校验 thread/source/executionKind 身份。 */
export function parseRecoveryActionPlan<TKind extends RecoveryExecutionKind = RecoveryExecutionKind>(
  value: unknown,
  outer?: {
    threadId: string
    sourceRunId?: string
    executionKind?: TKind
  }
): RecoveryActionPlan<TKind> | null | undefined {
  if (value === null) return null
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const candidate = value as Record<string, unknown>
  const incidentId = requiredRecoveryText(candidate.incidentId)
  const sourceRunId = requiredRecoveryText(candidate.sourceRunId)
  const threadId = requiredRecoveryText(candidate.threadId)
  const executionKind = requiredRecoveryText(candidate.executionKind)
  const status = requiredRecoveryText(candidate.status)
  const reasonCode = requiredRecoveryText(candidate.reasonCode)
  const message = requiredRecoveryText(candidate.message)
  if (
    candidate.schemaVersion !== 'recovery-action-plan.v1' ||
    !incidentId ||
    !sourceRunId ||
    !threadId ||
    !executionKind ||
    !['application_planning', 'workbench'].includes(executionKind) ||
    (outer?.executionKind !== undefined && executionKind !== outer.executionKind) ||
    !status ||
    !RECOVERY_INCIDENT_STATUSES.has(status as RecoveryIncidentStatus) ||
    !reasonCode ||
    !message ||
    (outer !== undefined && threadId !== outer.threadId) ||
    (outer?.sourceRunId !== undefined && sourceRunId !== outer.sourceRunId) ||
    !Array.isArray(candidate.alternateActions)
  ) {
    return undefined
  }

  const primaryAction =
    candidate.primaryAction === undefined || candidate.primaryAction === null
      ? candidate.primaryAction === null
        ? null
        : undefined
      : parseRecoveryAction(candidate.primaryAction)
  if (candidate.primaryAction !== undefined && candidate.primaryAction !== null && !primaryAction) {
    return undefined
  }
  const alternateActions: RecoveryAction[] = []
  for (const action of candidate.alternateActions) {
    const parsed = parseRecoveryAction(action)
    if (!parsed) return undefined
    alternateActions.push(parsed)
  }
  if (status === 'recoverable' && !primaryAction) return undefined
  return {
    schemaVersion: 'recovery-action-plan.v1',
    incidentId,
    sourceRunId,
    threadId,
    executionKind: executionKind as TKind,
    status: status as RecoveryIncidentStatus,
    reasonCode,
    message,
    ...(primaryAction !== undefined ? { primaryAction } : {}),
    alternateActions
  }
}
