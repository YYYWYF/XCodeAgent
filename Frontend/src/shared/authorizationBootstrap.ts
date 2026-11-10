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
  executor?: 'builtin_saas'
  initializationStatus?: 'pending' | 'running' | 'waiting' | 'ready' | 'needs_attention' | 'failed' | 'cancelled'
  initializationStage?: 'schema' | 'seed' | 'verify' | 'done'
  schemaReady?: boolean
  seedReady?: boolean
  seedOperationId?: string
  seedOperationStatus?: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'UNKNOWN'
  nextAction?: 'continue' | 'lookup_seed' | 'refresh_seed' | 'review' | 'none' | 'resume' | 'retry'
  operationId?: string
  operationStatus?: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'UNKNOWN' | 'SCHEMA_READY'
  initializedTables?: string[]
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
  if (raw.executor === 'builtin_saas') result.executor = 'builtin_saas'
  if (['pending', 'running', 'waiting', 'ready', 'needs_attention', 'failed', 'cancelled'].includes(String(raw.initializationStatus))) {
    result.initializationStatus = raw.initializationStatus as WorkflowAuthorizationBootstrap['initializationStatus']
  }
  if (['schema', 'seed', 'verify', 'done'].includes(String(raw.initializationStage))) {
    result.initializationStage = raw.initializationStage as WorkflowAuthorizationBootstrap['initializationStage']
  }
  if ((raw.nextAction === 'resume' || raw.nextAction === 'retry') && result.executor === 'builtin_saas') result.nextAction = raw.nextAction
  if (['RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN', 'SCHEMA_READY'].includes(String(raw.operationStatus))) {
    result.operationStatus = raw.operationStatus as WorkflowAuthorizationBootstrap['operationStatus']
  }
  if (typeof raw.schemaReady === 'boolean') result.schemaReady = raw.schemaReady
  if (raw.schemaReady === true && Array.isArray(raw.initializedTables) && raw.initializedTables.length === 4 &&
      new Set(raw.initializedTables).size === 4 && raw.initializedTables.every((name) => typeof name === 'string' && /^[a-zA-Z0-9_]{1,64}$/.test(name))) {
    result.initializedTables = [...raw.initializedTables] as string[]
  }
  if (typeof raw.seedReady === 'boolean') result.seedReady = result.schemaReady === true && raw.seedReady
  if (result.executor === 'builtin_saas' && result.schemaReady) {
    if (typeof raw.seedOperationId === 'string' && /^[1-9][0-9]{0,18}$/.test(raw.seedOperationId)) {
      result.seedOperationId = raw.seedOperationId
    }
    if (['RUNNING', 'SUCCEEDED', 'FAILED', 'UNKNOWN'].includes(String(raw.seedOperationStatus))) {
      result.seedOperationStatus = raw.seedOperationStatus as WorkflowAuthorizationBootstrap['seedOperationStatus']
    }
    if (['continue', 'lookup_seed', 'refresh_seed', 'review', 'none'].includes(String(raw.nextAction))) {
      result.nextAction = raw.nextAction as WorkflowAuthorizationBootstrap['nextAction']
    }
  }
  if (typeof raw.operationId === 'string' && /^[1-9][0-9]*$/.test(raw.operationId)) {
    result.operationId = raw.operationId.slice(0, 128)
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
      result.executor !== 'builtin_saas' && raw.ddlAvailable === true &&
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
  status: WorkflowAuthorizationBootstrap['status'],
  result?: WorkflowAuthorizationBootstrap
): string {
  if (result?.executor === 'builtin_saas') {
    if (result.initializationStatus) return result.schemaReady && result.seedReady
      ? '权限数据库初始化已就绪' : ['pending', 'running', 'waiting'].includes(result.initializationStatus)
        ? '权限数据库初始化运行中' : '权限数据库初始化待核对'
    if (result.schemaReady) {
      if (result.seedReady) return '权限表和权限数据已就绪'
      if (result.seedOperationStatus === 'RUNNING' && result.nextAction === 'refresh_seed') return '权限表已就绪，权限数据写入中'
      if (result.nextAction === 'continue') return '权限表已就绪，权限数据待初始化'
      return '权限表已就绪，权限数据待核对'
    }
    return result.operationStatus === 'RUNNING' ? '权限表初始化执行中' : '权限表初始化未完成／待核对'
  }
  return {
    executed: '权限数据库初始化成功',
    reused: '权限数据库初始化成功（复用已有结果）',
    manual_required: '权限数据库待手动初始化',
    failed: '权限数据库初始化未完成',
    skipped: '权限数据库初始化已跳过'
  }[status]
}
