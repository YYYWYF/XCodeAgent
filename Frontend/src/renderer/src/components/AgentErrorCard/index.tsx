import { CloseCircleOutlined, RedoOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { ApplicationPlanningFailureDiagnostic } from '../../service/applicationPlanningRecovery'
import { cx } from '../../utils'
import './AgentErrorCard.less'

const { Text } = Typography

type AgentErrorCardProps = {
  error?: string
  onRetry?: () => void
  retrying?: boolean
  retryLabel?: string
  title?: string
  /** 当前卡片是否由权威规划恢复投影驱动，用于移除旧的误导性重试提示。 */
  recovery?: boolean
  failureDiagnostic?: ApplicationPlanningFailureDiagnostic | null
}

/** 按真实错误类型区分模型连接异常和普通任务失败，避免把所有失败误报为模型问题。 */
export default function AgentErrorCard({
  error,
  onRetry,
  retrying,
  retryLabel = '重试',
  title,
  recovery = false,
  failureDiagnostic
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
        {!recovery ? (
          <Text className={cx('agent-error-card-hint')} type="secondary">
            {copy.hint}
          </Text>
        ) : null}
        {failureDiagnostic ? (
          <FailureDiagnostic diagnostic={failureDiagnostic} />
        ) : copy.detail && !recovery ? (
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
            {retryLabel}
          </Button>
        ) : null}
      </div>
    </section>
  )
}

/** 展示恢复 projection 携带的安全摘要与默认折叠的结构化错误字段。 */
function FailureDiagnostic({
  diagnostic
}: {
  diagnostic: ApplicationPlanningFailureDiagnostic
}): ReactElement {
  const headline = failureDiagnosticHeadline(diagnostic)
  return (
    <div className={cx('agent-error-card-diagnostic')}>
      <div className={cx('agent-error-card-diagnostic-summary')}>
        {headline ? (
          <Text className={cx('agent-error-card-diagnostic-headline')} strong>
            {headline}
          </Text>
        ) : null}
        {diagnostic.message ? (
          <Text className={cx('agent-error-card-diagnostic-message')}>
            {diagnostic.message}
          </Text>
        ) : null}
      </div>
      <details className={cx('agent-error-card-diagnostic-details')}>
        <summary>错误详情 ▾</summary>
        <dl>
          <DiagnosticRow label="错误代码" value={diagnostic.code} />
          <DiagnosticRow label="HTTP Status" value={diagnostic.httpStatus} />
          <DiagnosticRow label="来源" value={diagnostic.origin} />
          <DiagnosticRow label="Provider" value={diagnostic.provider} />
          <DiagnosticRow label="Model" value={diagnostic.model} />
          <DiagnosticRow label="失败步骤 / Operation" value={diagnostic.operation} />
          <DiagnosticRow label="Dependency" value={diagnostic.dependency} />
          <DiagnosticRow label="Source Run ID" value={diagnostic.sourceRunId} />
          <DiagnosticRow label="错误信息" value={diagnostic.message} />
        </dl>
      </details>
    </div>
  )
}

/** 根据稳定状态码、模型和错误码拼装用户最先需要看到的摘要标题。 */
function failureDiagnosticHeadline(diagnostic: ApplicationPlanningFailureDiagnostic): string {
  const status = diagnostic.httpStatus ? String(diagnostic.httpStatus) : ''
  const model = diagnostic.model?.trim() || ''
  return (
    [status, model].filter(Boolean).join(' · ') ||
    [diagnostic.code, model].filter(Boolean).join(' · ')
  )
}

/** 在详情表中统一显示可选字段，避免出现空白行或未处理对象。 */
function DiagnosticRow({
  label,
  value
}: {
  label: string
  value?: number | string | null
}): ReactElement {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value === null || value === undefined || value === '' ? '—' : String(value)}</dd>
    </>
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
