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
import { Button, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  IntegrationTestCheckRecord,
  ProcessStepRecord,
  WorkspaceInspectionProgress
} from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import { openLocalReportFile } from '../../../../utils/reportFile'
import { BuildExecutionRunCard } from '../WorkflowRunCard'
import DagGenerationProgress from './DagGenerationProgress'
import ProjectPlanUpdatePanel from './ProjectPlanUpdatePanel'
import ToolActivityChain from './ToolActivityChain'
import WorkspaceInspectionPanel from './WorkspaceInspectionPanel'
import './ProcessSteps.less'

const { Text } = Typography

type Props = {
  conversation?: boolean
  loading: boolean
  steps: ProcessStepRecord[]
  waitingPrompt?: string
  waitingForInput?: boolean
}

type ProcessDisplayItem =
  | { kind: 'step'; step: ProcessStepRecord }
  | { kind: 'tool-chain'; steps: ProcessStepRecord[] }

/** 渲染一条可折叠的 Agent 执行轨迹，并在测试步骤中保留结构化检查结果。 */
export default function ProcessSteps({
  conversation = false,
  loading,
  steps,
  waitingPrompt = '',
  waitingForInput = false
}: Props): ReactElement {
  const hasTestChecklist = steps.some((step) => Boolean(step.checks?.length))
  const hasProjectPlanUpdate = steps.some((step) => Boolean(step.projectPlanUpdate))
  const hasWorkspaceInspection = steps.some((step) => Boolean(step.workspaceInspection))
  const displayItems = buildProcessDisplayItems(steps, conversation)
  const toolActivityCount = conversation
    ? steps.filter((step) => isToolActivityStep(step)).length
    : 0
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
          <Text type="secondary">
            {loading
              ? currentStepLabel(steps, conversation)
              : waitingForInput
                ? '请根据下方提示补充修改需求'
                : conversation
                  ? toolActivityCount > 0
                    ? `${toolActivityCount} 次工具调用 · 可展开查看调用链`
                    : `已展示 ${steps.length} 个过程步骤`
                  : `已归档 ${steps.length} 个步骤`}
          </Text>
        </span>
      </summary>
      <div className={cx('process-steps-list')}>
        {displayItems.map((item, index) => {
          if (item.kind === 'tool-chain') {
            const latestStep = item.steps[item.steps.length - 1]
            return (
              <ToolActivityChain
                count={item.steps.length}
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
              waitingForInput={waitingForInput}
              waitingPrompt={waitingPrompt}
            />
          )
        })}
      </div>
    </details>
  )
}

/** 为自由对话把连续工具步骤折叠成一个调用链，其余阶段保持原有时间线。 */
function buildProcessDisplayItems(
  steps: ProcessStepRecord[],
  conversation: boolean
): ProcessDisplayItem[] {
  if (!conversation) return steps.map((step) => ({ kind: 'step', step }))

  const toolSteps = steps.filter((step) => isToolActivityStep(step))
  if (!toolSteps.length) return steps.map((step) => ({ kind: 'step', step }))

  let toolChainAdded = false
  const items: ProcessDisplayItem[] = []
  for (const step of steps) {
    if (isToolActivityStep(step)) {
      if (!toolChainAdded) {
        items.push({ kind: 'tool-chain', steps: toolSteps })
        toolChainAdded = true
      }
      continue
    }
    items.push({ kind: 'step', step })
  }
  return items
}

/** 渲染单个 Agent 步骤，仅让包含实际详情的步骤具备展开交互。 */
function ProcessStep({
  conversation,
  isLast,
  settled,
  step,
  waitingForInput,
  waitingPrompt
}: {
  conversation: boolean
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
  const hasWorkspaceInspectionProgress = Boolean(step.workspaceInspectionProgress)
  const hasDetail = Boolean(step.detail.trim())
  const hasResult = Boolean(step.result?.trim())
  const expandable =
    hasDetail ||
    hasResult ||
    hasChecks ||
    hasBuildRun ||
    hasDagGeneration ||
    hasProjectPlanUpdate ||
    hasWorkspaceInspection ||
    hasWorkspaceInspectionProgress
  const awaitingInput = waitingForInput && step.status === 'requires_user_input'
  const [open, setOpen] = useState(
    expandable &&
      (step.status === 'running' ||
        awaitingInput ||
        hasChecks ||
        hasBuildRun ||
        hasDagGeneration ||
        hasProjectPlanUpdate ||
        hasWorkspaceInspection ||
        hasWorkspaceInspectionProgress)
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
        hasWorkspaceInspection ||
        hasWorkspaceInspectionProgress)
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
    hasWorkspaceInspectionProgress,
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
    isLast && 'last'
  )
  const summaryContent = (
    <>
      <span className={cx('process-step-icon')}>{stepIcon(step, settled)}</span>
      <Text>{processStepTitle(step, conversation, settled)}</Text>
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
          !hasWorkspaceInspectionProgress &&
          step.detail && (
            <DetailBlock
              label={step.kind === 'reasoning' ? '思考内容' : '动作详情'}
              value={step.detail}
            />
          )}
        {step.checks && <IntegrationTestChecklist checks={step.checks} />}
        {step.checks && <FrontendPerformanceReport checks={step.checks} />}
        {step.dagGeneration && <DagGenerationProgress snapshot={step.dagGeneration} />}
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

/** 渲染 Lighthouse 前端性能报告的指标卡和完整报告打开入口。 */
function FrontendPerformanceReport({
  checks
}: {
  checks: IntegrationTestCheckRecord[]
}): ReactElement | null {
  const performanceCheck = checks.find(
    (check) =>
      check.id === 'frontend_performance' &&
      (Boolean(check.performanceScores) ||
        Boolean(check.performanceMetrics) ||
        Boolean(check.reportPath))
  )
  if (!performanceCheck) return null
  const scores = performanceCheck.performanceScores
  const metrics = performanceCheck.performanceMetrics
  const scoreEntries = scores
    ? [
        { key: 'performance', label: '性能', value: scores.performance },
        { key: 'accessibility', label: '可访问性', value: scores.accessibility },
        { key: 'bestPractices', label: '最佳实践', value: scores.bestPractices },
        { key: 'seo', label: 'SEO', value: scores.seo }
      ].filter((entry): entry is { key: string; label: string; value: number } =>
        typeof entry.value === 'number'
      )
    : []
  const metricEntries = metrics
    ? [
        { key: 'lcp', label: 'LCP', value: metrics.lcp, unit: 'ms' },
        { key: 'fcp', label: 'FCP', value: metrics.fcp, unit: 'ms' },
        { key: 'tbt', label: 'TBT', value: metrics.tbt, unit: 'ms' },
        { key: 'cls', label: 'CLS', value: metrics.cls, unit: 'value' },
        { key: 'si', label: 'SI', value: metrics.si, unit: 'ms' }
      ].filter(
        (entry): entry is { key: string; label: string; value: number; unit: string } =>
          typeof entry.value === 'number'
      )
    : []

  const handleOpenReport = async (): Promise<void> => {
    if (!performanceCheck.reportPath) {
      message.warning('尚未生成可打开的完整报告')
      return
    }
    try {
      await openLocalReportFile(performanceCheck.reportPath)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '打开完整报告失败')
    }
  }

  return (
    <section className={cx('frontend-performance-report')}>
      <div className={cx('frontend-performance-report-header')}>
        <span>
          <Text className={cx('frontend-performance-report-eyebrow')} strong>
            LIGHTHOUSE REPORT
          </Text>
          <Text className={cx('frontend-performance-report-title')} strong>
            前端性能报告
          </Text>
        </span>
        <Text type="secondary" className={cx('frontend-performance-report-advisory')}>
          仅报告，不影响质量门禁
        </Text>
      </div>
      {scoreEntries.length > 0 && (
        <div className={cx('frontend-performance-report-scores')}>
          {scoreEntries.map((entry) => (
            <div className={cx('frontend-performance-score')} key={entry.key}>
              <span className={cx('frontend-performance-score-label')}>
                <Text>{entry.label}</Text>
                <Text strong>{entry.value}</Text>
              </span>
              <span className={cx('frontend-performance-score-track')}>
                <i
                  className={cx(
                    'frontend-performance-score-fill',
                    scoreTone(entry.value)
                  )}
                  style={{ width: `${Math.max(0, Math.min(100, entry.value))}%` }}
                />
              </span>
            </div>
          ))}
        </div>
      )}
      {metricEntries.length > 0 && (
        <div className={cx('frontend-performance-report-metrics')}>
          {metricEntries.map((entry) => (
            <span className={cx('frontend-performance-metric')} key={entry.key}>
              <small>{entry.label}</small>
              <strong>
                {entry.unit === 'ms'
                  ? `${(entry.value / 1000).toFixed(2)}s`
                  : entry.value.toFixed(3)}
              </strong>
            </span>
          ))}
        </div>
      )}
      {performanceCheck.reportPath && (
        <div className={cx('frontend-performance-report-actions')}>
          <Button size="small" onClick={handleOpenReport}>
            打开完整报告
          </Button>
        </div>
      )}
    </section>
  )
}

/** 返回 Lighthouse 得分对应的视觉状态类。 */
function scoreTone(value: number): string {
  if (value >= 90) return 'good'
  if (value >= 50) return 'warn'
  return 'bad'
}

/** 返回当前执行步骤在总步骤中的位置与标题。 */
function currentStepLabel(steps: ProcessStepRecord[], conversation: boolean): string {
  const activeTool = [...steps]
    .reverse()
    .find((step) => isToolActivityStep(step) && step.status === 'running')
  if (conversation && activeTool) {
    const toolCount = steps.filter((step) => isToolActivityStep(step)).length
    return `${processStepTitle(activeTool, true, false)} · 已记录 ${toolCount} 次调用`
  }

  const activeIndex = steps.findIndex((step) => step.status === 'running')
  if (activeIndex < 0) return `正在准备 · ${steps.length} 个步骤`
  return `第 ${activeIndex + 1} / ${steps.length} 步 · ${processStepTitle(steps[activeIndex], conversation, false)}`
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
    classify_intent: '准备回答',
    respond_conversation: '生成回答',
    answer_workspace: '读取工作区并回答',
    execute_frontend: '修改前端文件',
    execute_backend: '修改后端文件',
    execute_workspace: '修改工作区文件',
    integration_test: '验证修改',
    launch_project: '启动预览',
    finalize_direct_modification: '整理结果'
  }
  const label =
    labels[step.nodeName || ''] || step.title.replace(/^正在执行\s*/, '').replace(/^已完成\s*/, '')
  return step.status === 'running' && !settled ? `正在${label}` : `已完成${label}`
}

/** 判断自由对话中需要合并展示的工具和命令步骤。 */
function isToolActivityStep(step: ProcessStepRecord): boolean {
  return step.kind === 'tool' || step.kind === 'command'
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
