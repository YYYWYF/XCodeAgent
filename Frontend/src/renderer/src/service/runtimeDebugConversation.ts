import { HttpAgent, randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { DebugCredential } from './runtimeWorkbench'

export type DebugPending = { interactionId: string; kind: 'clarification' | 'confirmation' | 'approval_required'; message: string }
export type DebugTool = { id: string; name: string; args: string; result: string; started: number; elapsed?: number }
export type DebugConversation = {
  status: 'idle' | 'running' | 'completed' | 'awaiting_user' | 'unknown'
  messages: { id: string; role: string; content: string }[]
  tools: DebugTool[]; pending?: DebugPending; error?: string
}

/** 调试仅保存有界的当前会话，不复用开发历史；网络中断后禁止重发同一次业务操作。 */
export class RuntimeDebugConversation {
  private client: HttpAgent
  private view: DebugConversation = { status: 'idle', messages: [], tools: [] }
  private running = false
  private stopped = false
  private timer?: ReturnType<typeof setTimeout>
  private receivedBytes = 0
  private readonly threadId = randomUUID()

  /** 只接受服务端签发的本机 Runtime 地址，凭据始终留在当前控制器内存中。 */
  constructor(private credential: DebugCredential, private agentId: string,
    private publish: (view: DebugConversation) => void) {
    const url = new URL(credential.url)
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || url.username || url.password || url.search || url.hash) throw new Error('调试地址不是受管本机 Runtime。')
    this.client = new HttpAgent({ url: `${url.origin}/agents/${encodeURIComponent(agentId)}/run`,
      threadId: this.threadId, headers: { Authorization: `Bearer ${credential.token}` },
      fetch: async (address, init) => {
        // 仅在官方 SDK 的 fetch 扩展点限制原始字节；事件解析仍完全交给 SDK。
        const response = await fetch(address, init)
        if (!response.body) return response
        const reader = response.body.getReader()
        let ended = false
        const body = new ReadableStream<Uint8Array>({
          pull: async (controller) => {
            try {
              const chunk = await reader.read()
              if (ended) return
              if (chunk.done) { ended = true; controller.close(); return }
              this.receivedBytes += chunk.value.byteLength
              if (this.receivedBytes > 512 * 1024) {
                ended = true
                // 业务层记录未知结果，正常关闭 SDK 输入流，避免底层流错误形成未处理拒绝。
                this.stop('调试响应累计超过 512 KB，已结束接收；结果可能未知，不会自动重发。')
                controller.close()
                await reader.cancel().catch(() => undefined)
                return
              }
              controller.enqueue(chunk.value)
            } catch {
              if (!ended) {
                ended = true
                this.stop('调试连接中断，结果可能未知，不会自动重发。')
                controller.close()
              }
            }
          },
          cancel: (reason) => { ended = true; return reader.cancel(reason).catch(() => undefined) }
        })
        return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers })
      } })
  }

  /** 发布轻量视图，合并频繁 token 更新，避免每个事件驱动 React 重绘。 */
  private update(immediate = false): void {
    if (immediate) { clearTimeout(this.timer); this.timer = undefined; this.publish({ ...this.view, tools: [...this.view.tools] }); return }
    this.timer ??= setTimeout(() => { this.timer = undefined; this.update(true) }, 80)
  }

  /** 显式中止客户端接收，不能将业务写入解释为撤销或执行成功。 */
  stop(reason = '已停止接收；服务端执行结果可能未知，系统不会自动重发。'): void {
    if (!this.running) return
    this.stopped = true
    this.client.abortRun()
    this.view = { ...this.view, status: 'unknown', pending: undefined, error: reason }
    this.update(true)
  }

  /** 卸载时移除发布回调并关闭网络流，凭据不进入任何持久化对象。 */
  dispose(): void {
    this.publish = () => undefined
    this.stop()
    clearTimeout(this.timer)
  }

  /** 普通问题和交互响应均使用新 runId；同一线程串行执行，不自动重试。 */
  async send(content: string, pageId: string, response?: string | boolean): Promise<void> {
    if (this.running || this.view.status === 'unknown') throw new Error('当前会话不可继续，请等待运行结束或新建会话。')
    if (Date.now() >= this.credential.expiresAt * 1000) throw new Error('调试凭据已过期，请停止后重新启动。')
    if (this.client.messages.length >= 60) throw new Error('当前会话已达 60 条消息上限，请新建会话。')
    const pending = this.view.pending
    if (pending && response === undefined) throw new Error('请先处理当前交互。')
    if (!pending && !content.trim()) return
    if (content.length > 8000) throw new Error('单次输入请控制在 8000 字以内。')
    if (pending?.kind === 'clarification' && (typeof response !== 'string' || !response.trim())) throw new Error('请输入澄清回答。')
    if (pending && pending.kind !== 'clarification' && typeof response !== 'boolean') throw new Error('请明确同意或拒绝。')
    if (!pending) this.client.addMessage({ id: randomUUID(), role: 'user', content })
    const runId = randomUUID()
    this.running = true; this.stopped = false
    this.view = { ...this.view, status: 'running', pending: undefined, error: undefined }
    let started = false, finished = false
    let result: { status: string; pendingInteraction?: DebugPending; error?: { message?: string } } | undefined
    let protocolError = ''
    /** 只消费当前 Agent/thread/run 的正式业务结果，其他自定义事件不持久化。 */
    const receive = (value: unknown): void => {
      if (!value || typeof value !== 'object') return
      const item = value as Record<string, unknown>
      if (item.schemaVersion === 1 && item.agentId === this.agentId && item.threadId === this.threadId
        && item.runId === runId && ['completed', 'awaiting_user', 'failed'].includes(String(item.status))) {
        result = item as typeof result
      }
    }
    const subscriber: AgentSubscriber = {
      onRunStartedEvent: ({ event }) => { started = event.runId === runId && event.threadId === this.threadId },
      onMessagesChanged: ({ messages }) => {
        if (this.stopped) return
        this.view.messages = messages.filter((m) => ['user', 'assistant'].includes(m.role) && typeof m.content === 'string')
          .slice(-60).map((m) => ({ id: m.id, role: m.role, content: String(m.content).slice(0, 32000) }))
        if (messages.length > 60) this.stop('会话达到消息上限，结果可能未知，请新建会话。')
        this.update()
      },
      onToolCallStartEvent: ({ event }) => {
        // 展示缓存仅保留最近 40 条，不把界面条数限制变成业务执行门禁。
        if (this.view.tools.length >= 40) this.view.tools.shift()
        this.view.tools.push({ id: event.toolCallId, name: event.toolCallName, args: '', result: '', started: Date.now() }); this.update()
      },
      onToolCallArgsEvent: ({ event }) => {
        const tool = this.view.tools.find((t) => t.id === event.toolCallId)
        if (tool) tool.args = (tool.args + event.delta).slice(0, 4096)
        this.update()
      },
      onToolCallResultEvent: ({ event }) => {
        const tool = this.view.tools.find((t) => t.id === event.toolCallId)
        if (tool) { tool.result = event.content.slice(0, 4096); tool.elapsed = Date.now() - tool.started }
        this.update()
      },
      onCustomEvent: ({ event }) => { if (['agent_runtime_result.v1', 'agent_runtime_error.v1'].includes(event.name)) receive(event.value) },
      onStateChanged: ({ state }) => {
        receive((state as { agentRuntime?: unknown }).agentRuntime)
      },
      onRunFinishedEvent: ({ event }) => {
        finished = event.runId === runId && event.threadId === this.threadId
        receive((event.result as { agentRuntime?: unknown } | undefined)?.agentRuntime)
      },
      onRunErrorEvent: ({ event }) => { protocolError = event.message }
    }
    this.update(true)
    try {
      const forwardedProps = pending ? { interactionResponse: { interactionId: pending.interactionId, value: response },
        pageId, actionId: 'debug_message', contextItems: [] } : { pageId, actionId: 'debug_message', contextItems: [] }
      await this.client.runAgent({ runId, forwardedProps }, subscriber)
      if (this.stopped) return
      if (!started || !finished || !result || protocolError) throw new Error(protocolError || '未收到完整运行终态，请核对业务结果。')
      if (result.status === 'failed') throw new Error(result.error?.message || 'Agent 执行失败，请核对业务结果。')
      const next = result.pendingInteraction
      if (result.status === 'awaiting_user' && (!next?.interactionId || typeof next.message !== 'string'
        || !['clarification', 'confirmation', 'approval_required'].includes(next.kind))) throw new Error('待交互数据不完整。')
      this.view = { ...this.view, status: result.status === 'awaiting_user' ? 'awaiting_user' : 'completed', pending: next }
    } catch (error) {
      if (!this.stopped) this.view = { ...this.view, status: 'unknown', pending: undefined,
        error: `${error instanceof Error ? error.message : '连接中断'}；结果可能未知，不会自动重发。` }
    } finally {
      this.running = false
      this.client.setState({})
      this.client.setMessages(this.view.status === 'unknown' ? [] : this.client.messages.slice(-60))
      this.update(true)
    }
  }
}
