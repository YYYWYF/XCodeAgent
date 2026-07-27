import {
  ArrowDownOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  RobotOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Spin, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { EditorMode, WorkflowRunPayload, WorkspaceCodeChangeSet } from '../../../../typings'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import CodeChangeCard from '../CodeChangeCard'
import ToolCallCard from '../ToolCallCard'
import ProcessSteps from '../ProcessSteps'
import WorkflowRunCard, {
  type ClarificationAnswers,
  workflowClarification
} from '../WorkflowRunCard'
import {
  processStepsForDisplay,
  workflowMessageContentForDisplay
} from '../../../../service/processStepHistory'
import type { AgentChatMessage, ChatCopy } from '../../types'
import { workflowCodeChanges, workflowFinalResultPresentation } from '../../utils'
import { shouldShowScrollToBottom } from './scrollState'
import './MessageList.less'

const { Text } = Typography

type MessageListProps = {
  codeChangeActionsDisabled: boolean
  copy: ChatCopy[EditorMode]
  loading: boolean
  messages: AgentChatMessage[]
  onRevertCodeChanges: (messageId: number, codeChanges: WorkspaceCodeChangeSet) => void
  onSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => Promise<void>
  onOpenCodeChangeFile: (codeChanges: WorkspaceCodeChangeSet, selectedPath: string) => void
  revertingCodeChangeIds: ReadonlySet<string>
}

/** 渲染聊天消息、Workflow 最终状态和代码变更操作。 */
export default function MessageList({
  codeChangeActionsDisabled,
  copy,
  loading,
  messages,
  onOpenCodeChangeFile,
  onRevertCodeChanges,
  revertingCodeChangeIds,
  onSubmitClarification
}: MessageListProps): ReactElement {
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const messageColumnRef = useRef<HTMLDivElement>(null)
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const activeAssistantMessageId = loading ? findLastAssistantMessageId(messages) : undefined
  const hasStreamingProcess = messages.some(
    (message) => message.id === activeAssistantMessageId && Boolean(message.processSteps?.length)
  )

  /** 根据当前滚动尺寸同步悬浮按钮是否可见。 */
  const updateScrollToBottomVisibility = useCallback((): void => {
    const container = scrollContainerRef.current
    if (!container) {
      setShowScrollToBottom(false)
      return
    }
    setShowScrollToBottom(shouldShowScrollToBottom(container))
  }, [])

  /** 平滑滚动到消息列表底部。 */
  const handleScrollToBottom = (): void => {
    const container = scrollContainerRef.current
    if (!container) return
    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' })
  }

  // 同时观察滚动容器和消息内容尺寸，覆盖窗口缩放、流式输出与卡片展开。
  useEffect(() => {
    const container = scrollContainerRef.current
    const messageColumn = messageColumnRef.current
    if (!container || !messageColumn || typeof ResizeObserver === 'undefined') return

    const observer = new ResizeObserver(updateScrollToBottomVisibility)
    observer.observe(container)
    observer.observe(messageColumn)
    return () => observer.disconnect()
  }, [updateScrollToBottomVisibility])

  // 会话或加载状态切换后在浏览器完成布局时重新计算，避免复用旧会话状态。
  useEffect(() => {
    const animationFrame = window.requestAnimationFrame(updateScrollToBottomVisibility)
    return () => window.cancelAnimationFrame(animationFrame)
  }, [loading, messages, updateScrollToBottomVisibility])

  return (
    <div className={cx('ai-message-list-shell')}>
      <div
        className={cx('ai-message-list')}
        aria-live="polite"
        onScroll={updateScrollToBottomVisibility}
        ref={scrollContainerRef}
      >
        <div className={cx('ai-message-column')} ref={messageColumnRef}>
          {messages.length === 0 ? (
            <div className={cx('ai-message-empty')}>
              <span className={cx('ai-message-empty-mark')}>
                <RobotOutlined />
              </span>
              <Text strong>从一个想法开始</Text>
              <Text type="secondary">{copy.empty}</Text>
            </div>
          ) : (
            messages.map((message) => {
              const messageLoading = message.id === activeAssistantMessageId
              const codeChanges = message.codeChanges ?? workflowCodeChanges(message.workflow)
              const finalResult = workflowFinalResultPresentation(message.workflow)
              const nonToolSteps = processStepsForDisplay(
                message.processSteps,
                message.workflow
              )?.filter((step) => step.kind !== 'tool' && step.kind !== 'command')
              const requiresClarification =
                message.workflow &&
                workflowClarification(message.workflow)?.status === 'requires_user_input'
              const visibleAssistantContent = workflowMessageContentForDisplay(
                message.content,
                message.workflow,
                Boolean(nonToolSteps?.length)
              )
              return (
                <article
                  className={cx(
                    'ai-message',
                    message.role,
                    message.role === 'assistant' && !messageLoading && 'completed'
                  )}
                  key={message.id}
                >
                  <div className={cx('ai-message-content')}>
                    {message.role === 'assistant' ? (
                      <>
                        {nonToolSteps && nonToolSteps.length > 0 && (
                          <ProcessSteps loading={messageLoading} steps={nonToolSteps} />
                        )}
                        {messageLoading &&
                          message.toolCalls?.map((toolCall) => (
                            <ToolCallCard key={toolCall.id} toolCall={toolCall} />
                          ))}
                        {!messageLoading && codeChanges && (
                          <div
                            className={cx('final-result-heading', finalResult.failed && 'failed')}
                          >
                            <span>
                              {finalResult.failed ? (
                                <CloseCircleOutlined />
                              ) : (
                                <CheckCircleOutlined />
                              )}
                            </span>
                            <div>
                              <Text strong>{finalResult.title}</Text>
                              <Text type="secondary">最终结果</Text>
                            </div>
                          </div>
                        )}
                        {visibleAssistantContent && (
                          <div
                            className={cx(!messageLoading && codeChanges && 'final-result-content')}
                          >
                            <MarkdownContent content={visibleAssistantContent} />
                          </div>
                        )}
                        {message.workflow && requiresClarification && (
                          <WorkflowRunCard
                            disabled={messageLoading}
                            onSubmitClarification={onSubmitClarification}
                            workflow={message.workflow}
                          />
                        )}
                        {!messageLoading && codeChanges && (
                          <CodeChangeCard
                            codeChanges={codeChanges}
                            loading={messageLoading}
                            onApproveAll={() => undefined}
                            onOpenFile={(path) => onOpenCodeChangeFile(codeChanges, path)}
                            onRevert={() => onRevertCodeChanges(message.id, codeChanges)}
                            revertDisabled={codeChangeActionsDisabled}
                            reverting={revertingCodeChangeIds.has(codeChanges.id)}
                          />
                        )}
                      </>
                    ) : (
                      <>
                        {message.skills && message.skills.length > 0 && (
                          <div className={cx('message-skill-labels')}>
                            {message.skills.map((skill) => (
                              <Tag key={skill.name} title={skill.description}>
                                <ToolOutlined />
                                <span>{skill.name}</span>
                              </Tag>
                            ))}
                          </div>
                        )}
                        <Text className={cx('ai-message-text')}>{message.content}</Text>
                      </>
                    )}
                  </div>
                </article>
              )
            })
          )}
          {loading && !hasStreamingProcess && (
            <div className={cx('ai-message', 'assistant', 'loading')}>
              <Spin size="small" />
              <Text type="secondary">正在运行 Workflow...</Text>
            </div>
          )}
        </div>
      </div>
      {showScrollToBottom && (
        <button
          aria-label="滚动到对话底部"
          className={cx('scroll-to-bottom-button')}
          onClick={handleScrollToBottom}
          title="滚动到对话底部"
          type="button"
        >
          <ArrowDownOutlined aria-hidden="true" />
        </button>
      )}
    </div>
  )
}

/** 从消息末尾向前查找当前正在流式更新的 Assistant 消息。 */
function findLastAssistantMessageId(messages: AgentChatMessage[]): number | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'assistant') return messages[index].id
  }
  return undefined
}
