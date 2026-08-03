import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  LoadingOutlined,
  MinusCircleOutlined,
  PauseCircleOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { IntegrationTestCheckRecord, ProcessStepRecord } from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import { BuildExecutionRunCard } from '../WorkflowRunCard'
import DagGenerationProgress from './DagGenerationProgress'
import ProjectPlanUpdatePanel from './ProjectPlanUpdatePanel'
import WorkspaceInspectionPanel from './WorkspaceInspectionPanel'
import './ProcessSteps.less'

const { Text } = Typography

type Props = {
  loading: boolean
  steps: ProcessStepRecord[]
  waitingPrompt?: string
  waitingForInput?: boolean
}

/** 渲染一条可折叠的 Agent 执行轨迹，并在测试步骤中保留结构化检查结果。 */
export default function ProcessSteps({
  loading,
  steps,
  waitingPrompt = '',
  waitingForInput = false
}: Props): ReactElement {
  const hasTestChecklist = steps.some((step) => Boolean(step.checks?.length))
  const hasProjectPlanUpdate = steps.some((step) => Boolean(step.projectPlanUpdate))
  const hasWorkspaceInspection = steps.some((step) => Boolean(step.workspaceInspection))
  const [open, setOpen] = useState(
    loading || waitingForInput || hasProjectPlanUpdate || hasWorkspaceInspection
  )

  useEffect(() => {
    if (
      loading ||
      waitingForInput ||
      hasTestChecklist ||
      hasProjectPlanUpdate ||
      hasWorkspaceInspection
    ) {
      setOpen(true)
    }
  }, [hasProjectPlanUpdate, hasTestChecklist, hasWorkspaceInspection, loading, waitingForInput])

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
            {loading ? 'Agent 正在执行' : waitingForInput ? 'Agent 等待补充' : 'Agent 执行完成'}
          </Text>
          <Text type="secondary">
            {loading
              ? currentStepLabel(steps)
              : waitingForInput
                ? '请根据下方提示补充修改需求'
                : `已归档 ${steps.length} 个步骤`}
          </Text>
        </span>
      </summary>
      <div className={cx('process-steps-list')}>
        {steps.map((step, index) => (
          <ProcessStep
            isLast={index === steps.length - 1}
            key={step.id}
            settled={!loading}
            step={step}
            waitingForInput={waitingForInput}
            waitingPrompt={waitingPrompt}
          />
        ))}
      </div>
    </details>
  )
}

/** 渲染单个 Agent 步骤，仅让包含实际详情的步骤具备展开交互。 */
function ProcessStep({
  isLast,
  settled,
  step,
  waitingForInput,
  waitingPrompt
}: {
  isLast: boolean
  settled: boolean
  step: ProcessStepRecord
  waitingForInput: boolean
  waitingPrompt: string
}): ReactElement {
  const hasChecks = Boolean(step.checks?.length)
  const hasBuildRun = Boolean(step.buildExecutionSlice)
  const hasDagGeneration = Boolean(step.dagGeneration)
  const hasProjectPlanUpdate = Boolean(step.projectPlanUpdate)
  const hasWorkspaceInspection = Boolean(step.workspaceInspection)
  const hasDetail = Boolean(step.detail.trim())
  const hasResult = Boolean(step.result?.trim())
  const expandable =
    hasDetail ||
    hasResult ||
    hasChecks ||
    hasBuildRun ||
    hasDagGeneration ||
    hasProjectPlanUpdate ||
    hasWorkspaceInspection
  const awaitingInput = waitingForInput && step.status === 'requires_user_input'
  const [open, setOpen] = useState(
    expandable &&
      (step.status === 'running' ||
        awaitingInput ||
        hasChecks ||
        hasBuildRun ||
        hasDagGeneration ||
        hasProjectPlanUpdate ||
        hasWorkspaceInspection)
  )

  useEffect(() => {
    if (
      expandable &&
      (step.status === 'running' ||
        awaitingInput ||
        hasChecks ||
        hasBuildRun ||
        hasDagGeneration ||
        hasProjectPlanUpdate ||
        hasWorkspaceInspection)
    ) {
      setOpen(true)
    }
  }, [
    awaitingInput,
    expandable,
    hasBuildRun,
    hasChecks,
    hasDagGeneration,
    hasProjectPlanUpdate,
    hasWorkspaceInspection,
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
    isLast && 'last'
  )
  const summaryContent = (
    <>
      <span className={cx('process-step-icon')}>{stepIcon(step, settled)}</span>
      <Text>{settled ? settledTitle(step.title) : step.title}</Text>
    </>
  )

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
      onToggle={(event) => setOpen(event.currentTarget.open)}
      open={open}
    >
      <summary className={cx('process-step-summary')}>{summaryContent}</summary>
      <div className={cx('process-step-detail')}>
        {!hasChecks &&
          !hasDagGeneration &&
          !hasProjectPlanUpdate &&
          !hasWorkspaceInspection &&
          step.detail && (
            <DetailBlock
              label={step.kind === 'reasoning' ? '思考内容' : '动作详情'}
              value={step.detail}
            />
          )}
        {step.checks && <IntegrationTestChecklist checks={step.checks} />}
        {step.dagGeneration && <DagGenerationProgress snapshot={step.dagGeneration} />}
        {step.projectPlanUpdate && <ProjectPlanUpdatePanel update={step.projectPlanUpdate} />}
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

  return (
    <section
      aria-label={summary}
      className={cx('integration-test-checklist', counts.running > 0 ? 'running' : 'settled')}
    >
      <div className={cx('integration-test-checklist-header')}>
        <div className={cx('integration-test-checklist-identity')}>
          <span className={cx('integration-test-checklist-mark')}>
            <SafetyCertificateOutlined />
          </span>
          <span>
            <Text className={cx('integration-test-checklist-eyebrow')}>QUALITY GATE</Text>
            <Text className={cx('integration-test-checklist-title')} strong>
              集成检查矩阵
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
        {checks.map((check, index) => (
          <li className={cx('integration-test-check', check.status)} key={check.id}>
            <span className={cx('integration-test-check-index')}>
              {String(index + 1).padStart(2, '0')}
            </span>
            <span className={cx('integration-test-check-icon')}>{testCheckIcon(check.status)}</span>
            <span className={cx('integration-test-check-content')}>
              <span className={cx('integration-test-check-heading')}>
                <Text>{check.name}</Text>
                <span className={cx('integration-test-check-scope')}>
                  {check.required ? 'REQUIRED' : 'OPTIONAL'}
                </span>
              </span>
              {(check.status === 'failed' || check.status === 'skipped') && check.evidence && (
                <Text type="secondary">{check.evidence}</Text>
              )}
            </span>
            <Text className={cx('integration-test-check-status')}>
              {testCheckStatusLabel(check.status)}
            </Text>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** 返回当前执行步骤在总步骤中的位置与标题。 */
function currentStepLabel(steps: ProcessStepRecord[]): string {
  const activeIndex = steps.findIndex((step) => step.status === 'running')
  if (activeIndex < 0) return `正在准备 · ${steps.length} 个步骤`
  return `第 ${activeIndex + 1} / ${steps.length} 步 · ${steps[activeIndex].title}`
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
