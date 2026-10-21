import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  DownOutlined,
  LoadingOutlined,
  MinusCircleOutlined,
  PauseCircleOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ToolOutlined,
  UpOutlined
} from '@ant-design/icons'
import { Button, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  IntegrationTestCheckRecord,
  ProcessStepRecord,
  WorkspaceInspectionProgress
} from '../../../../service/agUiAgent'
import type { WorkflowRunPayload } from '../../../../typings/workflow'
import { isCompactProcessStepNode } from '../../../../service/processStepHistory'
import { cx } from '../../../../utils'
import { openLocalReportFile } from '../../../../utils/reportFile'
import type { DagPendingPlanCompletion } from '../../dagPendingPlanPresentation'
import { BuildExecutionRunCard } from '../WorkflowRunCard'
import WorkspaceToolActivity from '../WorkflowRunCard/WorkspaceToolActivity'
import DagGenerationProgress from './DagGenerationProgress'
import ProjectPlanUpdatePanel from './ProjectPlanUpdatePanel'
import RepairInProgressPanel from './RepairInProgressPanel'
import RepairToolActivityRow from './RepairToolActivityRow'
import { buildProcessDisplayItems, isToolActivityStep } from './processDisplayItems'
import ConversationProgressSummary from './ConversationProgressSummary'
import { integrationTestCheckReportPath } from './reportAction'
import ToolActivityChain from './ToolActivityChain'
import WorkspaceInspectionPanel from './WorkspaceInspectionPanel'
import './ProcessSteps.less'

const { Text } = Typography

type Props = {
  conversation?: boolean
  dagPendingPlanCompletion?: DagPendingPlanCompletion
  loading: boolean
  onOpenPendingDagConfirmation?: () => void
  runFinishedAt?: string
  steps: ProcessStepRecord[]
  workflow?: WorkflowRunPayload
  waitingPrompt?: string
  waitingForInput?: boolean
}

/** 渲染一条可折叠的 Agent 执行轨迹，并在测试步骤中保留结构化检查结果。 */
export default function ProcessSteps({
  conversation = false,
  dagPendingPlanCompletion,
  loading,
  onOpenPendingDagConfirmation,
  runFinishedAt,
  steps,
  workflow,
  waitingPrompt = '',
  waitingForInput = false
}: Props): ReactElement {
  const hasTestChecklist = steps.some((step) => Boolean(step.checks?.length))
  const hasProjectPlanUpdate = steps.some((step) => Boolean(step.projectPlanUpdate))
  const hasWorkspaceInspection = steps.some((step) => Boolean(step.workspaceInspection))
  // 当前 PendingPlan 的提醒位于步骤内部，重进会话时须展开其外层轨迹；历史 Run 不参与。
  const hasPendingDagCompletion = Boolean(
    dagPendingPlanCompletion?.planningRunId &&
      steps.some(
        (step) => step.dagGeneration?.planningRunId === dagPendingPlanCompletion.planningRunId
      )
  )
  const displayItems = buildProcessDisplayItems(steps, conversation)
  const [open, setOpen] = useState(
    loading ||
      waitingForInput ||
      hasProjectPlanUpdate ||
      hasWorkspaceInspection ||
      hasPendingDagCompletion
  )

  useEffect(() => {
    if (
      loading ||
      waitingForInput ||
      hasTestChecklist ||
      hasProjectPlanUpdate ||
      hasWorkspaceInspection ||
      hasPendingDagCompletion
    ) {
      setOpen(true)
    }
  }, [
    hasPendingDagCompletion,
    hasProjectPlanUpdate,
    hasTestChecklist,
    hasWorkspaceInspection,
    loading,
    waitingForInput
  ])

  const statusClassName = loading ? 'running' : waitingForInput ? 'waiting' : 'completed'

  return (
    <details
      className={cx('process-steps', statusClassName)}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      open={open}
    >
      <summary className={cx('process-steps-summary')}>
        <span className={cx('process-steps-status')}>
          {loading ? (
            <LoadingOutlined spin />
          ) : waitingForInput ? (
            <PauseCircleOutlined />
          ) : (
            <CheckCircleOutlined />
          )}
        </span>
        <span className={cx('process-steps-heading')}>
          <Text strong>
            {conversation
              ? loading
                ? '正在处理请求'
                : waitingForInput
                  ? '等待你的确认'
                  : '请求处理完成'
              : loading
                ? 'Agent 正在执行'
                : waitingForInput
                  ? 'Agent 等待补充'
                  : 'Agent 执行完成'}
          </Text>
          {conversation ? (
            <ConversationProgressSummary
              currentStageLabel={conversationCurrentStageLabel(steps)}
              loading={loading}
              runFinishedAt={runFinishedAt || workflow?.summary.lifecycle?.updatedAt}
              waitingForInput={waitingForInput}
              workflow={workflow}
            />
          ) : (
            <Text type="secondary">
              {loading
                ? currentStepLabel(steps)
                : waitingForInput
                  ? '请根据下方提示补充修改需求'
                  : `已归档 ${steps.length} 个步骤`}
            </Text>
          )}
        </span>
      </summary>
      <div className={cx('process-steps-list')}>
        {displayItems.map((item, index) => {
          if (item.kind === 'tool-chain') {
            const latestStep = item.steps[item.steps.length - 1]
            return (
              <ToolActivityChain
                isLast={index === displayItems.length - 1}
                key="tool-activity-chain"
                latestDetail={latestStep.detail}
                latestIcon={stepIcon(latestStep, !loading)}
                latestTitle={processStepTitle(latestStep, conversation, !loading)}
              >
                {item.steps.map((step, toolIndex) => (
                  <ProcessStep
                    conversation={conversation}
                    isLast={toolIndex === item.steps.length - 1}
                    key={step.id}
                    settled={!loading}
                    step={step}
                    waitingForInput={waitingForInput}
                    waitingPrompt={waitingPrompt}
                  />
                ))}
              </ToolActivityChain>
            )
          }

          return (
            <ProcessStep
              conversation={conversation}
              isLast={index === displayItems.length - 1}
              key={item.step.id}
              settled={!loading}
              step={item.step}
              toolSteps={item.toolSteps}
              waitingForInput={waitingForInput}
              waitingPrompt={waitingPrompt}
              dagPendingPlanCompletion={dagPendingPlanCompletion}
              onOpenPendingDagConfirmation={onOpenPendingDagConfirmation}
            />
          )
        })}
      </div>
    </details>
  )
}

/** 渲染单个 Agent 步骤，仅让包含实际详情的步骤具备展开交互。 */
export function ProcessStep({
  conversation,
  dagPendingPlanCompletion,
  isLast,
  onOpenPendingDagConfirmation,
  settled,
  step,
  toolSteps = [],
  waitingForInput,
  waitingPrompt
}: {
  conversation: boolean
  dagPendingPlanCompletion?: DagPendingPlanCompletion
  isLast: boolean
  onOpenPendingDagConfirmation?: () => void
  settled: boolean
  step: ProcessStepRecord
  toolSteps?: ProcessStepRecord[]
  waitingForInput: boolean
  waitingPrompt: string
}): ReactElement {
  const hasChecks = Boolean(step.checks?.length)
  const hasBuildRun = Boolean(step.buildExecutionSlice)
  const hasDagGeneration = Boolean(step.dagGeneration)
  const hasProjectPlanUpdate = Boolean(step.projectPlanUpdate)
  const hasWorkspaceInspection = Boolean(step.workspaceInspection)
  const hasWorkspaceInspectionProgress = Boolean(step.workspaceInspectionProgress)
  // 扫描和 API 设计前置检查从出现到完成都默认收起，避免流式更新自动展开详情。
  const isDefaultCollapsedNode =
    hasWorkspaceInspection ||
    hasWorkspaceInspectionProgress ||
    step.nodeName === 'inspect_workspace' ||
    step.nodeName === 'scan_workspace_code' ||
    step.nodeName === 'api_design_readiness_gate'
  const isRepairStep =
    step.kind === 'workflow' &&
    (step.nodeName === 'small_task_repair' || step.nodeName === 'unit_test_repair')
  const hasRepairActivity = isRepairStep && step.status === 'running'
  const hasRepairCompletion = isRepairStep && step.status === 'completed'
  const hasRepairPanel = hasRepairActivity || hasRepairCompletion
  const dagSnapshotMatchesPendingPlan = Boolean(
    step.dagGeneration &&
      dagPendingPlanCompletion?.planningRunId &&
      step.dagGeneration.planningRunId === dagPendingPlanCompletion.planningRunId
  )
  const hideUnverifiedActiveDagSnapshot = Boolean(
    step.dagGeneration?.status === 'active' &&
      dagPendingPlanCompletion &&
      !dagSnapshotMatchesPendingPlan
  )
  const hasDetail = Boolean(step.detail.trim())
  const hasResult = Boolean(step.result?.trim())
  const hasToolActivity = toolSteps.length > 0
  const titleOnly = isCompactProcessStepNode(step.nodeName)
  const expandable =
    (!titleOnly || hasToolActivity) &&
    (hasDetail ||
      hasResult ||
      hasToolActivity ||
      hasChecks ||
      hasBuildRun ||
      hasDagGeneration ||
      hasProjectPlanUpdate ||
      hasWorkspaceInspection ||
      hasWorkspaceInspectionProgress ||
      hasRepairPanel)
  const awaitingInput = !titleOnly && waitingForInput && step.status === 'requires_user_input'
  // 二次修改的普通动作详情默认收起，流式更新不自动展开；结构化进度和确认提示沿用原展示规则。
  const collapseConversationDetail =
    conversation &&
    !hasChecks &&
    !hasBuildRun &&
    !hasDagGeneration &&
    !hasProjectPlanUpdate &&
    !hasWorkspaceInspection &&
    !hasWorkspaceInspectionProgress &&
    !hasRepairPanel &&
    !awaitingInput
  const [open, setOpen] = useState(
    expandable &&
      !isDefaultCollapsedNode &&
      !collapseConversationDetail &&
      (step.status === 'running' ||
        awaitingInput ||
        hasChecks ||
        hasBuildRun ||
        hasDagGeneration ||
        hasProjectPlanUpdate ||
        hasWorkspaceInspection ||
        hasWorkspaceInspectionProgress ||
        hasRepairPanel ||
        hasToolActivity)
  )

  useEffect(() => {
    if (collapseConversationDetail || isDefaultCollapsedNode) return
    if (
      expandable &&
      (step.status === 'running' ||
        awaitingInput ||
        hasChecks ||
        hasBuildRun ||
        hasDagGeneration ||
        hasProjectPlanUpdate ||
        hasWorkspaceInspection ||
        hasWorkspaceInspectionProgress ||
        hasRepairPanel)
    ) {
      setOpen(true)
    }
  }, [
    collapseConversationDetail,
    awaitingInput,
    expandable,
    hasBuildRun,
    hasChecks,
    hasDagGeneration,
    hasProjectPlanUpdate,
    hasWorkspaceInspection,
    hasWorkspaceInspectionProgress,
    hasRepairPanel,
    hasToolActivity,
    isDefaultCollapsedNode,
    step.status
  ])

  const className = cx(
    'process-step',
    step.kind,
    step.status,
    !expandable && 'static',
    hasChecks && 'has-checks',
    hasBuildRun && 'has-build-run',
    hasDagGeneration && 'has-dag-generation',
    hasProjectPlanUpdate && 'has-project-plan-update',
    hasWorkspaceInspection && 'has-workspace-inspection',
    hasWorkspaceInspectionProgress && 'has-workspace-inspection-progress',
    hasRepairPanel && 'has-repair-activity',
    hasRepairCompletion && 'repair-completed',
    isLast && 'last'
  )
  const summaryContent = (
    <>
      <span className={cx('process-step-icon')}>{stepIcon(step, settled)}</span>
      <Text>{processStepTitle(step, conversation, settled)}</Text>
    </>
  )

  if (
    isToolActivityStep(step) &&
    (step.nodeName === 'small_task_repair' || step.nodeName === 'unit_test_repair')
  ) {
    return (
      <RepairToolActivityRow
        icon={stepIcon(step, settled)}
        isLast={isLast}
        step={step}
        title={processStepTitle(step, conversation, settled)}
      />
    )
  }

  if (awaitingInput) {
    const clarificationText =
      step.detail.trim() ||
      waitingPrompt.trim() ||
      '请说明您想修改的具体内容，并补充修改位置和预期效果。'
    return (
      <div className={cx('process-step-clarification')}>
        <div className={cx('process-step-detail')}>
          <DetailBlock label="请补充输入" value={clarificationText} />
        </div>
      </div>
    )
  }

  if (!expandable) {
    return (
      <div className={className}>
        <div className={cx('process-step-summary')}>{summaryContent}</div>
      </div>
    )
  }

  return (
    <details
      className={className}
      onToggle={(event) => {
        if (event.target === event.currentTarget) setOpen(event.currentTarget.open)
      }}
      open={open}
    >
      <summary className={cx('process-step-summary')}>
        {summaryContent}
        <span className={cx('tool-activity-chain-toggle', 'process-step-toggle')}>
          <span>{open ? '收起详情' : '展开详情'}</span>
          {open ? <UpOutlined aria-hidden="true" /> : <DownOutlined aria-hidden="true" />}
        </span>
      </summary>
      <div className={cx('process-step-detail')}>
        {(!hasChecks || (step.nodeName === 'unit_test' && step.status === 'failed')) &&
          !hasDagGeneration &&
          !hasProjectPlanUpdate &&
          !hasWorkspaceInspection &&
          !hasWorkspaceInspectionProgress &&
          !hasRepairPanel &&
          step.detail && (
            <DetailBlock
              label={step.kind === 'reasoning' ? '思考内容' : '动作详情'}
              value={step.detail}
            />
          )}
        {hasRepairPanel && (
          <RepairInProgressPanel
            completed={hasRepairCompletion}
            detail={step.detail}
            executing={hasToolActivity}
          />
        )}
        {hasToolActivity && (
          <NodeToolActivityChain
            conversation={conversation}
            loading={!settled}
            steps={toolSteps}
            waitingForInput={waitingForInput}
            waitingPrompt={waitingPrompt}
          />
        )}
        {step.checks && <IntegrationTestChecklist checks={step.checks} />}
        {step.dagGeneration && !hideUnverifiedActiveDagSnapshot ? (
          <DagGenerationProgress
            completion={
              dagSnapshotMatchesPendingPlan
                ? {
                    summary: dagPendingPlanCompletion?.summary,
                    onGoToConfirmation: onOpenPendingDagConfirmation
                  }
                : undefined
            }
            snapshot={step.dagGeneration}
          />
        ) : null}
        {step.projectPlanUpdate && <ProjectPlanUpdatePanel update={step.projectPlanUpdate} />}
        {step.workspaceInspectionProgress && !step.workspaceInspection && (
          <WorkspaceInspectionProgressPanel progress={step.workspaceInspectionProgress} />
        )}
        {step.workspaceInspection && (
          <WorkspaceInspectionPanel snapshot={step.workspaceInspection} />
        )}
        {step.buildExecutionSlice && (
          <BuildExecutionRunCard executionSlice={step.buildExecutionSlice} status={step.status} />
        )}
        {step.result && <DetailBlock label="执行结果" value={step.result} />}
      </div>
    </details>
  )
}

/** 在所属流程节点的详情区渲染该节点自己的工具调用链。 */
function NodeToolActivityChain({
  conversation,
  loading,
  steps,
  waitingForInput,
  waitingPrompt
}: {
  conversation: boolean
  loading: boolean
  steps: ProcessStepRecord[]
  waitingForInput: boolean
  waitingPrompt: string
}): ReactElement {
  const latestStep =
    [...steps].reverse().find((tool) => tool.status === 'running') || steps[steps.length - 1]
  return (
    <ToolActivityChain
      isLast
      latestDetail={latestStep.detail}
      latestIcon={stepIcon(latestStep, !loading)}
      latestTitle={processStepTitle(latestStep, conversation, !loading)}
    >
      {steps.map((step, index) => (
        <ProcessStep
          conversation={conversation}
          isLast={index === steps.length - 1}
          key={step.id}
          settled={!loading}
          step={step}
          waitingForInput={waitingForInput}
          waitingPrompt={waitingPrompt}
        />
      ))}
    </ToolActivityChain>
  )
}

/** 展示代码图扫描期间的阶段、文件、符号和关系实时计数。 */
function WorkspaceInspectionProgressPanel({
  progress
}: {
  progress: WorkspaceInspectionProgress
}): ReactElement {
  const metrics = [
    { label: '发现文件', value: progress.filesDiscovered },
    { label: '已索引', value: progress.filesIndexed },
    { label: '符号', value: progress.symbolsIndexed },
    { label: '关系', value: progress.relationsIndexed }
  ]
  return (
    <section className={cx('workspace-inspection-progress')} aria-label="代码扫描进度">
      <div className={cx('workspace-inspection-progress-heading')}>
        <LoadingOutlined spin />
        <span>{progress.message || '正在扫描用户工作区代码…'}</span>
      </div>
      <div className={cx('workspace-inspection-progress-metrics')}>
        {metrics.map((metric) => (
          <span key={metric.label}>
            <strong>{metric.value.toLocaleString()}</strong>
            {metric.label}
          </span>
        ))}
      </div>
    </section>
  )
}

/** 渲染集成测试的实时检查清单，并在流程结束后保留最终状态。 */
function IntegrationTestChecklist({
  checks
}: {
  checks: IntegrationTestCheckRecord[]
}): ReactElement {
  const summary = testCheckSummary(checks)
  const counts = testCheckCounts(checks)
  const metrics: Array<{
    label: string
    status: IntegrationTestCheckRecord['status']
    value: number
  }> = [
    { label: '通过', status: 'passed', value: counts.passed },
    { label: '跳过', status: 'skipped', value: counts.skipped },
    { label: '失败', status: 'failed', value: counts.failed },
    { label: '运行', status: 'running', value: counts.running }
  ]

  /** 使用既有 Electron 安全能力在系统浏览器打开完整 Lighthouse HTML。 */
  const handleOpenReport = async (reportPath: string): Promise<void> => {
    try {
      await openLocalReportFile(reportPath)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '打开完整报告失败')
    }
  }

  return (
    <section
      aria-label={summary}
      aria-live="polite"
      aria-busy={counts.running > 0}
      className={cx('integration-test-checklist', counts.running > 0 ? 'running' : 'settled')}
    >
      <div className={cx('integration-test-checklist-header')}>
        <div className={cx('integration-test-checklist-identity')}>
          <span className={cx('integration-test-checklist-mark')}>
            <SafetyCertificateOutlined />
          </span>
          <span>
            <Text className={cx('integration-test-checklist-title')} strong>
              测试检查步骤
            </Text>
          </span>
        </div>
        <div className={cx('integration-test-checklist-metrics')}>
          {metrics.map((metric) => (
            <span
              className={cx('integration-test-checklist-metric', metric.status)}
              key={metric.status}
            >
              <strong>{metric.value}</strong>
              <small>{metric.label}</small>
            </span>
          ))}
        </div>
      </div>
      <div className={cx('integration-test-checklist-progress')} aria-hidden="true">
        {metrics
          .filter((metric) => metric.value > 0)
          .map((metric) => (
            <i
              className={cx(metric.status)}
              key={metric.status}
              style={{ flexGrow: metric.value }}
            />
          ))}
      </div>
      <ul>
        {checks.map((check, index) => {
          const reportPath = integrationTestCheckReportPath(check)
          return (
            <li className={cx('integration-test-check', check.status)} key={check.id}>
              <span className={cx('integration-test-check-index')}>
                {String(index + 1).padStart(2, '0')}
              </span>
              <span className={cx('integration-test-check-icon')}>
                {testCheckIcon(check.status)}
              </span>
              <div className={cx('integration-test-check-content')}>
                <span className={cx('integration-test-check-heading')}>
                  <Text>{check.name}</Text>
                </span>
                {testCheckCountLabel(check) && (
                  <Text type="secondary">{testCheckCountLabel(check)}</Text>
                )}
                {(check.status === 'running' ||
                  check.status === 'failed' ||
                  check.status === 'skipped') &&
                  check.evidence && <Text type="secondary">{check.evidence}</Text>}
                {check.status === 'running' && check.activeToolActivity && (
                  <WorkspaceToolActivity activity={check.activeToolActivity} />
                )}
              </div>
              <span className={cx('integration-test-check-actions')}>
                {reportPath && (
                  <Button
                    size="small"
                    className={cx('integration-test-check-report-button')}
                    onClick={() => void handleOpenReport(reportPath)}
                  >
                    打开完整报告
                  </Button>
                )}
                <Text className={cx('integration-test-check-status')}>
                  {testCheckStatusLabel(check.status)}
                </Text>
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

/** 返回当前执行步骤在总步骤中的位置与标题。 */
function currentStepLabel(steps: ProcessStepRecord[]): string {
  const activeIndex = steps.findIndex((step) => step.status === 'running')
  if (activeIndex < 0) return `正在准备 · ${steps.length} 个步骤`
  return `第 ${activeIndex + 1} / ${steps.length} 步 · ${processStepTitle(steps[activeIndex], false, false)}`
}

/** 选择当前自由对话的业务节点标题，工具调用只在执行详情中展示。 */
function conversationCurrentStageLabel(steps: ProcessStepRecord[]): string {
  const activeStage = [...steps]
    .reverse()
    .find((step) => step.kind === 'workflow' && step.status === 'running')
  return activeStage ? processStepTitle(activeStage, true, false) : '正在处理当前阶段'
}

/** 为自由对话把内部节点和工具步骤转换成用户可理解的实时状态。 */
function processStepTitle(
  step: ProcessStepRecord,
  conversation: boolean,
  settled: boolean
): string {
  if (!conversation) return settled ? settledTitle(step.title) : step.title
  if (isToolActivityStep(step)) {
    const action = step.kind === 'command' ? '执行' : '调用'
    const noun = step.kind === 'command' ? '命令' : '工具'
    if (step.status === 'failed') return `${action}失败 ${step.title} ${noun}`
    return step.status === 'running' && !settled
      ? `正在${action} ${step.title} ${noun}`
      : `已${action} ${step.title} ${noun}`
  }
  const labels: Record<string, string> = {
    classify_intent: '判断修改类型',
    respond_conversation: '生成回答',
    answer_workspace: '读取工作区并回答',
    execute_frontend: '修改前端文件',
    execute_backend: '修改后端文件',
    integration_test: '验证修改',
    validate_direct_fix: '验证本次修改',
    direct_modification_repair: '自动修复局部代码',
    unit_test: '执行单元测试',
    unit_test_repair: '修复单元测试失败',
    acceptance_phase_confirmation: '确认进入验收阶段',
    launch_project: '启动预览',
    acceptance_review: '等待用户验收',
    finalize_direct_modification: '整理结果'
  }
  const label =
    labels[step.nodeName || ''] || step.title.replace(/^正在执行\s*/, '').replace(/^已完成\s*/, '')
  if (step.status === 'failed') return `${label}失败`
  if (step.status === 'requires_user_input') return `等待确认 · ${label}`
  return step.status === 'running' && !settled ? `正在${label}` : `已完成${label}`
}

/** 渲染非结构化步骤的详情或执行结果。 */
function DetailBlock({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <section>
      <Text className={cx('process-step-detail-label')}>{label}</Text>
      <pre>{formatValue(value)}</pre>
    </section>
  )
}

/** 汇总已完成、通过、跳过和失败的集成测试检查数量。 */
function testCheckSummary(checks: IntegrationTestCheckRecord[]): string {
  const { passed, skipped, failed, running } = testCheckCounts(checks)
  const parts = [`已完成 ${checks.length - running}/${checks.length} 项`]
  if (passed) parts.push(`通过 ${passed} 项`)
  if (skipped) parts.push(`跳过 ${skipped} 项`)
  if (failed) parts.push(`失败 ${failed} 项`)
  if (running) parts.push(`进行中 ${running} 项`)
  return parts.join('，')
}

/** 按状态统计检查数量，供摘要、进度条和指标卡共同使用。 */
function testCheckCounts(
  checks: IntegrationTestCheckRecord[]
): Record<IntegrationTestCheckRecord['status'], number> {
  return checks.reduce<Record<IntegrationTestCheckRecord['status'], number>>(
    (counts, check) => ({ ...counts, [check.status]: counts[check.status] + 1 }),
    { running: 0, passed: 0, skipped: 0, failed: 0 }
  )
}

/** 根据检查状态返回与主题匹配的状态图标。 */
function testCheckIcon(status: IntegrationTestCheckRecord['status']): ReactElement {
  if (status === 'running') return <LoadingOutlined spin />
  if (status === 'passed') return <CheckCircleOutlined />
  if (status === 'skipped') return <MinusCircleOutlined />
  return <CloseCircleOutlined />
}

/** 返回检查状态对应的中文可见标签。 */
function testCheckStatusLabel(status: IntegrationTestCheckRecord['status']): string {
  if (status === 'running') return '检查中'
  if (status === 'passed') return '已通过'
  if (status === 'skipped') return '已跳过'
  return '未通过'
}

/** 返回单元测试检查项的通过数与总数，缺少结构化统计时不显示占位数字。 */
function testCheckCountLabel(check: IntegrationTestCheckRecord): string | undefined {
  if (
    check.passedTests === undefined ||
    check.totalTests === undefined ||
    check.totalTests < 0 ||
    check.passedTests < 0
  ) {
    return undefined
  }
  return `通过 ${Math.min(check.passedTests, check.totalTests)}/${check.totalTests} 个测试`
}

/** 根据步骤类型与终态选择时间线图标。 */
function stepIcon(step: ProcessStepRecord, settled: boolean): ReactElement {
  if (step.status === 'running' && !settled) return <LoadingOutlined spin />
  if (step.status === 'failed') return <CloseCircleOutlined />
  if (step.status === 'requires_user_input') return <PauseCircleOutlined />
  if (step.kind === 'reasoning') return <RobotOutlined />
  if (step.kind === 'command') return <CodeOutlined />
  if (step.kind === 'tool') return <ToolOutlined />
  return <CheckCircleOutlined />
}

/** 将实时步骤标题转换为完成态文案。 */
function settledTitle(title: string): string {
  return title
    .replace(/^正在思考/, '已思考')
    .replace(/^正在调用/, '已调用')
    .replace(/^正在执行/, '已执行')
}

/** 将 JSON 字符串格式化为便于阅读的详情，普通文本保持原样。 */
function formatValue(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}
