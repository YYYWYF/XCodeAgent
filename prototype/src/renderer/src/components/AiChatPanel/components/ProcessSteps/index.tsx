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
import type { ReactElement } from 'react'
import type { IntegrationTestCheckRecord, ProcessStepRecord } from '../../../../service/agUiAgent'
import type { WorkflowRunPayload } from '../../../../typings'
import { cx } from '../../../../utils'
import WorkflowRunCard, {
  type ClarificationAnswers,
  BuildExecutionRunCard,
  workflowClarification
} from '../WorkflowRunCard'
import type { WorkflowInteractionAvailability } from '../../planExecutionMode'
import DagGenerationProgress from './DagGenerationProgress'
import WorkspaceInspectionPanel from './WorkspaceInspectionPanel'
import './ProcessSteps.less'

const { Text } = Typography

type Props = {
  loading: boolean
  steps: ProcessStepRecord[]
  /** 当前消息对应的具体工作流名称。 */
  workflowTitle: string
  /** 开发工作流首节点的模板选择交互。 */
  inlineFirstNode?: ReactElement
  inlineFirstNodePending?: boolean
  /** 当前消息的 Workflow，用于把需要操作的节点交互嵌入节点轨迹。 */
  workflow?: WorkflowRunPayload
  interactionAvailability?: WorkflowInteractionAvailability
  interactionDisabled?: boolean
  onSubmitClarification?: (workflow: WorkflowRunPayload, answers: ClarificationAnswers) => void
  waitingPrompt?: string
  waitingForInput?: boolean
}

/** 渲染一条可折叠的 Agent 执行轨迹，并在测试步骤中保留结构化检查结果。 */
export default function ProcessSteps({
  loading,
  steps,
  workflowTitle,
  inlineFirstNode,
  inlineFirstNodePending = false,
  workflow,
  interactionAvailability = 'stale',
  interactionDisabled = false,
  onSubmitClarification,
  waitingPrompt = '',
  waitingForInput = false
}: Props): ReactElement {
  // 执行方式选择、用例授权与产物验收同构：交互卡内嵌在对应节点上，不再脱离流程轨迹单独渲染。
  const dispatchPending =
    workflowClarification(workflow)?.mode === 'background_dispatch' &&
    workflowClarification(workflow)?.status === 'requires_user_input'
  const statusClassName =
    loading ? 'running' : waitingForInput || inlineFirstNodePending || dispatchPending ? 'waiting' : 'completed'
  const testCaseAuthorizationPending =
    workflowClarification(workflow)?.mode === 'test_case_execute' &&
    workflowClarification(workflow)?.status === 'requires_user_input'
  const artifactAcceptancePending =
    workflowClarification(workflow)?.mode === 'page_acceptance' &&
    workflowClarification(workflow)?.status === 'requires_user_input'

  return (
    <div
      className={cx(
        'process-steps',
        statusClassName,
        inlineFirstNode && 'has-inline-first-node'
      )}
    >
      <div className={cx('process-steps-summary')}>
        <span className={cx('process-steps-status')}>
          {loading ? (
            <LoadingOutlined spin />
          ) : waitingForInput || inlineFirstNodePending || dispatchPending ? (
            <PauseCircleOutlined />
          ) : (
            <CheckCircleOutlined />
          )}
        </span>
        <span className={cx('process-steps-heading')}>
          <span className={cx('process-steps-title-row')}>
            <Text strong>{workflowTitle}</Text>
            <Text className={cx('process-steps-progress')}>
              {formatStepProgress(steps)}
            </Text>
          </span>
          {loading ? <Text type="secondary">{currentStepLabel(steps)}</Text> : null}
          {inlineFirstNodePending ? (
            <Text type="secondary">请选择页面模板后开始详细设计</Text>
          ) : dispatchPending ? (
            <Text type="secondary">请选择执行方式后继续</Text>
          ) : waitingForInput ? (
            <Text type="secondary">请根据下方提示补充修改需求</Text>
          ) : null}
        </span>
      </div>
      <div className={cx('process-steps-list')}>
        {steps.map((step, index) => (
          <ProcessStep
            isLast={index === steps.length - 1}
            key={step.id}
            settled={!loading}
            step={step}
            showTestCaseAuthorization={testCaseAuthorizationPending}
            showArtifactAcceptance={artifactAcceptancePending}
            showBackgroundDispatch={dispatchPending}
            inlineContent={index === 0 ? inlineFirstNode : undefined}
            workflow={workflow}
            interactionAvailability={interactionAvailability}
            interactionDisabled={interactionDisabled}
            onSubmitClarification={onSubmitClarification}
            waitingForInput={waitingForInput}
            waitingPrompt={waitingPrompt}
          />
        ))}
      </div>
    </div>
  )
}

/** 渲染单个 Agent 步骤，仅展示节点摘要；用户点击后再查看节点内部详情。 */
function ProcessStep({
  isLast,
  settled,
  step,
  inlineContent,
  showTestCaseAuthorization,
  showArtifactAcceptance,
  showBackgroundDispatch,
  workflow,
  interactionAvailability,
  interactionDisabled,
  onSubmitClarification,
  waitingForInput,
  waitingPrompt
}: {
  isLast: boolean
  settled: boolean
  step: ProcessStepRecord
  inlineContent?: ReactElement
  showTestCaseAuthorization: boolean
  showArtifactAcceptance: boolean
  showBackgroundDispatch: boolean
  workflow?: WorkflowRunPayload
  interactionAvailability: WorkflowInteractionAvailability
  interactionDisabled: boolean
  onSubmitClarification?: (workflow: WorkflowRunPayload, answers: ClarificationAnswers) => void
  waitingForInput: boolean
  waitingPrompt: string
}): ReactElement {
  const hasChecks = Boolean(step.checks?.length)
  const hasBuildRun = Boolean(step.buildExecutionSlice)
  const hasDagGeneration = Boolean(step.dagGeneration)
  const hasWorkspaceInspection = Boolean(step.workspaceInspection)
  const hasDetail = Boolean(step.detail.trim())
  const hasResult = Boolean(step.result?.trim())
  const expandable =
    hasDetail || hasResult || hasChecks || hasBuildRun || hasDagGeneration || hasWorkspaceInspection
  const awaitingInput = waitingForInput && step.status === 'requires_user_input'
  const awaitingTestCaseAuthorization =
    showTestCaseAuthorization && step.status === 'requires_user_input'
  const awaitingArtifactAcceptance =
    showArtifactAcceptance && step.status === 'requires_user_input'
  const awaitingBackgroundDispatch =
    showBackgroundDispatch && step.status === 'requires_user_input'

  const className = cx(
    'process-step',
    step.kind,
    step.status,
    !expandable && 'static',
    hasChecks && 'has-checks',
    hasBuildRun && 'has-build-run',
    hasDagGeneration && 'has-dag-generation',
    hasWorkspaceInspection && 'has-workspace-inspection',
    isLast && 'last'
  )
  const summaryContent = (
    <>
      <span className={cx('process-step-icon')}>{stepIcon(step, settled)}</span>
      <Text>{settled ? settledTitle(step.title) : step.title}</Text>
    </>
  )

  // 开发阶段的模板选择是工作流首节点内容，节点标题和卡片动作保持同一条时间线。
  if (inlineContent) {
    return (
      <div className={`${className} ${cx('process-step-interactive')}`}>
        <div className={cx('process-step-summary')}>{summaryContent}</div>
        <div className={cx('process-step-detail', 'process-step-interaction-detail')}>
          {inlineContent}
        </div>
      </div>
    )
  }

  // 用例授权是“确认执行用例”节点的动作，不再脱离流程轨迹单独渲染。
  if (awaitingTestCaseAuthorization && workflow) {
    return (
      <div className={`${className} ${cx('process-step-interactive')}`}>
        <div className={cx('process-step-summary')}>{summaryContent}</div>
        <div className={cx('process-step-detail', 'process-step-interaction-detail')}>
          <WorkflowRunCard
            embedded
            disabled={interactionDisabled}
            interactionAvailability={interactionAvailability}
            onSubmitClarification={onSubmitClarification}
            workflow={workflow}
          />
        </div>
      </div>
    )
  }

  // 产物验收是「确认验收」节点的动作，与用例授权同样内嵌在节点轨迹中。
  if (awaitingArtifactAcceptance && workflow) {
    return (
      <div className={`${className} ${cx('process-step-interactive')}`}>
        <div className={cx('process-step-summary')}>{summaryContent}</div>
        <div className={cx('process-step-detail', 'process-step-interaction-detail')}>
          <WorkflowRunCard
            embedded
            disabled={interactionDisabled}
            interactionAvailability={interactionAvailability}
            onSubmitClarification={onSubmitClarification}
            workflow={workflow}
          />
        </div>
      </div>
    )
  }

  // 执行方式选择是“选择执行方式”节点的动作，与用例授权同样内嵌在节点轨迹中。
  if (awaitingBackgroundDispatch && workflow) {
    return (
      <div className={`${className} ${cx('process-step-interactive')}`}>
        <div className={cx('process-step-summary')}>{summaryContent}</div>
        <div className={cx('process-step-detail', 'process-step-interaction-detail')}>
          <WorkflowRunCard
            embedded
            disabled={interactionDisabled}
            interactionAvailability={interactionAvailability}
            onSubmitClarification={onSubmitClarification}
            workflow={workflow}
          />
        </div>
      </div>
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
    <details className={className}>
      <summary className={cx('process-step-summary')}>{summaryContent}</summary>
      <div className={cx('process-step-detail')}>
        {!hasChecks && !hasDagGeneration && !hasWorkspaceInspection && step.detail && (
          <DetailBlock
            label={step.kind === 'reasoning' ? '思考内容' : undefined}
            value={step.detail}
          />
        )}
        {step.checks && <IntegrationTestChecklist checks={step.checks} />}
        {step.dagGeneration && <DagGenerationProgress snapshot={step.dagGeneration} />}
        {step.workspaceInspection && (
          <WorkspaceInspectionPanel snapshot={step.workspaceInspection} />
        )}
        {step.buildExecutionSlice && (
          <BuildExecutionRunCard
            executionSlice={step.buildExecutionSlice}
            status={step.status === 'pending' ? 'running' : step.status}
          />
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
      aria-busy={counts.running > 0}
      aria-live="polite"
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
              {testCheckCountLabel(check) && (
                <Text type="secondary">{testCheckCountLabel(check)}</Text>
              )}
              {(check.status === 'running' ||
                check.status === 'failed' ||
                check.status === 'skipped') &&
                check.evidence && <Text type="secondary">{check.evidence}</Text>}
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
  if (activeIndex >= 0) return steps[activeIndex].title
  const pendingIndex = steps.findIndex((step) => step.status === 'pending')
  return pendingIndex >= 0 ? `正在准备${steps[pendingIndex].title}` : '正在准备下一步'
}

/** 返回工作流当前节点与规划节点总数，折叠后也保留可读的进度信息。 */
function formatStepProgress(steps: ProcessStepRecord[]): string {
  if (!steps.length) return '0/0'
  const currentIndex = steps.findIndex(
    (step) =>
      step.status === 'pending' ||
      step.status === 'running' ||
      step.status === 'requires_user_input'
  )
  const current = currentIndex >= 0 ? currentIndex + 1 : steps.length
  const total = Math.max(steps.length, ...steps.map((step) => step.total || 0))
  return `${Math.min(current, total)}/${total}`
}

/** 渲染非结构化步骤的详情或执行结果。 */
function DetailBlock({ label, value }: { label?: string; value: string }): ReactElement {
  return (
    <section>
      {label ? <Text className={cx('process-step-detail-label')}>{label}</Text> : null}
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
  if (step.status === 'pending') return <MinusCircleOutlined />
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
