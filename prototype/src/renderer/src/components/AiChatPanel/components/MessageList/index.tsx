import {
  ArrowDownOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  RobotOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  ApplicationLifecycle,
  WorkflowRunPayload
} from '../../../../typings'
import type { ProcessStepRecord } from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import ToolCallCard from '../ToolCallCard'
import ProcessSteps from '../ProcessSteps'
import WorkflowRunCard, {
  type ClarificationAnswers
} from '../WorkflowRunCard'
import { workflowClarification } from '../WorkflowRunCard/workflowClarification'
import DetailConfirmationPageSelector from '../../../../components/DetailConfirmationPageSelector'
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
import {
  endpointDetailTargetKey,
  pageDetailTargetKey,
  workflowCodeChanges,
  workflowDetailTargetKey,
  workflowFinalResultPresentation
} from '../../utils'
import { workflowInteractionAvailability } from '../../planExecutionMode'
import { useWorkbenchPhase } from '../../../../context'
import { WORKBENCH_PHASE_AGENTS, type WorkbenchPhase } from '../../../../workbenchPhase'
import { isMessageListNearBottom, shouldShowScrollToBottom } from './scrollState'
import './MessageList.less'

const { Text } = Typography

// 对话区瘦身：工具调用 / 最终结果头隐藏；节点过程（ProcessSteps，构建中展开/完成收起）保留，
// 让选页面后的多节点生成链（检查工作区→规划 DAG→构建→集成测试）清晰可见。
const SLIM_CONVERSATION = true

/** 返回各阶段 Agent 的角色首字，保持头像识别简单直接。 */
function agentAvatarInitial(agentKey: WorkbenchPhase): string {
  const initials: Record<WorkbenchPhase, string> = {
    analysis: 'P',
    planning: 'PM',
    development: 'RD',
    testing: 'QA',
    review: 'CR',
    acceptance: '验'
  }
  return initials[agentKey]
}

/** 将当前 Agent 映射为用户可理解的具体工作流名称。 */
function workflowTitleForAgent(agentKey: WorkbenchPhase): string {
  const titles: Record<WorkbenchPhase, string> = {
    analysis: '需求分析工作流',
    planning: '项目计划编写工作流',
    development: '开发工作流',
    testing: '测试验证工作流',
    review: '代码审查工作流',
    acceptance: '验收确认'
  }
  return titles[agentKey]
}

/** 测试阶段按 Workflow 类型显示固定的工作流模板名称，用例名称由节点和右侧目录承载。 */
function testingWorkflowTitle(workflow: WorkflowRunPayload | undefined): string | undefined {
  const state = (workflow?.state || {}) as Record<string, unknown>
  const workflowType = String(state.testWorkflowType || '')
  if (workflowType === 'non-functional') return '非功测试工作流'
  if (workflowType === 'case') return '用例测试工作流'
  return undefined
}

/** 从工作流节点反推消息所属阶段，作为旧会话缺少显式 Agent 标记时的稳定兜底。 */
function workflowPhaseToAgentPhase(
  workflow?: WorkflowRunPayload
): WorkbenchPhase | undefined {
  const phase = String(workflow?.summary?.phase || '')
  if (['requirements', 'requirement_spec_confirmation'].includes(phase)) return 'analysis'
  if (
    ['project_planning', 'project_plan_confirmation', 'development_entry_confirmation'].includes(
      phase
    )
  )
    return 'planning'
  if (
    [
      'detail_confirmation',
      'inspect_workspace',
      'inspect_database_context',
      'prepare_build_tasks',
      'build',
      'integration_test',
      'launch_project'
    ].includes(phase)
  ) {
    return 'development'
  }
  if (phase === 'acceptance') return 'acceptance'
  if (['application_test', 'business_test', 'test', 'testing'].includes(phase)) {
    return 'testing'
  }
  if (['code_review', 'lint_check', 'security_scan', 'health_check', 'finalize_project'].includes(phase)) {
    return 'review'
  }
  return undefined
}

/** 过滤旧版本遗留的结构化控制消息，避免卡片选择与用户正文重复出现。 */
function isSyntheticWorkflowActionMessage(message: AgentChatMessage): boolean {
  if (message.role !== 'user') return false
  const content = message.content.trim()
  return (
    /^(?:开始|继续)实现(?:接口)?：/.test(content) ||
    content === '重试当前计划任务。' ||
    /^从 .+ 节点继续执行 workflow 调试。$/.test(content) ||
    content === '结束当前计划。' ||
    content === '暂停当前计划执行。'
  )
}

/** 隐藏旧版本写入对话的单产物完成提示，避免历史会话继续出现误导性阶段文案。 */
function isSyntheticArtifactCompletionMessage(message: AgentChatMessage): boolean {
  if (message.role !== 'assistant') return false
  const content = message.content.trim()
  return /代码产物已完成[\s\S]*进入测试阶段/.test(content)
}

/** 隐藏旧剧本在开发准入后追加的过渡消息；阶段切换应直接由确认弹框完成。 */
function isRedundantDevelopmentEntryMessage(message: AgentChatMessage): boolean {
  if (message.role !== 'assistant') return false
  return /^项目计划已确认，已创建(?:业务)?测试用例.*，即将进入开发阶段。$/.test(
    message.content.trim()
  )
}

/** 隐藏测试阶段旧剧本的启动与结果提示，测试节点和 Diff 授权本身已经表达完整流程。 */
function isRedundantTestingMessage(message: AgentChatMessage): boolean {
  const content = message.content.trim()
  if (message.role === 'user') return content === '开始应用测试'
  return (
    content === '测试报告内容已生成，请在右侧确认 Diff 后接受。' ||
    content === '测试结果已确认：启动、非功能和业务测试均已通过。'
  )
}

/** 隐藏旧版审查阶段的确认卡，审查阶段只保留报告 Diff 授权，不再追加确认事项。 */
function isRedundantReviewStartMessage(message: AgentChatMessage): boolean {
  if (message.role === 'user') return message.content.trim() === '开始代码审查'
  if (!message.workflow) return false
  return ['review_start', 'code_review'].includes(workflowClarification(message.workflow)?.mode || '')
}

/** 隐藏验收条已经表达过的旧启动消息，验收对话只保留用户进入对话后的提示。 */
function isRedundantAcceptanceStartMessage(message: AgentChatMessage): boolean {
  if (message.role === 'user') return message.content.trim() === '开始应用验收'
  if (message.content.trim() === '请根据需求文档基线完成应用验收。') return true
  if (!message.workflow) return false
  return workflowClarification(message.workflow)?.mode === 'application_acceptance'
}

/** 隐藏已完成但没有正文、节点或交互的空 assistant 消息，避免历史会话留下孤立头像。 */
function isCompletedEmptyAssistantMessage(message: AgentChatMessage): boolean {
  if (message.role !== 'assistant' || message.content.trim()) return false
  if (message.detailBlocker || message.codeChanges || message.processSteps?.length) return false
  return message.workflow?.summary?.status === 'completed'
}

/** assistant 消息头：标识当前是哪个阶段的 Agent 在回复。 */
function MessageAgentHeader({
  agentKey,
  loading = false
}: {
  agentKey: WorkbenchPhase
  loading?: boolean
}): ReactElement {
  const agent = WORKBENCH_PHASE_AGENTS[agentKey]
  return (
    <div className={cx('ai-message-agent', agentKey)}>
      <span className={cx('ai-message-agent-avatar')} aria-hidden="true">
        {agentAvatarInitial(agentKey)}
      </span>
      <span className={cx('ai-message-agent-name')}>{agent.role}</span>
      {loading && (
        <LoadingOutlined
          aria-label="Agent 正在处理"
          className={cx('ai-message-agent-loading')}
          spin
        />
      )}
    </div>
  )
}

type MessageListProps = {
  /** 当前查看任务所属阶段；查看历史任务时可与应用当前阶段不同。 */
  agentPhase?: WorkbenchPhase
  /** 当前对话所属阶段；优先于全局查看阶段，保证跨阶段查看历史会话不改 Agent。 */
  conversationPhase?: WorkbenchPhase
  applicationLifecycle?: ApplicationLifecycle
  /** 版本或历史阶段锁定时，所有会改变产物的卡片动作同时禁用。 */
  interactionsDisabled?: boolean
  loading: boolean
  messages: AgentChatMessage[]
  onSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => Promise<void>
  onDiscardArtifact: (docKey: WorkspaceDocKey) => void
  onStartDetailDesign?: DetailConfirmationStart
}

/** 渲染聊天消息、Workflow 最终状态和代码变更操作。 */
export default function MessageList({
  agentPhase,
  applicationLifecycle,
  conversationPhase,
  interactionsDisabled = false,
  loading,
  messages,
  onDiscardArtifact,
  onSubmitClarification,
  onStartDetailDesign,
}: MessageListProps): ReactElement {
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const messageColumnRef = useRef<HTMLDivElement>(null)
  // 每次会话挂载的首批消息从顶部呈现，避免历史内容一出现就被自动滚到底部。
  const initialMessagesPresentedRef = useRef(false)
  const followLatestContentRef = useRef(false)
  const restoringFollowRef = useRef(false)
  const scrollUpdateFrameRef = useRef<number>()
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)
  const { viewingPhase: currentPhase } = useWorkbenchPhase()
  const displayMessages = messages.filter(
    (message) =>
      !isSyntheticWorkflowActionMessage(message) &&
      !isSyntheticArtifactCompletionMessage(message) &&
      !isRedundantDevelopmentEntryMessage(message) &&
      !isRedundantTestingMessage(message) &&
      !isRedundantReviewStartMessage(message) &&
      !isRedundantAcceptanceStartMessage(message) &&
      !isCompletedEmptyAssistantMessage(message)
  )
  const activeAssistantMessageId = loading ? findLastAssistantMessageId(displayMessages) : undefined
  const hasWorkflowForTarget = (targetKey: string): boolean =>
    Boolean(targetKey) &&
    displayMessages.some(
      (message) => message.workflow && workflowMatchesTarget(message.workflow, targetKey)
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

  // 首批消息始终从对话区顶部开始；首批呈现后，新消息才沿用常规的底部跟随。
  useEffect(() => {
    const container = scrollContainerRef.current
    if (displayMessages.length === 0 || !container) return undefined

    if (!initialMessagesPresentedRef.current) {
      initialMessagesPresentedRef.current = true
      followLatestContentRef.current = false
      restoringFollowRef.current = false
      container.scrollTo({ top: 0, behavior: 'auto' })
      setShowScrollToBottom(false)
      return undefined
    }

    // 只有用户原本就在底部时才跟随新增消息；阅读历史时追加下一条 Workflow 不抢走滚动位置。
    // 这样同一主会话追加第二个产物时，列表保持连续，不会表现成整段内容被刷新。
    if (!followLatestContentRef.current && !isMessageListNearBottom(container)) {
      setShowScrollToBottom(shouldShowScrollToBottom(container))
      return undefined
    }
    followLatestContentRef.current = true
    container.scrollTo({ top: container.scrollHeight, behavior: 'auto' })
    scheduleScrollUpdate()
    const timer = window.setTimeout(scheduleScrollUpdate, 300)
    return () => window.clearTimeout(timer)
  }, [displayMessages.length, loading, messages, scheduleScrollUpdate])

  return (
    <div className={cx('ai-message-list-shell')}>
      <div
        className={cx('ai-message-list')}
        aria-live="polite"
        onScroll={handleScroll}
        ref={scrollContainerRef}
      >
        <div className={cx('ai-message-column')} ref={messageColumnRef}>
          {/* 只有原始会话确实没有任何消息时才显示空白占位；被过滤的控制消息也不应把已有对话伪装成空白。 */}
          {displayMessages.length === 0 ? (
            loading ? (
              <MessageAgentHeader
                agentKey={conversationPhase || agentPhase || currentPhase}
                loading
              />
            ) : messages.length === 0 ? (
              <div className={cx('ai-message-empty')}>
                <span className={cx('ai-message-empty-mark')}>
                  <RobotOutlined />
                </span>
                <Text strong>从一个想法开始</Text>
              </div>
            ) : null
          ) : (
            displayMessages.map((message, messageIndex) => {
              const messageLoading = message.id === activeAssistantMessageId
              const consecutiveAssistant =
                message.role === 'assistant' && messages[messageIndex - 1]?.role === 'assistant'
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
              // 每条消息优先使用创建时记录的 Agent，再按工作流节点和会话归属兜底；
              // 切换阶段只切换当前会话，不改写已经存在的历史消息身份。
              const workflowAgentPhase = workflowPhaseToAgentPhase(message.workflow)
              const messageAgentKey: WorkbenchPhase =
                message.agentPhase ||
                workflowAgentPhase ||
                conversationPhase ||
                agentPhase ||
                currentPhase
              const workflowTitle =
                testingWorkflowTitle(message.workflow) || workflowTitleForAgent(messageAgentKey)
              const requiresClarification =
                message.workflow &&
                workflowClarification(message.workflow)?.status === 'requires_user_input'
              // 只有最后一张待确认卡才可能是 active；历史卡（已提交 / 被新回复取代）一律 stale，
              // 防止设计阶段 fast-path 把所有 requires_user_input 历史快照都判成 active（按钮复活）。
              const lastPendingClarificationId = (() => {
                for (let i = displayMessages.length - 1; i >= 0; i -= 1) {
                  const candidate = displayMessages[i]
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
              // 测试会话刚创建时查看阶段可能还没从开发阶段同步过来，不能只看 agentPhase；
              // 以测试 Workflow 自身的运行状态兜底，保证第一条节点消息首次挂载就是展开态。
              const testWorkflowPhase = message.workflow?.summary?.phase
              const testWorkflowStatus = message.workflow?.summary?.status
              const testWorkflowRunning =
                (testWorkflowPhase === 'application_test' || testWorkflowPhase === 'business_test') &&
                testWorkflowStatus === 'running'
              const inlineTestCaseAuthorization =
                Boolean(message.workflow) &&
                workflowClarification(message.workflow)?.mode === 'test_case_execute' &&
                requiresClarification &&
                Boolean(visibleProcessSteps?.length)
              // 产物验收卡与用例授权同构：内嵌在「确认验收」节点上，不单独渲染。
              const inlineArtifactAcceptance =
                Boolean(message.workflow) &&
                workflowClarification(message.workflow)?.mode === 'page_acceptance' &&
                requiresClarification &&
                Boolean(visibleProcessSteps?.length)
              // 执行方式选择卡内嵌在「选择执行方式」节点上；节点轨迹存在时不再重复渲染独立卡。
              const inlineBackgroundDispatch =
                Boolean(message.workflow) &&
                workflowClarification(message.workflow)?.mode === 'background_dispatch' &&
                requiresClarification &&
                Boolean(visibleProcessSteps?.length)
              // 模板选择详设卡已经承载完整引导，隐藏历史中重复保存的普通说明正文。
              const visibleAssistantContent = message.detailBlocker
                ? ''
                : workflowMessageContentForDisplay(
                    message.content,
                    message.workflow,
                    Boolean(visibleProcessSteps?.length)
                  )
              const detailBlockerTargetKey =
                message.detailBlocker?.type === 'endpoint'
                  ? endpointDetailTargetKey(
                      message.detailBlocker.apiContractId,
                      message.detailBlocker.endpointId
                    )
                  : message.detailBlocker?.type === 'page'
                    ? pageDetailTargetKey(message.detailBlocker.pageId)
                    : ''
              const detailBlockerWorkflowStarted = hasWorkflowForTarget(detailBlockerTargetKey)
              const developmentTemplateSelector =
                message.detailBlocker?.type === 'page' ? (
                  <DetailConfirmationPageSelector
                    embedded
                    disabled={loading || detailBlockerWorkflowStarted || interactionsDisabled}
                    loading={loading}
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
                ) : message.detailBlocker?.type === 'endpoint' ? (
                  <DetailConfirmationPageSelector
                    embedded
                    disabled={loading || detailBlockerWorkflowStarted || interactionsDisabled}
                    loading={loading}
                    onStart={onStartDetailDesign}
                    selectedEndpoint={{
                      apiContractId: message.detailBlocker.apiContractId,
                      endpointId: message.detailBlocker.endpointId,
                      label: message.detailBlocker.label,
                      path: message.detailBlocker.path,
                      purpose: message.detailBlocker.purpose
                    }}
                  />
                ) : undefined
              const developmentProcessSteps: ProcessStepRecord[] | undefined =
                developmentTemplateSelector
                  ? (() => {
                      const templateStepId = `detail-template-${detailBlockerTargetKey || message.id}`
                      const hasTemplateStep = Boolean(
                        visibleProcessSteps?.some((step) => step.id === templateStepId)
                      )
                      const templateStep: ProcessStepRecord = {
                        id: templateStepId,
                        kind: 'workflow',
                        status: detailBlockerWorkflowStarted
                          ? 'completed'
                          : 'requires_user_input',
                        title:
                          message.detailBlocker?.type === 'endpoint'
                            ? '确认接口详细设计'
                            : '选择页面模板',
                        detail: detailBlockerWorkflowStarted
                          ? '模板已确认，继续执行详细设计 Workflow。'
                          : '请选择页面模板后开始详细设计。',
                        sequence: 1,
                        nodeName: 'detail_confirmation'
                      }
                      const nextSteps: ProcessStepRecord[] = (visibleProcessSteps || []).map(
                        (step): ProcessStepRecord =>
                        step.id === templateStepId && detailBlockerWorkflowStarted
                          ? { ...step, status: 'completed', detail: templateStep.detail }
                          : {
                              ...step,
                              sequence: step.sequence + (hasTemplateStep ? 0 : 1)
                            }
                      )
                      return hasTemplateStep ? nextSteps : [templateStep, ...nextSteps]
                    })()
                  : visibleProcessSteps
              return (
                <article
                  className={cx(
                    'ai-message',
                    message.role,
                    consecutiveAssistant && 'consecutive-assistant',
                    message.role === 'assistant' && !messageLoading && 'completed'
                  )}
                  key={message.id}
                >
                  <div className={cx('ai-message-content')}>
                    {message.role === 'assistant' ? (
                      <>
                        <MessageAgentHeader agentKey={messageAgentKey} loading={messageLoading} />
                        {developmentProcessSteps && developmentProcessSteps.length > 0 && (
                          <ProcessSteps
                            loading={messageLoading || testWorkflowRunning}
                            steps={developmentProcessSteps}
                            workflowTitle={workflowTitle}
                            inlineFirstNode={developmentTemplateSelector}
                            inlineFirstNodePending={
                              Boolean(developmentTemplateSelector) &&
                              !detailBlockerWorkflowStarted &&
                              !interactionsDisabled
                            }
                            workflow={message.workflow}
                            interactionAvailability={interactionAvailability}
                            interactionDisabled={interactionsDisabled || loading || messageLoading}
                            onSubmitClarification={onSubmitClarification}
                            waitingForInput={waitingForDirectModificationInput}
                            waitingPrompt={message.workflow?.summary?.message}
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
                        {message.workflow &&
                          requiresClarification &&
                          !inlineTestCaseAuthorization &&
                          !inlineArtifactAcceptance &&
                          !inlineBackgroundDispatch && (
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
                      </>
                    ) : (
                      <>
                        <div className={cx('ai-message-user-head')}>
                          <span className={cx('ai-message-user-avatar')} aria-hidden="true">
                            S
                          </span>
                          <span className={cx('ai-message-user-name')}>Steve Jobs</span>
                        </div>
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

/** 判断 Workflow 是否属于指定页面或接口，兼容页面携带依赖接口身份的任务快照。 */
function workflowMatchesTarget(workflow: WorkflowRunPayload, targetKey: string): boolean {
  if (workflowDetailTargetKey(workflow) === targetKey) return true
  if (!targetKey.startsWith('page:')) return false
  const pageId = targetKey.slice('page:'.length)
  const selectedPageIds = [workflow.state?.selectedPageId, workflow.result?.selectedPageId]
  return selectedPageIds.some((value) => typeof value === 'string' && value === pageId)
}
