import {
  ArrowLeftOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  EditOutlined,
  EyeOutlined,
  FileTextOutlined
} from '@ant-design/icons'
import { Alert, Button, Checkbox, Form, Input, Radio, Tag, Typography } from 'antd'
import type { FormInstance } from 'antd'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEventHandler, ReactElement } from 'react'
import MarkdownContent from '../MarkdownContent/MarkdownContent'
import DetailReview from '../AiChatPanel/components/WorkflowRunCard/DetailReview'
import RequirementSpecSummary from './RequirementSpecSummary'
import RequirementSpecEditor from './RequirementSpecEditor'
import UiDesignConfirmationPanel from './UiDesignConfirmationPanel'
import TechnicalPlanSummary from './TechnicalPlanSummary'
import { projectPlanReadingSections } from './ProjectPlanReadingSections'
import type {
  WorkflowClarification,
  WorkflowClarificationAnswer,
  WorkflowClarificationAnswers,
  WorkflowClarificationQuestion,
  WorkflowRunPayload
} from '../../typings'
import { cx } from '../../utils'
import { useTabToFillPlaceholder } from './hooks/useTabToFillPlaceholder'
import './ApplicationPlanningQuestionPanel.less'

const { Paragraph, Text, Title } = Typography
const { TextArea } = Input
const OTHER_OPTION_VALUE = '__other__'

type ProjectPlanConfirmationForm = {
  answers: WorkflowClarificationAnswers
}

type Props = {
  disabled?: boolean
  onSaveRequirementSpec: (
    workflow: WorkflowRunPayload,
    spec: Record<string, unknown>
  ) => Promise<Record<string, unknown> | undefined>
  onReturnHome: () => void
  onSubmit: (
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string
  ) => void
  rootPath?: string
  workflow: WorkflowRunPayload
}

// 从公开 Workflow 载荷中读取当前规划阶段的待确认内容。
function planningClarification(workflow: WorkflowRunPayload): WorkflowClarification | undefined {
  const candidates = [
    workflow.summary.clarification,
    workflow.state?.clarification,
    workflow.result?.clarification
  ]
  return candidates.find((value): value is WorkflowClarification =>
    Boolean(value && typeof value === 'object')
  )
}

// 为规划问题生成稳定表单字段，确保提交答案能与后端问题一一对应。
function questionKey(question: WorkflowClarificationQuestion, index: number): string {
  return question.id || question.header || question.question || `question-${index + 1}`
}

// 将选项答案统一转换为数组，兼容历史字符串答案和新的结构化答案。
function selectedAnswerValues(value?: WorkflowClarificationAnswer): string[] {
  if (typeof value === 'object' && value && !Array.isArray(value) && 'selected' in value) {
    return Array.isArray(value.selected) ? value.selected.map(String) : [String(value.selected)]
  }
  if (Array.isArray(value)) return value.map(String)
  return typeof value === 'string' && value ? [value] : []
}

// 读取“其他”选项对应的补充文本。
function otherAnswerText(value?: WorkflowClarificationAnswer): string {
  return typeof value === 'object' && value && !Array.isArray(value) && 'other' in value
    ? String(value.other || '')
    : ''
}

// 判断回答是否满足当前问题类型的必填约束。
function answerComplete(
  question: WorkflowClarificationQuestion,
  value?: WorkflowClarificationAnswer
): boolean {
  if (question.type === 'choice' || question.type === 'yesno') {
    const selected = selectedAnswerValues(value)
    if (!selected.length) return false
    return !selected.includes(OTHER_OPTION_VALUE) || Boolean(otherAnswerText(value).trim())
  }
  return typeof value === 'string' && Boolean(value.trim())
}

// 根据确认阶段给出符合创建规划语义的标题。
function panelTitle(mode?: string): string {
  if (mode === 'requirement_spec_confirmation') return '确认需求文档'
  if (mode === 'product_plan_confirmation') return '确认产品规划'
  if (mode === 'ui_design_confirmation') return '确认UI设计稿'
  if (mode === 'technical_plan_confirmation') return '确认技术规划'
  if (mode === 'technical_plan_generation_error') return '技术规划生成失败'
  if (mode === 'detail_review') return '审核页面与数据源细节'
  return '补充规划细节'
}

// 根据确认阶段给出下一步按钮文案。
function submitLabel(mode?: string): string {
  if (mode === 'requirement_spec_confirmation') return '需求正确，继续规划'
  if (mode === 'product_plan_confirmation') return '确认产品规划并设计 UI'
  if (mode === 'ui_design_confirmation') return '确认设计稿并继续'
  if (mode === 'technical_plan_confirmation') return '确认技术规划并进入工作区'
  if (mode === 'technical_plan_generation_error') return '重新生成技术规划'
  if (!mode) return '重新生成当前规划'
  return '提交回答并继续'
}

type ProjectPlanConfirmationLayoutProps = {
  appName: string
  artifactContent: string
  disabled?: boolean
  form: FormInstance<ProjectPlanConfirmationForm>
  hasFeedback: boolean
  onFeedbackTab: KeyboardEventHandler<HTMLTextAreaElement>
  onFinish: (values: ProjectPlanConfirmationForm) => void
  onReturnHome: () => void
  onToggleOriginal: () => void
  plan: Record<string, unknown>
  technicalPlanAnswerKey: string
  planVersion: string
  showOriginal: boolean
}

// 还原规划确认稿的完整工作区壳层，并把正式计划、原文和确认动作放在同一页面流中。
function ProjectPlanConfirmationLayout({
  appName,
  artifactContent,
  disabled,
  form,
  hasFeedback,
  onFeedbackTab,
  onFinish,
  onReturnHome,
  onToggleOriginal,
  plan,
  technicalPlanAnswerKey,
  planVersion,
  showOriginal
}: ProjectPlanConfirmationLayoutProps): ReactElement {
  const projectMark = appName.slice(0, 1) || '旅'
  const readingSections = useMemo(() => projectPlanReadingSections(plan), [plan])
  const [activeReadingSectionId, setActiveReadingSectionId] = useState(
    () => readingSections[0]?.id || ''
  )
  const contentRef = useRef<HTMLDivElement>(null)
  // 记录点击章节触发的程序滚动，避免平滑滚动过程覆盖点击后的高亮状态。
  const programmaticScrollSectionRef = useRef<string | null>(null)
  const programmaticScrollTimerRef = useRef<number | undefined>(undefined)

  // 根据主内容滚动位置计算当前阅读章节，让左侧高亮始终跟随视口位置。
  useEffect(() => {
    const contentRoot = contentRef.current
    if (!contentRoot || showOriginal) return

    const sectionTargets = readingSections
      .map((section) => ({ id: section.id, element: document.getElementById(section.id) }))
      .filter((target): target is { id: string; element: HTMLElement } => Boolean(target.element))
    if (!sectionTargets.length) return

    // 根据锚点与内容视口的相对位置，确定当前应该高亮的章节。
    const updateActiveSection = (): void => {
      if (programmaticScrollSectionRef.current) return
      const rootRect = contentRoot.getBoundingClientRect()
      const activationLine = rootRect.top + Math.min(180, Math.max(96, rootRect.height * 0.24))
      let currentSectionId = sectionTargets[0].id
      for (const target of sectionTargets) {
        if (target.element.getBoundingClientRect().top <= activationLine) {
          currentSectionId = target.id
        } else {
          break
        }
      }
      setActiveReadingSectionId((current) =>
        current === currentSectionId ? current : currentSectionId
      )
    }

    let frameId: number | undefined
    // 合并连续滚动事件，避免每个滚动像素都触发 React 状态更新。
    const scheduleActiveSectionUpdate = (): void => {
      if (frameId !== undefined) return
      frameId = window.requestAnimationFrame(() => {
        frameId = undefined
        updateActiveSection()
      })
    }

    // 平滑滚动结束后恢复基于视口位置的动态高亮计算。
    const handleScrollEnd = (): void => {
      if (!programmaticScrollSectionRef.current) return
      programmaticScrollSectionRef.current = null
      if (programmaticScrollTimerRef.current !== undefined) {
        window.clearTimeout(programmaticScrollTimerRef.current)
        programmaticScrollTimerRef.current = undefined
      }
      scheduleActiveSectionUpdate()
    }

    updateActiveSection()
    contentRoot.addEventListener('scroll', scheduleActiveSectionUpdate, { passive: true })
    contentRoot.addEventListener('scrollend', handleScrollEnd)
    window.addEventListener('resize', scheduleActiveSectionUpdate)
    return () => {
      contentRoot.removeEventListener('scroll', scheduleActiveSectionUpdate)
      contentRoot.removeEventListener('scrollend', handleScrollEnd)
      window.removeEventListener('resize', scheduleActiveSectionUpdate)
      if (frameId !== undefined) window.cancelAnimationFrame(frameId)
      if (programmaticScrollTimerRef.current !== undefined) {
        window.clearTimeout(programmaticScrollTimerRef.current)
        programmaticScrollTimerRef.current = undefined
      }
      programmaticScrollSectionRef.current = null
    }
  }, [readingSections, showOriginal])

  // 点击左侧章节时滚动到对应计划卡片，并立即反馈当前选中状态。
  const handleReadingSectionClick = useCallback(
    (sectionId: string): void => {
      if (showOriginal) return
      const contentRoot = contentRef.current
      const target = document.getElementById(sectionId)
      if (!contentRoot || !target) return
      const rootRect = contentRoot.getBoundingClientRect()
      const targetRect = target.getBoundingClientRect()
      const targetTop = contentRoot.scrollTop + targetRect.top - rootRect.top - 16
      if (programmaticScrollTimerRef.current !== undefined) {
        window.clearTimeout(programmaticScrollTimerRef.current)
      }
      programmaticScrollSectionRef.current = sectionId
      contentRoot.scrollTo({ top: Math.max(0, targetTop), behavior: 'smooth' })
      setActiveReadingSectionId(sectionId)
      // 兼容不触发 scrollend 的运行环境，保证滚动锁最终一定会释放。
      const releaseDelay = Math.min(
        1600,
        Math.max(500, Math.round(Math.abs(targetTop - contentRoot.scrollTop) * 0.75))
      )
      programmaticScrollTimerRef.current = window.setTimeout(() => {
        programmaticScrollSectionRef.current = null
        programmaticScrollTimerRef.current = undefined
      }, releaseDelay)
    },
    [showOriginal]
  )

  return (
    <div className={cx('project-plan-confirmation-shell')}>
      <header className={cx('project-plan-confirmation-topbar')}>
        <div className={cx('project-plan-confirmation-brand-group')}>
          <Button
            aria-label="回到首页"
            className={cx('project-plan-confirmation-back')}
            icon={<ArrowLeftOutlined />}
            onClick={onReturnHome}
            title="回到首页"
            type="text"
          />
          <div className={cx('project-plan-confirmation-brand')}>
            <span className={cx('project-plan-confirmation-brand-mark')}>✦</span>
            <span>XCodeAgent / 技术规划</span>
          </div>
        </div>
        <div className={cx('project-plan-confirmation-topbar-meta')}>
          <span>本地应用工程</span>
          <span className={cx('project-plan-confirmation-live-dot')} aria-hidden="true" />
          <span>待确认</span>
        </div>
      </header>

      <div className={cx('project-plan-confirmation-layout')}>
        <aside className={cx('project-plan-confirmation-sidebar')} aria-label="技术规划导航">
          <div className={cx('project-plan-confirmation-project-heading')}>
            <span className={cx('project-plan-confirmation-project-mark')}>{projectMark}</span>
            <div className={cx('project-plan-confirmation-project-copy')}>
              <strong>{appName}</strong>
              <span>技术规划 · {planVersion}</span>
            </div>
          </div>

          <nav className={cx('project-plan-confirmation-nav')} aria-label="技术规划阅读章节">
            {readingSections.map((section) => {
              const isActive = section.id === activeReadingSectionId
              return (
                <button
                  aria-current={isActive ? 'step' : undefined}
                  className={cx('project-plan-confirmation-nav-item', isActive && 'is-active')}
                  disabled={showOriginal}
                  key={section.id}
                  onClick={() => handleReadingSectionClick(section.id)}
                  type="button"
                >
                  {section.label}
                </button>
              )
            })}
          </nav>

          <div className={cx('project-plan-confirmation-progress')}>
            <span className={cx('project-plan-confirmation-sidebar-label')}>确认进度</span>
            <div className={cx('project-plan-confirmation-step', 'is-done')} data-step="01">
              需求文档 <span>完成</span>
            </div>
            <div className={cx('project-plan-confirmation-step', 'is-active')} data-step="02">
              技术规划 <span>当前</span>
            </div>
            <div className={cx('project-plan-confirmation-step')} data-step="03">
              进入工作区 <span>下一步</span>
            </div>
          </div>
        </aside>

        <main className={cx('project-plan-confirmation-main')}>
          <div className={cx('project-plan-confirmation-main-content')} ref={contentRef}>
            {showOriginal ? (
              <section className={cx('project-plan-confirmation-original')}>
                <header>
                  <Text strong>技术规划原文</Text>
                  <Text type="secondary">当前确认版本的 Markdown 文档</Text>
                </header>
                <MarkdownContent content={artifactContent} />
              </section>
            ) : (
              <TechnicalPlanSummary plan={plan} />
            )}
          </div>

          <Form
            className={cx('project-plan-confirmation-bar')}
            form={form}
            layout="vertical"
            onFinish={onFinish}
          >
              <Form.Item name={['answers', technicalPlanAnswerKey]} noStyle>
              <TextArea
                aria-label="技术规划意见"
                autoSize={{ minRows: 1, maxRows: 2 }}
                disabled={disabled}
                onKeyDown={onFeedbackTab}
                placeholder="意见（可选）：如需调整，请填写架构、API、工程设计或页面引用 (按 Tab 采用)"
              />
            </Form.Item>
            <div className={cx('project-plan-confirmation-actions')}>
              <Button disabled={disabled} onClick={onToggleOriginal} type="default">
                {showOriginal ? '返回规划' : '查看原文'}
              </Button>
              <Button
                disabled={disabled}
                htmlType="submit"
                icon={<CheckCircleOutlined />}
                type="primary"
              >
                {hasFeedback ? '提交意见，调整技术规划' : '技术规划正确，进入工作区'}
              </Button>
            </div>
          </Form>
        </main>
      </div>
    </div>
  )
}

// 从公开 Workflow 结果中读取 RequirementSpec 结构化状态，供默认概览视图使用。
function requirementSpec(workflow: WorkflowRunPayload): Record<string, unknown> | undefined {
  for (const source of [workflow.result, workflow.state]) {
    const value = source?.requirement_spec
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
  }
  return undefined
}

// 从公开 Workflow 结果中读取 TechnicalPlan 结构化状态，供开发审核视图使用。
function technicalPlan(workflow: WorkflowRunPayload): Record<string, unknown> | undefined {
  for (const source of [workflow.result, workflow.state]) {
    const value = source?.technical_plan
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
  }
  return undefined
}

// 使用创建规划页面自己的表单视觉展示问题和正式产物，不渲染通用 Workflow 卡片。
export default function ApplicationPlanningQuestionPanel({
  disabled,
  onSaveRequirementSpec,
  onReturnHome,
  onSubmit,
  rootPath,
  workflow
}: Props): ReactElement | null {
  const [form] = Form.useForm<{ answers: WorkflowClarificationAnswers }>()
  const requirementFeedback = Form.useWatch(['answers', 'requirement_spec_feedback'], form) as
    | WorkflowClarificationAnswer
    | undefined
  const [showArtifactDetail, setShowArtifactDetail] = useState(false)
  const [showTechnicalPlanOriginal, setShowTechnicalPlanOriginal] = useState(false)
  const [editingRequirement, setEditingRequirement] = useState(false)
  const [requirementDraft, setRequirementDraft] = useState<Record<string, unknown>>()
  const [savingRequirement, setSavingRequirement] = useState(false)
  const clarification = planningClarification(workflow)
  const questions = clarification?.questions || []
  const isRequirementConfirmation = clarification?.mode === 'requirement_spec_confirmation'
  const isProductPlanConfirmation = clarification?.mode === 'product_plan_confirmation'
  const isTechnicalPlanConfirmation =
    clarification?.mode === 'technical_plan_confirmation'
  const isTechnicalPlanGenerationError =
    clarification?.mode === 'technical_plan_generation_error'
  const isDocumentConfirmation =
    isRequirementConfirmation || isProductPlanConfirmation || isTechnicalPlanConfirmation
  const technicalPlanAnswerKey = questions[0]
    ? questionKey(questions[0], 0)
    : 'technical_plan_confirmation'
  const technicalPlanFeedback = Form.useWatch(['answers', technicalPlanAnswerKey], form) as
    | WorkflowClarificationAnswer
    | undefined
  const handleConfirmationFeedbackTab = useTabToFillPlaceholder(form, [
    'answers',
    isRequirementConfirmation ? 'requirement_spec_feedback' : technicalPlanAnswerKey
  ])
  const hasRecoveryAction = clarification?.status === 'requires_user_input' && !questions.length
  const artifact = workflow.confirmationArtifact
  const spec = artifact?.id === 'requirement_spec' ? requirementSpec(workflow) : undefined
  const plan = isTechnicalPlanConfirmation ? technicalPlan(workflow) : undefined
  const displayedSpec = requirementDraft || spec
  const canShowSummary = Boolean(spec)
  // 意见非空时切换提交语义，避免按钮继续暗示需求文档完全正确。
  const hasRequirementFeedback =
    typeof requirementFeedback === 'string' && Boolean(requirementFeedback.trim())
  const hasTechnicalPlanFeedback =
    typeof technicalPlanFeedback === 'string' && Boolean(technicalPlanFeedback.trim())

  if (!clarification) return null

  // run 中途流式快照可能产生未知 mode（如 undefined、技术规划校验错误），
  // 此时 clarification.pages 也会丢失。缓存最近一次有效的 UI 确认 workflow，run 中途用缓存
  // 渲染，保持左侧页面列表 + 右侧渲染区布局不动，逐页加载态由面板内 actingPageIds 控制。
  const clarificationPages = (clarification as unknown as Record<string, unknown>).pages
  const hasUiDesignPages = Array.isArray(clarificationPages) && clarificationPages.length > 0
  const lastValidUiWorkflowRef = useRef<WorkflowRunPayload | undefined>(undefined)
  if (clarification.mode === 'ui_design_confirmation' && hasUiDesignPages) {
    lastValidUiWorkflowRef.current = workflow
  }
  const effectiveWorkflow =
    clarification.mode === 'ui_design_confirmation' && hasUiDesignPages
      ? workflow
      : lastValidUiWorkflowRef.current ?? workflow
  // run 中途 mode 变未知但曾进入过
  // UI 确认阶段：用缓存的有效 UI workflow 渲染，保持布局不动，单页加载态由面板内控制。
  const knownConfirmationModes = new Set([
    'ui_design_confirmation',
    'product_plan_confirmation',
    'technical_plan_confirmation',
    'technical_plan_generation_error',
    'requirement_spec_confirmation',
    'detail_review',
  ])
  if (
    !knownConfirmationModes.has(clarification.mode || '') &&
    lastValidUiWorkflowRef.current
  ) {
    return (
      <UiDesignConfirmationPanel
        disabled={disabled}
        onSubmit={(currentWorkflow, answers) => onSubmit(currentWorkflow, answers)}
        workflow={effectiveWorkflow}
      />
    )
  }

  // 需求确认阶段的自然语言意见直接作为确认答案提交，后端据此区分确认或修订。
  const handleSubmit = (values: { answers?: WorkflowClarificationAnswers }): void => {
    if (isProductPlanConfirmation || isTechnicalPlanConfirmation) {
      const feedback = values.answers?.[technicalPlanAnswerKey]
      const feedbackText = typeof feedback === 'string' ? feedback.trim() : ''
      onSubmit(workflow, {
        [technicalPlanAnswerKey]: feedbackText || '正确，继续'
      })
      return
    }
    if (!isRequirementConfirmation) {
      onSubmit(
        workflow,
        questions.length
          ? values.answers || {}
          : { planning_recovery: '请重新生成当前规划，并提供可确认的正式文档或可填写的问题。' }
      )
      return
    }
    const feedback = values.answers?.requirement_spec_feedback
    const feedbackText = typeof feedback === 'string' ? feedback.trim() : ''
    onSubmit(
      workflow,
      {
        requirement_spec_confirmation: feedbackText || '正确，继续规划'
      },
      requirementDraft,
      feedbackText || undefined
    )
  }

  if (isTechnicalPlanGenerationError) {
    const errors = Array.isArray(clarification.errors)
      ? clarification.errors.map(String).filter(Boolean).slice(0, 8)
      : []
    return (
      <section className={cx('planning-question-panel')}>
        <Alert
          description={
            <>
              <Paragraph>{clarification.message || '技术规划自动修复后仍未通过校验。'}</Paragraph>
              {errors.length ? (
                <ul>{errors.map((error) => <li key={error}>{error}</li>)}</ul>
              ) : null}
            </>
          }
          message="未生成可确认的技术规划"
          showIcon
          type="error"
        />
        <Button
          disabled={disabled}
          onClick={() =>
            onSubmit(workflow, { planning_recovery: '请重新生成技术规划，并修复全部结构和契约错误。' })
          }
          size="large"
          type="primary"
        >
          重新生成技术规划
        </Button>
      </section>
    )
  }

  // 从当前结构化需求创建隔离草稿，再次进入编辑时继续使用已保存内容。
  const startRequirementEditing = (): void => {
    if (!spec) return
    setRequirementDraft(
      (current) => current ?? (JSON.parse(JSON.stringify(spec)) as Record<string, unknown>)
    )
    setShowArtifactDetail(false)
    setEditingRequirement(true)
  }

  // 通过 AG-UI 保存并重写后端 Markdown，成功后才退出编辑模式。
  const saveRequirementEditing = async (): Promise<void> => {
    if (!requirementDraft || savingRequirement) return
    setSavingRequirement(true)
    try {
      const savedSpec = await onSaveRequirementSpec(workflow, requirementDraft)
      if (!savedSpec) return
      setRequirementDraft(savedSpec)
      setEditingRequirement(false)
    } finally {
      setSavingRequirement(false)
    }
  }

  if (isTechnicalPlanConfirmation && plan) {
    const planApp =
      plan.app && typeof plan.app === 'object' ? (plan.app as Record<string, unknown>) : {}
    const planVersion =
      typeof plan.artifact_type === 'string' ? plan.artifact_type : 'technical-plan'
    const appName = typeof planApp.name === 'string' ? planApp.name : '开发技术规划'

    return (
      <section
        className={cx(
          'planning-question-panel',
          'is-document-confirmation',
          'is-technical-plan-confirmation'
        )}
      >
        <ProjectPlanConfirmationLayout
          appName={appName}
          artifactContent={artifact?.content || ''}
          disabled={disabled}
          form={form}
          hasFeedback={hasTechnicalPlanFeedback}
          onFeedbackTab={handleConfirmationFeedbackTab}
          onFinish={handleSubmit}
          onReturnHome={onReturnHome}
          onToggleOriginal={() => setShowTechnicalPlanOriginal((current) => !current)}
          plan={plan}
          planVersion={planVersion}
          technicalPlanAnswerKey={technicalPlanAnswerKey}
          showOriginal={showTechnicalPlanOriginal}
        />
      </section>
    )
  }

  if (clarification.mode === 'detail_review' && clarification.review) {
    return (
      <section className={cx('planning-question-panel', 'is-detail-review')}>
        <PlanningPanelHeader
          description="逐项检查页面目标、布局、交互、权限、API 和数据源设计，确认后保留在 plans 目录。"
          review
          title={panelTitle(clarification.mode)}
        />
        <DetailReview
          disabled={disabled}
          onConfirm={(submission) => onSubmit(workflow, { detail_review: submission })}
          review={clarification.review}
        />
      </section>
    )
  }

  if (clarification.mode === 'ui_design_confirmation') {
    return (
      <UiDesignConfirmationPanel
        disabled={disabled}
        onSubmit={(currentWorkflow, answers) => onSubmit(currentWorkflow, answers)}
        workflow={workflow}
      />
    )
  }

  return (
    <section
      className={cx(
        'planning-question-panel',
        isDocumentConfirmation && 'is-document-confirmation'
      )}
    >
      {artifact?.content ? (
        <section className={cx('planning-artifact-card')}>
          <header>
            <span className={cx('planning-artifact-icon')}>
              <FileTextOutlined />
            </span>
            <div>
              <Text strong>
                {artifact.id === 'requirement_spec'
                  ? '需求文档'
                  : artifact.id === 'product_plan'
                    ? '产品规划'
                    : artifact.id === 'technical_plan'
                      ? '技术规划'
                      : '当前规划'}
              </Text>
              <Text type="secondary">
                {isRequirementConfirmation
                  ? '请审核需求文档。需要补充时只在下方填写意见；文档正确时，直接点击右下角按钮继续。'
                  : isProductPlanConfirmation
                    ? '请由产品角色审核页面目标、业务信息、核心操作、跳转与验收标准；确认后进入 UI 设计。'
                    : isTechnicalPlanConfirmation
                      ? '请由开发角色审核架构、API、数据源、权限与页面实现契约；确认后进入工作区。'
                    : artifact.name}
              </Text>
            </div>
            {canShowSummary ? (
              <div className={cx('planning-artifact-actions')}>
                {editingRequirement ? (
                  <Button
                    aria-label="保存需求文档修改并退出编辑模式"
                    className={cx('planning-artifact-edit')}
                    disabled={disabled}
                    icon={<CheckCircleOutlined />}
                    loading={savingRequirement}
                    onClick={() => void saveRequirementEditing()}
                    size="small"
                    type="text"
                  >
                    保存并退出编辑
                  </Button>
                ) : (
                  <Button
                    className={cx('planning-artifact-toggle')}
                    icon={showArtifactDetail ? <ArrowLeftOutlined /> : <EyeOutlined />}
                    onClick={() => setShowArtifactDetail((current) => !current)}
                    size="small"
                    type="text"
                  >
                    {showArtifactDetail ? '返回概览' : '查看详细设计'}
                  </Button>
                )}
                {!editingRequirement && !showArtifactDetail ? (
                  <Button
                    aria-label="进入需求文档编辑模式"
                    className={cx('planning-artifact-edit')}
                    icon={<EditOutlined />}
                    onClick={startRequirementEditing}
                    size="small"
                    type="text"
                  >
                    进入编辑模式
                  </Button>
                ) : null}
              </div>
            ) : (
              <Tag>{artifact.id === 'technical_plan' ? 'JSON' : 'Markdown'}</Tag>
            )}
          </header>
          <div className={cx('planning-artifact-content')}>
            {editingRequirement && requirementDraft ? (
              <RequirementSpecEditor
                onChange={setRequirementDraft}
                rootPath={rootPath || '/'}
                spec={requirementDraft}
              />
            ) : canShowSummary && !showArtifactDetail ? (
              <RequirementSpecSummary spec={displayedSpec!} />
            ) : artifact.id === 'technical_plan' ? (
              plan ? (
                <TechnicalPlanSummary plan={plan} />
              ) : (
                <Text type="secondary">技术规划结构化数据暂不可用，请重新生成当前规划。</Text>
              )
            ) : (
              <MarkdownContent content={artifact.content} />
            )}
          </div>
        </section>
      ) : null}

      {!editingRequirement ? (
        <Form
          className={cx(
            'planning-question-form',
            isDocumentConfirmation && 'is-document-confirmation'
          )}
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
        >
          {!isDocumentConfirmation && questions.length ? (
            <header className={cx('planning-question-section-header')}>
              <span className={cx('planning-artifact-icon')}>
                <FileTextOutlined />
              </span>
              <div>
                <Text strong>补充细节</Text>
                <Text type="secondary">
                  为了让页面和功能规划更贴近真实业务，请补充下面这些关键信息。
                </Text>
              </div>
            </header>
          ) : null}
          {isDocumentConfirmation && !editingRequirement ? (
            <Form.Item
              className={cx('planning-confirmation-feedback')}
              name={[
                'answers',
                isRequirementConfirmation ? 'requirement_spec_feedback' : technicalPlanAnswerKey
              ]}
            >
              <TextArea
                aria-label={
                  isRequirementConfirmation
                    ? '需求文档意见'
                    : isProductPlanConfirmation
                      ? '产品规划意见'
                      : '技术规划意见'
                }
                autoSize={{ minRows: 1, maxRows: 2 }}
                disabled={disabled}
                placeholder={
                  isRequirementConfirmation
                    ? '意见（可选）：如需调整，请填写具体内容 (按 Tab 采用)'
                    : isProductPlanConfirmation
                      ? '意见（可选）：如需调整，请填写页面目标、业务信息、操作或跳转等修改内容 (按 Tab 采用)'
                      : '意见（可选）：如需调整，请填写架构、API、数据源、权限等修改内容 (按 Tab 采用)'
                }
                onKeyDown={handleConfirmationFeedbackTab}
              />
            </Form.Item>
          ) : (
            questions.map((question, index) => {
              const key = questionKey(question, index)
              return (
                <section className={cx('planning-question-card')} key={key}>
                  <div className={cx('planning-question-card-heading')}>
                    <div>
                      <div className={cx('planning-question-title')}>
                        {question.header || question.dimension ? (
                          <Tag>{question.header || question.dimension}</Tag>
                        ) : null}
                        <span aria-hidden className={cx('planning-question-required')}>
                          *
                        </span>
                        <Title level={5}>{question.question || '请补充规划细节'}</Title>
                      </div>
                      {question.default_assumption ? (
                        <Paragraph type="secondary">
                          默认建议：{question.default_assumption}
                        </Paragraph>
                      ) : null}
                    </div>
                  </div>
                  <Form.Item
                    name={['answers', key]}
                    required
                    rules={[
                      {
                        validator: (_rule, value) =>
                          answerComplete(question, value)
                            ? Promise.resolve()
                            : Promise.reject(new Error('请选择或补充这个问题'))
                      }
                    ]}
                  >
                    <PlanningQuestionControl disabled={disabled} question={question} />
                  </Form.Item>
                </section>
              )
            })
          )}

          {isDocumentConfirmation || questions.length ? (
            <div
              className={cx(
                'page-planning-actions',
                isDocumentConfirmation && 'is-document-confirmation'
              )}
            >
              <Button
                aria-label={
                  isRequirementConfirmation
                    ? hasRequirementFeedback
                      ? '提交需求文档意见并继续规划'
                      : '确认需求文档正确并继续规划'
                    : isProductPlanConfirmation
                      ? hasTechnicalPlanFeedback
                        ? '提交产品规划意见并调整规划'
                        : '确认产品规划正确并进入 UI 设计'
                      : isTechnicalPlanConfirmation
                      ? hasTechnicalPlanFeedback
                        ? '提交技术规划意见并调整规划'
                        : '确认技术规划正确并进入工作区'
                      : undefined
                }
                disabled={disabled}
                htmlType="submit"
                icon={
                  clarification.mode?.includes('confirmation') ? (
                    <CheckCircleOutlined />
                  ) : (
                    <BulbOutlined />
                  )
                }
                type="primary"
              >
                {editingRequirement
                  ? '确认修改并继续规划'
                  : isRequirementConfirmation && hasRequirementFeedback
                    ? '提交意见，继续规划'
                    : (isProductPlanConfirmation || isTechnicalPlanConfirmation) && hasTechnicalPlanFeedback
                      ? '提交意见，调整规划'
                      : isTechnicalPlanConfirmation
                        ? '技术规划正确，进入工作区'
                        : isProductPlanConfirmation
                          ? '产品规划正确，进入 UI 设计'
                        : submitLabel(clarification.mode)}
              </Button>
            </div>
          ) : null}
          {hasRecoveryAction ? (
            <section className={cx('planning-question-card')}>
              <Paragraph type="secondary">
                当前规划没有返回可填写的问题，无法安全继续。可重新生成本阶段规划；不会修改已保存的应用设置。
              </Paragraph>
              <div className={cx('page-planning-actions')}>
                <Button
                  disabled={disabled}
                  htmlType="submit"
                  icon={<BulbOutlined />}
                  type="primary"
                >
                  重新生成当前规划
                </Button>
              </div>
            </section>
          ) : null}
        </Form>
      ) : null}
    </section>
  )
}

// 根据模型返回的结构化问题，分别渲染单选、多选、是非题或自由文本。
function PlanningQuestionControl({
  disabled,
  onChange,
  question,
  value
}: {
  disabled?: boolean
  onChange?: (value: WorkflowClarificationAnswer) => void
  question: WorkflowClarificationQuestion
  value?: WorkflowClarificationAnswer
}): ReactElement {
  // Tab 键填充 placeholder 的处理函数
  const handleTabToFillPlaceholder = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>, placeholder?: string) => {
      if (e.key !== 'Tab') return

      const currentValue = typeof value === 'string' ? value : ''

      // 如果输入框已有内容，则不处理
      if (currentValue) return

      // 阻止默认的 Tab 行为（焦点切换）
      e.preventDefault()

      // 清理 placeholder 中的提示文本 (按 Tab 采用)
      const cleanValue = (placeholder || '').replace(/\s*\(按 Tab 采用\)\s*$/, '')

      // 触发 onChange 更新值
      onChange?.(cleanValue)
    },
    [value, onChange]
  )
  const options =
    question.type === 'yesno'
      ? [
          { label: '是', description: '', value: '是' },
          { label: '否', description: '', value: '否' }
        ]
      : (question.options || [])
          .filter((option) => option.label)
          .map((option) => ({
            label: option.label || '',
            description: option.description || '',
            value: option.value || option.label || ''
          }))
  const optionsWithOther =
    question.allowOther !== false && !options.some((option) => option.value === OTHER_OPTION_VALUE)
      ? [
          ...options,
          { label: '其他', description: '补充未覆盖的需求或偏好。', value: OTHER_OPTION_VALUE }
        ]
      : options
  const selected = selectedAnswerValues(value)
  const other = otherAnswerText(value)
  const emitSelection = (nextSelected: string[]): void => {
    onChange?.({ selected: nextSelected, other: other || undefined })
  }
  const emitOther = (nextOther: string): void => {
    onChange?.({ selected, other: nextOther })
  }

  if ((question.type === 'choice' || question.type === 'yesno') && optionsWithOther.length) {
    return (
      <div className={cx('planning-question-control')}>
        {question.type === 'choice' && question.multiSelect ? (
          <Checkbox.Group
            className={cx('planning-question-options')}
            disabled={disabled}
            onChange={(checkedValues) => emitSelection(checkedValues.map(String))}
            value={selected}
          >
            {optionsWithOther.map((option) => (
              <Checkbox
                className={cx('planning-question-option')}
                key={option.value}
                value={option.value}
              >
                <OptionCopy description={option.description} label={option.label} />
              </Checkbox>
            ))}
          </Checkbox.Group>
        ) : (
          <Radio.Group
            className={cx('planning-question-options')}
            disabled={disabled}
            onChange={(event) => emitSelection([String(event.target.value)])}
            value={selected[0]}
          >
            {optionsWithOther.map((option) => (
              <Radio
                className={cx('planning-question-option')}
                key={option.value}
                value={option.value}
              >
                <OptionCopy description={option.description} label={option.label} />
              </Radio>
            ))}
          </Radio.Group>
        )}
        {selected.includes(OTHER_OPTION_VALUE) ? (
          <TextArea
            autoSize={{ minRows: 2, maxRows: 5 }}
            disabled={disabled}
            onChange={(event) => emitOther(event.target.value)}
            placeholder="请补充其他选择或说明 (按 Tab 采用)"
            onKeyDown={(e) => {
              if (e.key !== 'Tab') return
              const target = e.target as HTMLTextAreaElement
              if (target.value) return
              e.preventDefault()
              emitOther('请补充其他选择或说明')
            }}
            value={other}
          />
        ) : null}
      </div>
    )
  }

  return (
    <TextArea
      autoSize={{ minRows: 3, maxRows: 7 }}
      disabled={disabled}
      onChange={(event) => onChange?.(event.target.value)}
      placeholder={
        question.placeholder
          ? `${question.placeholder} (按 Tab 采用)`
          : '请输入你的回答，也可以直接说明希望采用的方案。 (按 Tab 采用)'
      }
      onKeyDown={(e) =>
        handleTabToFillPlaceholder(
          e,
          question.placeholder || '请输入你的回答，也可以直接说明希望采用的方案。'
        )
      }
      value={typeof value === 'string' ? value : ''}
    />
  )
}

// 在选项控件中同时展示简短标签和模型提供的解释。
function OptionCopy({ description, label }: { description: string; label: string }): ReactElement {
  return (
    <span className={cx('planning-question-option-copy')}>
      <Text strong>{label}</Text>
      {description ? <Text type="secondary">{description}</Text> : null}
    </span>
  )
}

// 渲染创建规划专用的标题说明区。
function PlanningPanelHeader({
  description,
  review,
  title
}: {
  description: string
  review?: boolean
  title: string
}): ReactElement {
  return (
    <header className={cx('planning-question-panel-header')}>
      <span className={cx('planning-question-panel-icon')}>
        {review ? <CheckCircleOutlined /> : <BulbOutlined />}
      </span>
      <div>
        <Title level={4}>{title}</Title>
        <Paragraph type="secondary">{description}</Paragraph>
      </div>
    </header>
  )
}
