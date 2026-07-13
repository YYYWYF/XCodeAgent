import { HttpAgent, randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type { UserSkillCatalog } from '../typings'

type SkillCatalogAgUiPayload = UserSkillCatalog & {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
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

export async function requestUserSkills(): Promise<UserSkillCatalog> {
  const threadId = randomUUID()
  const agent = new HttpAgent({ url: getSkillCatalogUrl(), threadId })
  const message: Message = {
    id: randomUUID(),
    role: 'user',
    content: '读取本地用户技能列表。'
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
    { forwardedProps: { skillCatalog: { action: 'list' } } },
    subscriber
  )
  catalog = readSkillCatalogFromResult(result.result) ?? catalog
  if (!catalog) throw new Error('技能接口没有返回有效的 AG-UI 状态。')
  if (catalog.status === 'failed') {
    throw new Error(catalog.error?.message || '技能列表读取失败。')
  }
  if (!Array.isArray(catalog.skills) || !Array.isArray(catalog.issues)) {
    throw new Error('技能接口返回的数据结构无效。')
  }
  return {
    root: catalog.root,
    skills: catalog.skills,
    skippedCount: Number(catalog.skippedCount || 0),
    issues: catalog.issues
  }
}
