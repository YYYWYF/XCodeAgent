import type { ElectronAPI } from '@electron-toolkit/preload'
import type {
  DownloadZipAndExtractInput,
  DownloadZipAndExtractResult,
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

export type WriteTestDataResult = {
  success: true
  path: string
}

export type LoginResult = {
  token: string
}

export type AppPublicConfig = {
  env: 'dev' | 'st' | 'uat'
  apiBaseUrl: string
  appName: string
}

export type AppAPI = {
  getAppConfig: () => Promise<AppPublicConfig>
  download: {
    zipAndExtract: (request: DownloadZipAndExtractInput) => Promise<DownloadZipAndExtractResult>
  }
  tasks: {
    list: () => Promise<TaskMenuItem[]>
    getDetail: (taskId: string) => Promise<TaskDetail>
  }
  llm: {
    startChatStream: (request: LlmChatStreamRequest) => Promise<string>
    cancelChatStream: (streamId: string) => Promise<void>
    onStreamChunk: (listener: (payload: LlmStreamChunk) => void) => () => void
    onStreamDone: (listener: (payload: LlmStreamDone) => void) => () => void
    onStreamError: (listener: (payload: LlmStreamError) => void) => () => void
  }
  xcodeAgent: {
    listMarkdownFiles: () => Promise<XcodeAgentMarkdownFileSummary[]>
    getMarkdownFile: (
      fileName: XcodeAgentMarkdownFileName
    ) => Promise<XcodeAgentMarkdownFileContent>
    saveMarkdownFile: (
      fileName: XcodeAgentMarkdownFileName,
      content: string
    ) => Promise<XcodeAgentMarkdownFileContent>
    revealMarkdownFile: (fileName: XcodeAgentMarkdownFileName) => Promise<void>
  }
  login: () => Promise<LoginResult>
  writeTestData: () => Promise<WriteTestDataResult>
}

declare global {
  interface Window {
    electron: ElectronAPI
    api: AppAPI
  }
}
