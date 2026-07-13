import { HttpAgent, randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type {
  UserSkill,
  UserSkillCatalog,
  UserSkillDocument,
  UserSkillIssue
} from '../typings'

type SkillCatalogAgUiPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'list' | 'get' | 'save'
  root?: string
  skills?: UserSkill[]
  skippedCount?: number
  issues?: UserSkillIssue[]
  document?: UserSkillDocument
  error?: { type?: string; message?: string }
}

function getSkillCatalogUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl ? `${agentBaseUrl.replace(/\/$/, '')}/skills/run` : '/api/agent/skills/run'
}

function readSkillCatalogPayload(value: unknown): SkillCatalogAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<SkillCatalogAgUiPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as SkillCatalogAgUiPayload
}

function readSkillCatalogFromState(value: unknown): SkillCatalogAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readSkillCatalogPayload((value as { skillCatalog?: unknown }).skillCatalog)
}

function readSkillCatalogFromResult(value: unknown): SkillCatalogAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readSkillCatalogPayload((value as { skillCatalog?: unknown }).skillCatalog)
}

async function runSkillCatalogAgent(
  input: Record<string, unknown>,
  messageContent: string
): Promise<SkillCatalogAgUiPayload> {
  const threadId = randomUUID()
  const agent = new HttpAgent({ url: getSkillCatalogUrl(), threadId })
  const message: Message = {
    id: randomUUID(),
    role: 'user',
    content: messageContent
  }
  agent.addMessage(message)

  let catalog: SkillCatalogAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'skill-catalog') {
        catalog = readSkillCatalogPayload(event.value) ?? catalog
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      catalog = readSkillCatalogFromState(event.snapshot) ?? catalog
    }
  }
  const result = await agent.runAgent(
    { forwardedProps: { skillCatalog: input } },
    subscriber
  )
  catalog = readSkillCatalogFromResult(result.result) ?? catalog
  if (!catalog) throw new Error('技能接口没有返回有效的 AG-UI 状态。')
  if (catalog.status === 'failed') {
    throw new Error(catalog.error?.message || '技能操作失败。')
  }
  return catalog
}

export async function requestUserSkills(): Promise<UserSkillCatalog> {
  const catalog = await runSkillCatalogAgent({ action: 'list' }, '读取本地用户技能列表。')
  if (
    typeof catalog.root !== 'string' ||
    !Array.isArray(catalog.skills) ||
    !Array.isArray(catalog.issues)
  ) {
    throw new Error('技能接口返回的数据结构无效。')
  }
  return {
    root: catalog.root,
    skills: catalog.skills,
    skippedCount: Number(catalog.skippedCount || 0),
    issues: catalog.issues
  }
}

export async function requestUserSkillDocument(
  relativePath: string
): Promise<UserSkillDocument> {
  const response = await runSkillCatalogAgent(
    { action: 'get', relativePath },
    `读取用户技能 ${relativePath}。`
  )
  if (!response.document) throw new Error('技能接口没有返回完整内容。')
  return response.document
}

export async function saveUserSkillDocument(input: {
  relativePath: string
  content: string
  expectedRevision: string
}): Promise<UserSkillDocument> {
  const response = await runSkillCatalogAgent(
    { action: 'save', ...input },
    `保存用户技能 ${input.relativePath}。`
  )
  if (!response.document) throw new Error('技能接口没有返回保存结果。')
  return response.document
}
