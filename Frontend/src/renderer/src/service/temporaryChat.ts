import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import { createAgUiHttpAgent } from './authentication'

export type TemporaryChatMessage = {
  id: string
  role: 'user' | 'assistant' | 'error'
  content: string
}

type TemporaryChatPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'in_progress' | 'completed' | 'failed'
  answer?: string
  error?: { type?: string; message?: string }
}

function getTemporaryChatUrl(): string {
  const agentBaseUrl = window.devAgentStudio?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/temporary-chat/run`
    : '/api/agent/temporary-chat/run'
}

function readTemporaryChatPayload(value: unknown): TemporaryChatPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<TemporaryChatPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['in_progress', 'completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as TemporaryChatPayload
}

function readTemporaryChatState(value: unknown): TemporaryChatPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readTemporaryChatPayload((value as { temporaryChat?: unknown }).temporaryChat)
}

function readTemporaryChatResult(value: unknown): TemporaryChatPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readTemporaryChatPayload((value as { temporaryChat?: unknown }).temporaryChat)
}

/** 创建不写入本地会话目录的临时 AG-UI Thread 标识。 */
export function createTemporaryChatThreadId(): string {
  return randomUUID()
}

/** 通过独立 AG-UI 只读入口向 Workspace Assistant 发送一条问题。 */
export async function sendTemporaryChatQuestion(input: {
  threadId: string
  workspaceRoot: string
  request: string
  conversationSummary: string
  signal?: AbortSignal
}): Promise<string> {
  const agent = createAgUiHttpAgent({ url: getTemporaryChatUrl(), threadId: input.threadId })
  const userMessage: Message = {
    id: randomUUID(),
    role: 'user',
    content: input.request
  }
  agent.addMessage(userMessage)
  let payload: TemporaryChatPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'temporary-chat') {
        payload = readTemporaryChatPayload(event.value) ?? payload
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readTemporaryChatState(event.snapshot) ?? payload
    }
  }
  const abort = (): void => agent.abortRun()
  input.signal?.addEventListener('abort', abort, { once: true })
  try {
    if (input.signal?.aborted) throw createAbortError()
    const result = await agent.runAgent(
      {
        forwardedProps: {
          temporaryChat: {
            workspaceRoot: input.workspaceRoot,
            request: input.request,
            conversationSummary: input.conversationSummary
          }
        }
      },
      subscriber
    )
    if (input.signal?.aborted) throw createAbortError()
    payload = readTemporaryChatResult(result.result) ?? payload
    if (!payload) throw new Error('临时对话接口没有返回有效的 AG-UI 状态。')
    if (payload.status === 'failed') {
      throw new Error(payload.error?.message || '只读工程问答失败。')
    }
    if (payload.status !== 'completed' || typeof payload.answer !== 'string') {
      throw new Error('临时对话接口没有返回回答。')
    }
    return payload.answer
  } finally {
    input.signal?.removeEventListener('abort', abort)
  }
}

/** 生成统一的取消错误，避免把关闭临时对话显示成问答失败。 */
function createAbortError(): Error {
  const error = new Error('临时对话请求已取消。')
  error.name = 'AbortError'
  return error
}
