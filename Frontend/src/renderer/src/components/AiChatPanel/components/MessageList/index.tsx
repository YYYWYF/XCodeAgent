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
import type {
  ApplicationLifecycle,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../../../typings'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import CodeChangeCard from '../CodeChangeCard'
import CodeAnalysisCard from '../CodeAnalysisCard'
import { ToolCallChain } from '../ToolCallCard'
import ProcessSteps from '../ProcessSteps'
import WorkflowRunCard, {
  type ClarificationAnswers,
  workflowClarification
} from '../WorkflowRunCard'
import {
  processStepsForMessageDisplay,
  workflowMessageContentForDisplay
} from '../../../../service/processStepHistory'
import type { AgentChatMessage } from '../../types'
import { isConversationWorkflow } from '../../conversationMode'
import { workflowCodeChanges, workflowFinalResultPresentation } from '../../utils'
import { workflowInteractionAvailability } from '../../planExecutionMode'
import { isMessageListNearBottom, shouldShowScrollToBottom } from './scrollState'
import './MessageList.less'

const { Text } = Typography

type MessageListProps = {
  applicationLifecycle?: ApplicationLifecycle
  codeAnalysisActionsDisabled: boolean
  codeChangeActionsDisabled: boolean
  conversationRunning: boolean
  loading: boolean
  messages: AgentChatMessage[]
  onRevertCodeChanges: (messageId: number, codeChanges: WorkspaceCodeChangeSet) => void
  onSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => Promise<void>
  onOpenCodeChangeFile: (codeChanges: WorkspaceCodeChangeSet, selectedPath: string) => void
  onRetryCodeAnalysis: () => void
  revertingCodeChangeIds: ReadonlySet<string>
  workspaceRoot: string
}

/** 渲染聊天消息、Workflow 最终状态和代码变更操作。 */
export default function MessageList({
  applicationLifecycle,
  codeAnalysisActionsDisabled,
  codeChangeActionsDisabled,
  conversationRunning,
  loading,
  messages,
  onOpenCodeChangeFile,
  onRetryCodeAnalysis,
  onRevertCodeChanges,
  revertingCodeChangeIds,
  onSubmitClarification,
  workspaceRoot
}: MessageListProps): ReactElement {
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const messageColumnRef = useRef<HTMLDivElement>(null)
  const followLatestContentRef = useRef(true)
  const restoringFollowRef = useRef(false)
  const scrollUpdateFrameRef = useRef<number>()
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const activeAssistantMessageId = loading ? findLastAssistantMessageId(messages) : undefined
  const hasStreamingProcess = messages.some(
    (message) =>
      message.id === activeAssistantMessageId &&
      (Boolean(message.processSteps?.length) || message.codeAnalysis?.status === 'in_progress')
  )

  /** 根据滚动事件同步用户的跟随意图与悬浮按钮状态。 */
  const handleScroll = useCallback((): void => {
    const container = scrollContainerRef.current
    if (!container) {
      setShowScrollToBottom(false)
      return
    }

    const isNearBottom = isMessageListNearBottom(container)
    if (restoringFollowRef.current) {
      followLatestContentRef.current = true
      if (isNearBottom) restoringFollowRef.current = false
      setShowScrollToBottom(false)
      return
    }
    followLatestContentRef.current = isNearBottom
    setShowScrollToBottom(shouldShowScrollToBottom(container))
  }, [])

  /** 在下一动画帧跟随最新内容，或在暂停跟随时仅刷新按钮状态。 */
  const scheduleScrollUpdate = useCallback((): void => {
    if (scrollUpdateFrameRef.current !== undefined) {
      window.cancelAnimationFrame(scrollUpdateFrameRef.current)
    }
    scrollUpdateFrameRef.current = window.requestAnimationFrame(() => {
      scrollUpdateFrameRef.current = undefined
      const container = scrollContainerRef.current
      if (!container) {
        setShowScrollToBottom(false)
        return
      }
      if (followLatestContentRef.current) {
        container.scrollTo({ top: container.scrollHeight, behavior: 'auto' })
        setShowScrollToBottom(false)
        return
      }
      setShowScrollToBottom(shouldShowScrollToBottom(container))
    })
  }, [])

  /** 平滑滚动到底部并恢复对后续新内容的自动跟随。 */
  const handleScrollToBottom = (): void => {
    const container = scrollContainerRef.current
    if (!container) return
    followLatestContentRef.current = true
    restoringFollowRef.current = true
    setShowScrollToBottom(false)
    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' })
  }

  // 同时观察滚动容器和消息内容尺寸，覆盖窗口缩放、流式输出与卡片展开。
  useEffect(() => {
    const container = scrollContainerRef.current
    const messageColumn = messageColumnRef.current
    if (!container || !messageColumn || typeof ResizeObserver === 'undefined') return

    const observer = new ResizeObserver(scheduleScrollUpdate)
    observer.observe(container)
    observer.observe(messageColumn)
    return () => {
      observer.disconnect()
      if (scrollUpdateFrameRef.current !== undefined) {
        window.cancelAnimationFrame(scrollUpdateFrameRef.current)
        scrollUpdateFrameRef.current = undefined
      }
    }
  }, [scheduleScrollUpdate])

  // 消息或加载状态切换后主动安排一次跟随，ResizeObserver 不可用时仍可工作。
  useEffect(() => {
    scheduleScrollUpdate()
  }, [loading, messages, scheduleScrollUpdate])

  return (
    <div className={cx('ai-message-list-shell')}>
      <div
        className={cx('ai-message-list')}
        aria-live="polite"
        onScroll={handleScroll}
        ref={scrollContainerRef}
      >
        <div className={cx('ai-message-column')} ref={messageColumnRef}>
          {messages.length === 0 ? (
            <div className={cx('ai-message-empty')}>
              <span className={cx('ai-message-empty-mark')}>
                <RobotOutlined />
              </span>
              <Text strong>从一个想法开始</Text>
            </div>
          ) : (
            messages.map((message) => {
              const messageLoading = message.id === activeAssistantMessageId
              const codeChanges = message.codeChanges ?? workflowCodeChanges(message.workflow)
              const finalResult = workflowFinalResultPresentation(message.workflow)
              const conversation =
                (messageLoading && conversationRunning) || isConversationWorkflow(message.workflow)
              const visibleProcessSteps = processStepsForMessageDisplay(
                message.processSteps,
                message.workflow
              )
              const hasConversationToolActivity = Boolean(
                conversation &&
                  visibleProcessSteps?.some(
                    (step) => step.kind === 'tool' || step.kind === 'command'
                  )
              )
              const requiresClarification =
                message.workflow &&
                workflowClarification(message.workflow)?.status === 'requires_user_input'
              const interactionAvailability =
                message.workflow && requiresClarification
                  ? conversation
                    ? 'active'
                    : workflowInteractionAvailability(message.workflow, applicationLifecycle)
                  : 'stale'
              const visibleAssistantContent = workflowMessageContentForDisplay(
                message.content,
                message.workflow,
                Boolean(visibleProcessSteps?.length)
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
                        {visibleProcessSteps && visibleProcessSteps.length > 0 && (
                          <ProcessSteps
                            conversation={conversation}
                            loading={messageLoading}
                            steps={visibleProcessSteps}
                          />
                        )}
                        {messageLoading &&
                          message.toolCalls &&
                          message.toolCalls.length > 0 &&
                          // 自由对话已有安全化的过程步骤时只保留一份调用链，避免重复堆叠。
                          !hasConversationToolActivity && (
                            <ToolCallChain toolCalls={message.toolCalls} />
                          )}
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
                        {message.codeAnalysis && (
                          <CodeAnalysisCard
                            analysis={message.codeAnalysis}
                            disabled={codeAnalysisActionsDisabled}
                            onRetry={onRetryCodeAnalysis}
                            workspaceRoot={workspaceRoot}
                          />
                        )}
                        {message.workflow && requiresClarification && (
                          <WorkflowRunCard
                            disabled={loading || interactionAvailability !== 'active'}
                            interactionAvailability={interactionAvailability}
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
              <Text type="secondary">
                {conversationRunning ? '正在运行...' : '正在运行 Workflow...'}
              </Text>
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
