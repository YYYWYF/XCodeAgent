import type { AgentFile, AgentFileDocument } from '../typings'
import { runAgUiAction, type AgUiPayloadEnvelope } from './agUiClient'

type AgentFilesAgUiPayload = AgUiPayloadEnvelope & {
  status: 'completed' | 'failed'
  action?: 'get' | 'save'
  root?: string
  document?: AgentFileDocument
  error?: { type?: string; message?: string }
}

const AGENT_FILES_STATUS_LIST = ['completed', 'failed'] as const
const AGENT_FILES_EVENT = 'agent-files'
const AGENT_FILES_KEY = 'agentFiles'

function getAgentFilesUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/agent-files/run`
    : '/api/agent/agent-files/run'
}

function isDocument(value: unknown): value is AgentFileDocument {
  if (!value || typeof value !== 'object') return false
  const document = value as Partial<AgentFileDocument>
  return (
    typeof document.name === 'string' &&
    typeof document.relativePath === 'string' &&
    typeof document.content === 'string' &&
    typeof document.revision === 'string' &&
    typeof document.sizeBytes === 'number' &&
    Number.isFinite(document.sizeBytes) &&
    document.sizeBytes >= 0 &&
    typeof document.updatedAt === 'string' &&
    /^[a-f0-9]{64}$/.test(document.revision)
  )
}

async function runAgentFiles(
  input: Record<string, unknown>,
  messageContent: string
): Promise<AgentFilesAgUiPayload> {
  return runAgUiAction<AgentFilesAgUiPayload>({
    url: getAgentFilesUrl(),
    message: messageContent,
    eventName: AGENT_FILES_EVENT,
    stateKey: AGENT_FILES_KEY,
    forwardedProps: { [AGENT_FILES_KEY]: input },
    statusList: AGENT_FILES_STATUS_LIST,
    emptyMessage: '文件接口没有返回有效的 AG-UI 状态。',
    failedMessage: '文件操作失败。'
  })
}

function responseToAgentFile(response: AgentFilesAgUiPayload): AgentFile {
  if (typeof response.root !== 'string' || !isDocument(response.document)) {
    throw new Error('文件接口没有返回完整内容。')
  }
  return { root: response.root, document: response.document }
}

export async function requestAgentFile(): Promise<AgentFile> {
  const response = await runAgentFiles({ action: 'get' }, '读取环境 AGENTS.md。')
  return responseToAgentFile(response)
}

export async function saveAgentFile(input: {
  content: string
  expectedRevision: string
}): Promise<AgentFile> {
  const response = await runAgentFiles({ action: 'save', ...input }, '保存环境 AGENTS.md。')
  return responseToAgentFile(response)
}
