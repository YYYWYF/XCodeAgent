import { createAgUiHttpAgent } from './authentication'

export type DebugResponse = {
  statusCode: number
  elapsedMs: number
  bytes: number
  truncated: boolean
  headers: Record<string, string>
  body: string
}
export type DebugRequest = {
  workspaceRoot: string
  sourceId: string
  operationId: string
  expectedBase: string
  expectedMethod: string
  expectedPath: string
  requestMethod: 'GET' | 'POST' | 'PUT' | 'DELETE'
  requestPath: string
  pathValues: Record<string, string>
  query: Record<string, string>
  headers: Record<string, string>
  body: string | null
  timeoutMs: number
}
type Result = { response?: DebugResponse; error?: { message: string } }

/** 在独立 AG-UI 运行中发送请求，取消订阅时关闭当前请求流。 */
export async function sendExternalApiRequest(
  input: DebugRequest,
  signal: AbortSignal
): Promise<DebugResponse> {
  const base = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '') || '/api/agent'
  const agent = createAgUiHttpAgent({
    url: base + '/external-api-debug/run',
    threadId: crypto.randomUUID()
  })
  const abort = (): void => agent.abortRun()
  signal.addEventListener('abort', abort, { once: true })
  try {
    signal.throwIfAborted()
    const { result } = await agent.runAgent({ forwardedProps: { externalApiDebug: input } })
    signal.throwIfAborted()
    const data = (result as { externalApiDebug?: Result })?.externalApiDebug
    if (!data?.response) throw new Error(data?.error?.message || '未收到完整调试结果。')
    return data.response
  } finally {
    signal.removeEventListener('abort', abort)
  }
}
