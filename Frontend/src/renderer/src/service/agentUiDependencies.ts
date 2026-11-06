import { randomUUID } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

export type AgentUiDependencyInput = {
  workspace: string
  action: 'plan' | 'confirm' | 'get' | 'cancel'
  source?: 'registry' | 'local-vendor'
  entry?: string
  planId?: string
  reviewId?: string
}

export type AgentUiDependencyPlan = {
  planId: string
  reviewId: string
  source: 'registry' | 'local-vendor'
  manager: 'npm' | 'pnpm'
  managerVersion: string
  lockfile: string
  changes: Array<{ path: string; beforeSha256: string | null; after: string; afterSha256: string }>
  packages: Array<{ name: string; version: string; specifier: string; integrity: string }>
  sourceDependencies: Array<{ name: string; version: string }>
  checks: string[]
  recovery: string
}

export type AgentUiInstallation = {
  planId: string
  threadId: string
  runId: string
  state: 'running' | 'stopping' | 'stopped' | 'failed' | 'unknown' | 'stale' | 'verified'
  message?: string
  applied?: string[]
  commands?: Array<{ stage: string; returncode: number | null; log?: string }>
  recovery?: string
  installation?: {
    lockSha256: string
    packages: Array<{ name: string; version: string; integrity: string }>
  }
  styles?: Array<{ path: string; sha256: string }>
}

export type AgentUiDependencyPayload = {
  threadId: string
  runId: string
  status: 'in_progress' | 'completed' | 'failed'
  plan?: AgentUiDependencyPlan
  installation?: AgentUiInstallation
  progress?: { stage: string; message: string }
  error?: { message?: string }
}

/** 通过官方 AG-UI 客户端准备目标依赖；连接中断后由调用者查询原计划，不重发安装。 */
export async function runAgentUiDependencies(
  input: AgentUiDependencyInput,
  options: {
    threadId: string
    signal?: AbortSignal
    onUpdate?: (value: AgentUiDependencyPayload) => void
  }
): Promise<AgentUiDependencyPayload> {
  const runId = randomUUID()
  const origin = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '')
  const agent = createAgUiHttpAgent({
    url: origin ? `${origin}/agent-ui-dependencies/run` : '/api/agent/agent-ui-dependencies/run',
    threadId: options.threadId
  })
  let latest: AgentUiDependencyPayload | undefined
  /** 只接受本次标准流中的业务投影，不让迟到的其他运行污染当前计划。 */
  const accept = (value: unknown): void => {
    if (!value || typeof value !== 'object') return
    const candidate = value as AgentUiDependencyPayload
    if (candidate.runId !== runId || candidate.threadId !== options.threadId) return
    if (!['in_progress', 'completed', 'failed'].includes(candidate.status)) return
    if (input.planId && candidate.installation && candidate.installation.planId !== input.planId)
      return
    latest = { ...latest, ...candidate }
    options.onUpdate?.(latest)
  }
  /** 中止订阅，由后端等待当前安装进程收口；取消回执仍须读取原计划。 */
  const abort = (): void => agent.abortRun()
  options.signal?.addEventListener('abort', abort, { once: true })
  try {
    if (options.signal?.aborted) throw new Error('依赖操作订阅已关闭，请查询原计划状态。')
    const result = await agent.runAgent(
      { runId, forwardedProps: { agentUiDependencies: input } },
      {
        onCustomEvent: ({ event }) => {
          if (event.name === 'agent-ui-dependencies') accept(event.value)
        },
        onStateSnapshotEvent: ({ event }) =>
          accept((event.snapshot as { agentUiDependencies?: unknown }).agentUiDependencies)
      }
    )
    accept((result.result as { agentUiDependencies?: unknown } | undefined)?.agentUiDependencies)
    if (!latest || latest.status === 'in_progress') {
      throw new Error('依赖操作缺少终态证据，请查询原计划状态。')
    }
    return latest
  } finally {
    options.signal?.removeEventListener('abort', abort)
  }
}
