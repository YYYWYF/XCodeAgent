export type ApiErrorPayload = {
  code: string
  message: string
  status?: number
  requestId?: string
}

export type IpcResult<T> =
  | {
      ok: true
      data: T
    }
  | {
      ok: false
      error: ApiErrorPayload
    }

export type DownloadZipRequestParams = Record<string, string | number | boolean | null | undefined>

export type DownloadZipAndExtractInput = {
  targetDir: string
  params?: DownloadZipRequestParams
  overwrite?: boolean
  timeoutMs?: number
}

export type DownloadZipAndExtractResult = {
  targetDir: string
  entryCount: number
}

export type LlmChatMessage = {
  role: 'system' | 'user' | 'assistant'
  content: string
}

export type LlmChatStreamRequest = {
  messages: LlmChatMessage[]
  model?: string
  temperature?: number
}

export type LlmStreamChunk = {
  streamId: string
  delta: string
  raw?: unknown
}

export type LlmStreamDone = {
  streamId: string
}

export type LlmStreamError = {
  streamId: string
  error: ApiErrorPayload
}
