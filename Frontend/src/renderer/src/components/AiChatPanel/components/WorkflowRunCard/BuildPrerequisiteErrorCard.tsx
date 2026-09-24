import { Alert, Button, Space, Typography } from 'antd'
import type { ReactElement } from 'react'

const { Text } = Typography

type BuildPrerequisiteErrorCardProps = {
  disabled: boolean
  errors: string[]
  message?: string
  recommendedAction?: string
  onRetry: () => void
}

/** 展示可操作的 Build 前置错误，并在上游修复后重新执行同一门禁。 */
export default function BuildPrerequisiteErrorCard({
  disabled,
  errors,
  message,
  recommendedAction,
  onRetry
}: BuildPrerequisiteErrorCardProps): ReactElement {
  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Alert
        description={
          errors.length > 0 ? (
            <ul style={{ margin: 0, paddingInlineStart: 20 }}>
              {errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          ) : undefined
        }
        message={message || 'Build DAG 前置条件未满足。'}
        showIcon
        type="warning"
      />
      {recommendedAction ? <Text type="secondary">{recommendedAction}</Text> : null}
      <Button disabled={disabled} onClick={onRetry} type="primary">
        重新检查并继续
      </Button>
    </Space>
  )
}
