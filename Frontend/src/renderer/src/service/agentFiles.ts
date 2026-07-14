import { HttpAgent, randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type { AgentFile, AgentFileDocument } from '../typings'

type AgentFilesAction = 'get' | 'save'

type AgentFilesAgUiPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: AgentFilesAction
  root?: string
  document?: AgentFileDocument
  error?: { type?: string; message?: string }
}

function getAgentFilesUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/agent-files/run`
    : '/api/agent/agent-files/run'
}

function readAgentFilesPayload(value: unknown): AgentFilesAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<AgentFilesAgUiPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as AgentFilesAgUiPayload
}

function readAgentFilesFromState(value: unknown): AgentFilesAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readAgentFilesPayload((value as { agentFiles?: unknown }).agentFiles)
}

function readAgentFilesFromResult(value: unknown): AgentFilesAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readAgentFilesPayload((value as { agentFiles?: unknown }).agentFiles)
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
  const threadId = randomUUID()
  const agent = new HttpAgent({ url: getAgentFilesUrl(), threadId })
  const message: Message = {
    id: randomUUID(),
    role: 'user',
    content: messageContent
  }
  agent.addMessage(message)

  let agentFiles: AgentFilesAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'agent-files') {
        agentFiles = readAgentFilesPayload(event.value) ?? agentFiles
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      agentFiles = readAgentFilesFromState(event.snapshot) ?? agentFiles
    }
  }
  const result = await agent.runAgent({ forwardedProps: { agentFiles: input } }, subscriber)
  agentFiles = readAgentFilesFromResult(result.result) ?? agentFiles
  if (!agentFiles) throw new Error('文件接口没有返回有效的 AG-UI 状态。')
  if (agentFiles.status === 'failed') {
    throw new Error(agentFiles.error?.message || '文件操作失败。')
  }
  return agentFiles
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
