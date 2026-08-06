import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type {
  BuiltinSkill,
  UserSkill,
  UserSkillCatalog,
  UserSkillDocument,
  UserSkillIssue
} from '../typings'
import { createAgUiHttpAgent } from './authentication'

type SkillCatalogAgUiPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'list' | 'get' | 'save' | 'create' | 'delete' | 'import' | 'set-enabled'
  root?: string
  skills?: UserSkill[]
  builtinRoot?: string
  builtinSkills?: BuiltinSkill[]
  skippedCount?: number
  issues?: UserSkillIssue[]
  document?: UserSkillDocument
  deleted?: { name: string; relativePath: string }
  imported?: UserSkill
  skill?: UserSkill
  error?: { type?: string; message?: string }
}

function getSkillCatalogUrl(): string {
  /** 根据桌面运行时配置解析技能 AG-UI 地址。 */
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl ? `${agentBaseUrl.replace(/\/$/, '')}/skills/run` : '/api/agent/skills/run'
}

function readSkillCatalogPayload(value: unknown): SkillCatalogAgUiPayload | undefined {
  /** 严格读取技能动作返回的公共状态结构。 */
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
  /** 从 AG-UI STATE_SNAPSHOT 中提取技能目录状态。 */
  if (!value || typeof value !== 'object') return undefined
  return readSkillCatalogPayload((value as { skillCatalog?: unknown }).skillCatalog)
}

function readSkillCatalogFromResult(value: unknown): SkillCatalogAgUiPayload | undefined {
  /** 从 AG-UI 最终结果中提取技能目录状态。 */
  if (!value || typeof value !== 'object') return undefined
  return readSkillCatalogPayload((value as { skillCatalog?: unknown }).skillCatalog)
}

async function runSkillCatalogAgent(
  input: Record<string, unknown>,
  messageContent: string
): Promise<SkillCatalogAgUiPayload> {
  /** 运行一次独立技能动作并统一合并事件、快照和最终结果。 */
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: getSkillCatalogUrl(), threadId })
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
  const result = await agent.runAgent({ forwardedProps: { skillCatalog: input } }, subscriber)
  catalog = readSkillCatalogFromResult(result.result) ?? catalog
  if (!catalog) throw new Error('技能接口没有返回有效的 AG-UI 状态。')
  if (catalog.status === 'failed') {
    throw new Error(catalog.error?.message || '技能操作失败。')
  }
  return catalog
}

export async function requestUserSkills(): Promise<UserSkillCatalog> {
  /** 同时读取用户技能、内置技能和用户启用状态。 */
  const catalog = await runSkillCatalogAgent({ action: 'list' }, '读取本地用户技能列表。')
  if (
    typeof catalog.root !== 'string' ||
    !Array.isArray(catalog.skills) ||
    typeof catalog.builtinRoot !== 'string' ||
    !Array.isArray(catalog.builtinSkills) ||
    !Array.isArray(catalog.issues)
  ) {
    throw new Error('技能接口返回的数据结构无效。')
  }
  return {
    root: catalog.root,
    skills: catalog.skills,
    builtinRoot: catalog.builtinRoot,
    builtinSkills: catalog.builtinSkills,
    skippedCount: Number(catalog.skippedCount || 0),
    issues: catalog.issues
  }
}

export async function requestUserSkillDocument(relativePath: string): Promise<UserSkillDocument> {
  /** 读取一个可编辑用户技能的完整 SKILL.md。 */
  const response = await runSkillCatalogAgent(
    { action: 'get', relativePath },
    `读取用户技能 ${relativePath}。`
  )
  if (!response.document) throw new Error('技能接口没有返回完整内容。')
  return response.document
}

export async function createUserSkillDocument(input: {
  content: string
}): Promise<UserSkillDocument> {
  /** 创建默认开启的用户技能文档。 */
  const response = await runSkillCatalogAgent({ action: 'create', ...input }, '创建用户技能。')
  if (!response.document) throw new Error('技能接口没有返回创建结果。')
  return response.document
}

export async function saveUserSkillDocument(input: {
  relativePath: string
  content: string
  expectedRevision: string
}): Promise<UserSkillDocument> {
  /** 按内容 revision 乐观保存用户技能。 */
  const response = await runSkillCatalogAgent(
    { action: 'save', ...input },
    `保存用户技能 ${input.relativePath}。`
  )
  if (!response.document) throw new Error('技能接口没有返回保存结果。')
  return response.document
}

export async function deleteUserSkill(relativePath: string): Promise<void> {
  /** 删除用户技能及其后端启用状态。 */
  const response = await runSkillCatalogAgent(
    { action: 'delete', relativePath },
    `删除用户技能 ${relativePath}。`
  )
  if (response.deleted?.relativePath !== relativePath) {
    throw new Error('技能接口没有返回有效的删除结果。')
  }
}

export async function importUserSkillArchive(input: {
  archiveBase64: string
  fileName: string
}): Promise<UserSkill> {
  /** 通过 AG-UI 导入默认开启的用户技能 ZIP。 */
  const response = await runSkillCatalogAgent(
    { action: 'import', ...input },
    `导入用户技能压缩包 ${input.fileName}。`
  )
  if (!response.imported) throw new Error('技能接口没有返回有效的导入结果。')
  return response.imported
}

export async function setUserSkillEnabled(input: {
  relativePath: string
  enabled: boolean
}): Promise<UserSkill> {
  /** 持久化用户技能启用状态并返回服务端确认后的摘要。 */
  const response = await runSkillCatalogAgent(
    { action: 'set-enabled', ...input },
    `${input.enabled ? '开启' : '关闭'}用户技能 ${input.relativePath}。`
  )
  if (!response.skill) throw new Error('技能接口没有返回有效的启用状态。')
  return response.skill
}
