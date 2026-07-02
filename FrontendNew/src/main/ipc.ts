import { app, ipcMain, shell } from 'electron'
import { writeFile } from 'fs/promises'
import { join } from 'path'
import { startChatStream, cancelChatStream } from './api/llm'
import { listTasks, getTaskDetail } from './api/tasks'
import { simulateLogin } from './auth'
import type { LoginResult } from './auth'
import { publicAppConfig } from './env'
import type { PublicAppConfig } from './env'
import { toApiErrorPayload } from './http'
import { downloadZipAndExtract } from './util'
import {
  ensureXcodeAgentHome,
  getXcodeAgentMarkdownFilePath,
  listXcodeAgentMarkdownFiles,
  readXcodeAgentMarkdownFile,
  saveXcodeAgentMarkdownFile
} from './xcodeagent-home'
import type {
  DownloadZipAndExtractInput,
  DownloadZipAndExtractResult,
  IpcResult,
  LlmChatStreamRequest
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

type RegisterIpcHandlersOptions = {
  onLoginSuccess: () => void
}

const AUTH_LOGIN_CHANNEL = 'auth:login'
const APP_CONFIG_CHANNEL = 'app:get-config'
const DOWNLOAD_ZIP_EXTRACT_CHANNEL = 'download:zip-extract'
const LLM_STREAM_CANCEL_CHANNEL = 'llm:stream:cancel'
const LLM_STREAM_START_CHANNEL = 'llm:stream:start'
const TASK_GET_DETAIL_CHANNEL = 'task:get-detail'
const TASK_LIST_CHANNEL = 'task:list'
const TEST_DATA_FILE_CONTENT = '{"test":1}'
const WRITE_TEST_DATA_CHANNEL = 'write-test-data'
const XCODEAGENT_MARKDOWN_GET_CHANNEL = 'xcodeagent:markdown:get'
const XCODEAGENT_MARKDOWN_LIST_CHANNEL = 'xcodeagent:markdown:list'
const XCODEAGENT_MARKDOWN_REVEAL_CHANNEL = 'xcodeagent:markdown:reveal'
const XCODEAGENT_MARKDOWN_SAVE_CHANNEL = 'xcodeagent:markdown:save'

const toIpcResult = async <T>(action: () => Promise<T> | T): Promise<IpcResult<T>> => {
  try {
    return {
      ok: true,
      data: await action()
    }
  } catch (error) {
    return {
      ok: false,
      error: toApiErrorPayload(error)
    }
  }
}

export function registerIpcHandlers({ onLoginSuccess }: RegisterIpcHandlersOptions): void {
  ipcMain.on('ping', () => console.log('pong'))

  ipcMain.handle(APP_CONFIG_CHANNEL, (): PublicAppConfig => publicAppConfig)

  ipcMain.handle(
    DOWNLOAD_ZIP_EXTRACT_CHANNEL,
    async (
      _,
      request: DownloadZipAndExtractInput
    ): Promise<IpcResult<DownloadZipAndExtractResult>> =>
      toIpcResult(() => downloadZipAndExtract(request))
  )

  ipcMain.handle(TASK_LIST_CHANNEL, async (): Promise<IpcResult<TaskMenuItem[]>> =>
    toIpcResult(() => listTasks())
  )

  ipcMain.handle(
    TASK_GET_DETAIL_CHANNEL,
    async (_, taskId: string): Promise<IpcResult<TaskDetail>> =>
      toIpcResult(() => getTaskDetail(taskId))
  )

  ipcMain.handle(
    LLM_STREAM_START_CHANNEL,
    async (event, request: LlmChatStreamRequest): Promise<IpcResult<string>> =>
      toIpcResult(() => startChatStream(event.sender, request))
  )

  ipcMain.handle(LLM_STREAM_CANCEL_CHANNEL, async (_, streamId: string): Promise<IpcResult<void>> =>
    toIpcResult(() => cancelChatStream(streamId))
  )

  ipcMain.handle(
    XCODEAGENT_MARKDOWN_LIST_CHANNEL,
    async (): Promise<IpcResult<XcodeAgentMarkdownFileSummary[]>> =>
      toIpcResult(() => listXcodeAgentMarkdownFiles())
  )

  ipcMain.handle(
    XCODEAGENT_MARKDOWN_GET_CHANNEL,
    async (
      _,
      fileName: XcodeAgentMarkdownFileName
    ): Promise<IpcResult<XcodeAgentMarkdownFileContent>> =>
      toIpcResult(() => readXcodeAgentMarkdownFile(fileName))
  )

  ipcMain.handle(
    XCODEAGENT_MARKDOWN_SAVE_CHANNEL,
    async (
      _,
      fileName: XcodeAgentMarkdownFileName,
      content: string
    ): Promise<IpcResult<XcodeAgentMarkdownFileContent>> =>
      toIpcResult(() => saveXcodeAgentMarkdownFile(fileName, content))
  )

  ipcMain.handle(
    XCODEAGENT_MARKDOWN_REVEAL_CHANNEL,
    async (_, fileName: XcodeAgentMarkdownFileName): Promise<IpcResult<void>> =>
      toIpcResult(async () => {
        await ensureXcodeAgentHome()
        shell.showItemInFolder(getXcodeAgentMarkdownFilePath(fileName))
      })
  )

  ipcMain.handle(AUTH_LOGIN_CHANNEL, async (): Promise<LoginResult> => {
    const loginResult = await simulateLogin()

    setTimeout(onLoginSuccess, 0)

    return loginResult
  })

  ipcMain.handle(WRITE_TEST_DATA_CHANNEL, async (): Promise<WriteTestDataResult> => {
    const filePath = join(app.getPath('home'), 'data.json')

    await writeFile(filePath, TEST_DATA_FILE_CONTENT, 'utf-8')

    return {
      success: true,
      path: filePath
    }
  })
}
