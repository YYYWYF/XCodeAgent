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
import { useWorkbenchPhase } from '../../../../context'
import { WORKBENCH_PHASE_AGENTS } from '../../../../workbenchPhase'
import type {
  ApplicationLifecycle,
  DevelopmentPlanningPageOption,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../../../typings'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import CodeChangeCard from '../CodeChangeCard'
import { ToolCallChain } from '../ToolCallCard'
import ProcessSteps from '../ProcessSteps'
import VersionCommitReminder from '../VersionCommitReminder'
import WorkflowRunCard, {
  type ClarificationAnswers,
  workflowClarification
} from '../WorkflowRunCard'
import TemplatePreparingCard, {
  isTemplatePreparing
} from '../WorkflowRunCard/TemplatePreparingCard'
import DetailBlockerCard from '../../../DetailConfirmationPageSelector/DetailBlockerCard'
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

/** assistant 消息头：标识当前是哪个阶段的 Agent（产品 / 研发 / 审查）在回复。
 *  人像图标 + Agent 角色名，独占一行，下方换行展示正文/卡片。 */
function MessageAgentHeader({ agentKey }: { agentKey: 'product' | 'development' | 'test' }): ReactElement {
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
  applicationLifecycle?: ApplicationLifecycle
  codeChangeActionsDisabled: boolean
  conversationRunning: boolean
  /** 设计阶段：规划 workflow 确认卡始终可提交（由 planningSubmitRef 驱动），
   *  不走开发 execution 的 workflowInteractionAvailability 判定。 */
  designPhasePlanning?: boolean
  /** UI 设计稿确认：当前选中页 id（与右侧预览面板联动）。 */
  uiDesignActivePageId?: string
  /** UI 设计稿确认：选中页变化时通知外部（联动右侧预览）。 */
  onUiDesignActivePageChange?: (pageId: string) => void
  /** UI 设计稿确认：当前正在执行单页动作的 pageId（联动右侧加载态）。 */
  uiDesignActionPageId?: string | null
  /** UI 设计稿确认：单页动作页变化时通知外部。 */
  onUiDesignActionPageIdChange?: (pageId: string | null) => void
  /** 需求文档确认：保存编辑草稿（重写 Markdown+JSON），返回更新后的 spec。 */
  onSaveRequirementSpec?: (
    workflow: WorkflowRunPayload,
    spec: Record<string, unknown>
  ) => Promise<Record<string, unknown> | undefined>
  /** 需求文档确认：菜单根路径（驱动编辑器页面路由前缀）。 */
  rootPath?: string
  /** 模板就绪后点击进入开发阶段（放开 product 锁，恢复跟随旅程）。 */
  onEnterDevelopment?: () => void
  /** 模板生成失败后重试（重新触发模板生成）。 */
  onRetryTemplate?: () => void
  /** 当前应用是否正在生成模板（前端状态信号，lifecycle 在生成期间不变）。 */
  generatingTemplate?: boolean
  /** 开发阶段：detailBlocker 卡片点击「开始详细设计」。 */
  onStartDetailDesign?: (page: DevelopmentPlanningPageOption) => void
  loading: boolean
  messages: AgentChatMessage[]
  onRevertCodeChanges: (messageId: number, codeChanges: WorkspaceCodeChangeSet) => void
  onSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>
  ) => Promise<void>
  onOpenCodeChangeFile: (codeChanges: WorkspaceCodeChangeSet, selectedPath: string) => void
  revertingCodeChangeIds: ReadonlySet<string>
}

/** 渲染聊天消息、Workflow 最终状态和代码变更操作。 */
export default function MessageList({
  applicationLifecycle,
  codeChangeActionsDisabled,
  conversationRunning,
  designPhasePlanning = false,
  uiDesignActivePageId,
  onUiDesignActivePageChange,
  uiDesignActionPageId,
  onUiDesignActionPageIdChange,
  onSaveRequirementSpec,
  rootPath,
  onEnterDevelopment,
  onRetryTemplate,
  generatingTemplate,
  onStartDetailDesign,
  loading,
  messages,
  onOpenCodeChangeFile,
  onRevertCodeChanges,
  revertingCodeChangeIds,
  onSubmitClarification
}: MessageListProps): ReactElement {
  const { phase: currentPhase } = useWorkbenchPhase()
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const messageColumnRef = useRef<HTMLDivElement>(null)
  const followLatestContentRef = useRef(true)
  const restoringFollowRef = useRef(false)
  const scrollUpdateFrameRef = useRef<number>()
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const activeAssistantMessageId = loading ? findLastAssistantMessageId(messages) : undefined
  const hasStreamingProcess = messages.some(
    (message) => message.id === activeAssistantMessageId && Boolean(message.processSteps?.length)
  )
  const latestVersionReminderMessageId = findLatestVersionReminderMessageId(messages)

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
              {designPhasePlanning ? (
                <>
                  <Spin size="small" />
                  <Text type="secondary">产品 Agent 正在准备需求确认…</Text>
                </>
              ) : (
                <>
                  <span className={cx('ai-message-empty-mark')}>
                    <RobotOutlined />
                  </span>
                  <Text strong>从一个想法开始</Text>
                </>
              )}
            </div>
          ) : (
            messages.map((message) => {
              console.log('[msg-debug] id=', message.id, 'content=', JSON.stringify(message.content).slice(0, 50), 'hasWorkflow=', Boolean(message.workflow), 'phase=', message.workflow?.summary?.phase, 'clarStatus=', message.workflow ? workflowClarification(message.workflow)?.status : undefined, 'planningLoading=', message.planningLoading)
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
              // UI 确认阶段的卡片在换一换/选模板期间（workflow running，clarification
              // 可能短暂丢失 requires_user_input/mode）也保持显示，避免卡片闪烁。
              // 用 phase=ui_confirmation 作为权威判据（running 期间 phase 不丢），
              // 辅以 clarification.mode 兜底。
              const messageClarification = message.workflow ? workflowClarification(message.workflow) : undefined
              const isUiDesignConfirmationCard =
                message.workflow &&
                (message.workflow.summary?.phase === 'ui_confirmation' ||
                  messageClarification?.mode === 'ui_design_confirmation')
              // 创建规划的产品/技术阶段也展示 WorkflowRunCard，保证运行与确认状态连续可见。
              const isPlanningStageCard =
                message.workflow &&
                ['product_planning', 'project_planning', 'technical_planning'].includes(
                  String(message.workflow.summary?.phase || '')
                )
              const showWorkflowCard = Boolean(
                message.workflow && (requiresClarification || isUiDesignConfirmationCard || isPlanningStageCard)
              )
              const interactionAvailability =
                message.workflow && requiresClarification
                  ? conversation || designPhasePlanning
                    ? 'active'
                    : workflowInteractionAvailability(message.workflow, applicationLifecycle)
                  : 'stale'
              const visibleAssistantContent = workflowMessageContentForDisplay(
                message.content,
                message.workflow,
                Boolean(visibleProcessSteps?.length)
              )
              // 规划文档确认阶段由确认卡展示，隐藏流式 JSON 原文；生成中只显示加载态。
              // 规划占位消息（planningLoading）：用户提交后产品 Agent 正在思考，只显示 loading 态。
              const isPlanningArtifactConfirmationCard =
                message.workflow &&
                ['requirement_spec_confirmation', 'product_plan_confirmation', 'technical_plan_confirmation', 'project_plan_confirmation'].includes(
                  String(messageClarification?.mode || '')
                )
              const isPlanningStageRunningCard =
                message.workflow &&
                ['product_planning', 'project_planning', 'technical_planning'].includes(
                  String(message.workflow.summary?.phase || '')
                ) &&
                message.workflow.summary?.status === 'running'
              const isPlanningLoadingPlaceholder = Boolean(message.planningLoading)
              // 待确认卡片（requiresClarification）已由 WorkflowRunCard 展示表单/选项，
              // 隐藏流式文本原文（如「还有 N 个问题需要补充」），避免与卡片重复。
              const effectiveAssistantContent =
                isPlanningArtifactConfirmationCard || isPlanningStageRunningCard || isPlanningLoadingPlaceholder || (showWorkflowCard && requiresClarification)
                  ? ''
                  : visibleAssistantContent
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
                        {/* 统一 Agent 头：人像图标 + 当前阶段 Agent 角色名（产品/研发/审查），
                            独占一行，下方换行展示正文/卡片。 */}
                        <MessageAgentHeader agentKey={currentPhase} />
                        {/* 设计阶段规划占位消息：初次进入或用户提交后产品 Agent 正在准备，
                            planningLoading 标记的占位消息显示 loading 态，
                            流式 chunk 到达后 planningLoading 被清除，展示返回内容。 */}
                        {isPlanningLoadingPlaceholder && (
                          <div className={cx('ai-message-loading-placeholder')}>
                            <Spin size="small" />
                            <Text type="secondary">正在准备需求确认…</Text>
                          </div>
                        )}
                        {/* 开发阶段 detailBlocker：研发 Agent 流内挡板卡，
                            选中待设计页面时注入，展示「尚未进行详细设计」+ 开始按钮。 */}
                        {message.detailBlocker && (
                          <DetailBlockerCard
                            disabled={loading}
                            onStart={(page) => onStartDetailDesign?.(page)}
                            selectedPage={{
                              pageId: message.detailBlocker!.pageId,
                              key: message.detailBlocker!.pageId,
                              label: message.detailBlocker!.label,
                              path: message.detailBlocker!.path || '/',
                              purpose: message.detailBlocker!.purpose || '',
                              designed: false
                            }}
                          />
                        )}
                        {visibleProcessSteps && visibleProcessSteps.length > 0 && !(designPhasePlanning && showWorkflowCard) && (
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
                        {effectiveAssistantContent && (
                          <div
                            className={cx(!messageLoading && codeChanges && 'final-result-content')}
                          >
                            <MarkdownContent content={effectiveAssistantContent} />
                          </div>
                        )}
                        {showWorkflowCard && (
                          <WorkflowRunCard
                            disabled={loading || interactionAvailability !== 'active'}
                            interactionAvailability={interactionAvailability}
                            onSubmitClarification={onSubmitClarification}
                            uiDesignActivePageId={uiDesignActivePageId}
                            onUiDesignActivePageChange={onUiDesignActivePageChange}
                            uiDesignActionPageId={uiDesignActionPageId}
                            onUiDesignActionPageIdChange={onUiDesignActionPageIdChange}
                            onSaveRequirementSpec={onSaveRequirementSpec}
                            rootPath={rootPath}
                            workflow={message.workflow!}
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
                        {!messageLoading &&
                          codeChanges &&
                          message.workflow &&
                          message.id === latestVersionReminderMessageId && (
                            <VersionCommitReminder
                              codeChanges={codeChanges}
                              disabled={codeChangeActionsDisabled}
                              onReview={() =>
                                codeChanges.files[0] &&
                                onOpenCodeChangeFile(codeChanges, codeChanges.files[0].path)
                              }
                              workflow={message.workflow}
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
          {designPhasePlanning && (generatingTemplate || isTemplatePreparing(applicationLifecycle)) ? (
            <article className={cx('ai-message', 'assistant', 'template-preparing-message')}>
              <div className={cx('ai-message-content')}>
                <TemplatePreparingCard
                  lifecycle={applicationLifecycle}
                  onEnterDevelopment={onEnterDevelopment}
                  onRetry={onRetryTemplate}
                />
              </div>
            </article>
          ) : null}
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

/** 只为最新一轮快速修改显示版本提醒，避免历史消息重复读取 Git 状态。 */
function findLatestVersionReminderMessageId(messages: AgentChatMessage[]): number | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    const workflow = message.workflow
    const codeChanges = message.codeChanges ?? workflowCodeChanges(workflow)
    if (
      message.role === 'assistant' &&
      workflow &&
      codeChanges?.files.length &&
      isConversationWorkflow(workflow) &&
      workflow.summary.intent === 'workspace_change' &&
      ['completed', 'failed'].includes(String(workflow.summary.status))
    ) {
      return message.id
    }
  }
  return undefined
}
