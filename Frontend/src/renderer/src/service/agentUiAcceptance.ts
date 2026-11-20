import { randomUUID } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

export type AcceptanceSource = 'code' | 'official-mock' | 'replay' | 'real'
export type AcceptanceOutcome = 'passed' | 'failed' | 'unknown'
export type AcceptanceCase = {
  caseId: string
  fingerprint: string
  pageId: string
  pageLabel: string
  agentId: string
  layer: 'code' | 'visual' | 'connection' | 'business'
  scenario: string
  label: string
  applicable: boolean
  checks: Array<{ id: string; label: string }>
  status: AcceptanceOutcome | 'pending' | 'stale' | 'not_applicable'
  evidence: { method: 'user_observed'; checkedAt: string; source: AcceptanceSource } | null
}
export type AcceptanceMatrix = {
  ready: boolean
  cases: AcceptanceCase[]
  method: 'user_observed'
  automaticExecution: false
}
export type AcceptanceReport = {
  caseId: string
  fingerprint: string
  source: AcceptanceSource
  outcome: AcceptanceOutcome
  checks: string[]
}

/** 用官方 AG-UI SDK 读取或登记人工核验；断流不自动重发，保存不执行业务。 */
export async function runAgentUiAcceptance(
  workspace: string,
  report?: AcceptanceReport,
  signal?: AbortSignal
): Promise<AcceptanceMatrix> {
  const threadId = randomUUID()
  const runId = randomUUID()
  const origin = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '')
  const agent = createAgUiHttpAgent({
    url: origin
      ? `${origin}/agent-runtime-acceptance/run`
      : '/api/agent/agent-runtime-acceptance/run',
    threadId
  })
  let latest:
    | { status?: string; matrix?: AcceptanceMatrix; error?: { message?: string } }
    | undefined
  let finished = false
  /** 只消费本轮标准业务投影，不能让旧线程或迟到结果覆盖当前工程。 */
  const accept = (value: unknown): void => {
    if (!value || typeof value !== 'object') return
    const candidate = value as { threadId?: string; runId?: string } & NonNullable<typeof latest>
    if (candidate.threadId === threadId && candidate.runId === runId) latest = candidate
  }
  /** 关闭订阅时中止 SDK，不重发可能已经落盘的核验记录。 */
  const abort = (): void => agent.abortRun()
  signal?.addEventListener('abort', abort, { once: true })
  try {
    if (signal?.aborted) throw new Error('核验订阅已关闭，请重新读取。')
    const result = await agent.runAgent(
      {
        runId,
        forwardedProps: {
          runtimeAcceptance: {
            action: report ? 'record' : 'get',
            workspace,
            ...(report ? { report } : {})
          }
        }
      },
      {
        onCustomEvent: ({ event }) => {
          if (event.name === 'runtime-acceptance') accept(event.value)
        },
        onStateSnapshotEvent: ({ event }) =>
          accept((event.snapshot as { runtimeAcceptance?: unknown }).runtimeAcceptance),
        onRunFinishedEvent: ({ event }) => {
          finished = event.threadId === threadId && event.runId === runId
        }
      }
    )
    accept((result.result as { runtimeAcceptance?: unknown } | undefined)?.runtimeAcceptance)
    if (signal?.aborted) throw new Error('核验订阅已关闭，请重新读取。')
    if (!finished || latest?.status !== 'completed' || !latest.matrix) {
      throw new Error(latest?.error?.message || '未取得完整核验结果，请重新读取；不会自动重发。')
    }
    return latest.matrix
  } finally {
    signal?.removeEventListener('abort', abort)
  }
}
