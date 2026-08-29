import { CloseCircleOutlined, RedoOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../utils'
import './AgentErrorCard.less'

const { Text } = Typography

type AgentErrorCardProps = {
  error?: string
  onRetry?: () => void
  retrying?: boolean
  title?: string
}

/** 按真实错误类型区分模型连接异常和普通任务失败，避免把所有失败误报为模型问题。 */
export default function AgentErrorCard({
  error,
  onRetry,
  retrying,
  title
}: AgentErrorCardProps): ReactElement {
  const copy = readableAgentError(error)
  const resolvedTitle = title || (copy.modelServiceError ? '模型服务异常' : '任务执行异常')

  return (
    <section
      aria-label={resolvedTitle}
      aria-live="assertive"
      className={cx('agent-error-card')}
      role="alert"
    >
      <span aria-hidden="true" className={cx('agent-error-card-icon')}>
        <CloseCircleOutlined />
      </span>
      <div className={cx('agent-error-card-copy')}>
        <Text className={cx('agent-error-card-title')} strong>
          {resolvedTitle}
        </Text>
        <Text className={cx('agent-error-card-message')}>{copy.message}</Text>
        <Text className={cx('agent-error-card-hint')} type="secondary">
          {copy.hint}
        </Text>
        {copy.detail ? (
          <Text className={cx('agent-error-card-detail')} type="secondary">
            错误详情：{copy.detail}
          </Text>
        ) : null}
        {onRetry ? (
          <Button
            className={cx('agent-error-card-retry')}
            icon={<RedoOutlined />}
            loading={retrying}
            onClick={onRetry}
            type="primary"
          >
            重试
          </Button>
        ) : null}
      </div>
    </section>
  )
}

/** 将连接异常和普通运行异常分别翻译成可操作提示，并保留原始详情。 */
function readableAgentError(error?: string): {
  message: string
  hint: string
  modelServiceError: boolean
  detail?: string
} {
  const normalized = error?.trim() || ''
  const isConnectionError =
    /failed to fetch|networkerror|load failed|fetch failed|econnrefused|econnreset|etimedout|网络请求失败|无法连接/i.test(
      normalized
    )
  if (isConnectionError) {
    return {
      message: '暂时无法连接模型服务。',
      hint: '请检查模型服务地址、密钥配置、后端状态和网络连接后重试。',
      modelServiceError: true,
      detail: normalized || undefined
    }
  }
  return {
    message: normalized || '任务没有返回有效结果。',
    hint: '请查看错误详情和相关执行记录后重试。',
    modelServiceError: false
  }
}
