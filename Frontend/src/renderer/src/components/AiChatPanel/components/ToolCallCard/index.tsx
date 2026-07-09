import { CheckCircleOutlined, LoadingOutlined, ToolOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import type { ToolCallRecord } from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import './ToolCallCard.less'

const { Text } = Typography

type Props = {
  toolCall: ToolCallRecord
}

export default function ToolCallCard({ toolCall }: Props): ReactElement {
  const completed = toolCall.status === 'completed'
  const title = completed
    ? `已调用 ${toolCall.name} 工具`
    : `调用 ${toolCall.name} 工具中`

  return (
    <details className={cx('tool-call-card', completed && 'completed')}>
      <summary className={cx('tool-call-summary')}>
        <span className={cx('tool-call-icon')}>
          {completed ? <CheckCircleOutlined /> : <LoadingOutlined />}
        </span>
        <Text strong>{title}</Text>
      </summary>
      <div className={cx('tool-call-body')}>
        <ToolCallSection title="入参" value={toolCall.args} empty="暂无入参" />
        {completed && (
          <ToolCallSection title="结果" value={toolCall.result} empty="工具未返回结果内容" />
        )}
      </div>
    </details>
  )
}

function ToolCallSection({
  title,
  value,
  empty
}: {
  title: string
  value?: string
  empty: string
}): ReactElement {
  return (
    <section className={cx('tool-call-section')}>
      <Text className={cx('tool-call-section-title')}>
        <ToolOutlined /> {title}
      </Text>
      <pre className={cx('tool-call-content')}>{formatToolCallValue(value) || empty}</pre>
    </section>
  )
}

function formatToolCallValue(value?: string): string {
  const trimmedValue = value?.trim()
  if (!trimmedValue) return ''

  try {
    return JSON.stringify(JSON.parse(trimmedValue), null, 2)
  } catch {
    return trimmedValue
  }
}
