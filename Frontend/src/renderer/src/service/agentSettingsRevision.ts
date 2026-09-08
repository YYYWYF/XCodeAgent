import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import { createAgUiHttpAgent } from './authentication'
import { getApplicationPlanningUrl } from './applicationPagePlanning'

export type AgentPromptSettingsInput = {
  persona: { role: string; tone: string }
  systemPrompt: string
  constraints: string[]
}

export type AgentSettingsRevisionDiff = {
  field: string
  label: string
  before: string
  after: string
}

export type AgentSettingsRevisionPreview = {
  active: true
  changeId: string
  agentId: string
  basedOnContractHash: string
  candidateContractHash: string
  changedSections: Array<'prompt' | 'model'>
  fieldDiffs: AgentSettingsRevisionDiff[]
  draftSha256: string
  basedOnLifecycleRevision: number
  agentSettings: Record<string, Record<string, unknown>>
  impact: {
    agentBuild: boolean
    toolAdapterBuild: boolean
    integrationTest: boolean
    launchEvidence: boolean
    unrelatedAgents: boolean
  }
}

type AgentSettingsRevisionEnvelope = Partial<AgentSettingsRevisionPreview> & {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  active?: boolean
  action?: string
  error?: { message?: string }
  contractHash?: string
  technicalPlanSha256?: string
}

export type AgentSettingsRevisionState = {
  contractHash: string
  technicalPlanSha256: string
  preview?: AgentSettingsRevisionPreview
}

type PrepareInput = {
  action: 'prepare_agent_settings_revision'
  workspaceRoot: string
  agentId: string
  basedOnContractHash: string
  basedOnTechnicalPlanSha256: string
  changedSections: Array<'prompt' | 'model'>
  settingsPatch: {
    prompt?: AgentPromptSettingsInput
    model?: { generation: { temperature: number } }
  }
}

type PreviewIdentity = Pick<
  AgentSettingsRevisionPreview,
  'changeId' | 'agentId' | 'basedOnLifecycleRevision' | 'draftSha256'
> & { workspaceRoot: string }

/** 查询正式 Contract 身份及尚未确认的修改预览，供页面重建和 CAS 保存使用。 */
export async function getAgentSettingsRevision(
  workspaceRoot: string,
  agentId: string
): Promise<AgentSettingsRevisionState> {
  const payload = await runAgentSettingsRevision(
    { action: 'get_agent_settings_revision', workspaceRoot, agentId },
    '读取当前智能体待确认的配置修改。'
  )
  if (!payload.contractHash || !payload.technicalPlanSha256) {
    throw new Error('Agent Settings 接口没有返回当前正式配置身份。')
  }
  return {
    contractHash: payload.contractHash,
    technicalPlanSha256: payload.technicalPlanSha256,
    ...(payload.active === true ? { preview: toPreview(payload) } : {})
  }
}

/** 提交严格的 Prompt/Temperature Patch 并生成正式修改预览。 */
export async function prepareAgentSettingsRevision(
  input: PrepareInput
): Promise<AgentSettingsRevisionPreview> {
  const payload = await runAgentSettingsRevision(input, '生成当前智能体的配置修改预览。')
  return toPreview(payload)
}

/** 确认应用当前修改预览并返回新的正式 Contract 身份。 */
export async function confirmAgentSettingsRevision(
  input: PreviewIdentity
): Promise<{ contractHash: string; technicalPlanSha256: string }> {
  const payload = await runAgentSettingsRevision(
    { action: 'confirm_agent_settings_revision', ...input },
    '确认并应用当前智能体配置。'
  )
  if (!payload.contractHash || !payload.technicalPlanSha256) {
    throw new Error('Agent Settings 确认接口没有返回新的正式 Contract。')
  }
  return {
    contractHash: payload.contractHash,
    technicalPlanSha256: payload.technicalPlanSha256
  }
}

/** 放弃当前修改预览并保持正式 Agent Contract 不变。 */
export async function abandonAgentSettingsRevision(input: PreviewIdentity): Promise<void> {
  await runAgentSettingsRevision(
    { action: 'abandon_agent_settings_revision', ...input },
    '放弃当前智能体配置修改。'
  )
}

/** 通过独立应用规划 AG-UI action 执行一次 Agent Settings 操作。 */
async function runAgentSettingsRevision(
  input: Record<string, unknown>,
  messageContent: string
): Promise<AgentSettingsRevisionEnvelope> {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: getApplicationPlanningUrl(), threadId })
  const userMessage: Message = {
    id: randomUUID(),
    role: 'user',
    content: messageContent
  }
  agent.addMessage(userMessage)
  let envelope: AgentSettingsRevisionEnvelope | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'agent-settings-revision') return
      envelope = readEnvelope(event.value) ?? envelope
    },
    onStateSnapshotEvent: ({ event }) => {
      envelope = readEnvelope(
        (event.snapshot as { agentSettingsRevision?: unknown })?.agentSettingsRevision
      ) ?? envelope
    }
  }
  const result = await agent.runAgent(
    { forwardedProps: { agentSettingsRevision: input } },
    subscriber
  )
  envelope = readEnvelope(
    (result.result as { agentSettingsRevision?: unknown })?.agentSettingsRevision
  ) ?? envelope
  if (!envelope) throw new Error('Agent Settings 接口没有返回有效的 AG-UI 状态。')
  if (envelope.status === 'failed') {
    throw new Error(envelope.error?.message || 'Agent Settings 操作失败。')
  }
  return envelope
}

/** 校验 AG-UI action 的公共响应信封。 */
function readEnvelope(value: unknown): AgentSettingsRevisionEnvelope | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<AgentSettingsRevisionEnvelope>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as AgentSettingsRevisionEnvelope
}

/** 校验成功响应具备可供确认的完整预览身份。 */
function toPreview(payload: AgentSettingsRevisionEnvelope): AgentSettingsRevisionPreview {
  if (
    payload.active !== true ||
    typeof payload.changeId !== 'string' ||
    typeof payload.agentId !== 'string' ||
    typeof payload.basedOnContractHash !== 'string' ||
    typeof payload.candidateContractHash !== 'string' ||
    !Array.isArray(payload.changedSections) ||
    !Array.isArray(payload.fieldDiffs) ||
    typeof payload.draftSha256 !== 'string' ||
    typeof payload.basedOnLifecycleRevision !== 'number' ||
    !payload.agentSettings ||
    !payload.impact
  ) {
    throw new Error('Agent Settings 接口没有返回完整的修改预览。')
  }
  return payload as AgentSettingsRevisionPreview
}
