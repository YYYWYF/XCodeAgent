export type WorkflowAuthorizationBootstrap = {
  status: 'executed' | 'reused' | 'skipped' | 'failed' | 'manual_required'
  datasourceMode?: 'direct' | 'builtin' | 'dbid' | 'static' | 'external_api' | 'unknown'
  executionAttempted?: boolean
  requiresManualAction?: boolean
  ddlAvailable?: boolean
  ddlPath?: string
  ddl?: string
  message?: string
  failureCategory?: string
  exitCode?: number | null
  logDirectory?: string
  manifestFingerprint?: string
  durationMs?: number
}

/** 实时和历史共用严格白名单，只保留有界文本与合法结果字段。 */
export function readAuthorizationBootstrap(
  value: unknown
): WorkflowAuthorizationBootstrap | undefined {
  if (!value || typeof value !== 'object') return undefined
  const raw = value as Record<string, unknown>
  if (
    !['executed', 'reused', 'skipped', 'failed', 'manual_required'].includes(String(raw.status))
  ) {
    return undefined
  }
  const result: WorkflowAuthorizationBootstrap = {
    status: raw.status as WorkflowAuthorizationBootstrap['status']
  }
  if (
    ['direct', 'builtin', 'dbid', 'static', 'external_api', 'unknown'].includes(
      String(raw.datasourceMode)
    )
  ) {
    result.datasourceMode = raw.datasourceMode as WorkflowAuthorizationBootstrap['datasourceMode']
  }
  for (const key of ['executionAttempted', 'requiresManualAction'] as const) {
    if (typeof raw[key] === 'boolean') result[key] = raw[key]
  }
  for (const key of [
    'message',
    'failureCategory',
    'logDirectory',
    'manifestFingerprint'
  ] as const) {
    if (typeof raw[key] === 'string') result[key] = raw[key].slice(0, 2000)
  }
  result.ddlPath = 'backend/sql/authorization/schema.sql'
  // 超长 SQL 整体拒绝，避免复制截断的 DDL；成功与跳过状态不携带 SQL。
  if (result.status === 'failed' || result.status === 'manual_required') {
    const ddl = raw.ddl
    result.ddlAvailable =
      raw.ddlAvailable === true &&
      typeof ddl === 'string' &&
      Boolean(ddl.trim()) &&
      ddl.length <= 256 * 1024 &&
      new TextEncoder().encode(ddl).length <= 256 * 1024
    if (result.ddlAvailable) result.ddl = ddl as string
  }
  if (
    raw.exitCode === null ||
    (typeof raw.exitCode === 'number' && Number.isInteger(raw.exitCode))
  ) {
    result.exitCode = raw.exitCode
  }
  if (
    typeof raw.durationMs === 'number' &&
    Number.isFinite(raw.durationMs) &&
    raw.durationMs >= 0
  ) {
    result.durationMs = raw.durationMs
  }
  return result
}

/** 按真实初始化结果生成节点与详情共用标题，节点 completed 不代表数据库成功。 */
export function authorizationBootstrapTitle(
  status: WorkflowAuthorizationBootstrap['status']
): string {
  return {
    executed: '权限数据库初始化成功',
    reused: '权限数据库初始化成功（复用已有结果）',
    manual_required: '权限数据库待手动初始化',
    failed: '权限数据库初始化未完成',
    skipped: '权限数据库初始化已跳过'
  }[status]
}
