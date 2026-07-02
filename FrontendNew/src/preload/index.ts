import { contextBridge, ipcRenderer } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
import type { IpcRendererEvent } from 'electron'
import type {
  ApiErrorPayload,
  DownloadZipAndExtractInput,
  DownloadZipAndExtractResult,
  IpcResult,
  LlmChatStreamRequest,
  LlmStreamChunk,
  LlmStreamDone,
  LlmStreamError
} from '../shared/api'
import type { TaskDetail, TaskMenuItem } from '../shared/task'
import type {
  XcodeAgentMarkdownFileContent,
  XcodeAgentMarkdownFileName,
  XcodeAgentMarkdownFileSummary
} from '../shared/xcodeagent'

type WriteTestDataResult = {
  success: true
  path: string
}

type LoginResult = {
  token: string
}

type AppPublicConfig = {
  env: 'dev' | 'st' | 'uat'
  apiBaseUrl: string
  appName: string
}

class ApiClientError extends Error {
  readonly payload: ApiErrorPayload

  constructor(payload: ApiErrorPayload) {
    super(payload.message)
    this.name = 'ApiClientError'
    this.payload = payload
  }
}

const unwrapIpcResult = <T>(result: IpcResult<T>): T => {
  if (result.ok) {
    return result.data
  }

  throw new ApiClientError(result.error)
}

const invokeApi = async <T>(channel: string, ...args: unknown[]): Promise<T> => {
  const result = (await ipcRenderer.invoke(channel, ...args)) as IpcResult<T>

  return unwrapIpcResult(result)
}

const subscribe = <TPayload>(
  channel: string,
  listener: (payload: TPayload) => void
): (() => void) => {
  const handler = (_event: IpcRendererEvent, payload: TPayload): void => {
    listener(payload)
  }

  ipcRenderer.on(channel, handler)

  return () => {
    ipcRenderer.removeListener(channel, handler)
  }
}

// Custom APIs for renderer
const api = {
  getAppConfig: (): Promise<AppPublicConfig> => ipcRenderer.invoke('app:get-config'),
  download: {
    zipAndExtract: (request: DownloadZipAndExtractInput): Promise<DownloadZipAndExtractResult> =>
      invokeApi('download:zip-extract', request)
  },
  tasks: {
    list: (): Promise<TaskMenuItem[]> => invokeApi('task:list'),
    getDetail: (taskId: string): Promise<TaskDetail> => invokeApi('task:get-detail', taskId)
  },
  llm: {
    startChatStream: (request: LlmChatStreamRequest): Promise<string> =>
      invokeApi('llm:stream:start', request),
    cancelChatStream: (streamId: string): Promise<void> => invokeApi('llm:stream:cancel', streamId),
    onStreamChunk: (listener: (payload: LlmStreamChunk) => void): (() => void) =>
      subscribe('llm:stream:chunk', listener),
    onStreamDone: (listener: (payload: LlmStreamDone) => void): (() => void) =>
      subscribe('llm:stream:done', listener),
    onStreamError: (listener: (payload: LlmStreamError) => void): (() => void) =>
      subscribe('llm:stream:error', listener)
  },
  xcodeAgent: {
    listMarkdownFiles: (): Promise<XcodeAgentMarkdownFileSummary[]> =>
      invokeApi('xcodeagent:markdown:list'),
    getMarkdownFile: (
      fileName: XcodeAgentMarkdownFileName
    ): Promise<XcodeAgentMarkdownFileContent> => invokeApi('xcodeagent:markdown:get', fileName),
    saveMarkdownFile: (
      fileName: XcodeAgentMarkdownFileName,
      content: string
    ): Promise<XcodeAgentMarkdownFileContent> =>
      invokeApi('xcodeagent:markdown:save', fileName, content),
    revealMarkdownFile: (fileName: XcodeAgentMarkdownFileName): Promise<void> =>
      invokeApi('xcodeagent:markdown:reveal', fileName)
  },
  login: (): Promise<LoginResult> => ipcRenderer.invoke('auth:login'),
  writeTestData: (): Promise<WriteTestDataResult> => ipcRenderer.invoke('write-test-data')
}

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
}
