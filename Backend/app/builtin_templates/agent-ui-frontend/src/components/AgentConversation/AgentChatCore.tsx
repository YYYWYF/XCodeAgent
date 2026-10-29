import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Empty,
  Input,
  Skeleton,
  Tag,
  Typography
} from 'antd'
import {
  PaperClipOutlined,
  SendOutlined,
  StopOutlined
} from '@ant-design/icons'
import { MockAgentConversationAdapter } from '@/apis/agentConversationMock'
import { AgentMessageBubble } from './AgentMessageBubble'
import { agentIconCompatibilityProps } from './iconCompatibility'
import type {
  AgentConversationAdapter,
  AgentMessage,
  AgentRunStatus,
  AgentUiTemplateConfig,
  ConversationThread
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

/** 把运行状态映射为用户可读的模拟状态。 */
function runStatusText(status: AgentRunStatus): string {
  const labels: Record<AgentRunStatus, string> = {
    idle: '等待提问',
    running: '模拟运行中，可随时停止',
    waiting_approval: '等待模拟审批',
    stopped: '模拟运行已停止',
    error: '模拟运行失败，可重试',
    completed: '模拟运行已完成'
  }
  return labels[status]
}

/** 构造与固定配置绑定且不触发网络请求的 Mock Adapter。 */
export function createMockAgentConversationAdapter(
  config: AgentUiTemplateConfig
): AgentConversationAdapter {
  return new MockAgentConversationAdapter({
    agentName: config.name,
    assistantMessage: config.mock.assistantMessage,
    toolTitle: config.mock.toolTitle,
    toolDetail: config.mock.toolDetail,
    approvalTitle: config.mock.approvalTitle,
    approvalDetail: config.mock.approvalDetail,
    successMessage: config.mock.successMessage,
    errorMessage: config.mock.errorMessage
  })
}

/** 渲染共享消息流、运行状态、审批、错误重试和 Composer。 */
export function AgentChatCore({
  config,
  adapter: providedAdapter,
  threadId,
  onThreadCreated
}: AgentChatCoreProps): React.ReactElement {
  const adapter = useMemo(
    () => providedAdapter ?? createMockAgentConversationAdapter(config),
    [config, providedAdapter]
  )
  const [activeThreadId, setActiveThreadId] = useState(threadId)
  const [messages, setMessages] = useState<AgentMessage[]>([])
  const [status, setStatus] = useState<AgentRunStatus>('idle')
  const [input, setInput] = useState('')
  const [error, setError] = useState<string>()
  const activeRunId = useRef<string>()
  const lastRunId = useRef<string>()
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  useEffect(() => {
    /** 切换会话时从 Adapter 重载权威消息快照。 */
    async function loadThreadMessages(): Promise<void> {
      setActiveThreadId(threadId)
      setError(undefined)
      if (!threadId) {
        setMessages([])
        setStatus('idle')
        return
      }
      try {
        const nextMessages = await adapter.loadMessages(threadId)
        if (mounted.current) setMessages(nextMessages)
      } catch (reason) {
        if (mounted.current) setError(reason instanceof Error ? reason.message : '会话加载失败。')
      }
    }
    void loadThreadMessages()
  }, [adapter, threadId])

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
    if (!normalized || status === 'running') return
    setError(undefined)
    setStatus('running')
    const runId = createRunId()
    activeRunId.current = runId
    lastRunId.current = runId
    try {
      const nextThreadId = await ensureThread()
      const result = await adapter.sendMessage({
        runId,
        threadId: nextThreadId,
        agentId: config.agentId,
        actionId: config.actionId,
        content: normalized,
        contextItems: config.contextItems.map((item) => ({ ...item }))
      })
      if (!mounted.current) return
      setMessages(result.messages)
      setStatus(result.status)
      setError(result.error)
      setInput('')
    } catch (reason) {
      if (!mounted.current) return
      setStatus('error')
      setError(reason instanceof Error ? reason.message : '模拟运行失败。')
    } finally {
      if (activeRunId.current === runId) activeRunId.current = undefined
    }
  }

  /** 停止当前仍在等待的模拟运行。 */
  async function stopRun(): Promise<void> {
    if (!activeRunId.current) return
    try {
      await adapter.stop(activeRunId.current)
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
              <Empty description={`向${config.name}提问，当前仅使用本地模拟数据。`} />
              <div className="x-agent-chat__suggestions">
                {config.suggestedQuestions.map((question) => (
                  <Button key={question} onClick={() => void submitMessage(question)}>
                    {question}
                  </Button>
                ))}
                {config.features.tools ? (
                  <Button onClick={() => void submitMessage('模拟工具：执行一次示例查询')}>
                    模拟工具调用
                  </Button>
                ) : null}
                {config.features.approvals ? (
                  <Button onClick={() => void submitMessage('模拟审批：提交一项待确认操作')}>
                    模拟审批流程
                  </Button>
                ) : null}
                <Button danger onClick={() => void submitMessage('模拟失败：验证错误与重试')}>
                  模拟失败重试
                </Button>
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
        {error ? (
          <Alert
            action={<Button onClick={() => void retryRun()}>重试</Button>}
            description={error}
            message="本次模拟运行未完成"
            showIcon
            type="error"
          />
        ) : null}
      </div>
      <div className="x-agent-chat__status">
        <span>模拟运行 · {runStatusText(status)}</span>
        <div className="x-agent-context" aria-label="允许提交的页面上下文">
          {config.contextItems.map((item) => <Tag key={item.id}>{item.label}</Tag>)}
        </div>
      </div>
      <div className="x-agent-composer">
        {config.features.attachments ? (
          <Button
            aria-label="模拟附件入口"
            disabled
            icon={<PaperClipOutlined {...agentIconCompatibilityProps} />}
          />
        ) : null}
        <Input.TextArea
          aria-label={`向${config.name}提问`}
          autoSize={{ minRows: 1, maxRows: 4 }}
          disabled={status === 'running'}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleComposerKeyDown}
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          value={input}
        />
        {status === 'running' ? (
          <Button
            icon={<StopOutlined {...agentIconCompatibilityProps} />}
            onClick={() => void stopRun()}
          >
            停止
          </Button>
        ) : (
          <Button
            disabled={!input.trim()}
            icon={<SendOutlined {...agentIconCompatibilityProps} />}
            onClick={() => void submitMessage(input)}
            type="primary"
          >
            发送
          </Button>
        )}
      </div>
    </section>
  )
}
