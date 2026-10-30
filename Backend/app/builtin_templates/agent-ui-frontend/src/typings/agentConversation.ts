export const AGENT_UI_FRONTEND_TEMPLATE_VERSION = 'agent-ui.v1' as const

export type AgentSurfaceType = 'standalone_page' | 'floating_panel'
export type AgentRunStatus =
  | 'idle'
  | 'running'
  | 'waiting_approval'
  | 'waiting_input'
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
  runtime?: { path: string; pageId: string }
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
  pendingInteraction?: PendingAgentInteraction
  requiresSync?: boolean
}

export interface PendingAgentInteraction {
  schemaVersion: 1
  interactionId: string
  kind: 'clarification' | 'confirmation' | 'approval_required'
  message: string
  payload: Record<string, unknown>
}

export interface AgentConversationAdapter {
  /** 只读同步 checkpoint 状态，结果未知时不得直接重放上一操作。 */
  refreshRun?(threadId: string, runId: string): Promise<AgentRunResult>
  /** 读取同一会话的完整运行快照，切换会话时恢复待交互事项。 */
  loadRun?(threadId: string): Promise<AgentRunResult>
  /** 订阅真实运行的消息及状态增量；离线设计适配器无需网络订阅。 */
  subscribe?(listener: (result: AgentRunResult) => void): () => void
  /** 回答当前服务端待交互项，恢复同一逻辑会话。 */
  resolveInteraction?(input: { runId: string; interactionId: string; value: unknown; responseRunId?: string }): Promise<AgentRunResult>
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
