import { randomUUID } from 'crypto'
import type { WebContents } from 'electron'
import type { Readable } from 'stream'
import { backendHttp, createApiError, toApiErrorPayload } from '../http'
import type {
  LlmChatStreamRequest,
  LlmStreamChunk,
  LlmStreamDone,
  LlmStreamError
} from '../../shared/api'

export const LLM_STREAM_CHUNK_CHANNEL = 'llm:stream:chunk'
export const LLM_STREAM_DONE_CHANNEL = 'llm:stream:done'
export const LLM_STREAM_ERROR_CHANNEL = 'llm:stream:error'

const LLM_CHAT_STREAM_ENDPOINT = '/llm/chat'

const activeChatStreams = new Map<string, AbortController>()

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null

const safeJsonParse = (value: string): unknown => {
  try {
    return JSON.parse(value) as unknown
  } catch {
    return value
  }
}

const getNestedString = (value: unknown, path: string[]): string | undefined => {
  let currentValue = value

  for (const key of path) {
    if (!isRecord(currentValue)) {
      return undefined
    }

    currentValue = currentValue[key]
  }

  return typeof currentValue === 'string' ? currentValue : undefined
}

const extractDelta = (value: unknown): string => {
  if (typeof value === 'string') {
    return value
  }

  const directDelta = getNestedString(value, ['delta'])

  if (directDelta) {
    return directDelta
  }

  const directContent = getNestedString(value, ['content'])

  if (directContent) {
    return directContent
  }

  if (isRecord(value) && Array.isArray(value.choices)) {
    const firstChoice = value.choices[0] as unknown
    const choiceDelta = getNestedString(firstChoice, ['delta', 'content'])

    if (choiceDelta) {
      return choiceDelta
    }
  }

  return ''
}

const sendToRenderer = <TPayload>(
  webContents: WebContents,
  channel: string,
  payload: TPayload
): void => {
  if (!webContents.isDestroyed()) {
    webContents.send(channel, payload)
  }
}

const emitChunk = (webContents: WebContents, streamId: string, data: string): boolean => {
  if (data === '[DONE]') {
    return false
  }

  const raw = safeJsonParse(data)
  const delta = extractDelta(raw)

  if (delta) {
    const payload: LlmStreamChunk = {
      streamId,
      delta,
      raw
    }

    sendToRenderer(webContents, LLM_STREAM_CHUNK_CHANNEL, payload)
  }

  return true
}

const parseSseLine = (
  line: string,
  dataLines: string[],
  onData: (data: string) => boolean
): boolean => {
  if (!line) {
    if (dataLines.length === 0) {
      return true
    }

    const data = dataLines.join('\n')
    dataLines.length = 0

    return onData(data)
  }

  if (line.startsWith(':')) {
    return true
  }

  if (line.startsWith('data:')) {
    dataLines.push(line.slice(5).trimStart())
  }

  return true
}

const parseSseStream = async (
  stream: Readable,
  onData: (data: string) => boolean
): Promise<boolean> => {
  let buffer = ''
  const dataLines: string[] = []

  for await (const chunk of stream) {
    buffer += Buffer.isBuffer(chunk) ? chunk.toString('utf-8') : String(chunk)

    const lines = buffer.split(/\r?\n/)
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      const shouldContinue = parseSseLine(line, dataLines, onData)

      if (!shouldContinue) {
        return false
      }
    }
  }

  if (buffer) {
    const shouldContinue = parseSseLine(buffer, dataLines, onData)

    if (!shouldContinue) {
      return false
    }
  }

  if (dataLines.length > 0) {
    return onData(dataLines.join('\n'))
  }

  return true
}

const runChatStream = async (
  webContents: WebContents,
  streamId: string,
  request: LlmChatStreamRequest,
  controller: AbortController
): Promise<void> => {
  try {
    const response = await backendHttp.post<Readable>(LLM_CHAT_STREAM_ENDPOINT, request, {
      responseType: 'stream',
      signal: controller.signal,
      headers: {
        Accept: 'text/event-stream'
      }
    })

    await parseSseStream(response.data, (data) => emitChunk(webContents, streamId, data))

    if (!controller.signal.aborted) {
      const payload: LlmStreamDone = { streamId }
      sendToRenderer(webContents, LLM_STREAM_DONE_CHANNEL, payload)
    }
  } catch (error) {
    if (!controller.signal.aborted) {
      const payload: LlmStreamError = {
        streamId,
        error: toApiErrorPayload(error)
      }

      sendToRenderer(webContents, LLM_STREAM_ERROR_CHANNEL, payload)
    }
  } finally {
    activeChatStreams.delete(streamId)
  }
}

export const startChatStream = (
  webContents: WebContents,
  request: LlmChatStreamRequest
): string => {
  if (!Array.isArray(request?.messages) || request.messages.length === 0) {
    throw createApiError('INVALID_CHAT_MESSAGES', 'At least one chat message is required')
  }

  const streamId = randomUUID()
  const controller = new AbortController()

  activeChatStreams.set(streamId, controller)
  webContents.once('destroyed', () => {
    controller.abort()
    activeChatStreams.delete(streamId)
  })

  void runChatStream(webContents, streamId, request, controller)

  return streamId
}

export const cancelChatStream = (streamId: string): void => {
  const controller = activeChatStreams.get(streamId)

  if (!controller) {
    return
  }

  controller.abort()
  activeChatStreams.delete(streamId)
}
