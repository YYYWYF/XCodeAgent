import { HttpAgent, randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type {
  AgentConversationAdapter, AgentMessage, AgentRunResult, AgentUiTemplateConfig,
  ConversationThread, PendingAgentInteraction, ResolveApprovalInput, SendMessageInput
} from '@/typings/agentConversation'

interface RuntimePayload {
  schemaVersion: 1
  agentId: string
  threadId: string
  runId: string
  status: 'completed' | 'awaiting_user' | 'failed'
  pendingInteraction?: PendingAgentInteraction | null
  error?: { message?: string }
}

interface RuntimeThread {
  info: ConversationThread
  agent: HttpAgent
  result: AgentRunResult
  active?: { runId: string; stopped: boolean; pendingInteraction?: PendingAgentInteraction }
}

/** 校验当前业务载荷身份；AG-UI 事件、快照与 delta 的解析由官方 SDK 负责。 */
function runtimePayload(value: unknown, agentId: string, threadId: string, runId: string): RuntimePayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const result = value as RuntimePayload
  if (result.schemaVersion !== 1 || result.agentId !== agentId || result.threadId !== threadId || result.runId !== runId
    || !['completed', 'awaiting_user', 'failed'].includes(result.status)) return undefined
  if (result.status === 'awaiting_user') {
    const pending = result.pendingInteraction
    if (!pending || pending.schemaVersion !== 1 || !pending.interactionId || typeof pending.message !== 'string'
      || !['clarification', 'confirmation', 'approval_required'].includes(pending.kind)
      || !pending.payload || typeof pending.payload !== 'object' || Array.isArray(pending.payload)) return undefined
  }
  return result
}

/** 使用正式同源 AG-UI 端点驱动共享会话组件；不持久化身份、不自动重放业务操作。 */
export class RuntimeAgentConversationAdapter implements AgentConversationAdapter {
  private readonly threads = new Map<string, RuntimeThread>()
  private readonly listeners = new Set<(result: AgentRunResult) => void>()
  private readonly messageTimes = new Map<string, string>()

  /** 只接受当前 Agent 的精确相对路径，禁止端口、凭据或外部 URL。 */
  constructor(private readonly config: AgentUiTemplateConfig) {
    if (!config.runtime || config.runtime.path !== `/agents/${config.agentId}/run`
      || !/^[a-z][a-z0-9_]*$/.test(config.agentId)) throw new Error('Agent Runtime 配置无效。')
  }

  /** 订阅官方消息流投影；卸载后移除监听，避免迟到事件修改其他会话。 */
  subscribe(listener: (result: AgentRunResult) => void): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  /** 只列出本组件创建的会话身份，不虚构服务端历史查询接口。 */
  async listThreads(): Promise<ConversationThread[]> {
    return [...this.threads.values()].map((item) => ({ ...item.info }))
  }

  /** 创建稳定 threadId；匿名身份只由同源 Runtime Cookie 提供。 */
  async createThread(): Promise<ConversationThread> {
    const id = randomUUID()
    const now = new Date().toISOString()
    const info = { id, title: '新会话', createdAt: now, updatedAt: now }
    this.threads.set(id, { info, agent: new HttpAgent({ url: this.config.runtime!.path, threadId: id }),
      result: { runId: '', threadId: id, status: 'idle', messages: [] } })
    return { ...info }
  }

  /** 返回官方客户端已收到的消息快照，不发送额外业务请求。 */
  async loadMessages(threadId: string): Promise<AgentMessage[]> {
    return this.messages(this.thread(threadId))
  }

  /** 恢复当前会话的业务状态与待交互信息，不发起新模型调用。 */
  async loadRun(threadId: string): Promise<AgentRunResult> {
    const thread = this.thread(threadId)
    return { ...thread.result, messages: this.messages(thread) }
  }

  /** 只读读取当前 checkpoint，不恢复中断、不追加消息、不调用业务操作。 */
  async refreshRun(threadId: string, runId: string): Promise<AgentRunResult> {
    const thread = this.thread(threadId)
    if (thread.active) throw new Error('当前会话仍在运行。')
    return this.execute(thread, runId, { inspectInteraction: true })
  }

  /** 普通提问只能使用当前配置声明的 Agent 与 action，等待交互时禁止开启另一轮。 */
  async sendMessage(input: SendMessageInput): Promise<AgentRunResult> {
    const thread = this.thread(input.threadId)
    if (thread.result.requiresSync) throw new Error('请先同步服务端会话状态。')
    if (input.agentId !== this.config.agentId || input.actionId !== this.config.actionId) throw new Error('Agent 或操作与当前页面配置不一致。')
    if (thread.result.pendingInteraction) throw new Error('请先处理当前待确认或待回答事项。')
    if (thread.active) throw new Error('当前会话仍在运行。')
    thread.agent.addMessage({ id: randomUUID(), role: 'user', content: input.content })
    thread.info.title = input.content.slice(0, 30)
    return this.execute(thread, input.runId, this.pageContext())
  }

  /** 中止当前网络流；已提交的业务操作不被当作撤销，也不会自动重发。 */
  async stop(runId: string): Promise<void> {
    const thread = [...this.threads.values()].find((item) => item.active?.runId === runId)
    if (!thread?.active) return
    thread.active.stopped = true
    thread.agent.abortRun()
    this.publish(thread, { runId, threadId: thread.info.id, status: 'stopped', messages: this.messages(thread),
      pendingInteraction: thread.active.pendingInteraction, requiresSync: true })
  }

  /** 网络中断后的执行结果可能未知，禁止把写操作作为普通重试重放。 */
  async retry(_runId: string, _retryRunId: string): Promise<AgentRunResult> {
    throw new Error('运行结果可能尚未确定，请核对业务状态；系统不会自动重放本次操作。')
  }

  /** 将明确批准或拒绝映射到当前服务端交互身份。 */
  async resolveApproval(input: ResolveApprovalInput): Promise<AgentRunResult> {
    return this.resolveInteraction({ runId: input.runId, interactionId: input.approvalId, value: input.approved })
  }

  /** 以新协议运行恢复同一 thread/checkpoint；澄清答案不当作批准。 */
  async resolveInteraction(input: { runId: string; interactionId: string; value: unknown; responseRunId?: string }): Promise<AgentRunResult> {
    const thread = [...this.threads.values()].find((item) => item.result.runId === input.runId)
    const pending = thread?.result.pendingInteraction
    if (!thread || thread.active || thread.result.requiresSync || !pending || pending.interactionId !== input.interactionId) throw new Error('请先同步会话，并使用当前待处理事项。')
    if (pending.kind === 'clarification' ? typeof input.value !== 'string' || !input.value.trim() : typeof input.value !== 'boolean') {
      throw new Error('澄清必须填写回答，确认或审批必须明确选择同意或拒绝。')
    }
    return this.execute(thread, input.responseRunId || randomUUID(), {
      ...this.pageContext(),
      interactionResponse: { interactionId: pending.interactionId, value: input.value }
    })
  }

  /** 每次恢复都携带当前页面声明的上下文，不把用户输入当作身份或权限。 */
  private pageContext(): Record<string, unknown> {
    return { pageId: this.config.runtime!.pageId, actionId: this.config.actionId,
      contextItems: this.config.contextItems.map((item) => ({ ...item })) }
  }

  /** 严格读取当前组件创建的会话，禁止隐式创建或跨会话使用 checkpoint。 */
  private thread(threadId: string): RuntimeThread {
    const thread = this.threads.get(threadId)
    if (!thread) throw new Error('当前会话不存在，请新建会话。')
    return thread
  }

  /** 将官方 Message 类型投影为展示模型，忽略非用户正文及内部工具参数。 */
  private messages(thread: RuntimeThread): AgentMessage[] {
    return thread.agent.messages.flatMap((message: Message) => {
      if ((message.role !== 'user' && message.role !== 'assistant') || typeof message.content !== 'string') return []
      if (!this.messageTimes.has(message.id)) this.messageTimes.set(message.id, new Date().toISOString())
      return [{ id: message.id, threadId: thread.info.id, role: message.role,
        author: message.role === 'user' ? '我' : this.config.name, content: message.content,
        createdAt: this.messageTimes.get(message.id)!, status: thread.result.status }]
    })
  }

  /** 发布不可变展示快照，不把监听者的对象回写官方客户端状态。 */
  private publish(thread: RuntimeThread, result: AgentRunResult): AgentRunResult {
    thread.result = result
    thread.info.updatedAt = new Date().toISOString()
    const snapshot = { ...result, messages: result.messages.map((message) => ({ ...message })) }
    for (const listener of this.listeners) listener(snapshot)
    return snapshot
  }

  /** 执行一次完整 AG-UI 生命周期，只有匹配身份的业务结果和运行结束才构成终态。 */
  private async execute(thread: RuntimeThread, runId: string, forwardedProps: Record<string, unknown>): Promise<AgentRunResult> {
    const active = { runId, stopped: false, pendingInteraction: thread.result.pendingInteraction }
    thread.active = active
    let payload: RuntimePayload | undefined
    let finished = false
    let protocolError: string | undefined
    /** 仅在当前运行仍有效时刷新流式正文。 */
    const progress = (): void => {
      if (thread.active === active && !active.stopped) this.publish(thread, {
        runId, threadId: thread.info.id, status: 'running', messages: this.messages(thread),
        pendingInteraction: active.pendingInteraction
      })
    }
    /** 接受匹配当前 Agent、thread 和 run 的业务状态。 */
    const receive = (value: unknown): void => {
      payload = runtimePayload(value, this.config.agentId, thread.info.id, runId) ?? payload
    }
    const subscriber: AgentSubscriber = {
      onMessagesChanged: progress,
      onCustomEvent: ({ event }) => {
        if (event.name === 'agent_runtime_result.v1' || event.name === 'agent_runtime_error.v1') receive(event.value)
      },
      onStateChanged: ({ state }) => { receive((state as { agentRuntime?: unknown }).agentRuntime) },
      onRunFinishedEvent: () => { finished = true },
      onRunErrorEvent: ({ event }) => { protocolError = event.message }
    }
    this.publish(thread, { runId, threadId: thread.info.id, status: 'running', messages: this.messages(thread),
      pendingInteraction: active.pendingInteraction })
    try {
      await thread.agent.runAgent({ runId, forwardedProps }, subscriber)
      if (active.stopped) return thread.result
      if (!finished || !payload || protocolError) throw new Error(protocolError || 'Runtime 未返回完整业务结果，请核对业务状态。')
      const status = payload.status === 'failed' ? 'error' : payload.status === 'awaiting_user' ? 'waiting_input' : 'completed'
      return this.publish(thread, { runId, threadId: thread.info.id, status,
        messages: this.messages(thread), pendingInteraction: payload.pendingInteraction ?? (payload.status === 'failed' ? active.pendingInteraction : undefined),
        requiresSync: payload.status === 'failed',
        error: payload.status === 'failed' ? payload.error?.message || 'Agent 运行失败。' : undefined })
    } catch (reason) {
      if (active.stopped) return thread.result
      return this.publish(thread, { runId, threadId: thread.info.id, status: 'error', messages: this.messages(thread),
        pendingInteraction: active.pendingInteraction, requiresSync: true,
        error: reason instanceof Error ? reason.message : 'Agent 连接中断，请核对业务状态。' })
    } finally {
      if (thread.active === active) thread.active = undefined
    }
  }
}
