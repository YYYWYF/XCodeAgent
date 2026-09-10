import { Button, message, Spin } from 'antd'
import { useRef } from 'react'
import type { WorkflowClarificationAnswers, WorkflowRunPayload } from '../../typings'
import type { RequirementSpecDraftSaveResult } from '../../service/applicationPagePlanning'
import {
  applicationPlanningDisplayStatus,
  type ApplicationPlanningCurrentState
} from '../../service/activeApplicationPlanning'
import { workflowConfirmation } from '../../service/applicationPlanningRuntimeHelpers'
import { isAuthenticationFailure } from '../../service/authentication'
import { cx } from '../../utils'
import { formatError } from './utils'
import AgentErrorCard from '../AgentErrorCard'
import ApplicationPlanningProgress from './ApplicationPlanningProgress'
import ApplicationPlanningQuestionPanel from './ApplicationPlanningQuestionPanel'
import UiDesignStreamingPreview from './UiDesignStreamingPreview'
import {
  planningWorkflowPhase,
  planningWorkflowRequiresUserInput
} from './planningWorkflowState'
import {
  planningUiDesignPageTotal,
  technicalPlanConfirmationReady,
  workflowProgressCopy,
  workflowProgressEvents
} from './applicationPlanningPresentation'
import './ApplicationPagePlanningModal.less'

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
  planning: ApplicationPlanningCurrentState
  streamingContent: string
  generatingTemplate: boolean
  theme: 'dark' | 'light'
  visible: boolean
  onReturnHome: () => void
  onSubmit: (
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string,
    designChangeRequest?: string
  ) => Promise<void>
  onSaveRequirementSpec: (spec: Record<string, unknown>) => Promise<RequirementSpecDraftSaveResult>
  onRetry: () => void
}

// 展示产品、UI 与技术规划的当前状态；所有业务动作由应用根部提供。
export default function ApplicationPagePlanningModal({
  planning,
  streamingContent,
  generatingTemplate,
  theme,
  visible,
  onReturnHome,
  onSubmit,
  onSaveRequirementSpec,
  onRetry
}: Props): JSX.Element {
  const application = planning.application
  // UI 确认期间保留面板，避免逐页动作的中间快照短暂丢失 clarification 时回切进度页。
  const enteredUiConfirmationRef = useRef(false)
  const workflow = planning.workflow
  const running = planning.transportState === 'running'
  const displayStatus = applicationPlanningDisplayStatus(planning)
  const error =
    planning.error ||
    (displayStatus === 'error' ? '上次规划流程中断，请重试或检查当前规划内容。' : '')
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
  // 已离开 UI 确认阶段（流转到规划入口或技术规划）：解除 UI 面板锁定。
  if (
    enteredUiConfirmationRef.current &&
    planningWorkflowPhase(workflow) &&
    planningWorkflowPhase(workflow) !== 'ui_confirmation'
  ) {
    enteredUiConfirmationRef.current = false
  }
  const inUiConfirmationStage = enteredUiConfirmationRef.current
  const showingProgress =
    generatingTemplate || !workflow || (running && !awaitingUserInput && !inUiConfirmationStage)
  // run 中途流式快照可能短暂丢失 clarification，此时确认面板会返回 null 导致白屏。
  // 有 workflow 但无 clarification 时显示加载态兜底，避免空白。
  const hasClarification = Boolean(
    workflow?.summary?.clarification ||
      workflow?.state?.clarification ||
      workflow?.result?.clarification
  )
  // UI确认节点生成期间，流式展示已就绪的设计稿，避免干等到最后一次性出现。
  // 排除 ui_confirmation 已完成（用户确认或跳过后同 run 流转到规划入口，
  // 但入口 started 帧到达前可能短暂停留在 ui_confirmation completed 帧），
  // 否则会误显示"设计稿生成中"。
  const streamingUiPhase =
    showingProgress &&
    planningWorkflowPhase(workflow) === 'ui_confirmation' &&
    workflow?.summary?.status !== 'completed'
  const streamingUiTotal = planningUiDesignPageTotal(workflow)
  const isTechnicalPlanConfirmation = technicalPlanConfirmationReady(workflow)

  // 将保存结果转为确认卡的文档展示反馈，视图不构造或写入 Workflow。
  const handleSaveRequirementSpec = async (
    _workflow: WorkflowRunPayload,
    spec: Record<string, unknown>
  ): Promise<Record<string, unknown> | undefined> => {
    if (!application.workspaceRoot) return undefined
    try {
      const saved = await onSaveRequirementSpec(spec)
      message.success('需求文档修改已同步到 Markdown')
      return saved.requirementSpec
    } catch (reason) {
      if (isAuthenticationFailure(reason)) return undefined
      message.error(formatError(reason, '保存需求文档失败'))
      return undefined
    }
  }

  return (
    <main
      aria-hidden={!visible}
      className={cx(
        'welcome-modal',
        'page-planning-modal',
        'page-planning-screen',
        isTechnicalPlanConfirmation && 'is-technical-plan-confirmation',
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
          {error ? (
            <AgentErrorCard
              error={error}
              onRetry={workflowConfirmation(workflow) ? undefined : onRetry}
              retrying={running}
            />
          ) : (
            <section className={cx('page-planning-review')}>
              {showingProgress ? (
                <div className={cx('page-planning-loading')}>
                  <ApplicationPlanningProgress
                    events={workflowProgressEvents(workflow, generatingTemplate)}
                    fallbackMessage={
                      generatingTemplate ? '正在下载模板代码并准备工作区…' : progressCopy.fallback
                    }
                    streamingContent={streamingContent}
                    title={generatingTemplate ? '正在准备应用模板' : progressCopy.title}
                  />
                  {streamingUiPhase && workflow ? (
                    <UiDesignStreamingPreview workflow={workflow} total={streamingUiTotal} />
                  ) : null}
                </div>
              ) : null}
              {!showingProgress && workflow && hasClarification ? (
                <ApplicationPlanningQuestionPanel
                  disabled={running}
                  onSaveRequirementSpec={handleSaveRequirementSpec}
                  onReturnHome={onReturnHome}
                  onSubmit={onSubmit}
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
