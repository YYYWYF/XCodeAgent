import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Empty,
  Skeleton,
  Tag,
  Typography
} from 'antd'
import { createAgentConversationAdapter } from '@/apis/agentConversationAdapter'
export { createAgentConversationAdapter } from '@/apis/agentConversationAdapter'
import { AgentMessageBubble } from './AgentMessageBubble'
import { AgentInteraction } from './AgentInteraction'
import { AgentComposer } from './AgentComposer'
import type {
  AgentConversationAdapter,
  AgentMessage,
  AgentRunResult,
  AgentRunStatus,
  AgentUiTemplateConfig,
  ConversationThread,
  PendingAgentInteraction
} from '@/typings/agentConversation'

const { Text } = Typography

export interface AgentChatCoreProps {
  config: AgentUiTemplateConfig
  adapter?: AgentConversationAdapter
  threadId?: string
  onThreadCreated?: (thread: ConversationThread) => void
}

/** 创建当前组件提交给 Adapter 的运行标识。 */
function createRunId(): string {
  return `agent-run-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

/** 把运行状态映射为用户可读的状态。 */
function runStatusText(status: AgentRunStatus): string {
  const labels: Record<AgentRunStatus, string> = {
    idle: '等待提问',
    running: '运行中，可随时停止',
    waiting_approval: '等待审批',
    waiting_input: '等待用户处理',
    stopped: '运行已停止，请核对业务状态',
    error: '运行未完成',
    completed: '运行已完成'
  }
  return labels[status]
}

/** 渲染共享消息流、运行状态、审批、错误重试和 Composer。 */
export function AgentChatCore({
  config,
  adapter: providedAdapter,
  threadId,
  onThreadCreated
}: AgentChatCoreProps): React.ReactElement {
  const adapter = useMemo(
    () => providedAdapter ?? createAgentConversationAdapter(config),
    [config, providedAdapter]
  )
  const [activeThreadId, setActiveThreadId] = useState(threadId)
  const [messages, setMessages] = useState<AgentMessage[]>([])
  const [status, setStatus] = useState<AgentRunStatus>('idle')
  const [input, setInput] = useState('')
  const [error, setError] = useState<string>()
  const [pending, setPending] = useState<PendingAgentInteraction>()
  const [requiresSync, setRequiresSync] = useState(false)
  const activeRunId = useRef<string>()
  const lastRunId = useRef<string>()
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    const unsubscribe = adapter.subscribe?.((result) => {
      if (mounted.current && activeRunId.current === result.runId) applyResult(result)
    })
    return () => {
      mounted.current = false
      unsubscribe?.()
      const runId = activeRunId.current
      activeRunId.current = undefined
      if (runId) void adapter.stop(runId)
    }
  }, [adapter])

  useEffect(() => {
    let current = true
    /** 切换会话时从 Adapter 重载权威消息快照。 */
    async function loadThreadMessages(): Promise<void> {
      if (threadId && activeThreadId && threadId !== activeThreadId) {
        const runId = activeRunId.current
        activeRunId.current = undefined
        if (runId) await adapter.stop(runId)
      }
      setActiveThreadId(threadId)
      setError(undefined)
      setPending(undefined)
      setRequiresSync(false)
      if (!threadId) {
        setMessages([])
        setStatus('idle')
        return
      }
      try {
        if (adapter.loadRun) {
          const result = await adapter.loadRun(threadId)
          if (current && mounted.current) applyResult(result)
        } else {
          const nextMessages = await adapter.loadMessages(threadId)
          if (current && mounted.current) setMessages(nextMessages)
        }
      } catch (reason) {
        if (current && mounted.current) setError(reason instanceof Error ? reason.message : '会话加载失败。')
      }
    }
    void loadThreadMessages()
    return () => { current = false }
  }, [adapter, threadId])

  /** 流式消息和最终响应共用一处状态更新，防止业务等待状态被协议结束覆盖。 */
  function applyResult(result: AgentRunResult): void {
    lastRunId.current = result.runId || undefined
    setMessages(result.messages)
    setStatus(result.status)
    setError(result.error)
    setPending(result.pendingInteraction)
    setRequiresSync(result.requiresSync === true)
  }

  /** 结果未知时只读同步服务端状态，不重放提问、批准或拒绝。 */
  async function refreshRun(): Promise<void> {
    if (!activeThreadId || !adapter.refreshRun || activeRunId.current) return
    const runId = createRunId()
    activeRunId.current = runId
    setStatus('running')
    try {
      const result = await adapter.refreshRun(activeThreadId, runId)
      if (mounted.current && activeRunId.current === runId) applyResult(result)
    } catch (reason) {
      if (mounted.current && activeRunId.current === runId) {
        setStatus('error')
        setError(reason instanceof Error ? reason.message : '会话同步失败。')
      }
    } finally { if (activeRunId.current === runId) activeRunId.current = undefined }
  }

  /** 确保提交前已经存在一个 Adapter 管理的会话。 */
  async function ensureThread(): Promise<string> {
    if (activeThreadId) return activeThreadId
    const thread = await adapter.createThread()
    if (mounted.current) setActiveThreadId(thread.id)
    onThreadCreated?.(thread)
    return thread.id
  }

  /** 把页面允许的上下文白名单连同问题提交给 Adapter。 */
  async function submitMessage(content: string): Promise<void> {
    const normalized = content.trim()
    if (!normalized || status === 'running' || activeRunId.current || pending || requiresSync) return
    setError(undefined)
    setStatus('running')
    const runId = createRunId()
    activeRunId.current = runId
    lastRunId.current = runId
    try {
      const nextThreadId = await ensureThread()
      if (activeRunId.current !== runId) return
      const result = await adapter.sendMessage({
        runId,
        threadId: nextThreadId,
        agentId: config.agentId,
        actionId: config.actionId,
        content: normalized,
        contextItems: config.contextItems.map((item) => ({ ...item }))
      })
      if (!mounted.current || activeRunId.current !== runId) return
      applyResult(result)
      setInput('')
    } catch (reason) {
      if (!mounted.current) return
      setStatus('error')
      setError(reason instanceof Error ? reason.message : '运行失败。')
    } finally {
      if (activeRunId.current === runId) activeRunId.current = undefined
    }
  }

/** 停止当前仍在等待的运行。 */
  async function stopRun(): Promise<void> {
    if (!activeRunId.current) return
    const runId = activeRunId.current
    try {
      await adapter.stop(runId)
      if (activeRunId.current === runId) activeRunId.current = undefined
      if (mounted.current) setStatus('stopped')
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : '停止失败。')
    }
  }

  /** 重试最近一次失败或停止的模拟运行。 */
  async function retryRun(): Promise<void> {
    if (!lastRunId.current) return
    setError(undefined)
    setStatus('running')
    const retryRunId = createRunId()
    activeRunId.current = retryRunId
    try {
      const result = await adapter.retry(lastRunId.current, retryRunId)
      lastRunId.current = result.runId
      if (!mounted.current) return
      setMessages(result.messages)
      setStatus(result.status)
      setError(result.error)
    } catch (reason) {
      if (!mounted.current) return
      setStatus('error')
      setError(reason instanceof Error ? reason.message : '重试失败。')
    } finally {
      if (activeRunId.current === retryRunId) activeRunId.current = undefined
    }
  }

  /** 处理当前消息中的批准或拒绝动作。 */
  async function resolveApproval(message: AgentMessage, approved: boolean): Promise<void> {
    if (!lastRunId.current || !message.approval) return
    try {
      const result = await adapter.resolveApproval({
        runId: lastRunId.current,
        approvalId: message.approval.id,
        approved
      })
      if (!mounted.current) return
      setMessages(result.messages)
      setStatus(result.status)
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : '审批失败。')
    }
  }

  /** 明确回答当前交互后，以新运行标识继续同一会话，失败时不自动重放。 */
  async function respond(value: unknown): Promise<void> {
    if (!pending || requiresSync || !adapter.resolveInteraction || !lastRunId.current || activeRunId.current || status === 'running') return
    const runId = createRunId()
    activeRunId.current = runId
    setStatus('running')
    try {
      const result = await adapter.resolveInteraction({ runId: lastRunId.current,
        interactionId: pending.interactionId, value, responseRunId: runId })
      if (!mounted.current || activeRunId.current !== runId) return
      lastRunId.current = result.runId
      applyResult(result)
    } catch (reason) {
      if (mounted.current) { setStatus('error'); setError(reason instanceof Error ? reason.message : '交互提交失败。') }
    } finally { if (activeRunId.current === runId) activeRunId.current = undefined }
  }

  /** 处理 Composer 的 Enter 发送与 Shift+Enter 换行。 */
  function handleComposerKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== 'Enter' || event.shiftKey) return
    event.preventDefault()
    void submitMessage(input)
  }

  const empty = messages.length === 0 && status !== 'running'
  return (
    <section className="x-agent-chat" aria-label={`${config.name}会话`}>
      <div
        aria-busy={status === 'running'}
        aria-live="polite"
        className="x-agent-chat__messages"
      >
        {status === 'running' && messages.length === 0 ? (
          <div className="x-agent-chat__state"><Skeleton active /></div>
        ) : empty ? (
          <div className="x-agent-chat__state">
            <div className="x-agent-chat__empty">
              <Empty description={config.runtime ? `向${config.name}提问` : `向${config.name}提问，当前仅使用本地模拟数据。`} />
              <div className="x-agent-chat__suggestions">
                {config.suggestedQuestions.map((question) => (
                  <Button key={question} onClick={() => void submitMessage(question)}>
                    {question}
                  </Button>
                ))}
                {!config.runtime && config.features.tools ? (
                  <Button onClick={() => void submitMessage('模拟工具：执行一次示例查询')}>
                    模拟工具调用
                  </Button>
                ) : null}
                {!config.runtime && config.features.approvals ? (
                  <Button onClick={() => void submitMessage('模拟审批：提交一项待确认操作')}>
                    模拟审批流程
                  </Button>
                ) : null}
                {!config.runtime && <Button danger onClick={() => void submitMessage('模拟失败：验证错误与重试')}>
                  模拟失败重试
                </Button>}
              </div>
            </div>
          </div>
        ) : (
          <div className="x-agent-chat__stack">
            {messages.map((message) => (
              <AgentMessageBubble
                disabled={status === 'running'}
                key={message.id}
                message={message}
                onApproval={(item, approved) => void resolveApproval(item, approved)}
              />
            ))}
          </div>
        )}
        {pending && <AgentInteraction key={pending.interactionId} pending={pending} disabled={status === 'running' || requiresSync} onRespond={(value) => void respond(value)} />}
        {requiresSync && <Button disabled={status === 'running'} onClick={() => void refreshRun()}>同步会话状态</Button>}
        {error ? (
          <Alert
            action={!config.runtime ? <Button onClick={() => void retryRun()}>重试</Button> : undefined}
            description={error}
            message={config.runtime ? '本次运行未完成，请核对业务状态' : '本次模拟运行未完成'}
            showIcon
            type="error"
          />
        ) : null}
      </div>
      <div className="x-agent-chat__status">
        <span>{config.runtime ? 'Agent Runtime' : '模拟运行'} · {runStatusText(status)}</span>
        <div className="x-agent-context" aria-label="允许提交的页面上下文">
          {config.contextItems.map((item) => <Tag key={item.id}>{item.label}</Tag>)}
        </div>
      </div>
      <AgentComposer name={config.name} attachments={config.features.attachments} running={status === 'running'}
        blocked={Boolean(pending) || requiresSync} input={input} onInput={setInput} onKeyDown={handleComposerKeyDown}
        onStop={() => void stopRun()} onSend={() => void submitMessage(input)} />
    </section>
  )
}
