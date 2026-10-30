import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'
import { AgUiBusinessError } from './agUiBusinessError'
import type { ApplicationLifecycle, DevelopmentArtifactProgress, WorkbenchExecution } from '../typings'

export const runtimeModuleLabels = {
  prompt: '提示词', model: '模型', memory: '记忆', tools: '工具',
  skills: '技能', knowledge: '知识', context: '上下文'
} as const
export type RuntimeAgent = {
  agentId: string; name: string; purpose: string; pageIds: string[]; contractHash: string
  agentSettings: Record<keyof typeof runtimeModuleLabels, Record<string, unknown>>
  development?: {
    progress?: DevelopmentArtifactProgress | null
    modules: { module: string; label: string; status: string; reason?: string | null; files: string[] }[]
    execution?: WorkbenchExecution | null
  }
}
export type SettingsPreview = {
  changeId: string; agentId: string; lifecycleRevision: number; draftSha256: string
  before: EditableAgentSettings; after: EditableAgentSettings; pageIds: string[]; markdown: string
  fieldDiffs: { field: string; label: string; before: unknown; after: unknown }[]
}
export type AgentPromptSettings = { systemPrompt: string; persona?: { role: string; tone: string }; constraints?: string[] }
export type EditableAgentSettings = { prompt: AgentPromptSettings; temperature: number | null }
export type AgentSettingsPatch = { prompt?: AgentPromptSettings; model?: { generation: { temperature: number } | null } }
export type SettingsAction =
  | { action: 'get_settings'; agentId: string }
  | { action: 'preview_settings'; agentId: string; contractHash: string; technicalPlanSha256: string; settingsPatch: AgentSettingsPatch }
  | { action: 'confirm_settings' | 'cancel_settings'; agentId: string; changeId: string; lifecycleRevision: number; draftSha256: string }
export type RuntimeSettingsResult = {
  editableSettings?: EditableAgentSettings
  contractHash?: string; technicalPlanSha256?: string; preview?: SettingsPreview | null
  agent?: RuntimeAgent; lifecycle?: ApplicationLifecycle; saved?: boolean; cancelled?: boolean
}
type Payload = RuntimeSettingsResult & {
  schemaVersion: 1; runId: string; threadId: string; status: 'completed' | 'failed'
  agents?: RuntimeAgent[]; runtimeEnabled?: boolean
  error?: { message?: string }
}

/** 校验业务信封，传输和事件解析全部交给官方 AG-UI 客户端。 */
function readPayload(value: unknown): Payload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const data = value as Partial<Payload>
  return data.schemaVersion === 1 && typeof data.runId === 'string' && typeof data.threadId === 'string'
    && ['completed', 'failed'].includes(String(data.status)) ? data as Payload : undefined
}

/** 通过独立 AG-UI 会话读取 Agent 目录，业务调试统一由应用预览承载。 */
export async function requestRuntimeWorkbench(workspaceRoot: string,
  action: 'list'
): Promise<Payload> {
  return runWorkbench({ workspaceRoot, action })
}

/** 设置编辑使用相同官方 AG-UI 通道，确认仅回传服务端草稿身份。 */
export async function requestRuntimeSettings(workspaceRoot: string, input: SettingsAction): Promise<Payload> {
  return runWorkbench({ workspaceRoot, ...input })
}

/** 发出一次独立操作并读取完整业务终态，不向聊天输入框写入指令。 */
async function runWorkbench(input: { workspaceRoot: string; action: 'list' } | (SettingsAction & { workspaceRoot: string })): Promise<Payload> {
  const base = window.devAgentStudio?.agentBaseUrl
  const agent = createAgUiHttpAgent({ url: base ? `${base.replace(/\/$/, '')}/agent-runtime-workbench/run`
    : '/api/agent/agent-runtime-workbench/run', threadId: randomUUID() })
  agent.addMessage({ id: randomUUID(), role: 'user', content: '读取或更新当前应用的 Agent 配置。' })
  let payload: Payload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'agent-runtime-workbench') payload = readPayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readPayload((event.snapshot as { agentRuntimeWorkbench?: unknown }).agentRuntimeWorkbench) ?? payload
    }
  }
  await agent.runAgent({ forwardedProps: { agentRuntimeWorkbench: input } }, subscriber)
  if (!payload) throw new Error('Runtime 工作台未返回完整 AG-UI 结果。')
  if (payload.status === 'failed') throw new AgUiBusinessError(payload.error?.message || 'Runtime 操作失败。')
  return payload
}
