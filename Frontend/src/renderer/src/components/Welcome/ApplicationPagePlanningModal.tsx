import { ReloadOutlined } from '@ant-design/icons'
import { Button, message, Result, Spin, Steps } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  ApplicationConfig,
  ApplicationPlanningConfirmation,
  ApplicationLifecycle,
  WorkflowClarification,
  WorkflowClarificationAnswers,
  WorkflowRunPayload
} from '../../typings'
import {
  buildApplicationPlanningRequest,
  createApplicationPlanningSession,
  saveRequirementSpecDraft
} from '../../service/applicationPagePlanning'
import { getApplicationLifecycle } from '../../service/applicationLifecycle'
import { isAuthenticationFailure } from '../../service/authentication'
import { cx } from '../../utils'
import { formatError } from './utils'
import ApplicationPlanningProgress, {
  type ApplicationPlanningProgressEvent
} from './ApplicationPlanningProgress'
import ApplicationPlanningQuestionPanel from './ApplicationPlanningQuestionPanel'
import UiDesignStreamingPreview from './UiDesignStreamingPreview'
import { planningWorkflowPhase, planningWorkflowRequiresUserInput } from './planningWorkflowState'
import type { ActivePlanningStatus } from '../../service/activeApplicationPlanning'
import './ApplicationPagePlanningModal.less'

const { Step } = Steps

// 绘制带轻微弧度的单向返回箭头，避免视觉上接近刷新图标。
function CurvedBackIcon(): JSX.Element {
  return (
    <svg
      aria-hidden="true"
      className={cx('page-planning-back-glyph')}
      fill="none"
      viewBox="0 0 24 24"
    >
      <path d="M10 7.5 5.5 12 10 16.5" />
      <path d="M6 12h7.2c3.4 0 5.8 2.1 5.8 5" />
    </svg>
  )
}

type Props = {
  application: ApplicationConfig
  initialStatus: ActivePlanningStatus
  initialLifecycle: ApplicationLifecycle
  initialWorkflow?: WorkflowRunPayload
  theme: 'dark' | 'light'
  threadId: string
  visible: boolean
  onReturnHome: () => void
  onConfirmed: (confirmation: ApplicationPlanningConfirmation) => Promise<boolean>
  onStatusChange: (status: ActivePlanningStatus) => void
  onWorkflowChange: (workflow: WorkflowRunPayload) => void
  onStopHandlerChange: (handler?: () => Promise<void>) => void
}

const phaseOrder = ['requirements', 'ui_confirmation', 'project_planning']

const phaseProgress: Record<
  string,
  { active: number; complete: number; message: string; title: string }
> = {
  requirements: {
    active: 18,
    complete: 30,
    message: '正在分析需求并生成需求文档…',
    title: '正在确认产品需求'
  },
  ui_confirmation: {
    active: 45,
    complete: 60,
    message: '正在为各页面生成设计稿…',
    title: '正在生成UI设计稿'
  },
  project_planning: {
    active: 75,
    complete: 100,
    message: '正在生成项目计划…',
    title: '正在规划项目结构'
  }
}

// 从 Workflow 公开状态中读取 specs/plans 产物校验结果。
function workflowConfirmation(
  workflow?: WorkflowRunPayload
): ApplicationPlanningConfirmation | undefined {
  for (const source of [workflow?.result, workflow?.state]) {
    const value = source?.application_planning_confirmation
    if (value && typeof value === 'object') return value as ApplicationPlanningConfirmation
  }
  return undefined
}

// 读取需求文档里登记的页面总数，用于 UI确认生成期间渲染未就绪骨架。
function planningUiDesignPageTotal(workflow?: WorkflowRunPayload): number {
  if (!workflow) return 0
  for (const source of [workflow.result, workflow.state]) {
    const spec = source?.requirement_spec
    if (spec && typeof spec === 'object' && !Array.isArray(spec)) {
      const pages = (spec as Record<string, unknown>).pages
      if (Array.isArray(pages)) return pages.length
    }
  }
  return 0
}

// 把后端保存后的 RequirementSpec 和 Markdown 正文合并回当前确认卡。
function withSavedRequirementSpec(
  workflow: WorkflowRunPayload,
  saved: Awaited<ReturnType<typeof saveRequirementSpecDraft>>
): WorkflowRunPayload {
  return {
    ...workflow,
    confirmationArtifact: saved.artifact,
    state: { ...workflow.state, requirement_spec: saved.requirementSpec },
    result: { ...workflow.result, requirement_spec: saved.requirementSpec }
  }
}

// 用提交前读取的权威 lifecycle 替换旧 Workflow 快照中的交互并发令牌。
function withAuthoritativeLifecycle(
  workflow: WorkflowRunPayload,
  lifecycle: ApplicationLifecycle
): WorkflowRunPayload {
  return {
    ...workflow,
    state: { ...workflow.state, lifecycle },
    result: { ...workflow.result, lifecycle }
  }
}

// 根据当前阶段计算两步规划条的高亮位置。
function workflowStep(workflow?: WorkflowRunPayload): number {
  const phase = planningWorkflowPhase(workflow)
  const index = phaseOrder.indexOf(phase)
  return index >= 0 ? index : 0
}

// 判断当前是否已经进入项目规划确认，便于切换成完整的项目规划工作区壳层。
function projectPlanConfirmationReady(workflow?: WorkflowRunPayload): boolean {
  const clarifications = [
    workflow?.summary.clarification,
    workflow?.state?.clarification,
    workflow?.result?.clarification
  ]
  return clarifications.some((clarification) => {
    if (!clarification || typeof clarification !== 'object') return false
    return (clarification as WorkflowClarification).mode === 'project_plan_confirmation'
  })
}

// 将独立 Workflow 的当前节点转换为原页面规划进度组件需要的阶段时间线。
function workflowProgressEvents(
  workflow?: WorkflowRunPayload,
  preparingTemplate = false
): ApplicationPlanningProgressEvent[] {
  if (!workflow) return []
  const currentIndex = workflowStep(workflow)
  const finished = workflow.summary.status === 'completed'
  const events = phaseOrder.slice(0, currentIndex + 1).map((stage, index) => {
    const meta = phaseProgress[stage]
    const completed = index < currentIndex || (finished && index === currentIndex)
    return {
      stage,
      percent: completed ? meta.complete : meta.active,
      message: completed ? `${meta.title.replace('正在', '')}已完成` : meta.message,
      detail:
        index === currentIndex && workflow.summary.message
          ? String(workflow.summary.message)
          : undefined
    }
  })
  if (preparingTemplate) {
    events.push({
      stage: 'application_template',
      percent: 92,
      message: '正在下载模板代码并准备工作区…',
      detail: undefined
    })
  }
  return events
}

// 返回当前节点在动态进度卡上的标题与兜底说明。
function workflowProgressCopy(workflow?: WorkflowRunPayload): { fallback: string; title: string } {
  const stage = phaseOrder[workflowStep(workflow)]
  const meta = phaseProgress[stage] || phaseProgress.requirements
  return { fallback: meta.message, title: meta.title }
}

// 在创建应用弹窗中运行并可视化独立的两节点规划 Graph。
export default function ApplicationPagePlanningModal({
  application,
  initialStatus,
  initialLifecycle,
  initialWorkflow,
  theme,
  threadId,
  visible,
  onReturnHome,
  onConfirmed,
  onStatusChange,
  onWorkflowChange,
  onStopHandlerChange
}: Props): JSX.Element {
  const session = useMemo(() => createApplicationPlanningSession(threadId), [threadId])
  const originalRequest = useMemo(() => buildApplicationPlanningRequest(application), [application])
  const startedRef = useRef(false)
  const completedRef = useRef(false)
  // 一旦进入过 UI 确认阶段就锁定：单页"选模板/换一换"run 期间 workflow 流式快照
  // 可能短暂丢失 clarification/phase，导致 showingProgress 闪烁切回进度页白屏。
  // 锁定后整个会话不再切回全屏进度页，逐页动作只在渲染区显示加载态。
  const enteredUiConfirmationRef = useRef(false)
  const [workflow, setWorkflow] = useState<WorkflowRunPayload | undefined>(initialWorkflow)
  const [running, setRunning] = useState(false)
  const [preparingTemplate, setPreparingTemplate] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [error, setError] = useState(
    initialStatus === 'error' ? '上次规划流程中断，请重试或检查当前规划内容。' : ''
  )
  const progressCopy = workflowProgressCopy(workflow)
  const awaitingUserInput = planningWorkflowRequiresUserInput(workflow)
  // 检测是否已进入 UI 确认阶段：一旦命中即锁定，避免 run 期间流式快照丢失导致回切进度页。
  if (
    !enteredUiConfirmationRef.current &&
    planningWorkflowPhase(workflow) === 'ui_confirmation' &&
    Boolean(
      workflow?.summary?.clarification ||
        workflow?.state?.clarification ||
        workflow?.result?.clarification
    )
  ) {
    enteredUiConfirmationRef.current = true
  }
  const inUiConfirmationStage = enteredUiConfirmationRef.current
  const showingProgress =
    !workflow || (running && !awaitingUserInput && !inUiConfirmationStage)
  // run 中途流式快照可能短暂丢失 clarification，此时确认面板会返回 null 导致白屏。
  // 有 workflow 但无 clarification 时显示加载态兜底，避免空白。
  const hasClarification = Boolean(
    workflow?.summary?.clarification ||
      workflow?.state?.clarification ||
      workflow?.result?.clarification
  )
  // UI确认节点生成期间，流式展示已就绪的设计稿，避免干等到最后一次性出现。
  // 排除 ui_confirmation 已完成（用户一键确认全部设计稿后同 run 流转到 project_planning，
  // 但 project_planning 的 started 帧到达前可能短暂停留在 ui_confirmation completed 帧），
  // 否则会误显示"设计稿生成中"。
  const streamingUiPhase =
    showingProgress &&
    planningWorkflowPhase(workflow) === 'ui_confirmation' &&
    workflow?.summary?.status !== 'completed'
  const streamingUiTotal = planningUiDesignPageTotal(workflow)
  const isProjectPlanConfirmation = projectPlanConfirmationReady(workflow)

  // 向首页注册当前 AG-UI 会话的停止句柄，以便从规划页外安全取消运行。
  useEffect(() => {
    onStopHandlerChange(() => session.stop())
    return () => onStopHandlerChange(undefined)
  }, [onStopHandlerChange, session])

  // 将运行、待查看或异常状态同步给首页的规划入口。
  useEffect(() => {
    const status: ActivePlanningStatus = error
      ? 'error'
      : (running && !awaitingUserInput) || !workflow
        ? 'running'
        : 'ready'
    onStatusChange(status)
  }, [awaitingUserInput, error, onStatusChange, running, workflow])

  // 同步组件内 Workflow 展示状态与可跨重启恢复的外部快照。
  const handleWorkflowChange = (nextWorkflow: WorkflowRunPayload): void => {
    // 每个全屏规划实例只接收自己的线程事件，避免并行应用互相覆盖问题卡片。
    if (nextWorkflow.threadId !== threadId) return
    setWorkflow(nextWorkflow)
    onWorkflowChange(nextWorkflow)
  }

  // 保持加载界面直到模板准备完成，失败时恢复可重试状态。
  const completePlanning = async (confirmation: ApplicationPlanningConfirmation): Promise<void> => {
    completedRef.current = true
    setPreparingTemplate(true)
    try {
      const succeeded = await onConfirmed(confirmation)
      if (succeeded) return
      completedRef.current = false
      setError('应用模板准备失败，请重试；成功后才会进入工作台。')
    } catch (reason) {
      completedRef.current = false
      throw reason
    } finally {
      setPreparingTemplate(false)
    }
  }

  // 运行初始或恢复轮次，并在项目规划确认后直接打开工作台。
  const runPlanning = async (
    messageText: string,
    answers?: WorkflowClarificationAnswers,
    resumeState?: WorkflowRunPayload,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string
  ): Promise<void> => {
    if (!application.workspaceRoot) return
    setRunning(true)
    onStatusChange('running')
    setError('')
    setStreamingContent('')
    try {
      let currentResumeState = resumeState
      if (currentResumeState) {
        const lifecycle = await getApplicationLifecycle(application, threadId)
        currentResumeState = withAuthoritativeLifecycle(currentResumeState, lifecycle)
        handleWorkflowChange(currentResumeState)
      }
      const result = await session.sendMessage(messageText, {
        application,
        clarificationAnswers: answers,
        editedRequirementSpec,
        requirementSpecFeedback,
        editorMode: 'frontend',
        originalRequest,
        resumeState: currentResumeState,
        workflowDebug: currentResumeState
          ? undefined
          : {
              enabled: true,
              resumeFrom:
                initialLifecycle.initialization.stage === 'generating_project_plan' ||
                initialLifecycle.initialization.stage === 'awaiting_project_plan_confirmation'
                  ? 'project_planning'
                  : initialLifecycle.initialization.stage === 'generating_ui_designs' ||
                      initialLifecycle.initialization.stage === 'awaiting_ui_design_confirmation'
                    ? 'ui_confirmation'
                    : 'requirements'
            },
        workflowScope: 'application_planning',
        workspaceRoot: application.workspaceRoot,
        onContent: setStreamingContent,
        onWorkflow: handleWorkflowChange
      })
      if (result.workflow) handleWorkflowChange(result.workflow)
      const confirmation = workflowConfirmation(result.workflow)
      if (confirmation && !completedRef.current) {
        await completePlanning(confirmation)
      }
    } catch (reason) {
      if (isAuthenticationFailure(reason)) return
      setError(formatError(reason, '创建规划运行失败'))
    } finally {
      setRunning(false)
    }
  }

  // 冷启动时只读恢复同一线程的 checkpoint，禁止借恢复动作执行任何规划节点。
  const recoverPlanning = async (): Promise<void> => {
    if (!application.workspaceRoot) return
    setRunning(true)
    setError('')
    setStreamingContent('')
    try {
      const result = await session.sendMessage('读取待确认的应用规划状态。', {
        application,
        applicationPlanningRecovery: {
          action: 'get',
          workspaceRoot: application.workspaceRoot,
          applicationId: application.id
        },
        editorMode: 'frontend',
        workflowScope: 'application_planning',
        workspaceRoot: application.workspaceRoot,
        onContent: setStreamingContent,
        onWorkflow: handleWorkflowChange
      })
      if (result.workflow) handleWorkflowChange(result.workflow)
    } catch (reason) {
      if (isAuthenticationFailure(reason)) return
      setError(formatError(reason, '恢复待确认规划失败'))
    } finally {
      setRunning(false)
    }
  }

  // 首次挂载时启动新规划，或使用同一线程和最新快照恢复未完成规划。
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    if (
      initialLifecycle.initialization.stage === 'generating_application_template_files' ||
      initialLifecycle.initialization.stage === 'application_template_generation_failed' ||
      initialLifecycle.initialization.stage === 'ready_for_workbench'
    ) {
      return
    }
    if (initialLifecycle.initialization.status === 'awaiting_user') {
      void recoverPlanning()
      return
    }
    if (initialStatus !== 'running') return
    if (initialWorkflow) {
      void runPlanning('请从上次保存的规划状态继续执行。', undefined, initialWorkflow)
      return
    }
    void runPlanning(originalRequest)
    // 同一 thread 的启动/恢复动作必须只执行一次，后续渲染由 startedRef 拦截。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialLifecycle.initialization.stage, originalRequest])

  // 提交当前确认卡答案，并由后端从公开状态推断恢复节点。
  const handleSubmitClarification = (
    currentWorkflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string
  ): void => {
    void runPlanning(
      '请根据本轮确认继续创建规划。',
      answers,
      currentWorkflow,
      editedRequirementSpec,
      requirementSpecFeedback
    )
  }

  // 保存需求编辑草稿并刷新当前确认卡，不确认文档也不继续规划。
  const handleSaveRequirementSpec = async (
    currentWorkflow: WorkflowRunPayload,
    spec: Record<string, unknown>
  ): Promise<Record<string, unknown> | undefined> => {
    if (!application.workspaceRoot) return undefined
    try {
      const saved = await saveRequirementSpecDraft(
        application.workspaceRoot,
        spec,
        currentWorkflow.threadId || threadId
      )
      setWorkflow((current) => (current ? withSavedRequirementSpec(current, saved) : current))
      message.success('需求文档修改已同步到 Markdown')
      return saved.requirementSpec
    } catch (reason) {
      if (isAuthenticationFailure(reason)) return undefined
      message.error(formatError(reason, '保存需求文档失败'))
      return undefined
    }
  }

  // 模板准备失败时直接重试本地准备动作，避免重复提交已确认的项目计划。
  const retryAfterFailure = async (): Promise<void> => {
    const confirmation = workflowConfirmation(workflow)
    if (!confirmation) {
      await runPlanning(originalRequest)
      return
    }
    setRunning(true)
    onStatusChange('running')
    setError('')
    try {
      await completePlanning(confirmation)
    } catch (reason) {
      if (isAuthenticationFailure(reason)) return
      setError(formatError(reason, '应用模板准备失败'))
    } finally {
      setRunning(false)
    }
  }

  return (
    <main
      aria-hidden={!visible}
      className={cx(
        'welcome-modal',
        'page-planning-modal',
        'page-planning-screen',
        isProjectPlanConfirmation && 'is-project-plan-confirmation',
        `theme-${theme}`,
        !visible && 'is-hidden'
      )}
    >
      <header className={cx('page-planning-screen-header')}>
        <div className={cx('page-planning-title')}>
          <Button
            aria-label="回到首页"
            className={cx('page-planning-title-back')}
            icon={<CurvedBackIcon />}
            onClick={onReturnHome}
            title="回到首页"
            type="text"
          />
          <span className={cx('page-planning-title-divider')} />
          <span className={cx('page-planning-title-copy')}>
            <strong>生成应用规划</strong>
            <small>「{application.appName}」</small>
          </span>
        </div>
        <span className={cx('page-planning-background-hint')}>
          返回首页后，规划将在后台继续运行
        </span>
      </header>

      <div className={cx('page-planning-screen-body')}>
        <div className={cx('page-planning-screen-content')}>
          {!isProjectPlanConfirmation ? (
            <Steps
              className={cx('page-planning-steps')}
              current={workflowStep(workflow)}
              size="small"
            >
              <Step title="需求确认" />
              <Step title="UI确认" />
              <Step title="项目规划" />
            </Steps>
          ) : null}

          {error ? (
            <Result
              extra={
                <Button
                  icon={<ReloadOutlined />}
                  onClick={() => void retryAfterFailure()}
                  type="primary"
                >
                  重试
                </Button>
              }
              status="error"
              subTitle={`${error} 请检查网络或模型服务后重试。`}
              title="应用初始化失败"
            />
          ) : (
            <section className={cx('page-planning-review')}>
              {showingProgress ? (
                <div className={cx('page-planning-loading')}>
                  <ApplicationPlanningProgress
                    events={workflowProgressEvents(workflow, preparingTemplate)}
                    fallbackMessage={
                      preparingTemplate ? '正在下载模板代码并准备工作区…' : progressCopy.fallback
                    }
                    streamingContent={streamingContent}
                    title={preparingTemplate ? '正在准备应用模板' : progressCopy.title}
                  />
                  {streamingUiPhase && workflow ? (
                    <UiDesignStreamingPreview workflow={workflow} total={streamingUiTotal} />
                  ) : null}
                </div>
              ) : null}
              {!showingProgress && workflow && hasClarification ? (
                <ApplicationPlanningQuestionPanel
                  datasourceType={application.datasource.type}
                  disabled={running}
                  onSaveRequirementSpec={handleSaveRequirementSpec}
                  onReturnHome={onReturnHome}
                  onSubmit={handleSubmitClarification}
                  rootPath={application.schema?.menus?.rootPath || '/'}
                  workflow={workflow}
                />
              ) : null}
              {!showingProgress && workflow && !hasClarification ? (
                <div className={cx('page-planning-loading')}>
                  <Spin />
                </div>
              ) : null}
            </section>
          )}
        </div>
      </div>
    </main>
  )
}
