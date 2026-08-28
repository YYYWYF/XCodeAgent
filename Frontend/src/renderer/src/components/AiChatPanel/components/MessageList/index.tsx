import {
  ArrowDownOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  RobotOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import { Spin, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useWorkbenchPhase } from '../../../../context'
import {
  WORKBENCH_PHASE_AGENTS,
  workbenchPhaseForNode,
  type WorkbenchPhase
} from '../../../../workbenchPhase'
import {
  planningWorkflowActivity,
  planningWorkflowNeedsChatLoading,
  planningWorkflowPhase,
  planningWorkflowRequiresUserInput
} from '../../../Welcome/planningWorkflowState'
import type {
  ApplicationLifecycle,
  DevelopmentPlanningPageOption,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../../../typings'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import AgentErrorCard from '../../../AgentErrorCard'
import CodeChangeCard from '../CodeChangeCard'
import { ToolCallChain } from '../ToolCallCard'
import ProcessSteps from '../ProcessSteps'
import VersionCommitReminder from '../VersionCommitReminder'
import WorkflowRunCard, { type ClarificationAnswers } from '../WorkflowRunCard'
import { workflowClarification } from '../WorkflowRunCard/workflowClarification'
import EntityDesignChatCard from '../WorkflowRunCard/EntityDesignChatCard'
import TemplatePreparingCard, {
  isTemplatePreparing
} from '../WorkflowRunCard/TemplatePreparingCard'
import DetailBlockerCard from '../../../DetailConfirmationPageSelector/DetailBlockerCard'
import {
  isStructuredPlanningWorkflow,
  processStepsForMessageDisplay,
  workflowMessageContentForDisplay
} from '../../../../service/processStepHistory'
import type { AgentChatMessage } from '../../types'
import { isConversationWorkflow } from '../../conversationMode'
import {
  isEntityDesignWorkflow,
  workflowCodeChanges,
  workflowCodeChangesBeforeConfirmation,
  workflowFinalResultPresentation,
  workflowShouldShowCodeChanges,
  workflowShouldShowCodeReview,
  workflowShouldShowProjectLaunch
} from '../../utils'
import { workflowInteractionAvailability } from '../../planExecutionMode'
import { isMessageListNearBottom, shouldShowScrollToBottom } from './scrollState'
import PlanningWorkflowActivity from './PlanningWorkflowActivity'
import {
  isSupersededPlanningStageEntryMessage,
  isSupersededPlanningPhaseMessage,
  isSupersededPlanningProgressMessage,
  isTemplateSupersededPlanningProgressMessage,
  latestUiDesignPreviewMessageIndex
} from './uiDesignPreviewHistory'
import './MessageList.less'

const { Text } = Typography

/** 实体设计消息只保留正文：去掉“页面与数据源设计已生成”等工作流摘要；
 *  正文是模板话术时，从载荷合成真实设计内容（澄清说明、AI 建议文本）。 */
function entityDesignMessageContent(
  content: string,
  workflow: WorkflowRunPayload | undefined
): string {
  const normalizedContent = content.trim()
  if (!normalizedContent) return ''
  const clarification = workflow ? workflowClarification(workflow) : undefined
  if (!clarification) return content
  const clarificationMessage = clarification.message?.trim()
  const summaryMessage = workflow?.summary.message?.trim()
  const boilerplate = new Set<string>()
  if (clarificationMessage) boilerplate.add(clarificationMessage)
  if (summaryMessage) boilerplate.add(summaryMessage)
  const paragraphs = normalizedContent
    .split(/\n+/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
  const remaining = paragraphs.filter((paragraph) => !boilerplate.has(paragraph))
  if (remaining.length > 0) return remaining.join('\n\n')

  // 消息正文只是后端模板话术时，从实体设计载荷还原真实设计内容，
  // 保证历史会话读起来是实际生成/确认的过程。
  const parts: string[] = []
  if (clarificationMessage) parts.push(clarificationMessage)
  const entityDesign = clarification.review?.summary?.entityDesign
  if (entityDesign && typeof entityDesign === 'object') {
    const aiSuggestions = (entityDesign as Record<string, unknown>).ai_suggestions
    if (aiSuggestions && typeof aiSuggestions === 'object') {
      const aiText = String((aiSuggestions as Record<string, unknown>).text || '').trim()
      if (aiText) parts.push(aiText)
    }
  }
  return parts.join('\n\n')
}

/** assistant 消息头：标识当前是哪个阶段的 Agent（产品 / 规划 / 研发 / 测试 / 审查 / 验收）在回复。
 *  人像图标 + Agent 角色名，独占一行，下方换行展示正文/卡片。 */
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

/** 创建规划快照未到达时的加载卡，文案跟随当前产品/规划 Agent 身份。 */
function PlanningPendingCard({
  agentKey,
  detail
}: {
  agentKey: WorkbenchPhase
  detail: string
}): ReactElement {
  const agent = WORKBENCH_PHASE_AGENTS[agentKey]
  return (
    <section aria-live="polite" className={cx('planning-workflow-activity', 'running')}>
      <span className={cx('planning-workflow-activity-icon')} aria-hidden="true">
        <LoadingOutlined spin />
      </span>
      <div className={cx('planning-workflow-activity-copy')}>
        <Text strong>{agent.role} 正在处理</Text>
        <Text type="secondary">{detail}</Text>
      </div>
    </section>
  )
}

/** 从已答澄清卡之后的最近一条 user 留痕解析「header：答案」行，用于历史卡只读回填。 */
function parseAnsweredClarificationTrace(
  messages: AgentChatMessage[],
  cardIndex: number
): Record<string, string> | undefined {
  for (let index = cardIndex + 1; index < messages.length; index += 1) {
    const message = messages[index]
    if (message.role === 'assistant' && message.workflow) break
    if (message.role !== 'user') continue
    const answers: Record<string, string> = {}
    for (const line of (message.content || '').split('\n')) {
      const match = line.match(/^([^：\n]{1,40})：(.+)$/)
      if (match) answers[match[1].trim()] = match[2].trim()
    }
    if (Object.keys(answers).length > 0) return answers
  }
  return undefined
}

/** 从消息自身的 Workflow 节点推导 Agent 身份，避免阶段推进后历史消息被重新标记。 */
function messageAgentPhase(
  workflow: WorkflowRunPayload | undefined,
  fallback: WorkbenchPhase
): WorkbenchPhase {
  if (!workflow) return fallback
  const phase =
    String(workflow.summary?.phase || '').trim() ||
    String(workflow.result?.phase || '').trim() ||
    [...(workflow.events || [])]
      .reverse()
      .map((event) => String(event.nodeName || event.node?.id || '').trim())
      .find(Boolean) ||
    ''
  return workbenchPhaseForNode(phase, fallback)
}

type MessageListProps = {
  applicationLifecycle?: ApplicationLifecycle
  codeChangeActionsDisabled: boolean
  conversationRunning: boolean
  entityDesignSession?: boolean
  /** 当前会话最新一轮模型/Workflow 错误，优先在消息区展示统一错误卡片。 */
  error?: string
  /** 设计阶段：规划 workflow 确认卡始终可提交（由 planningSubmitRef 驱动），
   *  不走开发 execution 的 workflowInteractionAvailability 判定。 */
  designPhasePlanning?: boolean
  /** UI 设计稿确认：当前选中页 id（与右侧预览面板联动）。 */
  uiDesignActivePageId?: string
  /** UI 设计稿确认：选中页变化时通知外部（联动右侧预览）。 */
  onUiDesignActivePageChange?: (pageId: string) => void
  /** UI 设计稿确认：当前正在执行动作的 pageId 集合（联动右侧逐页加载态）。 */
  uiDesignActingPageIds?: string[]
  /** UI 设计稿确认：动作页集合变化时通知外部。 */
  onUiDesignActingPageIdsChange?: (ids: string[]) => void
  /** 需求文档确认：保存编辑草稿（重写 Markdown+JSON），返回更新后的 spec。 */
  onSaveRequirementSpec?: (
    workflow: WorkflowRunPayload,
    spec: Record<string, unknown>
  ) => Promise<Record<string, unknown> | undefined>
  /** 需求文档确认：菜单根路径（驱动编辑器页面路由前缀）。 */
  rootPath?: string
  /** 模板就绪后点击进入开发阶段（放开 product 锁，恢复跟随旅程）。 */
  onEnterDevelopment?: () => void
  /** 当前应用是否正在生成模板（前端状态信号，lifecycle 在生成期间不变）。 */
  generatingTemplate?: boolean
  /** 设计阶段最新的规划 workflow（activePlannings 权威快照，每轮 no-op resume 都会更新）。
   *  UI 设计稿确认卡片优先用它渲染，绕过消息对象里可能滞留的旧 message.workflow，
   *  保证后台生成池写入的最新页面状态实时反映到卡片。 */
  planningWorkflow?: WorkflowRunPayload
  /** 开发阶段：detailBlocker 卡片点击「开始详细设计」。 */
  onStartDetailDesign?: (page: DevelopmentPlanningPageOption) => void
  loading: boolean
  messages: AgentChatMessage[]
  onEntityDesignGateJump?: (entityId: string) => void
  onRevertCodeChanges: (messageId: number, codeChanges: WorkspaceCodeChangeSet) => void
  onSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>
  ) => Promise<void>
  onOpenCodeChangeFile: (codeChanges: WorkspaceCodeChangeSet, selectedPath: string) => void
  /** 错误来自当前设计阶段规划时，允许用户回到规划页重试。 */
  onRetryError?: () => void
  revertingCodeChangeIds: ReadonlySet<string>
  workspaceRoot?: string
}

/** 渲染聊天消息、Workflow 最终状态和代码变更操作。 */
export default function MessageList({
  applicationLifecycle,
  codeChangeActionsDisabled,
  conversationRunning,
  entityDesignSession = false,
  designPhasePlanning = false,
  error,
  uiDesignActivePageId,
  onUiDesignActivePageChange,
  uiDesignActingPageIds,
  onUiDesignActingPageIdsChange,
  onSaveRequirementSpec,
  rootPath,
  onEnterDevelopment,
  generatingTemplate,
  planningWorkflow,
  onStartDetailDesign,
  loading,
  messages,
  onEntityDesignGateJump,
  onOpenCodeChangeFile,
  onRevertCodeChanges,
  onRetryError,
  revertingCodeChangeIds,
  onSubmitClarification,
  workspaceRoot
}: MessageListProps): ReactElement {
  const { phase: currentPhase } = useWorkbenchPhase()
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const messageColumnRef = useRef<HTMLDivElement>(null)
  const followLatestContentRef = useRef(true)
  const restoringFollowRef = useRef(false)
  const scrollUpdateFrameRef = useRef<number>()
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const activeAssistantMessageId = loading ? findLastAssistantMessageId(messages) : undefined
  const latestAssistantMessageId = findLastAssistantMessageId(messages)
  const visibleError = error?.trim() || ''
  const latestAssistantMessage = findLastAssistantMessage(messages)
  const latestAssistantMessageError = latestAssistantMessage
    ? latestAssistantMessage.error?.trim() ||
      workflowFailureMessage(latestAssistantMessage.workflow)
    : ''
  // 外部错误属于新的系统提示；只有它已经被当前错误消息承载时才跳过独立追加，避免重复显示。
  const showStandaloneError = Boolean(visibleError && visibleError !== latestAssistantMessageError)
  const hasStreamingProcess = messages.some(
    (message) => message.id === activeAssistantMessageId && Boolean(message.processSteps?.length)
  )
  const latestVersionReminderMessageId = findLatestVersionReminderMessageId(messages)
  const latestUiDesignPreviewIndex = latestUiDesignPreviewMessageIndex(messages)
  const currentPlanningPhase = designPhasePlanning
    ? planningWorkflowPhase(planningWorkflow)
    : ''
  // 模板准备状态由 lifecycle/当前生成任务直接驱动，优先级高于规划会话的空加载占位。
  const templatePreparationVisible =
    designPhasePlanning && (generatingTemplate || isTemplatePreparing(applicationLifecycle))

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

  /** 在下一动画帧直接贴底，卡片展开/收起导致高度变化时也保持贴底。
      仅当用户处于跟随状态（接近底部）时才自动贴底；用户上翻查看历史卡片时，
      轮询/流式更新不再把视图拽到底部，改为显示「回到底部」悬浮按钮。 */
  const scheduleScrollUpdate = useCallback((): void => {
    if (scrollUpdateFrameRef.current !== undefined) {
      window.cancelAnimationFrame(scrollUpdateFrameRef.current)
    }
    scrollUpdateFrameRef.current = window.requestAnimationFrame(() => {
      // 双 rAF 等待 React 提交与浏览器布局全部稳定后再贴底，避免中间态高度截断滚动。
      scrollUpdateFrameRef.current = window.requestAnimationFrame(() => {
        scrollUpdateFrameRef.current = undefined
        const container = scrollContainerRef.current
        if (!container) {
          setShowScrollToBottom(false)
          return
        }
        if (!followLatestContentRef.current) {
          // 用户已上翻：不强行贴底，仅提示有新内容。
          setShowScrollToBottom(true)
          return
        }
        container.scrollTo({ top: container.scrollHeight, behavior: 'auto' })
        setShowScrollToBottom(false)
      })
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

  // 观察滚动容器和消息内容尺寸，覆盖窗口缩放、流式输出与卡片展开。
  // 不用 MutationObserver：下拉框/输入框等交互会在列内产生 DOM 变化，
  // 若一并触发贴底会把视图拉走，导致无法继续操作。
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
  }, [loading, messages, scheduleScrollUpdate, visibleError])

  return (
    <div className={cx('ai-message-list-shell')}>
      <div
        className={cx('ai-message-list')}
        aria-live="polite"
        onScroll={handleScroll}
        ref={scrollContainerRef}
      >
        <div className={cx('ai-message-column')} ref={messageColumnRef}>
          {messages.length === 0 && !visibleError && !templatePreparationVisible ? (
            designPhasePlanning ? (
              // 设计阶段空态也渲染为消息流里的同一张边框加载卡，不再使用全屏 Spin。
              <article className={cx('ai-message', 'assistant')}>
                <div className={cx('ai-message-content')}>
                  <MessageAgentHeader agentKey={currentPhase} />
                  <PlanningPendingCard
                    agentKey={currentPhase}
                    detail={
                      currentPhase === 'planning' ? '正在恢复规划阶段…' : '正在准备需求确认…'
                    }
                  />
                </div>
              </article>
            ) : (
              <div className={cx('ai-message-empty')}>
                <span className={cx('ai-message-empty-mark')}>
                  <RobotOutlined />
                </span>
                <Text strong>从一个想法开始</Text>
              </div>
            )
          ) : (
            messages.map((message, messageIndex) => {
              // 模板已开始准备后，空的 planning loading 已被更具体的模板卡取代；
              // 已完成的 TechnicalPlan 消息不满足此条件，仍作为历史保留。
              if (
                isTemplateSupersededPlanningProgressMessage(message, templatePreparationVisible)
              ) {
                return null
              }
              // 以 activePlannings 的当前权威阶段收口冲突消息：保留 UI 设计稿历史，
              // 只隐藏过早生成的 TechnicalPlan，进入规划窗口后也不回显旧入口卡。
              if (
                designPhasePlanning &&
                isSupersededPlanningPhaseMessage(message, currentPlanningPhase)
              ) {
                return null
              }
              // TechnicalPlan 已开始后，入口动作已经消费；不在规划窗口继续展示可点击入口卡。
              if (
                designPhasePlanning &&
                isSupersededPlanningStageEntryMessage(messages, messageIndex)
              ) {
                return null
              }
              // 新确认卡或结果消息已经到达时，不再渲染前一帧的空 loading，
              // 同时避免 loading 收口后留下只有 Agent 头像的空消息。
              if (
                designPhasePlanning &&
                isSupersededPlanningProgressMessage(message, latestAssistantMessageId)
              ) {
                return null
              }
              const messageLoading = message.id === activeAssistantMessageId
              const entityDesignMessage = isEntityDesignWorkflow(message.workflow)
              // 实体会话内所有消息按对话样式渲染，运行中的临时快照缺少实体
              // 上下文时也不回退显示 Agent 流程信息。
              const hideEntityWorkflowChrome = entityDesignSession || entityDesignMessage
              const isCurrentErrorMessage =
                Boolean(message.error || workflowFailureMessage(message.workflow)) &&
                message.role === 'assistant' &&
                message.id === latestAssistantMessageId
              const messageError = message.error || workflowFailureMessage(message.workflow)
              const codeChanges = message.codeChanges ?? workflowCodeChanges(message.workflow)
              const visibleCodeChanges = workflowShouldShowCodeChanges(message.workflow)
                ? codeChanges
                : undefined
              const codeChangesBeforeConfirmation = workflowCodeChangesBeforeConfirmation(
                message.workflow
              )
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
                message.workflow && planningWorkflowRequiresUserInput(message.workflow)
              // 设计规划已经由专用进度块表达当前意图和生成阶段，不再重复展示
              // 通用 ProcessSteps 的“执行完成 / 已归档步骤”摘要。
              const planningActivity = designPhasePlanning
                ? planningWorkflowActivity(message.workflow)
                : undefined
              // 只有真正携带实体设计载荷的确认才渲染聊天卡片；
              // DDL 审批等其它确认类型继续走 WorkflowRunCard 的审批卡片。
              const entityDesignCardVisible =
                hideEntityWorkflowChrome &&
                Boolean(
                  message.workflow &&
                    workflowClarification(message.workflow)?.review?.summary?.entityDesign
                )
              // UI 确认阶段的卡片在换一换/选模板期间（workflow running，clarification
              // 可能短暂丢失 requires_user_input/mode）也保持显示，避免卡片闪烁。
              // 用 phase=ui_confirmation 作为权威判据（running 期间 phase 不丢），
              // 辅以 clarification.mode 兜底。
              const messageClarification = message.workflow
                ? workflowClarification(message.workflow)
                : undefined
              const isUiDesignConfirmationCard =
                message.workflow &&
                (message.workflow.summary?.phase === 'ui_confirmation' ||
                  messageClarification?.mode === 'ui_design_confirmation')
              const isLatestUiDesignConfirmationCard =
                isUiDesignConfirmationCard && messageIndex === latestUiDesignPreviewIndex
              // 项目启动节点使用专用卡片覆盖运行、完成与失败状态。
              const isLaunchProjectCard = workflowShouldShowProjectLaunch(
                message.workflow,
                currentPhase
              )
              // 审查结果需要在后续验收快照中继续显示，不能只依赖当前 phase。
              const isReviewPhaseConfirmationCard =
                message.workflow && messageClarification?.mode === 'review_phase_confirmation'
              const isAcceptancePhaseConfirmationCard =
                message.workflow && messageClarification?.mode === 'acceptance_phase_confirmation'
              const isCodeReviewCard = workflowShouldShowCodeReview(message.workflow)
              // 创建规划的产品/技术阶段也展示 WorkflowRunCard，保证运行与确认状态连续可见。
              const isPlanningStageCard =
                message.workflow &&
                ['product_planning', 'project_planning', 'technical_planning'].includes(
                  String(message.workflow.summary?.phase || '')
                ) &&
                message.workflow.summary?.status !== 'running'
              const showWorkflowCard = Boolean(
                message.workflow &&
                  ((requiresClarification && !isUiDesignConfirmationCard) ||
                    isLatestUiDesignConfirmationCard ||
                    isPlanningStageCard ||
                    isLaunchProjectCard ||
                    isReviewPhaseConfirmationCard ||
                    isAcceptancePhaseConfirmationCard ||
                    isCodeReviewCard)
              )
              // 设计阶段/会话内只有列表末尾的待答卡可交互：其后出现答案留痕或下一张卡
              // 即证明它已被回答。历史待答卡渲染为失效态，避免旧表单以空白可填样式误导。
              const interactionAvailability =
                message.workflow && requiresClarification
                  ? messageIndex < messages.length - 1
                    ? 'stale'
                    : conversation || designPhasePlanning
                      ? 'active'
                      : workflowInteractionAvailability(message.workflow, applicationLifecycle)
                  : 'stale'
              // 已答过的历史澄清卡：从其后最近的 user 留痕解析「header：答案」行回填为
              // 只读摘要，避免旧表单以空白可填样式重现（恢复会话时 localStorage 草稿已丢）。
              const historicalClarificationAnswers =
                interactionAvailability === 'stale' &&
                requiresClarification &&
                messageClarification?.questions?.length
                  ? parseAnsweredClarificationTrace(messages, messageIndex)
                  : undefined
              const visibleAssistantContent = hideEntityWorkflowChrome
                ? entityDesignMessageContent(message.content, message.workflow)
                : workflowMessageContentForDisplay(
                    message.content,
                    message.workflow,
                    Boolean(visibleProcessSteps?.length)
                  )
              // 规划文档确认阶段由确认卡展示，隐藏流式 JSON 原文；生成中只显示加载态。
              // 规划占位消息（planningLoading）：用户提交后产品 Agent 正在思考，只显示 loading 态。
              const isPlanningArtifactConfirmationCard =
                message.workflow &&
                [
                  'requirement_document_confirmation',
                  'technical_plan_confirmation',
                  'project_plan_confirmation'
                ].includes(String(messageClarification?.mode || ''))
              // 已不再是当前待确认卡的规划确认卡一律锁定：一旦用户操作（留痕）或流程推进，
              // 其后会出现留痕 user 消息与下一张卡片，该卡便不再是列表末尾。操作类按钮
              // （确认/放弃/修改）随 disabled 禁用，查看类按钮不读 disabled、仍可点开。
              // 当前真正待确认的卡是列表末尾消息，其后没有任何消息，因此不受影响、保持可点。
              const planningArtifactAnswered =
                Boolean(isPlanningArtifactConfirmationCard) && messageIndex < messages.length - 1
              const isPlanningStageRunningCard =
                message.workflow &&
                isStructuredPlanningWorkflow(message.workflow) &&
                message.workflow.summary?.status === 'running'
              const isPlanningLoadingPlaceholder = Boolean(message.planningLoading)
              // 某些首帧会先带 running workflow，随后旧的占位标记可能被流式文本清掉；
              // 只要当前仍在运行且消息没有正文/确认卡，就继续显示生成中，避免只剩 Agent 头像。
              const isPlanningWorkflowRunning =
                designPhasePlanning && message.workflow?.summary?.status === 'running'
              // UI 确认阶段的轮询 no-op resume 会经历 running→requires_user_input 抖动。
              // running 期间 showWorkflowCard 可能因 requiresClarification/index 抖动瞬间为 false，
              // 导致 planningWorkflowNeedsChatLoading 返回 true、闪现"正在生成设计方案"loading 卡片，
              // 随后 requires_user_input 到达又切回 UI 确认卡，如此循环闪烁。UI 确认卡本身就能
              // 覆盖 running 态（卡片内 actingPageIds 显示逐页生成中），不需要外层 loading 卡片。
              // 只要这张消息是 UI 确认卡（phase=ui_confirmation），强制不显示 loading。
              const showPlanningLoading =
                !messageError &&
                !isUiDesignConfirmationCard &&
                planningWorkflowNeedsChatLoading(
                  message.workflow,
                  designPhasePlanning,
                  isPlanningLoadingPlaceholder,
                  showWorkflowCard,
                  visibleAssistantContent,
                  messageIndex === messages.length - 1
                )
              // 待确认卡片（requiresClarification）已由 WorkflowRunCard 展示表单/选项，
              // 隐藏流式文本原文（如「还有 N 个问题需要补充」），避免与卡片重复。
              // UI 确认阶段轮询 run 期间 status=running 但 phase 仍是 ui_confirmation，
              // 此时流式文本会短暂替代卡片造成闪烁，也需隐藏。
              const effectiveAssistantContent =
                messageError ||
                isPlanningArtifactConfirmationCard ||
                isPlanningStageRunningCard ||
                isLaunchProjectCard ||
                isAcceptancePhaseConfirmationCard ||
                isCodeReviewCard ||
                showPlanningLoading ||
                isUiDesignConfirmationCard ||
                (showWorkflowCard && requiresClarification && !entityDesignCardVisible)
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
                        {/* 统一 Agent 头：人像图标 + 当前阶段 Agent 角色名（产品/规划/研发/测试/审查），
                            独占一行，下方换行展示正文/卡片。 */}
                        <MessageAgentHeader
                          agentKey={messageAgentPhase(message.workflow, currentPhase)}
                        />
                        {messageError ? (
                          <AgentErrorCard
                            error={messageError}
                            onRetry={isCurrentErrorMessage ? onRetryError : undefined}
                          />
                        ) : null}
                        {/* 创建规划占位消息：初次进入或用户提交后当前阶段 Agent 正在准备，
                            planningLoading 标记的占位消息显示与「正在分析需求」一致的
                            边框卡片加载态，流式 chunk 到达后 planningLoading 被清除并展示返回内容。 */}
                        {showPlanningLoading &&
                          (planningActivity && message.workflow ? (
                            <PlanningWorkflowActivity workflow={message.workflow} />
                          ) : (
                            <PlanningPendingCard
                              agentKey={messageAgentPhase(message.workflow, currentPhase)}
                              detail={
                                isPlanningWorkflowRunning
                                  ? currentPhase === 'planning'
                                    ? '正在生成技术规划…'
                                    : '正在生成设计方案…'
                                  : designPhasePlanning
                                    ? currentPhase === 'planning'
                                      ? '正在恢复规划阶段…'
                                      : '正在准备需求确认…'
                                    : '正在处理…'
                              }
                            />
                          ))}
                        {!showPlanningLoading &&
                        !messageError &&
                        planningActivity &&
                        message.workflow ? (
                          <PlanningWorkflowActivity workflow={message.workflow} />
                        ) : null}
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
                        {!hideEntityWorkflowChrome &&
                          visibleProcessSteps &&
                          visibleProcessSteps.length > 0 &&
                          !designPhasePlanning && (
                            <ProcessSteps
                              conversation={conversation}
                              loading={messageLoading}
                              steps={visibleProcessSteps}
                            />
                          )}
                        {!hideEntityWorkflowChrome &&
                          messageLoading &&
                          message.toolCalls &&
                          message.toolCalls.length > 0 &&
                          // 自由对话已有安全化的过程步骤时只保留一份调用链，避免重复堆叠。
                          !hasConversationToolActivity && (
                            <ToolCallChain toolCalls={message.toolCalls} />
                          )}
                        {!messageLoading && visibleCodeChanges && (
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
                            className={cx(
                              !messageLoading && visibleCodeChanges && 'final-result-content'
                            )}
                          >
                            <MarkdownContent content={effectiveAssistantContent} />
                          </div>
                        )}
                        {!messageLoading && visibleCodeChanges && codeChangesBeforeConfirmation && (
                          <CodeChangeCard
                            codeChanges={visibleCodeChanges}
                            loading={messageLoading}
                            onApproveAll={() => undefined}
                            onOpenFile={(path) => onOpenCodeChangeFile(visibleCodeChanges, path)}
                            onRevert={() => onRevertCodeChanges(message.id, visibleCodeChanges)}
                            revertDisabled={codeChangeActionsDisabled}
                            reverting={revertingCodeChangeIds.has(visibleCodeChanges.id)}
                          />
                        )}
                        {message.workflow &&
                          (entityDesignCardVisible ? (
                            // 实体设计卡片在确认后也要保留展示（锁定态），
                            // 否则完成快照会清掉确认设计的上下文。
                            <EntityDesignChatCard
                              disabled={loading || interactionAvailability !== 'active'}
                              onInteraction={scheduleScrollUpdate}
                              onSubmitClarification={onSubmitClarification}
                              workflow={message.workflow}
                              workspaceRoot={workspaceRoot}
                            />
                          ) : showWorkflowCard ? (
                            <WorkflowRunCard
                              disabled={
                                loading ||
                                interactionAvailability !== 'active' ||
                                planningArtifactAnswered
                              }
                              historicalClarificationAnswers={historicalClarificationAnswers}
                              interactionAvailability={interactionAvailability}
                              onEntityDesignGateJump={onEntityDesignGateJump}
                              onSubmitClarification={onSubmitClarification}
                              uiDesignActivePageId={uiDesignActivePageId}
                              onUiDesignActivePageChange={onUiDesignActivePageChange}
                              uiDesignActingPageIds={uiDesignActingPageIds}
                              onUiDesignActingPageIdsChange={onUiDesignActingPageIdsChange}
                              onSaveRequirementSpec={onSaveRequirementSpec}
                              rootPath={rootPath}
                              planningWorkflow={planningWorkflow}
                              workflow={message.workflow}
                              workspaceRoot={workspaceRoot}
                            />
                          ) : null)}
                        {entityDesignSession && messageLoading && !requiresClarification && (
                          <EntityDesignChatCard loading />
                        )}
                        {!messageLoading &&
                          visibleCodeChanges &&
                          !codeChangesBeforeConfirmation && (
                            <CodeChangeCard
                              codeChanges={visibleCodeChanges}
                              loading={messageLoading}
                              onApproveAll={() => undefined}
                              onOpenFile={(path) => onOpenCodeChangeFile(visibleCodeChanges, path)}
                              onRevert={() => onRevertCodeChanges(message.id, visibleCodeChanges)}
                              revertDisabled={codeChangeActionsDisabled}
                              reverting={revertingCodeChangeIds.has(visibleCodeChanges.id)}
                            />
                          )}
                        {!messageLoading &&
                          visibleCodeChanges &&
                          message.workflow &&
                          message.id === latestVersionReminderMessageId && (
                            <VersionCommitReminder
                              codeChanges={visibleCodeChanges}
                              disabled={codeChangeActionsDisabled}
                              onReview={() =>
                                visibleCodeChanges.files[0] &&
                                onOpenCodeChangeFile(
                                  visibleCodeChanges,
                                  visibleCodeChanges.files[0].path
                                )
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
          {showStandaloneError ? (
            <article className={cx('ai-message', 'assistant')}>
              <div className={cx('ai-message-content')}>
                <MessageAgentHeader agentKey={currentPhase} />
                <AgentErrorCard
                  error={visibleError}
                  onRetry={onRetryError}
                  title={
                    /确认卡|中断|过期|版本/.test(visibleError) ? '规划确认未完成' : undefined
                  }
                />
              </div>
            </article>
          ) : null}
          {templatePreparationVisible ? (
            <article className={cx('ai-message', 'assistant', 'template-preparing-message')}>
              <div className={cx('ai-message-content')}>
                <TemplatePreparingCard
                  lifecycle={applicationLifecycle}
                  onEnterDevelopment={onEnterDevelopment}
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
  return findLastAssistantMessage(messages)?.id
}

/** 从消息末尾向前查找最新的 Assistant 消息，供错误去重和展示顺序复用。 */
function findLastAssistantMessage(messages: AgentChatMessage[]): AgentChatMessage | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'assistant') return messages[index]
  }
  return undefined
}

/** 从 Workflow 终态提取失败摘要，覆盖后端未额外发送 RUN_ERROR 的失败路径。 */
function workflowFailureMessage(workflow?: WorkflowRunPayload): string | undefined {
  if (workflow?.summary.status !== 'failed') return undefined
  return workflow.summary.message?.trim() || '本次模型调用未完成。'
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
