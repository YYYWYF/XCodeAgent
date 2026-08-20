import { CloseCircleOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
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

/** 把模型连接失败、运行失败和后端返回的异常统一呈现为可理解的错误卡片。 */
export default function AgentErrorCard({
  error,
  title = '模型服务异常'
}: AgentErrorCardProps): ReactElement {
  const copy = readableAgentError(error)

  return (
    <section
      aria-label={title}
      aria-live="assertive"
      className={cx('agent-error-card')}
      role="alert"
    >
      <span aria-hidden="true" className={cx('agent-error-card-icon')}>
        <CloseCircleOutlined />
      </span>
      <div className={cx('agent-error-card-copy')}>
        <Text className={cx('agent-error-card-title')} strong>
          {title}
        </Text>
        <Text className={cx('agent-error-card-message')}>{copy.message}</Text>
        <Text className={cx('agent-error-card-hint')} type="secondary">
          请检查模型服务地址、密钥配置、后端状态和网络连接后重试。
        </Text>
        {copy.detail ? (
          <Text className={cx('agent-error-card-detail')} type="secondary">
            错误详情：{copy.detail}
          </Text>
        ) : null}
      </div>
    </section>
  )
}

/** 将浏览器网络层的笼统异常翻译成用户能采取行动的提示，并保留原始详情。 */
function readableAgentError(error?: string): { message: string; detail?: string } {
  const normalized = error?.trim() || ''
  const isConnectionError = /failed to fetch|networkerror|load failed|fetch failed|econnrefused|econnreset|etimedout|网络请求失败|无法连接/i.test(
    normalized
  )
  if (isConnectionError) {
    return {
      message: '暂时无法连接模型服务。',
      detail: normalized || undefined
    }
  }
  return {
    message: normalized || '模型服务没有返回有效结果。'
  }
}
