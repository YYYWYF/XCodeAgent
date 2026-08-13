import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type { CodeAnalysisResult } from '../typings'
import { createAgUiHttpAgent } from './authentication'

export type CodeAnalysisCallbacks = {
  onUpdate?: (result: CodeAnalysisResult) => void
}

export type CodeAnalysisRun = {
  abort: () => void
  promise: Promise<CodeAnalysisResult>
}

/** 返回独立代码审查 AG-UI 地址。 */
function getCodeAnalysisUrl(): string {
  const baseUrl = (window.xcodeAgent?.agentBaseUrl || 'http://127.0.0.1:8000').replace(/\/$/, '')
  return `${baseUrl}/code-analysis/run`
}

/** 从未知值中读取版本正确的代码审查状态。 */
export function readCodeAnalysisResult(value: unknown): CodeAnalysisResult | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<CodeAnalysisResult>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['in_progress', 'completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as CodeAnalysisResult
}

/** 从 AG-UI 状态快照中读取代码审查状态。 */
function readCodeAnalysisState(value: unknown): CodeAnalysisResult | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readCodeAnalysisResult((value as { codeAnalysis?: unknown }).codeAnalysis)
}

/** 从 AG-UI 最终结果中读取代码审查状态。 */
function readCodeAnalysisFinalResult(value: unknown): CodeAnalysisResult | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readCodeAnalysisResult((value as { codeAnalysis?: unknown }).codeAnalysis)
}

/** 启动可取消的前端源码扫描，并实时投射 AG-UI 状态。 */
export function startFrontendCodeAnalysis(
  workspaceRoot: string,
  callbacks: CodeAnalysisCallbacks = {}
): CodeAnalysisRun {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: getCodeAnalysisUrl(), threadId })
  const message: Message = {
    id: randomUUID(),
    role: 'user',
    content: '扫描当前工作区前端代码。'
  }
  agent.addMessage(message)
  let latest: CodeAnalysisResult | undefined
  const update = (candidate: CodeAnalysisResult | undefined): void => {
    if (!candidate) return
    latest = candidate
    callbacks.onUpdate?.(candidate)
  }
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'code-analysis') update(readCodeAnalysisResult(event.value))
    },
    onStateSnapshotEvent: ({ event }) => update(readCodeAnalysisState(event.snapshot))
  }
  const promise = agent
    .runAgent({ forwardedProps: { codeAnalysis: { action: 'scan', workspaceRoot } } }, subscriber)
    .then((result) => {
      update(readCodeAnalysisFinalResult(result.result))
      if (!latest) throw new Error('代码扫描接口没有返回有效的 AG-UI 状态。')
      if (latest.status === 'failed') {
        throw new Error(latest.error?.message || '前端代码扫描失败。')
      }
      if (latest.status !== 'completed') {
        throw new Error('前端代码扫描已停止。')
      }
      return latest
    })
  return { abort: () => agent.abortRun(), promise }
}

/** 按需安全读取一份正式代码审查 Markdown 报告。 */
export async function getFrontendCodeAnalysisReport(
  workspaceRoot: string,
  reportPath: string
): Promise<string> {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: getCodeAnalysisUrl(), threadId })
  agent.addMessage({ id: randomUUID(), role: 'user', content: '读取前端代码审查报告。' })
  let latest: CodeAnalysisResult | undefined
  let content = ''
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'code-analysis' || !event.value || typeof event.value !== 'object') return
      latest = readCodeAnalysisResult(event.value) ?? latest
      const candidate = (event.value as { content?: unknown }).content
      if (typeof candidate === 'string') content = candidate
    },
    onStateSnapshotEvent: ({ event }) => {
      latest = readCodeAnalysisState(event.snapshot) ?? latest
      const state = (event.snapshot as { codeAnalysis?: { content?: unknown } })?.codeAnalysis
      if (typeof state?.content === 'string') content = state.content
    }
  }
  const result = await agent.runAgent(
    {
      forwardedProps: { codeAnalysis: { action: 'get-report', workspaceRoot, reportPath } }
    },
    subscriber
  )
  const finalPayload = (result.result as { codeAnalysis?: { content?: unknown } })?.codeAnalysis
  latest = readCodeAnalysisFinalResult(result.result) ?? latest
  if (typeof finalPayload?.content === 'string') content = finalPayload.content
  if (latest?.status === 'failed') throw new Error(latest.error?.message || '报告读取失败。')
  if (!content) throw new Error('报告正文为空或未返回。')
  return content
}
