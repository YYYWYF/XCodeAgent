import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  HourglassOutlined,
  LoadingOutlined,
  MoonOutlined,
  PauseCircleOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Input,
  Progress,
  Radio,
  Tag,
  Tooltip,
  Typography
} from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type {
  WorkflowBuildExecutionSlice,
  WorkflowBuildExecutionTask,
  WorkflowClarification,
  WorkflowClarificationQuestion,
  WorkflowClarificationSelectionGroup,
  WorkflowClarificationAnswer,
  WorkflowClarificationAnswers,
  WorkflowRunPayload
} from '../../../../typings'
import { cx } from '../../../../utils'
import type { WorkspaceDocKey } from '../../types'
import { pageAcceptanceContinuationMessage, backgroundDispatchContinuationMessage } from '../../workflowContinuation'
import type { WorkflowInteractionAvailability } from '../../planExecutionMode'
import BackgroundDispatchCard, {
  type BackgroundDispatchOption
} from '../BackgroundDispatchCard'
import { DetailReviewAuthBar } from './DetailReview'
import {
  taskId,
  numberValue,
  taskStatusColor,
  taskStatusText,
  dedupeStrings,
  taskDependencies,
  dedupeLocalizedTaskTexts,
  taskFailureCategoryText,
  displayTaskTitle,
  displayTaskDescription,
  sortBuildTasksForDisplay
} from './taskDisplay'
import './WorkflowRunCard.less'

const { Text } = Typography
const { TextArea } = Input

const OTHER_OPTION_VALUE = '__other__'

// 设计阶段三份产物的确认卡 mode → 文档信息（驱动 ArtifactConfirmationCard 渲染）。
const ARTIFACT_CONFIRMATION_MAP: Record<
  string,
  { docKey: WorkspaceDocKey; title: string; summary: string }
> = {
  requirement_spec_confirmation: {
    docKey: 'requirement-spec',
    title: '需求文档',
    summary: '需求文档已生成，请确认内容。'
  },
  project_plan_confirmation: {
    docKey: 'project-plan',
    title: '项目计划',
    summary: '项目计划已生成，确认后生成构建任务清单。'
  }
}

export type ClarificationAnswers = WorkflowClarificationAnswers

/** 执行方式选择卡的固定选项：同步执行或进入某套任务系统；描述与任务抽屉头部同一套口径。 */
const BACKGROUND_DISPATCH_OPTIONS: BackgroundDispatchOption[] = [
  {
    key: 'sync',
    label: '同步任务',
    description: '常规算力，当场执行并实时展示生成过程，执行完成前需要等待。',
    icon: <ThunderboltOutlined />
  },
  {
    key: 'async',
    label: '异步任务',
    description: '常规算力队列，后台执行，消耗码豆。',
    icon: <HourglassOutlined />
  },
  {
    key: 'tide',
    label: '潮汐任务',
    description: '闲时算力队列，低优先级执行，不消耗码豆。',
    icon: <MoonOutlined />
  }
]

type WorkflowRunCardProps = {
  disabled?: boolean
  /** 是否作为流程节点的内嵌动作渲染，避免形成独立的对话卡片。 */
  embedded?: boolean
  interactionAvailability: WorkflowInteractionAvailability
  onDiscard?: (docKey: WorkspaceDocKey) => void
  onSubmitClarification?: (workflow: WorkflowRunPayload, answers: ClarificationAnswers) => void
  workflow: WorkflowRunPayload
}

export default function WorkflowRunCard({
  disabled,
  embedded = false,
  interactionAvailability,
  onSubmitClarification,
  workflow
}: WorkflowRunCardProps): ReactElement | null {
  const status = String(workflow.summary.status || 'unknown')
  const artifacts = workflow.summary.artifacts || {}
  const clarification = workflowClarification(workflow)
  const cardCopy = workflowCardCopy(clarification?.mode, workflow.summary.phase)
  const clarificationQuestions = clarification?.questions || []
  const isQuestionCard = clarificationQuestions.length > 0
  const isTestCaseAuthorization = clarification?.mode === 'test_case_execute'
  const isArtifactAcceptance = clarification?.mode === 'page_acceptance'
  const isBackgroundDispatch = clarification?.mode === 'background_dispatch'
  const detailReview = clarification?.mode === 'detail_review' ? clarification.review : undefined
  const artifactConfirmation = clarification?.mode
    ? ARTIFACT_CONFIRMATION_MAP[clarification.mode]
    : undefined
  const requiresConfirmation =
    clarification?.status === 'requires_user_input'
  // 使用问题自身提供的默认答案初始化卡片，保留可直接调整的演示起点。
  const [answers, setAnswers] = useState<ClarificationAnswers>(() => {
    const initial: ClarificationAnswers = {}
    clarificationQuestions.forEach((question, index) => {
      const preset = (
        question as WorkflowClarificationQuestion & { presetAnswer?: WorkflowClarificationAnswer }
      ).presetAnswer
      if (preset !== undefined) initial[clarificationQuestionKey(question, index)] = preset
    })
    return initial
  })
  const [clarificationStep, setClarificationStep] = useState(0)
  // 应用级验收的主交互位于右侧预览底部，避免在对话区重复渲染一张确认卡。
  if (clarification?.mode === 'application_acceptance') return null
  // 开发准入由独立弹框承载，计划对话只保留项目 Agent 的确认消息。
  if (clarification?.mode === 'development_entry_confirmation') return null
  const canSubmitClarification =
    clarification?.status === 'requires_user_input' &&
    clarificationQuestions.length > 0 &&
    clarificationQuestions.every((question, index) =>
      clarificationAnswerComplete(question, answers[clarificationQuestionKey(question, index)])
    )
  // 分步向导：一次只展示一个待确认项，降低单屏信息量。
  const totalQuestions = clarificationQuestions.length
  const safeStep = totalQuestions > 0 ? Math.min(clarificationStep, totalQuestions - 1) : 0
  const currentQuestion = totalQuestions > 0 ? clarificationQuestions[safeStep] : undefined
  const currentAnswerKey = currentQuestion
    ? clarificationQuestionKey(currentQuestion, safeStep)
    : ''
  const currentRequired = currentQuestion ? currentQuestion.required !== false : true
  const currentComplete = currentQuestion
    ? clarificationAnswerComplete(currentQuestion, answers[currentAnswerKey])
    : false
  const updateAnswer = (key: string, value: WorkflowClarificationAnswer): void => {
    setAnswers((currentAnswers) => ({
      ...currentAnswers,
      [key]: value
    }))
  }

  // 产物确认已由消息中的“文件改动”卡片承担确认（接受按钮），授权行不再重复渲染。
  if (artifactConfirmation && requiresConfirmation) {
    return null
  }
  // 逐文件接受：确认同样由“文件改动”卡承担，这里不渲染待确认卡。
  if (clarification?.mode === 'file_acceptance' && requiresConfirmation) {
    return null
  }
  // 开发详细设计的产物授权统一由右侧 Diff 和输入框上方授权区块承载，
  // 对话区不再渲染“待确认事项 / 确认保存”卡片。
  if (detailReview) return null

  // 执行方式选择是「选择执行方式」节点的动作：紧凑卡内嵌在节点轨迹中渲染，
  // 不再脱离流程单独成块；选项即提交，解释话术收进选项的注释图标。
  if (isBackgroundDispatch && requiresConfirmation) {
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-test-case-authorization',
          'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && <span className={cx('workflow-run-signal')} aria-hidden="true" />}
        {!embedded && (
          <Text className={cx('workflow-run-name')} strong>
            选择执行方式
          </Text>
        )}
        <BackgroundDispatchCard
          disabled={disabled || interactionAvailability !== 'active'}
          options={BACKGROUND_DISPATCH_OPTIONS}
          answerKey={
            String(workflow.state?.dispatchTarget || '') === 'endpoint'
              ? 'background_dispatch_endpoint'
              : 'background_dispatch'
          }
          onSelect={(key, answerKey) =>
            onSubmitClarification?.(workflow, { [answerKey]: key })
          }
        />
        {interactionAvailability !== 'active' && (
          <Alert
            className={cx('workflow-dispatch-availability')}
            message={
              interactionAvailability === 'unavailable'
                ? '正在校准确认状态，请稍候。'
                : '该确认已提交或已失效，请在当前工作流继续操作。'
            }
            showIcon
            type="info"
          />
        )}
      </div>
    )
  }

  // 产物验收只负责启动动作；页面预览与接口调试统一放在右侧开发产物工作区，由验收工作流先行打开。
  if (isArtifactAcceptance && requiresConfirmation) {    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-test-case-authorization',
          'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && <span className={cx('workflow-run-signal')} aria-hidden="true" />}
        {!embedded && (
          <Text className={cx('workflow-run-name')} strong>
            产物验收
          </Text>
        )}
        <Text className={cx('workflow-test-case-authorization-copy')}>
          请在右侧预览确认实现内容，确认后接受产物。
        </Text>
        <Button
          className={cx('workflow-test-case-authorization-action')}
          type="primary"
          disabled={disabled || interactionAvailability !== 'active'}
          onClick={() => onSubmitClarification?.(workflow, { page_acceptance: 'accepted' })}
        >
          确认验收
        </Button>
      </div>
    )
  }

  // 用例授权只负责启动动作；用例详情、脚本和预期结果统一放在右侧用例面板。
  if (isTestCaseAuthorization && requiresConfirmation) {
    return (
      <div
        className={cx(
          'workflow-run-card',
          'workflow-run-card-test-case-authorization',
          'workflow-run-card-pending',
          embedded && 'workflow-run-card-embedded'
        )}
      >
        {!embedded && <span className={cx('workflow-run-signal')} aria-hidden="true" />}
        {!embedded && (
          <Text className={cx('workflow-run-name')} strong>
            授权执行用例
          </Text>
        )}
        <Text className={cx('workflow-test-case-authorization-copy')}>
          请在右侧查看用例内容，确认后开始执行。
        </Text>
        <Button
          className={cx('workflow-test-case-authorization-action')}
          type="primary"
          disabled={disabled || interactionAvailability !== 'active'}
          onClick={() => onSubmitClarification?.(workflow, { confirm_test_case: '是' })}
        >
          开始执行
        </Button>
      </div>
    )
  }

  return (
    <div
      className={cx(
        'workflow-run-card',
        isQuestionCard && 'workflow-run-card-question',
        requiresConfirmation && 'workflow-run-card-pending'
      )}
    >
      <div className={cx('workflow-run-header')}>
        <div className={cx('workflow-run-title')}>
          <span className={cx('workflow-run-signal')} aria-hidden="true" />
          <div>
            <Text className={cx('workflow-run-name')} strong>
              {isTestCaseAuthorization ? cardCopy.title : isQuestionCard ? '待确认事项' : cardCopy.title}
            </Text>
          </div>
        </div>
        {isQuestionCard && !isTestCaseAuthorization ? (
          <Text className={cx('workflow-clarification-stepper-header')} type="secondary">
            第 {safeStep + 1} / {totalQuestions} 项
          </Text>
        ) : (
          <Tag className={cx('workflow-run-status')} color={workflowStatusColor(status)}>
            {workflowStatusText(status)}
          </Tag>
        )}
      </div>
      {workflow.summary.message && (
        <div className={cx('workflow-run-message')}>
          <Text>{String(workflow.summary.message)}</Text>
        </div>
      )}
      {Object.keys(artifacts).length > 0 && (
        <div className={cx('workflow-artifacts')}>
          <div className={cx('workflow-section-heading')}>
            <Text type="secondary">已生成产物</Text>
            <span>{Object.keys(artifacts).length} 个</span>
          </div>
          {Object.entries(artifacts).map(([name, path]) => (
            <div className={cx('workflow-artifact-item')} key={name}>
              <span className={cx('workflow-artifact-marker')} aria-hidden="true" />
              <Text code>
                {name}: {path}
              </Text>
            </div>
          ))}
        </div>
      )}
      {(clarificationQuestions.length > 0 || detailReview) && (
        <div className={cx('workflow-clarification')}>
          {requiresConfirmation && interactionAvailability !== 'active' && (
            <Alert
              message={
                interactionAvailability === 'unavailable'
                  ? '正在校准确认状态，请稍候。'
                  : '该确认已提交或已失效，请在当前工作流继续操作。'
              }
              showIcon
              type="info"
            />
          )}
          {detailReview ? (
            <DetailReviewAuthBar
              detailReview={detailReview}
              disabled={disabled}
              onConfirm={(submission) =>
                onSubmitClarification?.(workflow, { detail_review: submission })
              }
            />
          ) : (
            currentQuestion && (
              <div className={cx('workflow-clarification-body')}>
                <ClarificationContext clarification={clarification} />
                <div
                  className={cx('workflow-clarification-question')}
                  key={currentQuestion.id || safeStep}
                >
                  <div className={cx('workflow-clarification-title')}>
                    <Tag>{currentQuestion.header || currentQuestion.dimension || '需求'}</Tag>
                    <Text>{currentQuestion.question || '请补充需求细节。'}</Text>
                    <Text
                      className={cx(
                        'workflow-required-hint',
                        currentRequired ? 'required' : 'optional'
                      )}
                      type={currentRequired ? 'danger' : 'secondary'}
                    >
                      {currentRequired ? '必填' : '选填'}
                    </Text>
                  </div>
                  <ClarificationQuestionControl
                    disabled={disabled}
                    onChange={(value) => updateAnswer(currentAnswerKey, value)}
                    question={currentQuestion}
                    value={answers[currentAnswerKey]}
                  />
                </div>
                <div className={cx('workflow-clarification-nav')}>
                  <Button
                    disabled={safeStep === 0}
                    onClick={() => setClarificationStep((s) => Math.max(0, s - 1))}
                  >
                    上一步
                  </Button>
                  {safeStep >= totalQuestions - 1 ? (
                    <Button
                      type="primary"
                      disabled={disabled || !canSubmitClarification}
                      onClick={() => onSubmitClarification?.(workflow, answers)}
                    >
                      {cardCopy.primaryAction}
                    </Button>
                  ) : (
                    <Button
                      type="primary"
                      disabled={!disabled && currentRequired && !currentComplete}
                      onClick={() =>
                        setClarificationStep((s) => Math.min(totalQuestions - 1, s + 1))
                      }
                    >
                      下一步
                    </Button>
                  )}
                </div>
              </div>
            )
          )}
        </div>
      )}
    </div>
  )
}

type WorkflowCardCopy = {
  title: string
  primaryAction: string
}

/** 将结构化交互卡转换为当前业务动作的文案，避免把需求确认误称为工作流执行。 */
function workflowCardCopy(mode?: string, phase?: string): WorkflowCardCopy {
  switch (mode) {
    case 'requirement_clarification':
      return {
        title: '细化需求',
        primaryAction: '确认需求并生成文档'
      }
    case 'requirement_revision':
      return {
        title: '修改需求文档',
        primaryAction: '提交修改意见'
      }
    case 'requirement_spec_confirmation':
      return {
        title: '确认需求文档',
        primaryAction: '确认并生成项目计划'
      }
    case 'project_plan_revision':
      return {
        title: '修改项目计划',
        primaryAction: '提交修改意见'
      }
    case 'project_plan_confirmation':
      return {
        title: '确认项目计划',
        primaryAction: '确认并进入开发'
      }
    case 'page_acceptance':
      return {
        title: '页面验收',
        primaryAction: '确认验收'
      }
    case 'test_case_execute':
      return {
        title: '授权执行用例',
        primaryAction: '开始执行'
      }
    case 'agent_acceptance':
      return {
        title: '智能体验收',
        primaryAction: '确认验收'
      }
    default:
      // 没有明确交互类型时保留通用标题，兼容其它工作台过程卡片。
      return {
        title: phase === 'requirements' ? '需求细化' : '工作流执行',
        primaryAction: '确认并继续'
      }
  }
}

function BuildExecutionSliceProgress({
  executionSlice
}: {
  executionSlice: WorkflowBuildExecutionSlice
}): ReactElement | null {
  /** 展示当前页面或数据源范围的构建进度，不做应用级汇总。 */

  const [activeTaskKeys, setActiveTaskKeys] = useState<string[]>([])
  const scope = executionSlice.scope
  if (!scope) return null
  const tasks = Array.isArray(executionSlice.tasks) ? executionSlice.tasks : []
  const summary = executionSlice.summary || {}
  const total = numberValue(summary.total, tasks.length)
  const completed = numberValue(
    summary.completed,
    tasks.filter((task) => task.status === 'completed').length
  )
  const failed = numberValue(
    summary.failed,
    tasks.filter((task) => task.status === 'failed').length
  )
  const running = numberValue(
    summary.running,
    tasks.filter((task) => task.status === 'running').length
  )
  const pending = numberValue(
    summary.pending,
    tasks.filter((task) => !task.status || task.status === 'pending').length
  )
  const reused = numberValue(summary.reused, executionSlice.reusable_task_ids?.length || 0)
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0
  const targetLabel =
    scope.type === 'page'
      ? '页面'
      : scope.type === 'data_source'
        ? '数据源'
        : scope.type === 'endpoint'
          ? '接口'
          : '应用'
  const targetId = scope.targetId || executionSlice.target_unit_ids?.[0] || ''
  const progressStatus =
    failed > 0 ? 'exception' : completed === total && total > 0 ? 'success' : 'active'
  const displayTasks = sortBuildTasksForDisplay(tasks)
  const expandedTaskKeys = new Set(activeTaskKeys)

  return (
    <div className={cx('workflow-build-progress')}>
      <div className={cx('workflow-build-progress-header')}>
        <div>
          <Text strong>执行进度</Text>
          <Text type="secondary">
            {targetId ? `${targetLabel}：${targetId}` : `${targetLabel}执行范围`}
          </Text>
        </div>
        <Tag
          className={cx(
            'workflow-build-progress-count-tag',
            failed > 0 ? 'failed' : completed === total && total > 0 ? 'completed' : 'running'
          )}
          color={failed > 0 ? 'red' : completed === total && total > 0 ? 'green' : 'purple'}
        >
          {completed}/{total}
        </Tag>
      </div>
      <Progress
        percent={percent}
        showInfo={false}
        status={progressStatus}
        strokeColor={failed > 0 ? 'var(--wb-danger)' : 'var(--wb-accent)'}
        trailColor="var(--wb-surface-subtle)"
      />
      <Text className={cx('workflow-build-progress-percent')} type="secondary">
        {percent}% 完成
      </Text>
      <div className={cx('workflow-build-progress-stats')}>
        <BuildProgressStat
          icon={<PauseCircleOutlined />}
          label="待执行"
          tone="pending"
          value={pending}
        />
        <BuildProgressStat
          icon={<LoadingOutlined />}
          label="执行中"
          tone="running"
          value={running}
        />
        <BuildProgressStat
          icon={<CheckCircleOutlined />}
          label="已完成"
          tone="completed"
          value={completed}
        />
        <BuildProgressStat
          icon={<CloseCircleOutlined />}
          label="失败"
          tone="failed"
          value={failed}
        />
        <BuildProgressStat
          icon={<ClockCircleOutlined />}
          label="已复用"
          tone="reused"
          value={reused}
        />
      </div>
      {tasks.length > 0 && (
        <div className={cx('workflow-build-task-section')}>
          <Text strong>任务详情</Text>
          <Collapse
            activeKey={activeTaskKeys}
            className={cx('workflow-build-task-list')}
            expandIconPosition="right"
            onChange={(keys) => {
              const nextKeys = Array.isArray(keys) ? keys : [keys]
              setActiveTaskKeys(nextKeys.map(String))
            }}
          >
            {displayTasks.map((task) => (
              <Collapse.Panel
                className={cx('workflow-build-task-panel', task.status || 'pending')}
                header={
                  <BuildExecutionTaskHeader
                    expanded={expandedTaskKeys.has(taskId(task))}
                    task={task}
                  />
                }
                key={taskId(task)}
              >
                <BuildExecutionTaskDetails task={task} />
              </Collapse.Panel>
            ))}
          </Collapse>
        </div>
      )}
    </div>
  )
}

export function BuildExecutionRunCard({
  executionSlice,
  status
}: {
  executionSlice: WorkflowBuildExecutionSlice
  status: 'running' | 'completed' | 'failed' | 'requires_user_input'
}): ReactElement {
  /** 在对应构建步骤内部渲染独立的构建轮次卡片。 */

  return (
    <section className={cx('workflow-run-card', 'workflow-build-run-card', status)}>
      <div className={cx('workflow-run-header')}>
        <div className={cx('workflow-run-title')}>
          <span className={cx('workflow-run-signal')} aria-hidden="true" />
          <Text className={cx('workflow-run-name')} strong>
            构建执行
          </Text>
        </div>
        <Tag className={cx('workflow-run-status')} color={workflowStatusColor(status)}>
          {workflowStatusText(status)}
        </Tag>
      </div>
      <BuildExecutionSliceProgress executionSlice={executionSlice} />
    </section>
  )
}

function BuildProgressStat({
  icon,
  label,
  tone,
  value
}: {
  icon: ReactElement
  label: string
  tone: 'pending' | 'running' | 'completed' | 'failed' | 'reused'
  value: number
}): ReactElement {
  /** 渲染当前构建范围内的单项计数，避免上升到应用级统计。 */

  return (
    <span className={cx('workflow-build-progress-stat', tone)}>
      <span className={cx('workflow-build-progress-stat-icon')} aria-hidden="true">
        {icon}
      </span>
      <Text strong>{value}</Text>
      <Text type="secondary">{label}</Text>
    </span>
  )
}

function BuildExecutionTaskHeader({
  expanded,
  task
}: {
  expanded: boolean
  task: WorkflowBuildExecutionTask
}): ReactElement {
  /** 渲染可折叠任务卡片的头部摘要。 */

  const status = String(task.status || 'pending')
  const title = displayTaskTitle(task)
  const description = displayTaskDescription(task)
  return (
    <div className={cx('workflow-build-task-header-shell')}>
      <div className={cx('workflow-build-task-header', status)}>
        <span className={cx('workflow-build-task-status-icon')} aria-hidden="true">
          {taskStatusIcon(status)}
        </span>
        <div className={cx('workflow-build-task-title')}>
          <Text strong>{title}</Text>
          <Text type="secondary">{description}</Text>
        </div>
        <Tag
          className={cx('workflow-build-task-status-tag', status)}
          color={taskStatusColor(status)}
        >
          {taskStatusText(status)}
        </Tag>
      </div>
      {buildToolActivityPlacement(task, expanded) === 'header' && (
        <BuildToolActivity activity={task.activeToolActivity!} />
      )}
    </div>
  )
}

function BuildExecutionTaskDetails({ task }: { task: WorkflowBuildExecutionTask }): ReactElement {
  /** 展示单个构建任务的定位、失败原因、文件范围和验收点。 */

  const dependencies = taskDependencies(task)
  const paths = [
    ...stringList(task.targetFiles),
    ...stringList(task.target_files),
    ...stringList(task.allowed_paths),
    ...stringList(task.allowedPaths)
  ]
  const acceptance = dedupeLocalizedTaskTexts(
    [...stringList(task.acceptanceCriteria), ...stringList(task.acceptance_criteria)],
    task
  )
  const failureReason = taskFailureReason(task)
  const failureCategory = taskFailureCategoryText(task.failure_category)
  return (
    <div className={cx('workflow-build-task-details')}>
      <div className={cx('workflow-build-task-detail-grid')}>
        <BuildTaskDetailItem label="任务 ID" value={taskId(task)} />
        <BuildTaskDetailItem
          label="依赖"
          value={dependencies.length > 0 ? dependencies.join('、') : '无'}
        />
      </div>
      {task.status === 'failed' && (
        <div className={cx('workflow-build-task-detail-block', 'workflow-build-task-failure')}>
          <Text type="secondary">失败原因</Text>
          <Text>{failureReason || '任务执行失败，但后端未返回具体原因。'}</Text>
          {failureCategory && <Tag color="red">{failureCategory}</Tag>}
        </div>
      )}
      {paths.length > 0 && (
        <div className={cx('workflow-build-task-detail-block')}>
          <Text type="secondary">文件范围</Text>
          <div className={cx('workflow-build-task-tags')}>
            {dedupeStrings(paths).map((path) => (
              <Tag key={path}>{path}</Tag>
            ))}
          </div>
        </div>
      )}
      {acceptance.length > 0 && (
        <div className={cx('workflow-build-task-detail-block')}>
          <Text type="secondary">验收点</Text>
          <ul className={cx('workflow-build-task-detail-list')}>
            {acceptance.map((item) => (
              <li key={item}>
                <Text>{item}</Text>
              </li>
            ))}
          </ul>
        </div>
      )}
      {buildToolActivityPlacement(task, true) === 'details' && (
        <BuildToolActivity activity={task.activeToolActivity!} />
      )}
    </div>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function buildToolActivityPlacement(
  task: WorkflowBuildExecutionTask,
  expanded: boolean
): 'header' | 'details' | undefined {
  /** 决定实时工具活动的唯一渲染位置，任务终态或无活动时不展示。 */

  if (task.status !== 'running' || !task.activeToolActivity) return undefined
  return expanded ? 'details' : 'header'
}

function BuildToolActivity({
  activity
}: {
  activity: NonNullable<WorkflowBuildExecutionTask['activeToolActivity']>
}): ReactElement {
  /** 以单行高亮样式展示当前任务最新工具操作，不展开原始工具参数。 */

  return (
    <div
      aria-label={activity.message}
      aria-live="polite"
      className={cx('workflow-build-tool-activity', activity.status)}
      title={activity.message}
    >
      <span className={cx('workflow-build-tool-activity-icon')} aria-hidden="true">
        {activity.status === 'running' ? <LoadingOutlined spin /> : <CloseCircleOutlined />}
      </span>
      <Text>{activity.message}</Text>
    </div>
  )
}

function BuildTaskDetailItem({ label, value }: { label: string; value: string }): ReactElement {
  /** 渲染任务详情中的单个键值项。 */

  return (
    <div className={cx('workflow-build-task-detail-item')}>
      <Text type="secondary">{label}</Text>
      <Text>{value}</Text>
    </div>
  )
}

function taskFailureReason(task: WorkflowBuildExecutionTask): string {
  /** 提取失败原因，兼容后端后续扩展的 failure_detail 文本字段。 */

  if (typeof task.failure_reason === 'string' && task.failure_reason.trim()) {
    return task.failure_reason.trim()
  }
  const detail = objectValue(task.failure_detail)
  for (const key of ['reason', 'message', 'agent_note']) {
    const value = detail[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

function taskStatusIcon(status: string): ReactElement {
  /** 将任务状态映射为卡片头部图标。 */

  if (status === 'completed') return <CheckCircleOutlined />
  if (status === 'failed') return <CloseCircleOutlined />
  if (status === 'running') return <LoadingOutlined />
  return <PauseCircleOutlined />
}

function ClarificationContext({
  clarification
}: {
  clarification?: WorkflowClarification
}): ReactElement | null {
  const groups = (clarification?.selection_groups || []).filter(
    (group) => Array.isArray(group.items) && group.items.length > 0
  )
  const context = clarification?.context
  if (groups.length === 0 && !context) return null

  return (
    <div className={cx('workflow-clarification-context')}>
      {groups.map((group, index) => (
        <SelectionGroup group={group} key={`${group.type || group.title}-${index}`} />
      ))}
      {context && <WorkflowContext context={context} />}
    </div>
  )
}

function SelectionGroup({ group }: { group: WorkflowClarificationSelectionGroup }): ReactElement {
  return (
    <div className={cx('workflow-selection-group')}>
      <Text strong>{group.title || group.type || '候选项'}</Text>
      <ul className={cx('workflow-selection-list')}>
        {(group.items || []).map((item) => (
          <li className={cx('workflow-selection-item')} key={item.id || item.label}>
            <Text>{item.label || item.name || item.id}</Text>
            <ul className={cx('workflow-selection-item-meta')}>
              {item.id && (
                <li>
                  <Text type="secondary">id: </Text>
                  <Text code>{item.id}</Text>
                </li>
              )}
              {item.description && (
                <li>
                  <Text type="secondary">{item.description}</Text>
                </li>
              )}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  )
}

function WorkflowContext({ context }: { context: Record<string, unknown> }): ReactElement {
  const page = objectValue(context.page)
  const layout = objectValue(context.layout)
  const interactions = stringList(context.interactions)
  const dataSources = objectList(context.data_sources)
  const permissions = stringList(context.permissions)

  return (
    <div className={cx('workflow-page-context')}>
      {Object.keys(page).length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text strong>{stringValue(page.name) || '页面'}</Text>
          <Text type="secondary">
            {stringValue(page.path)}
            {stringValue(page.goal) ? `：${stringValue(page.goal)}` : ''}
          </Text>
        </div>
      )}
      {stringList(layout.structure).length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">布局</Text>
          <Text>{stringList(layout.structure).join('、')}</Text>
        </div>
      )}
      {interactions.length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">交互</Text>
          <Text>{interactions.join('、')}</Text>
        </div>
      )}
      {dataSources.length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">数据源</Text>
          <Text>
            {dataSources
              .map((source) => stringValue(source.name) || stringValue(source.id))
              .filter(Boolean)
              .join('、')}
          </Text>
        </div>
      )}
      {permissions.length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">权限</Text>
          <Text>{permissions.join('、')}</Text>
        </div>
      )}
    </div>
  )
}

function ClarificationQuestionControl({
  disabled,
  onChange,
  question,
  value
}: {
  disabled?: boolean
  onChange: (value: WorkflowClarificationAnswer) => void
  question: WorkflowClarificationQuestion
  value?: WorkflowClarificationAnswer
}): ReactElement {
  const options = (question.options || [])
    .filter((option) => option.label)
    .map((option) => ({
      label: option.label || '',
      value: option.value || option.label || '',
      description: option.description || ''
    }))
  const optionsWithOther =
    question.allowOther !== false && !options.some((option) => option.value === OTHER_OPTION_VALUE)
      ? [...options, { label: '其他', value: OTHER_OPTION_VALUE, description: '' }]
      : options
  const selectedValues = selectedAnswerValues(value)
  const otherSelected = selectedValues.includes(OTHER_OPTION_VALUE)
  const otherValue = answerOtherText(value)
  const setSelectedValues = (selected: string[]): void => {
    onChange({ selected, other: otherValue || undefined })
  }
  const setOtherValue = (other: string): void => {
    onChange({ selected: selectedValues, other })
  }

  if (question.type === 'yesno') {
    return (
      <>
        <Radio.Group
          disabled={disabled}
          onChange={(event) => setSelectedValues([String(event.target.value)])}
          value={selectedValues[0]}
        >
          <Radio value="是">是</Radio>
          <Radio value="否">否</Radio>
          {question.allowOther !== false && <Radio value={OTHER_OPTION_VALUE}>其他</Radio>}
        </Radio.Group>
        {otherSelected && (
          <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />
        )}
      </>
    )
  }

  if (question.type === 'choice' && optionsWithOther.length > 0) {
    if (question.multiSelect) {
      return (
        <>
          <Checkbox.Group
            disabled={disabled}
            onChange={(checkedValues) => setSelectedValues(checkedValues.map(String))}
            value={selectedValues}
          >
            {optionsWithOther.map((option) => (
              <Checkbox
                className={cx('workflow-clarification-option')}
                key={option.value}
                value={option.value}
              >
                {renderOptionLabel(option)}
              </Checkbox>
            ))}
          </Checkbox.Group>
          {otherSelected && (
            <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />
          )}
        </>
      )
    }

    return (
      <>
        <Radio.Group
          disabled={disabled}
          onChange={(event) => setSelectedValues([String(event.target.value)])}
          value={selectedValues[0]}
        >
          {optionsWithOther.map((option) => (
            <Radio
              className={cx('workflow-clarification-option')}
              key={option.value}
              value={option.value}
            >
              {renderOptionLabel(option)}
            </Radio>
          ))}
        </Radio.Group>
        {otherSelected && (
          <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />
        )}
      </>
    )
  }

  return (
    <TextArea
      autoSize={false}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      placeholder={question.placeholder || '请输入你的补充说明'}
      // 单行输入保持每个确认步骤的底部操作栏在同一水平线上；超长内容仍可横向编辑滚动。
      rows={1}
      value={typeof value === 'string' ? value : ''}
    />
  )
}

/** 待确认选项带说明时在文案上加悬浮提示；无说明时保持原样（对齐正式工程 description 字段）。 */
function renderOptionLabel(option: { label: string; description: string }): ReactElement {
  if (!option.description) return <span>{option.label}</span>
  return (
    <Tooltip
      overlayClassName={cx('workflow-clarification-option-tooltip')}
      placement="top"
      title={option.description}
    >
      <span>{option.label}</span>
    </Tooltip>
  )
}

function OtherInput({
  disabled,
  onChange,
  value
}: {
  disabled?: boolean
  onChange: (value: string) => void
  value: string
}): ReactElement {
  return (
    <TextArea
      autoSize={{ minRows: 2, maxRows: 4 }}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      placeholder="请补充其他选择或说明"
      value={value}
    />
  )
}

function clarificationQuestionKey(question: WorkflowClarificationQuestion, index: number): string {
  return question.id || question.header || question.question || String(index)
}

function clarificationAnswerComplete(
  question: WorkflowClarificationQuestion,
  value: WorkflowClarificationAnswer | undefined
): boolean {
  if (question.required === false) return true
  if (question.type === 'choice' || question.type === 'yesno') {
    const selected = selectedAnswerValues(value)
    if (selected.length === 0) return false
    return !selected.includes(OTHER_OPTION_VALUE) || Boolean(answerOtherText(value).trim())
  }
  if (Array.isArray(value)) return value.length > 0
  return typeof value === 'string' && value.trim().length > 0
}

function selectedAnswerValues(value: WorkflowClarificationAnswer | undefined): string[] {
  if (typeof value === 'object' && value && !Array.isArray(value) && 'selected' in value) {
    const selected = value.selected
    return Array.isArray(selected) ? selected.map(String) : [String(selected)]
  }
  if (Array.isArray(value)) return value.map(String)
  return typeof value === 'string' && value ? [value] : []
}

function answerOtherText(value: WorkflowClarificationAnswer | undefined): string {
  return typeof value === 'object' && value && !Array.isArray(value) && 'other' in value
    ? String(value.other || '')
    : ''
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function objectList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> =>
        Boolean(item && typeof item === 'object')
      )
    : []
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter((item) => item.trim()) : []
}

// eslint-disable-next-line react-refresh/only-export-components
export function workflowOriginalRequest(workflow: WorkflowRunPayload): string {
  for (const source of [workflow.result, workflow.state]) {
    const requirementSpec = objectValue(source?.requirement_spec)
    const sourceRequest = requirementSpec.source_request
    if (typeof sourceRequest === 'string' && sourceRequest.trim()) return sourceRequest.trim()
  }

  const resultRequest = workflow.result?.request
  if (typeof resultRequest === 'string' && resultRequest.trim()) return resultRequest.trim()

  const stateRequest = workflow.state?.request
  if (typeof stateRequest === 'string' && stateRequest.trim()) return stateRequest.trim()

  const startedEvent = workflow.events.find((event) => event.type === 'workflow.run.started')
  const eventRequest = startedEvent?.data?.request
  return typeof eventRequest === 'string' ? eventRequest.trim() : ''
}

/** 根据当前结构化交互生成恢复 Workflow 所需的用户可见消息。 */
// eslint-disable-next-line react-refresh/only-export-components
export function buildClarificationContinuationMessage(
  workflow: WorkflowRunPayload,
  answers: ClarificationAnswers
): string {
  const clarification = workflowClarification(workflow)
  const acceptanceMessage = pageAcceptanceContinuationMessage(clarification, answers)
  if (acceptanceMessage) return acceptanceMessage
  const dispatchMessage = backgroundDispatchContinuationMessage(clarification, answers)
  if (dispatchMessage) return dispatchMessage
  if (clarification?.mode === 'detail_review' && answers.detail_review) {
    const submission = answers.detail_review
    if (
      typeof submission === 'object' &&
      !Array.isArray(submission) &&
      'review_status' in submission
    ) {
      return '已整体审阅并确认全部页面和数据源设计，请合并本次结构化修改后继续。'
    }
  }
  const mode = clarification?.mode
  // 逐文件接受：确认动作由消息中的“文件改动”卡（接受按钮）承担，这里生成续跑消息。
  if (mode === 'file_acceptance') {
    return `已接受文件 ${String(answers.file_acceptance || '')} 的变更，请继续下一个文件。`
  }
  if (mode === 'development_entry_confirmation') {
    return '确认进入开发阶段。'
  }
  if (mode === 'agent_dependency_gate') {
    const action = String(answers.agent_dependency_action || '')
    if (action === 'open_design') return '已进入实体详细设计，请审阅并确认后返回智能体流程。'
    if (action === 'confirm_entity_design') return '已确认实体详细设计，请重新校验智能体依赖。'
    if (action === 'recheck') return '已发起实体依赖重新检测。'
  }
  if (
    mode === 'requirement_spec_confirmation' &&
    answers.confirm_requirement_spec !== undefined &&
    !answerConfirmsYes(answers.confirm_requirement_spec)
  ) {
    return '需求文档需要修改，请返回分析阶段补充调整意见。'
  }
  if (
    mode === 'project_plan_confirmation' &&
    answers.confirm_project_plan !== undefined &&
    !answerConfirmsYes(answers.confirm_project_plan)
  ) {
    return '项目计划需要修改，请继续补充调整意见。'
  }
  // 产物确认卡（需求/项目计划/构建任务）：无问答，直接授权推进，返回非空文案保证提交不早退。
  if (mode && ARTIFACT_CONFIRMATION_MAP[mode]) {
    return `已确认${ARTIFACT_CONFIRMATION_MAP[mode].title}，请继续下一步。`
  }
  const questions = clarification?.questions || []
  // originalRequest 历史上用于此处早退判断，但 continuation 最终仅由回答内容拼成（见下方 return）。
  // 冷启动澄清（工作台 autostart 触发）workflow 未携带 request 字段，不应据此早退导致确认无法提交。
  if (questions.length === 0) return ''

  const answerLines = questions
    .map((question, index) => {
      const key = clarificationQuestionKey(question, index)
      const value = answers[key]
      const answer = clarificationAnswerText(value)
      if (!answer || !String(answer).trim()) return ''
      return `- ${
        question.header || question.dimension || `问题${index + 1}`
      }：${question.question || '请补充需求细节。'}\n  回答：${answer}`
    })
    .filter(Boolean)

  if (answerLines.length === 0) return ''

  return answerLines.join('\n')
}

/** 读取需求/计划确认卡的 yes/no 答案，避免“否”被误当成继续推进。 */
function answerConfirmsYes(value: WorkflowClarificationAnswer | undefined): boolean {
  if (value && typeof value === 'object' && !Array.isArray(value) && 'selected' in value) {
    const selected = value.selected
    return (Array.isArray(selected) ? selected : [selected]).some((item) => String(item) === '是')
  }
  if (Array.isArray(value)) return value.some((item) => String(item) === '是')
  return value === '是'
}

function clarificationAnswerText(value: WorkflowClarificationAnswer | undefined): string {
  if (typeof value === 'object' && value && !Array.isArray(value) && 'selected' in value) {
    const selected = selectedAnswerValues(value).filter((item) => item !== OTHER_OPTION_VALUE)
    const parts = selected.length > 0 ? [`已选：${selected.join('、')}`] : []
    const other = answerOtherText(value).trim()
    if (other) parts.push(`其他补充：${other}`)
    return parts.join('；')
  }
  if (Array.isArray(value)) return value.join('、')
  return typeof value === 'string' ? value : ''
}

// 从 Workflow payload 的多个位置读取待确认载荷，兼容流式快照、最终结果和自定义事件。
// eslint-disable-next-line react-refresh/only-export-components
export function workflowClarification(
  workflow: WorkflowRunPayload | undefined
): WorkflowClarification | undefined {
  // 历史会话可能残留不完整快照；读取确认信息时必须把它当作无交互工作流处理，不能让消息列表白屏。
  if (!workflow || !workflow.summary || typeof workflow.summary !== 'object') return undefined
  const fromSummary = workflow.summary.clarification
  if (fromSummary && typeof fromSummary === 'object') return fromSummary

  const stateClarification = workflow.state?.clarification
  if (stateClarification && typeof stateClarification === 'object') {
    return stateClarification as WorkflowClarification
  }

  const resultClarification = workflow.result?.clarification
  if (resultClarification && typeof resultClarification === 'object') {
    return resultClarification as WorkflowClarification
  }

  const clarificationEvent = (Array.isArray(workflow.events) ? workflow.events : [])
    .slice()
    .reverse()
    .find((event) => {
      const detail = event.data?.detail
      return Boolean(detail && typeof detail === 'object' && 'clarification' in detail)
    })
  const eventClarification = clarificationEvent?.data?.detail
  if (
    eventClarification &&
    typeof eventClarification === 'object' &&
    'clarification' in eventClarification
  ) {
    const clarification = (eventClarification as { clarification?: unknown }).clarification
    if (clarification && typeof clarification === 'object') {
      return clarification as WorkflowClarification
    }
  }

  return undefined
}

/** 将工作流状态映射为符合工作区语义色的标签颜色。 */
function workflowStatusColor(status: string): string {
  if (status === 'completed' || status === 'passed') return 'green'
  if (status === 'failed' || status === 'error') return 'red'
  if (status === 'requires_user_input') return 'gold'
  if (status === 'running') return 'purple'
  return 'default'
}

function workflowStatusText(status: string): string {
  /** 将工作流状态映射为中文标签。 */

  if (status === 'completed' || status === 'passed') return '完成'
  if (status === 'failed' || status === 'error') return '失败'
  if (status === 'requires_user_input') return '待确认'
  if (status === 'running') return '运行中'
  return status || '未知'
}
