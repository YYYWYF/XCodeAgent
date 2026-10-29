export const AGENT_UI_FRONTEND_TEMPLATE_VERSION = 'agent-ui.v1' as const

export type AgentSurfaceType = 'standalone_page' | 'floating_panel'
export type AgentRunStatus =
  | 'idle'
  | 'running'
  | 'waiting_approval'
  | 'stopped'
  | 'error'
  | 'completed'

export interface AgentContextItem {
  id: string
  label: string
  value: string
}

export interface AgentCapability {
  id: string
  label: string
}

export interface AgentUiFeatureFlags {
  attachments: boolean
  approvals: boolean
  tools: boolean
  maximize: boolean
}

export interface AgentUiMockContent {
  userMessage: string
  assistantMessage: string
  toolTitle: string
  toolDetail: string
  approvalTitle: string
  approvalDetail: string
  successMessage: string
  errorMessage: string
}

export interface AgentUiTemplateConfig {
  templateVersion: typeof AGENT_UI_FRONTEND_TEMPLATE_VERSION
  agentId: string
  surface: AgentSurfaceType
  name: string
  responsibility: string
  actionId: string
  contextItems: AgentContextItem[]
  capabilities: AgentCapability[]
  suggestedQuestions: string[]
  features: AgentUiFeatureFlags
  mock: AgentUiMockContent
}

export interface ConversationThread {
  id: string
  title: string
  createdAt: string
  updatedAt: string
}

export interface AgentToolSummary {
  id: string
  title: string
  detail: string
  status: 'running' | 'completed' | 'error'
}

export interface AgentApprovalRequest {
  id: string
  title: string
  detail: string
  status: 'pending' | 'approved' | 'rejected'
}

export interface AgentMessage {
  id: string
  threadId: string
  role: 'user' | 'assistant'
  author: string
  content: string
  createdAt: string
  status: AgentRunStatus
  tool?: AgentToolSummary
  approval?: AgentApprovalRequest
}

export interface SendMessageInput {
  runId: string
  threadId: string
  agentId: string
  actionId: string
  content: string
  contextItems: AgentContextItem[]
}

export interface ResolveApprovalInput {
  runId: string
  approvalId: string
  approved: boolean
}

export interface AgentRunResult {
  runId: string
  threadId: string
  status: AgentRunStatus
  messages: AgentMessage[]
  error?: string
}

export interface AgentConversationAdapter {
  /** 列出当前适配器可见的会话。 */
  listThreads(): Promise<ConversationThread[]>
  /** 创建一个空白会话。 */
  createThread(): Promise<ConversationThread>
  /** 加载指定会话的完整消息快照。 */
  loadMessages(threadId: string): Promise<AgentMessage[]>
  /** 提交一次带稳定 action 和上下文白名单的运行。 */
  sendMessage(input: SendMessageInput): Promise<AgentRunResult>
  /** 停止指定运行。 */
  stop(runId: string): Promise<void>
  /** 用新的运行标识重试指定失败或停止的运行。 */
  retry(runId: string, retryRunId: string): Promise<AgentRunResult>
  /** 处理指定运行中的审批请求。 */
  resolveApproval(input: ResolveApprovalInput): Promise<AgentRunResult>
}
