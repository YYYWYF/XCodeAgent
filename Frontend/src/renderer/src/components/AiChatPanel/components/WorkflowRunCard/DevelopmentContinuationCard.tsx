import { ArrowRightOutlined, CheckCircleOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { ChatSessionDevelopmentContinuation } from '../../../../service/chatSessions'
import { cx } from '../../../../utils'

const { Text } = Typography

type DevelopmentContinuationCardProps = {
  continuation: ChatSessionDevelopmentContinuation
  disabled?: boolean
  onContinue: () => void
}

/** 提示实体前置已完成，并让用户在当前会话显式恢复原页面或 Endpoint 开发。 */
export default function DevelopmentContinuationCard({
  continuation,
  disabled,
  onContinue
}: DevelopmentContinuationCardProps): ReactElement {
  const targetKind = continuation.target.type === 'page' ? '页面' : '接口'
  const started = continuation.status === 'started'
  return (
    <div className={cx('workflow-development-continuation-card')}>
      <span className={cx('workflow-development-continuation-icon')} aria-hidden="true">
        <CheckCircleOutlined />
      </span>
      <div className={cx('workflow-development-continuation-copy')}>
        <Text strong>实体设计已完成</Text>
        <Text type="secondary">
          前置条件已经更新，可以继续开发{targetKind}「{continuation.target.label}」。
        </Text>
      </div>
      <Button
        disabled={disabled || started}
        icon={<ArrowRightOutlined />}
        onClick={onContinue}
        type="primary"
      >
        {started ? '已继续开发' : `继续开发${targetKind}`}
      </Button>
    </div>
  )
}
