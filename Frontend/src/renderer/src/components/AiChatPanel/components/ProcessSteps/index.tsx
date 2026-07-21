import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  LoadingOutlined,
  MinusCircleOutlined,
  RobotOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { IntegrationTestCheckRecord, ProcessStepRecord } from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import './ProcessSteps.less'

const { Text } = Typography

type Props = {
  loading: boolean
  steps: ProcessStepRecord[]
}

export default function ProcessSteps({ loading, steps }: Props): ReactElement {
  const [open, setOpen] = useState(loading)
  const hasTestChecklist = steps.some((step) => Boolean(step.checks?.length))

  useEffect(() => {
    if (loading || hasTestChecklist) setOpen(true)
  }, [hasTestChecklist, loading])

  return (
    <details
      className={cx('process-steps', loading ? 'running' : 'completed')}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      open={open}
    >
      <summary className={cx('process-steps-summary')}>
        <span className={cx('process-steps-status')}>
          {loading ? <LoadingOutlined spin /> : <CheckCircleOutlined />}
        </span>
        <span className={cx('process-steps-heading')}>
          <Text strong>{loading ? 'Agent 正在执行' : '任务已完成'}</Text>
          <Text type="secondary">
            {loading ? currentStepLabel(steps) : `${steps.length} 个步骤`}
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
          />
        ))}
      </div>
    </details>
  )
}

function ProcessStep({
  isLast,
  settled,
  step
}: {
  isLast: boolean
  settled: boolean
  step: ProcessStepRecord
}): ReactElement {
  const hasChecks = Boolean(step.checks?.length)
  const [open, setOpen] = useState(step.status === 'running' || hasChecks)

  useEffect(() => {
    if (step.status === 'running' || hasChecks) setOpen(true)
  }, [hasChecks, step.status])

  return (
    <details
      className={cx(
        'process-step',
        step.kind,
        step.status,
        hasChecks && 'has-checks',
        isLast && 'last'
      )}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      open={open}
    >
      <summary className={cx('process-step-summary')}>
        <span className={cx('process-step-icon')}>{stepIcon(step, settled)}</span>
        <Text>{settled ? settledTitle(step.title) : step.title}</Text>
      </summary>
      <div className={cx('process-step-detail')}>
        {!hasChecks && step.detail && (
          <DetailBlock
            label={step.kind === 'reasoning' ? '思考内容' : '动作详情'}
            value={step.detail}
          />
        )}
        {step.checks && <IntegrationTestChecklist checks={step.checks} />}
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

  return (
    <section className={cx('integration-test-checklist')}>
      <div className={cx('integration-test-checklist-header')}>
        <Text className={cx('process-step-detail-label')}>检查结果</Text>
        <Text type="secondary">{summary}</Text>
      </div>
      <ul>
        {checks.map((check) => (
          <li className={cx('integration-test-check', check.status)} key={check.id}>
            <span className={cx('integration-test-check-icon')}>{testCheckIcon(check.status)}</span>
            <span className={cx('integration-test-check-content')}>
              <Text>{check.name}</Text>
              {check.status === 'failed' && check.evidence && (
                <Text type="secondary">{check.evidence}</Text>
              )}
            </span>
            <Text className={cx('integration-test-check-status')} type="secondary">
              {testCheckStatusLabel(check.status)}
            </Text>
          </li>
        ))}
      </ul>
    </section>
  )
}

function currentStepLabel(steps: ProcessStepRecord[]): string {
  const activeIndex = steps.findIndex((step) => step.status === 'running')
  if (activeIndex < 0) return `正在准备 · ${steps.length} 个步骤`
  return `第 ${activeIndex + 1} / ${steps.length} 步 · ${steps[activeIndex].title}`
}

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
  const count = (status: IntegrationTestCheckRecord['status']): number =>
    checks.filter((check) => check.status === status).length
  const passed = count('passed')
  const skipped = count('skipped')
  const failed = count('failed')
  const running = count('running')
  const parts = [`已完成 ${checks.length - running}/${checks.length} 项`]
  if (passed) parts.push(`通过 ${passed} 项`)
  if (skipped) parts.push(`跳过 ${skipped} 项`)
  if (failed) parts.push(`失败 ${failed} 项`)
  if (running) parts.push(`进行中 ${running} 项`)
  return parts.join('，')
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

function stepIcon(step: ProcessStepRecord, settled: boolean): ReactElement {
  if (step.status === 'running' && !settled) return <LoadingOutlined spin />
  if (step.kind === 'reasoning') return <RobotOutlined />
  if (step.kind === 'command') return <CodeOutlined />
  if (step.kind === 'tool') return <ToolOutlined />
  return <CheckCircleOutlined />
}

function settledTitle(title: string): string {
  return title
    .replace(/^正在思考/, '已思考')
    .replace(/^正在调用/, '已调用')
    .replace(/^正在执行/, '已执行')
}

function formatValue(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}
