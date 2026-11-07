import { CheckOutlined, CloseCircleOutlined, CopyOutlined, RedoOutlined } from '@ant-design/icons'
import { Button, Tooltip, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { RecoveryReadDiagnostic } from '../../typings'
import { cx } from '../../utils'
import ErrorDiagnostics from './ErrorDiagnostics'
import { errorDiagnosticText } from './diagnosticText'
import { useDiagnosticClipboard } from './useDiagnosticClipboard'
import './AgentErrorCard.less'

const { Text } = Typography

type AgentErrorCardProps = {
  error?: string
  details?: string
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
  details,
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
  const diagnosticText = errorDiagnosticText(details?.trim() || error?.trim() || copy.message, diagnostic)
  const { copied, copyFailed, copying, copyDiagnostic } = useDiagnosticClipboard(diagnosticText)
  const copyLabel = copied ? '已复制' : '复制诊断信息'
  const showDetails = Boolean(diagnostic || details?.trim() || copy.detail)

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
        <div className={cx('agent-error-card-header')}>
          <Text className={cx('agent-error-card-title')} strong>
            {resolvedTitle}
          </Text>
          <Tooltip title={copyLabel}>
            <Button
              type="text"
              size="small"
              className={cx('agent-error-card-copy-button')}
              aria-label={copyLabel}
              loading={copying}
              icon={copied ? <CheckOutlined /> : <CopyOutlined />}
              onClick={() => {
                // 标题旁复制按钮沿用当前可见诊断文本和原生剪贴板反馈。
                void copyDiagnostic()
              }}
            />
          </Tooltip>
        </div>
        <Text className={cx('agent-error-card-message')}>{copy?.message}</Text>
        {copy?.hint ? (
          <Text className={cx('agent-error-card-hint')} type="secondary">
            {copy.hint}
          </Text>
        ) : null}
        {showDetails || copyFailed || onRetry ? <div className={cx('agent-error-card-actions')}>
          <ErrorDiagnostics
            text={diagnosticText}
            showDetails={showDetails}
            copyFailed={copyFailed}
          />
          {onRetry ? (
            <Button
              className={cx('agent-error-card-retry')}
              icon={<RedoOutlined />}
              loading={retrying}
              disabled={retryDisabled}
              onClick={onRetry}
              type="default"
              danger
            >
              {retryLabel}
            </Button>
          ) : null}
        </div> : null}
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
