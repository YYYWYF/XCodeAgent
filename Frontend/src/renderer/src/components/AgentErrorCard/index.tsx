import { CloseCircleOutlined, RedoOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { RecoveryReadDiagnostic } from '../../typings'
import { cx } from '../../utils'
import ErrorDiagnostics from './ErrorDiagnostics'
import { errorDiagnosticText } from './diagnosticText'
import './AgentErrorCard.less'

const { Text } = Typography

type AgentErrorCardProps = {
  error?: string
  onRetry?: () => void
  retrying?: boolean
  retryDisabled?: boolean
  retryLabel?: string
  title?: string
  diagnostic?: RecoveryReadDiagnostic
}

/** 按真实错误类型区分模型连接异常和普通任务失败，避免把所有失败误报为模型问题。 */
export default function AgentErrorCard({
  error,
  onRetry,
  retrying,
  retryDisabled = false,
  retryLabel = '重试',
  title,
  diagnostic
}: AgentErrorCardProps): ReactElement {
  const copy = diagnostic
    ? {
        message: error?.trim() || diagnostic.reason,
        hint: '可复制诊断信息联系维护人员，或重新同步状态。',
        modelServiceError: false
      }
    : readableAgentError(error)
  const resolvedTitle = title || (copy?.modelServiceError ? '模型服务异常' : '任务执行异常')
  const diagnosticText = errorDiagnosticText(error?.trim() || copy.message, diagnostic)

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
        <Text className={cx('agent-error-card-message')}>{copy?.message}</Text>
        {copy?.hint ? (
          <Text className={cx('agent-error-card-hint')} type="secondary">
            {copy.hint}
          </Text>
        ) : null}
        <ErrorDiagnostics text={diagnosticText} showDetails={Boolean(diagnostic || copy.detail)} />
        {onRetry ? (
          <Button
            className={cx('agent-error-card-retry')}
            icon={<RedoOutlined />}
            loading={retrying}
            disabled={retryDisabled}
            onClick={onRetry}
            type="primary"
          >
            {retryLabel}
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
  if (normalized.startsWith('无权修改（超出职责范围）：')) {
    return {
      message: normalized,
      hint: '二次修改只允许写入 frontend/ 或 backend/；本次目标超出范围，已在执行前拒绝，未写入文件。',
      modelServiceError: false
    }
  }
  return {
    message: normalized || '任务没有返回有效结果。',
    hint: '可复制诊断信息联系维护人员，或查看相关执行记录。',
    modelServiceError: false
  }
}
