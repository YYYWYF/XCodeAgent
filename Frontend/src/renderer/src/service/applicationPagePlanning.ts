import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type {
  ApplicationConfig,
  WorkflowConfirmationArtifact
} from '../typings'
import { DatasourceEnum } from '../typings'
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
  const datasource = application.datasource?.type || schema.datasource?.type || DatasourceEnum.DB
  const authEnabled = application.auth?.enable ?? schema.auth?.enable ?? false
  return [
    `请为新应用「${appName}」完成需求确认和项目规划。`,
    `应用场景：${scenario}`,
    `目标终端：${terminal}。`,
    `导航布局：${layout.type || '由规划阶段确定'}，页头=${layout.useHeader ? '启用' : '禁用'}，页脚=${layout.useFooter ? '启用' : '禁用'}。`,
    `数据库类型：${datasource}。`,
    `认证：${authEnabled ? '启用' : '不启用'}。`,
    '本轮只完成需求文档和项目计划，不生成页面细节或代码。'
  ].join('\n')
}
