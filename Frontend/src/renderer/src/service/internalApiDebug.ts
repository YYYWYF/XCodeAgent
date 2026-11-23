import { createAgUiHttpAgent } from './authentication'
import type { DebugResponse } from './externalApiDebug'

export type DebugMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS'
export type DebugHttpValues = {
  pathValues: Record<string, string>
  query: Record<string, string>
  headers: Record<string, string>
  body: string | null
  timeoutMs: number
}
export type InternalDebugTarget = {
  workspaceRoot: string
  iterationId: string
  apiContractId: string
  endpointId: string
}
export type InternalDebugDefinition = {
  method: DebugMethod
  path: string
  pathNames: string[]
  parameters: Array<{ location: 'path' | 'query' | 'header'; name: string; type: string; required: boolean }>
  body: string
}
export type InternalDebugRequest = DebugHttpValues & InternalDebugTarget & {
  attemptId: string
  expectedMethod: DebugMethod
  expectedPath: string
}

/** 通过独立 AG-UI 流读取定义或发送请求，取消只作用于当前动作。 */
async function runInternalDebugAction<T extends object>(input: object, signal: AbortSignal): Promise<T> {
  const base = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '') || '/api/agent'
  const agent = createAgUiHttpAgent({ url: base + '/internal-api-debug/run', threadId: crypto.randomUUID() })
  /** 断开当前调试订阅。 */
  const abort = (): void => agent.abortRun()
  signal.addEventListener('abort', abort, { once: true })
  try {
    signal.throwIfAborted()
    const { result } = await agent.runAgent({ forwardedProps: { internalApiDebug: input } })
    signal.throwIfAborted()
    const value = (result as { internalApiDebug?: T & { error?: { message: string } } } | undefined)?.internalApiDebug
    if (!value || value.error) throw new Error(value?.error?.message || '未收到完整调试结果。')
    return value
  } finally {
    signal.removeEventListener('abort', abort)
  }
}

/** 读取选中接口的正式请求定义，未完成或失效的选择由后端拒绝。 */
export async function describeInternalApi(target: InternalDebugTarget, signal: AbortSignal): Promise<InternalDebugDefinition> {
  const { endpoint } = await runInternalDebugAction<{ endpoint?: InternalDebugDefinition }>(
    { action: 'describe', ...target }, signal
  )
  if (!endpoint) throw new Error('未收到接口请求定义。')
  return endpoint
}

/** 发送到当前预览轮次的受管服务。 */
export async function sendInternalApiRequest(input: InternalDebugRequest, signal: AbortSignal): Promise<DebugResponse> {
  const { response } = await runInternalDebugAction<{ response?: DebugResponse }>(
    { action: 'send', ...input }, signal
  )
  if (!response) throw new Error('未收到完整调试结果。')
  return response
}
