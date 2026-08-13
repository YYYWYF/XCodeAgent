import {
  ArrowDownOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  RobotOutlined,
  ToolOutlined,
  UserOutlined
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
import ToolCallCard from '../ToolCallCard'
import ProcessSteps from '../ProcessSteps'
import WorkflowRunCard, {
  type ClarificationAnswers,
  workflowClarification
} from '../WorkflowRunCard'
import DetailConfirmationPageSelector from '../../../../components/DetailConfirmationPageSelector'
import type { DevelopmentPlanningPageOption } from '../../../../typings'

type DetailConfirmationStart = (
  targetType: 'page' | 'endpoint',
  targetId: string,
  targetLabel: string,
  hasDetailPlan: boolean,
  targetContext?: {
    apiContractId?: string
    endpointId?: string
    templateId?: string
    templateName?: string
    templateSourcePath?: string
  }
) => Promise<void>
import {
  processStepsForMessageDisplay,
  workflowMessageContentForDisplay
} from '../../../../service/processStepHistory'
import type { AgentChatMessage, WorkspaceDocKey } from '../../types'
import { isDirectModificationWaitingForInput } from '../../directModificationMode'
import { workflowCodeChanges, workflowFinalResultPresentation } from '../../utils'
import { workflowInteractionAvailability } from '../../planExecutionMode'
import { useWorkbenchPhase } from '../../../../context'
import { WORKBENCH_PHASE_AGENTS, type WorkbenchPhase } from '../../../../workbenchPhase'
import { isMessageListNearBottom, shouldShowScrollToBottom } from './scrollState'
import './MessageList.less'

const { Text } = Typography

// 对话区瘦身：工具调用 / 最终结果头隐藏；节点过程（ProcessSteps，构建中展开/完成收起）保留，
// 让选页面后的多节点生成链（检查工作区→规划 DAG→构建→集成测试）清晰可见。
const SLIM_CONVERSATION = true

/** assistant 消息头：标识当前是哪个阶段的 Agent（产品 / 研发 / 审查）在回复。 */
function MessageAgentHeader({ agentKey }: { agentKey: WorkbenchPhase }): ReactElement {
  const agent = WORKBENCH_PHASE_AGENTS[agentKey]
  return (
    <div className={cx('ai-message-agent', agentKey)}>
      <span className={cx('ai-message-agent-avatar')} aria-hidden="true">
        <UserOutlined />
      </span>
      <span className={cx('ai-message-agent-name')}>{agent.role}</span>
    </div>
  )
}

type MessageListProps = {
  /** 当前查看任务所属阶段；查看历史任务时可与应用当前阶段不同。 */
  agentPhase?: WorkbenchPhase
  applicationLifecycle?: ApplicationLifecycle
  codeChangeActionsDisabled: boolean
  /** 版本或历史阶段锁定时，所有会改变产物的卡片动作同时禁用。 */
  interactionsDisabled?: boolean
  loading: boolean
  messages: AgentChatMessage[]
  onRevertCodeChanges: (messageId: number, codeChanges: WorkspaceCodeChangeSet) => void
  onSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => Promise<void>
  onDiscardArtifact: (docKey: WorkspaceDocKey) => void
  onOpenCodeChangeFile: (codeChanges: WorkspaceCodeChangeSet, selectedPath: string) => void
  /** 待设计目标（页面/接口）作为对话节点，含模板选择与开始详细设计。 */
  lockedPage?: DevelopmentPlanningPageOption
  lockedEndpoint?: {
    apiContractId: string
    endpointId: string
    hasDetailPlan?: boolean
    label: string
    path?: string
    purpose?: string
  }
  onStartDetailDesign?: DetailConfirmationStart
  revertingCodeChangeIds: ReadonlySet<string>
}

/** 渲染聊天消息、Workflow 最终状态和代码变更操作。 */
export default function MessageList({
  agentPhase,
  applicationLifecycle,
  codeChangeActionsDisabled,
  interactionsDisabled = false,
  loading,
  messages,
  onDiscardArtifact,
  onOpenCodeChangeFile,
  onRevertCodeChanges,
  onSubmitClarification,
  lockedPage,
  lockedEndpoint,
  onStartDetailDesign,
  revertingCodeChangeIds
}: MessageListProps): ReactElement {
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const messageColumnRef = useRef<HTMLDivElement>(null)
  const followLatestContentRef = useRef(true)
  const restoringFollowRef = useRef(false)
  const scrollUpdateFrameRef = useRef<number>()
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const { phase: currentPhase } = useWorkbenchPhase()
  const activeAssistantMessageId = loading ? findLastAssistantMessageId(messages) : undefined
  const hasStreamingProcess = messages.some(
    (message) => message.id === activeAssistantMessageId && Boolean(message.processSteps?.length)
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
    // 新消息/卡片出现时强制跟随最新：同步 scrollTo + rAF + 300ms 内容稳定后补滚，
    // 确保异步渲染的工作流确认卡（授权块）始终落在视口内。
    followLatestContentRef.current = true
    const container = scrollContainerRef.current
    if (container) container.scrollTo({ top: container.scrollHeight, behavior: 'auto' })
    scheduleScrollUpdate()
    const timer = window.setTimeout(scheduleScrollUpdate, 300)
    return () => window.clearTimeout(timer)
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
              const visibleProcessSteps = processStepsForMessageDisplay(
                message.processSteps,
                message.workflow,
                messageLoading
              )
              const waitingForDirectModificationInput = isDirectModificationWaitingForInput(
                message.workflow
              )
              // 一段对话只对应当前阶段的一个 Agent：无论历史消息源自哪个节点 phase，
              // 统一以当前阶段为准（设计=产品Agent、开发=研发Agent、审查=审查Agent），
              // 避免同一对话里混出现多个 Agent。
              const messageAgentKey: WorkbenchPhase = agentPhase || currentPhase
              const requiresClarification =
                message.workflow &&
                workflowClarification(message.workflow)?.status === 'requires_user_input'
              // 只有最后一张待确认卡才可能是 active；历史卡（已提交 / 被新回复取代）一律 stale，
              // 防止设计阶段 fast-path 把所有 requires_user_input 历史快照都判成 active（按钮复活）。
              const lastPendingClarificationId = (() => {
                for (let i = messages.length - 1; i >= 0; i -= 1) {
                  const candidate = messages[i]
                  if (
                    candidate.workflow &&
                    workflowClarification(candidate.workflow)?.status === 'requires_user_input'
                  ) {
                    return candidate.id
                  }
                }
                return -1
              })()
              const interactionAvailability =
                message.workflow && requiresClarification
                  ? message.id === lastPendingClarificationId
                    ? workflowInteractionAvailability(message.workflow, applicationLifecycle)
                    : 'stale'
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
                        <MessageAgentHeader agentKey={messageAgentKey} />
                        {message.detailBlocker && (
                          <DetailConfirmationPageSelector
                            disabled={loading}
                            onStart={onStartDetailDesign}
                            selectedPage={{
                              pageId: message.detailBlocker.pageId,
                              key: message.detailBlocker.pageId,
                              label: message.detailBlocker.label,
                              path: message.detailBlocker.path || '/',
                              purpose: message.detailBlocker.purpose || '',
                              designed: false
                            }}
                          />
                        )}
                        {visibleProcessSteps && visibleProcessSteps.length > 0 && (
                          <ProcessSteps
                            loading={messageLoading}
                            steps={visibleProcessSteps}
                            waitingForInput={waitingForDirectModificationInput}
                            waitingPrompt={message.workflow?.summary.message}
                          />
                        )}
                        {!SLIM_CONVERSATION &&
                          messageLoading &&
                          message.toolCalls?.map((toolCall) => (
                            <ToolCallCard key={toolCall.id} toolCall={toolCall} />
                          ))}
                        {!SLIM_CONVERSATION && !messageLoading && codeChanges && (
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
                            disabled={
                              interactionsDisabled ||
                              loading ||
                              interactionAvailability !== 'active'
                            }
                            interactionAvailability={interactionAvailability}
                            onDiscard={onDiscardArtifact}
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
          {(lockedPage || lockedEndpoint) &&
            !messages.some(
              (message) =>
                message.detailBlocker?.pageId === (lockedPage ? lockedPage.pageId : '')
            ) && (
            <article className={cx('ai-message', 'assistant', 'completed')}>
              <div className={cx('ai-message-content')}>
                <MessageAgentHeader agentKey="development" />
                <DetailConfirmationPageSelector
                  disabled={loading || interactionsDisabled}
                  onStart={onStartDetailDesign}
                  selectedEndpoint={lockedEndpoint}
                  selectedPage={lockedPage}
                />
              </div>
            </article>
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
