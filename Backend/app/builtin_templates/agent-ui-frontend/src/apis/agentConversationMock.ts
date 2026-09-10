import type {
  AgentApprovalRequest,
  AgentConversationAdapter,
  AgentMessage,
  AgentRunResult,
  AgentRunStatus,
  ConversationThread,
  ResolveApprovalInput,
  SendMessageInput
} from '@/typings/agentConversation'

type AgentMockScenario = 'success' | 'tool' | 'approval' | 'error'

interface MockAgentConversationAdapterOptions {
  agentName: string
  assistantMessage: string
  toolTitle: string
  toolDetail: string
  approvalTitle: string
  approvalDetail: string
  successMessage: string
  errorMessage: string
  latencyMs?: number
}

interface StoredRun {
  input: SendMessageInput
  status: AgentRunStatus
  scenario: AgentMockScenario
}

const DEFAULT_LATENCY_MS = 420

/** 返回当前时间的稳定 ISO 文本。 */
function nowIso(): string {
  return new Date().toISOString()
}

/** 创建仅用于当前内存 Mock 的稳定唯一标识。 */
function createMockId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

/** 等待受控的本地模拟延迟，不发起任何网络请求。 */
function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, milliseconds))
}

/** 深复制会话消息，避免调用方修改适配器内部状态。 */
function cloneMessages(messages: AgentMessage[]): AgentMessage[] {
  return messages.map((message) => ({
    ...message,
    tool: message.tool ? { ...message.tool } : undefined,
    approval: message.approval ? { ...message.approval } : undefined
  }))
}

/** 根据明确的模拟问题选择可复现的本地运行场景。 */
function scenarioForContent(content: string): AgentMockScenario {
  if (content.includes('模拟失败')) return 'error'
  if (content.includes('模拟审批')) return 'approval'
  if (content.includes('模拟工具')) return 'tool'
  return 'success'
}

/** 提供不含 HTTP、SSE 或持久化的生成应用 Agent 会话 Mock。 */
export class MockAgentConversationAdapter implements AgentConversationAdapter {
  private readonly threads: ConversationThread[] = []
  private readonly messages = new Map<string, AgentMessage[]>()
  private readonly runs = new Map<string, StoredRun>()
  private readonly options: Required<MockAgentConversationAdapterOptions>

  /** 保存固定 Mock 文案和延迟配置。 */
  constructor(options: MockAgentConversationAdapterOptions) {
    this.options = {
      ...options,
      latencyMs: options.latencyMs ?? DEFAULT_LATENCY_MS
    }
  }

  /** 返回按最近更新时间倒序排列的会话副本。 */
  async listThreads(): Promise<ConversationThread[]> {
    return [...this.threads]
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
      .map((thread) => ({ ...thread }))
  }

  /** 创建并登记一个新的本地空白会话。 */
  async createThread(): Promise<ConversationThread> {
    const createdAt = nowIso()
    const thread: ConversationThread = {
      id: createMockId('thread'),
      title: '新会话',
      createdAt,
      updatedAt: createdAt
    }
    this.threads.push(thread)
    this.messages.set(thread.id, [])
    return { ...thread }
  }

  /** 读取指定会话的不可变消息快照。 */
  async loadMessages(threadId: string): Promise<AgentMessage[]> {
    const messages = this.messages.get(threadId)
    if (!messages) throw new Error('模拟会话不存在，请新建会话后重试。')
    return cloneMessages(messages)
  }

  /** 执行一次可停止的本地模拟运行。 */
  async sendMessage(input: SendMessageInput): Promise<AgentRunResult> {
    return this.executeRun(input, scenarioForContent(input.content))
  }

  /** 把仍在运行的本地任务标记为停止。 */
  async stop(runId: string): Promise<void> {
    const run = this.runs.get(runId)
    if (!run) throw new Error('找不到要停止的模拟运行。')
    if (run.status === 'running') run.status = 'stopped'
  }

  /** 以成功场景重试失败或停止的运行。 */
  async retry(runId: string, retryRunId: string): Promise<AgentRunResult> {
    const run = this.runs.get(runId)
    if (!run) throw new Error('找不到要重试的模拟运行。')
    if (run.status !== 'error' && run.status !== 'stopped') {
      throw new Error('只有失败或停止的模拟运行可以重试。')
    }
    const retryInput = {
      ...run.input,
      runId: retryRunId
    }
    return this.executeRun(retryInput, 'success')
  }

  /** 在待审批运行中登记批准或拒绝，并返回新的完整消息快照。 */
  async resolveApproval(input: ResolveApprovalInput): Promise<AgentRunResult> {
    const run = this.runs.get(input.runId)
    if (!run || run.status !== 'waiting_approval') {
      throw new Error('当前模拟运行没有待处理审批。')
    }
    const messages = this.messages.get(run.input.threadId) ?? []
    const message = messages.find(
      (item) => item.approval?.id === input.approvalId
    )
    if (!message?.approval) throw new Error('找不到待处理的模拟审批。')
    message.approval = {
      ...message.approval,
      status: input.approved ? 'approved' : 'rejected'
    }
    if (input.approved) {
      run.status = 'completed'
      messages.push(this.assistantMessage(run.input, 'completed', this.options.successMessage))
    } else {
      run.status = 'stopped'
    }
    this.touchThread(run.input.threadId)
    return this.result(input.runId, run.input.threadId, run.status)
  }

  /** 执行单个场景，并在完成前复核停止标记。 */
  private async executeRun(
    input: SendMessageInput,
    scenario: AgentMockScenario
  ): Promise<AgentRunResult> {
    const messages = this.messages.get(input.threadId)
    if (!messages) throw new Error('模拟会话不存在，请新建会话后重试。')
    const run: StoredRun = { input: { ...input }, status: 'running', scenario }
    this.runs.set(input.runId, run)
    messages.push({
      id: createMockId('message'),
      threadId: input.threadId,
      role: 'user',
      author: '你',
      content: input.content.trim(),
      createdAt: nowIso(),
      status: 'completed'
    })
    this.renameThreadFromMessage(input.threadId, input.content)
    await delay(this.options.latencyMs)
    if (run.status === 'stopped') {
      messages.push(this.assistantMessage(input, 'stopped', '已停止本次模拟运行。'))
      this.touchThread(input.threadId)
      return this.result(input.runId, input.threadId, 'stopped')
    }
    this.completeScenario(run, messages)
    this.touchThread(input.threadId)
    return this.result(input.runId, input.threadId, run.status)
  }

  /** 按场景写入助手、Tool、审批或错误消息。 */
  private completeScenario(run: StoredRun, messages: AgentMessage[]): void {
    const { input, scenario } = run
    if (scenario === 'error') {
      run.status = 'error'
      messages.push(this.assistantMessage(input, 'error', this.options.errorMessage))
      return
    }
    if (scenario === 'approval') {
      run.status = 'waiting_approval'
      const approval: AgentApprovalRequest = {
        id: createMockId('approval'),
        title: this.options.approvalTitle,
        detail: this.options.approvalDetail,
        status: 'pending'
      }
      messages.push({
        ...this.assistantMessage(input, 'waiting_approval', this.options.assistantMessage),
        approval
      })
      return
    }
    run.status = 'completed'
    messages.push({
      ...this.assistantMessage(input, 'completed', this.options.assistantMessage),
      tool:
        scenario === 'tool'
          ? {
              id: createMockId('tool'),
              title: this.options.toolTitle,
              detail: this.options.toolDetail,
              status: 'completed'
            }
          : undefined
    })
  }

  /** 创建统一的助手消息结构。 */
  private assistantMessage(
    input: SendMessageInput,
    status: AgentRunStatus,
    content: string
  ): AgentMessage {
    return {
      id: createMockId('message'),
      threadId: input.threadId,
      role: 'assistant',
      author: this.options.agentName,
      content,
      createdAt: nowIso(),
      status
    }
  }

  /** 返回指定运行的稳定结果快照。 */
  private result(
    runId: string,
    threadId: string,
    status: AgentRunStatus
  ): AgentRunResult {
    return {
      runId,
      threadId,
      status,
      messages: cloneMessages(this.messages.get(threadId) ?? []),
      error: status === 'error' ? this.options.errorMessage : undefined
    }
  }

  /** 用第一条真实提问更新空白会话标题。 */
  private renameThreadFromMessage(threadId: string, content: string): void {
    const thread = this.threads.find((item) => item.id === threadId)
    if (!thread || thread.title !== '新会话') return
    thread.title = content.trim().slice(0, 24) || '新会话'
  }

  /** 更新会话时间用于最近会话排序。 */
  private touchThread(threadId: string): void {
    const thread = this.threads.find((item) => item.id === threadId)
    if (thread) thread.updatedAt = nowIso()
  }
}
