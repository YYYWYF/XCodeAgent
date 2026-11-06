import { randomUUID } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

export type AgentUiTransferInput = {
  workspace: string
  action: 'plan' | 'initialize' | 'confirm' | 'get' | 'cancel'
  installationPlanId?: string
  planId?: string
  targetSourceRoot?: string
}

/** 静态连接条件仅供审阅；同源会话和真实运行证据须独立验证。 */
export type AgentUiConnectionRequirements = {
  agentId: string
  endpoint: string
  method: 'POST'
  principalSource: 'runtime_anonymous_session'
  healthPath: '/__runtime/health'
  verificationScope: 'static_contract'
  sendEnabled: false
  checks: Array<{
    id: string
    status: 'supported' | 'blocked' | 'not_verified' | 'not_required'
    reason: string
  }>
}

export type AgentUiTransferPlan = {
  planId: string
  readOnly: true
  executionAvailable: true
  workspace: string
  status: 'reviewable' | 'blocked'
  blockers: string[]
  conflicts: string[]
  source: {
    projectRoot: string
    draft: string
    revision: string
    sourceRoot: string
    sourceDigest: string
    buildDigest: string
    controls: Record<string, string>
  }
  target: { projectRoot: string; sourceRoot: string; snapshotSha256: string }
  formal: Record<string, string | null>
  installation: {
    planId: string
    evidence: unknown
    styles: Array<{ path: string; sha256: string }>
  }
  publicApi: {
    packages: Array<{ name: string; version: string; installedDigest: string }>
    catalogSha256: string
    sharedApiSha256: string
  }
  tool: { creatorVersion: string; installedDigest: string; hostIntegrationSha256: string }
  hostIntegration: {
    status: 'reviewable' | 'blocked' | 'unavailable'
    reason: string
    endpoint?: string
    connection?: 'blocked_T27' | 'plain_text'
    connectionRequirements?: AgentUiConnectionRequirements
    pageIds?: string[]
  }
  changes: Array<{
    sourcePath: string
    targetPath: string
    operation: 'add' | 'modify' | 'conflict' | 'unchanged'
    sourceSha256: string
    beforeSha256: string | null
    afterSha256: string
    after: string
    diff: string
    importAdjustments: Array<{
      specifier: string
      after: string
      kind: string
      start: number
      end: number
    }>
  }>
  excluded: Array<{ path: string; reason: string }>
  protected: Record<string, string>
  generatedReferences: Array<{
    sourcePath: string
    reference: string
    targetPath: string
    owner: string
  }>
  initialization: {
    required: boolean
    requiresReplan: boolean
    owner: 'target-public-tool'
    input: { sourceRoot: string; mode: 'platform' }
  }
  validation: string[]
  recovery: string
}

export type AgentUiTransferPayload = {
  threadId: string
  runId: string
  status: 'in_progress' | 'completed' | 'failed'
  plan?: AgentUiTransferPlan
  transfer?: {
    planId: string
    installationPlanId: string
    action: string
    state:
      | 'running'
      | 'stopping'
      | 'initialized'
      | 'verified'
      | 'failed'
      | 'stopped'
      | 'unknown'
      | 'stale'
    message?: string
    recordPath?: string
    recovery?: string
    applied?: string[]
    mapping?: Array<{
      sourcePath: string
      targetPath: string
      sourceSha256: string
      afterSha256: string
    }>
  }
  progress?: { stage: string; message: string }
  error?: { message?: string }
}

/** 通过官方 AG-UI 客户端审阅、执行或查询同一转入；断流绝不自动重放写入。 */
export async function runAgentUiDesignTransfer(
  input: AgentUiTransferInput,
  options: {
    threadId: string
    signal?: AbortSignal
    onUpdate?: (value: AgentUiTransferPayload) => void
  }
): Promise<AgentUiTransferPayload> {
  const runId = randomUUID()
  const origin = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '')
  const agent = createAgUiHttpAgent({
    url: origin
      ? `${origin}/agent-ui-design-transfer/run`
      : '/api/agent/agent-ui-design-transfer/run',
    threadId: options.threadId
  })
  let latest: AgentUiTransferPayload | undefined
  let finished = false
  /** 只接收当前会话、运行、安装与目标；迟到的其他计划不能成为本次结果。 */
  const accept = (value: unknown): void => {
    if (options.signal?.aborted || !value || typeof value !== 'object') return
    const candidate = value as AgentUiTransferPayload
    if (candidate.threadId !== options.threadId || candidate.runId !== runId) return
    if (!['in_progress', 'completed', 'failed'].includes(candidate.status)) return
    if (
      candidate.status === 'completed' &&
      !(input.action === 'plan' ? candidate.plan : candidate.transfer)
    )
      return
    if (
      candidate.plan &&
      ((input.installationPlanId &&
        candidate.plan.installation?.planId !== input.installationPlanId) ||
        typeof candidate.plan.target?.sourceRoot !== 'string' ||
        !candidate.plan.target.sourceRoot.startsWith('src/') ||
        (input.targetSourceRoot && candidate.plan.target.sourceRoot !== input.targetSourceRoot) ||
        !/^[a-f0-9]{64}$/.test(candidate.plan.planId) ||
        candidate.plan.readOnly !== true ||
        candidate.plan.executionAvailable !== true ||
        !['reviewable', 'blocked'].includes(candidate.plan.status))
    )
      return
    if (
      candidate.transfer &&
      (candidate.transfer.planId !== input.planId ||
        ![
          'running',
          'stopping',
          'initialized',
          'verified',
          'failed',
          'stopped',
          'unknown',
          'stale'
        ].includes(candidate.transfer.state) ||
        !['initialize', 'confirm'].includes(candidate.transfer.action) ||
        (['initialize', 'confirm'].includes(input.action) &&
          candidate.transfer.action !== input.action) ||
        (input.installationPlanId &&
          candidate.transfer.installationPlanId !== input.installationPlanId))
    )
      return
    latest = candidate
    options.onUpdate?.(candidate)
  }
  /** 取消当前订阅；后端停止原执行，客户端不隐式重新请求。 */
  const abort = (): void => agent.abortRun()
  options.signal?.addEventListener('abort', abort, { once: true })
  try {
    if (options.signal?.aborted) throw new Error('设计转入规划已取消。')
    const result = await agent.runAgent(
      { runId, forwardedProps: { agentUiDesignTransfer: input } },
      {
        onCustomEvent: ({ event }) => {
          if (event.name === 'agent-ui-design-transfer') accept(event.value)
        },
        onStateSnapshotEvent: ({ event }) =>
          accept((event.snapshot as { agentUiDesignTransfer?: unknown }).agentUiDesignTransfer),
        onRunFinishedEvent: ({ event }) => {
          finished = event.threadId === options.threadId && event.runId === runId
        }
      }
    )
    if (options.signal?.aborted) throw new Error('设计转入规划已取消。')
    accept(
      (result.result as { agentUiDesignTransfer?: unknown } | undefined)?.agentUiDesignTransfer
    )
    if (!finished || !latest || latest.status === 'in_progress') {
      throw new Error('缺少当前计划终态；请查询原任务，写入不会自动重试。')
    }
    return latest
  } finally {
    options.signal?.removeEventListener('abort', abort)
  }
}
