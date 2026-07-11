import {
  CheckCircleOutlined,
  CodeOutlined,
  LoadingOutlined,
  RobotOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { ProcessStepRecord } from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import './ProcessSteps.less'

const { Text } = Typography

type Props = {
  loading: boolean
  steps: ProcessStepRecord[]
}

export default function ProcessSteps({ loading, steps }: Props): ReactElement {
  const [open, setOpen] = useState(loading)

  useEffect(() => {
    setOpen(loading)
  }, [loading])

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
        <Text strong>{loading ? '正在处理' : '已处理'}</Text>
        <Text type="secondary">· {steps.length} 个步骤</Text>
      </summary>
      <div className={cx('process-steps-list')}>
        {steps.map((step) => <ProcessStep key={step.id} settled={!loading} step={step} />)}
      </div>
    </details>
  )
}

function ProcessStep({ settled, step }: { settled: boolean; step: ProcessStepRecord }): ReactElement {
  return (
    <details className={cx('process-step', step.kind, step.status)}>
      <summary className={cx('process-step-summary')}>
        <span className={cx('process-step-icon')}>{stepIcon(step, settled)}</span>
        <Text>{settled ? settledTitle(step.title) : step.title}</Text>
      </summary>
      <div className={cx('process-step-detail')}>
        {step.detail && <DetailBlock label={step.kind === 'reasoning' ? '思考内容' : '动作详情'} value={step.detail} />}
        {step.result && <DetailBlock label="执行结果" value={step.result} />}
      </div>
    </details>
  )
}

function DetailBlock({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <section>
      <Text className={cx('process-step-detail-label')}>{label}</Text>
      <pre>{formatValue(value)}</pre>
    </section>
  )
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
