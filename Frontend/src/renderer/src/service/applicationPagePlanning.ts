import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  WorkflowConfirmationArtifact
} from '../typings'
import { AgUiChatSession } from './agUiAgent'
import { createAgUiHttpAgent } from './authentication'

type RequirementSpecDraftPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'save'
  requirementSpec?: Record<string, unknown>
  artifact?: WorkflowConfirmationArtifact
  error?: { message?: string }
}

type ApplicationLifecyclePayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'create' | 'get' | 'complete_template_generation'
  lifecycle?: ApplicationLifecycle
  error?: { message?: string }
}

// 读取创建规划两节点 Graph 的 AG-UI 地址。
function getApplicationPlanningUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/application-page-planning/run`
    : '/api/agent/application-page-planning/run'
}

// 为一次新建应用规划会话创建稳定线程标识。
export function createPagePlanningThreadId(): string {
  return randomUUID()
}

// 创建复用标准 Workflow AG-UI 协议的独立规划会话。
export function createApplicationPlanningSession(threadId: string): AgUiChatSession {
  return new AgUiChatSession(threadId, getApplicationPlanningUrl())
}

// 校验生命周期 AG-UI 动作的统一响应信封。
function readApplicationLifecyclePayload(
  value: unknown
): ApplicationLifecyclePayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<ApplicationLifecyclePayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as ApplicationLifecyclePayload
}

// 从 AG-UI StateSnapshot 中读取生命周期动作结果。
function readApplicationLifecycleState(value: unknown): ApplicationLifecyclePayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readApplicationLifecyclePayload(
    (value as { applicationLifecycle?: unknown }).applicationLifecycle
  )
}

// 通过同一 AG-UI 端点创建、读取或更新应用生命周期。
async function runApplicationLifecycleAction(
  threadId: string,
  action: Record<string, unknown>
): Promise<ApplicationLifecycle> {
  const agent = createAgUiHttpAgent({ url: getApplicationPlanningUrl(), threadId })
  agent.addMessage({ id: randomUUID(), role: 'user', content: '同步应用生命周期状态。' })
  let payload: ApplicationLifecyclePayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'application-lifecycle') return
      payload = readApplicationLifecyclePayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readApplicationLifecycleState(event.snapshot) ?? payload
    }
  }
  const result = await agent.runAgent(
    { forwardedProps: { applicationLifecycle: action } },
    subscriber
  )
  payload = readApplicationLifecycleState(result.result) ?? payload
  if (!payload) throw new Error('生命周期接口没有返回有效的 AG-UI 状态。')
  if (payload.status === 'failed') {
    throw new Error(payload.error?.message || '生命周期操作失败。')
  }
  if (!payload.lifecycle) throw new Error('生命周期接口没有返回 lifecycle。')
  return payload.lifecycle
}

// 为新应用显式创建生命周期状态，不读取或推断旧数据。
export async function createApplicationLifecycle(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationLifecycle> {
  if (!application.workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  return runApplicationLifecycleAction(threadId, {
    action: 'create',
    workspaceRoot: application.workspaceRoot,
    application: { id: application.id, appName: application.appName }
  })
}

// 读取新应用已经存在的权威生命周期，不为缺失状态创建兼容快照。
export async function getApplicationLifecycle(
  application: ApplicationConfig,
  threadId = createPagePlanningThreadId()
): Promise<ApplicationLifecycle> {
  if (!application.workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  return runApplicationLifecycleAction(threadId, {
    action: 'get',
    workspaceRoot: application.workspaceRoot
  })
}

// 把应用模板文件的真实生成结果提交给后端，由状态机决定 ready 或 failed。
export async function completeApplicationTemplateGeneration(
  application: ApplicationConfig,
  threadId: string,
  succeeded: boolean,
  errorMessage?: string
): Promise<ApplicationLifecycle> {
  if (!application.workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  return runApplicationLifecycleAction(threadId, {
    action: 'complete_template_generation',
    workspaceRoot: application.workspaceRoot,
    succeeded,
    errorMessage
  })
}

// 校验需求文档草稿保存动作的 AG-UI 响应信封。
function readRequirementSpecDraftPayload(value: unknown): RequirementSpecDraftPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<RequirementSpecDraftPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as RequirementSpecDraftPayload
}

// 从 AG-UI 状态快照中读取需求文档草稿保存结果。
function readRequirementSpecDraftState(value: unknown): RequirementSpecDraftPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readRequirementSpecDraftPayload(
    (value as { requirementSpecDraft?: unknown }).requirementSpecDraft
  )
}

// 调用规划端点的独立保存动作，在不确认需求的前提下重写 Markdown 与 JSON。
export async function saveRequirementSpecDraft(
  workspaceRoot: string,
  spec: Record<string, unknown>,
  threadId: string
): Promise<{
  artifact: WorkflowConfirmationArtifact
  requirementSpec: Record<string, unknown>
}> {
  const agent = createAgUiHttpAgent({ url: getApplicationPlanningUrl(), threadId })
  const userMessage: Message = {
    id: randomUUID(),
    role: 'user',
    content: '保存当前需求文档编辑草稿。'
  }
  agent.addMessage(userMessage)

  let draftPayload: RequirementSpecDraftPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'requirement-spec-draft') return
      draftPayload = readRequirementSpecDraftPayload(event.value) ?? draftPayload
    },
    onStateSnapshotEvent: ({ event }) => {
      draftPayload = readRequirementSpecDraftState(event.snapshot) ?? draftPayload
    }
  }
  const result = await agent.runAgent(
    { forwardedProps: { requirementSpecDraft: { action: 'save', workspaceRoot, spec } } },
    subscriber
  )
  draftPayload = readRequirementSpecDraftState(result.result) ?? draftPayload
  if (!draftPayload) throw new Error('需求文档保存接口没有返回有效的 AG-UI 状态。')
  if (draftPayload.status === 'failed') {
    throw new Error(draftPayload.error?.message || '需求文档保存失败。')
  }
  if (!draftPayload.artifact || !draftPayload.requirementSpec) {
    throw new Error('需求文档保存接口没有返回更新后的文档。')
  }
  return {
    artifact: draftPayload.artifact,
    requirementSpec: draftPayload.requirementSpec
  }
}

// 把创建表单配置转换为 requirements 节点可直接分析的原始需求。
export function buildApplicationPlanningRequest(application: ApplicationConfig): string {
  // 兼容旧应用索引只在 schema 内保存完整配置的情况，避免规划弹窗因缺字段直接白屏。
  const schema = application.schema || ({} as ApplicationConfig['schema'])
  const appName = application.appName || schema.appName || application.name || '未命名应用'
  const scenario = application.senario || schema.senario || '用户暂未补充场景说明。'
  const terminal = application.terminal || schema.terminal || 'PC'
  const layout = application.layout ||
    schema.layout || {
      type: '',
      useHeader: true,
      useFooter: false
    }
  const datasource = application.datasource?.type || schema.datasource?.type || 'None'
  const authEnabled = application.auth?.enable ?? schema.auth?.enable ?? false
  return [
    `请为新应用「${appName}」完成需求确认和项目规划。`,
    `应用场景：${scenario}`,
    `目标终端：${terminal}。`,
    `导航布局：${layout.type || '由规划阶段确定'}，页头=${layout.useHeader ? '启用' : '禁用'}，页脚=${layout.useFooter ? '启用' : '禁用'}。`,
    `数据源偏好：${datasource}。`,
    `认证：${authEnabled ? '启用' : '不启用'}。`,
    '本轮只完成需求文档和项目计划，不生成页面细节或代码。'
  ].join('\n')
}
