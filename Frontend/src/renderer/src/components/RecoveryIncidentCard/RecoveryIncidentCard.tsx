import { Modal, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { RecoveryFailureDiagnostic } from '../../service/recoveryActionPlan'
import type { RecoveryIncidentPresentation } from '../../service/recoveryIncident'
import { recoveryFailureMessage } from '../../service/recoveryFailureMessage'
import { cx } from '../../utils'
import '../ApplicationPlanningRecoveryIncidentCard/ApplicationPlanningRecoveryIncidentCard.less'
import RetryEntryNotice from '../RetryEntryNotice'
import EndedTaskNotice from '../AiChatPanel/components/NextDevelopmentGuide/EndedTaskNotice'
import AgentErrorCard from '../AgentErrorCard'
import DiagnosticDetails, { recoveryDiagnosticText } from './RecoveryDiagnosticDetails'

const { Text } = Typography

export type RecoveryIncidentCardProps = {
  incident: RecoveryIncidentPresentation
  onAction?: () => void
  retrying?: boolean
  disabled?: boolean
  error?: string
  testId?: string
}

/** 渲染 Planning 与 Workbench 共用的当前 Recovery Incident，动作仍由 Backend ActionPlan 决定。 */
export default function RecoveryIncidentCard({
  incident,
  onAction,
  retrying = false,
  disabled = false,
  error,
  testId = 'recovery-incident'
}: RecoveryIncidentCardProps): ReactElement {
  if (incident.kind === 'needs_attention' && incident.reasonCode === 'RETRY_ENTRY_NOT_FOUND') {
    return <RetryEntryNotice key={testId} />
  }
  const action = incident.kind === 'recoverable' ? incident.action : undefined
  const hasAction = incident.kind === 'recoverable' && Boolean(action)
  const canRetry = incident.kind === 'needs_attention'

  /** 遵循 Backend requiresConfirmation，确认策略不由前端猜测。 */
  const handleAction = (): void => {
    if (!onAction || retrying || disabled || (!hasAction && !canRetry)) return
    if (action?.requiresConfirmation) {
      Modal.confirm({
        title: '确定执行此恢复操作？',
        content: action.description,
        cancelText: '取消',
        okText: '继续',
        onOk: onAction
      })
      return
    }
    onAction()
  }

  // 正常停止复用开发阶段的继续卡片，仍沿用服务端恢复动作与原确认要求。
  if (incident.kind === 'recoverable' && incident.presentation === 'stopped_generation') {
    return <div className={cx('stopped-generation-recovery')} data-testid={testId}>
      <EndedTaskNotice mode="stopped_generation" continuing={retrying}
        disabled={disabled || !onAction} onContinue={async () => handleAction()} />
      {error ? <AgentErrorCard title="继续生成失败" error={error} /> : null}
    </div>
  }
  return (
    <div className={cx('application-planning-recovery-incident')} data-testid={testId}>
      <AgentErrorCard title={incident.title} error={incident.recoveryMessage}
        summary={<>
          {(recoveryFailureMessage(incident.failureDiagnostic) || incident.failureMessage)?.trim() !== incident.recoveryMessage.trim()
            ? <FailureSummary diagnostic={incident.failureDiagnostic} message={incident.failureMessage} /> : null}
          <Text className={cx('application-planning-recovery-incident-recovery-message')} type="secondary">
            {incident.recoveryMessage}
          </Text>
          {error ? <Text className={cx('application-planning-recovery-incident-message')} type="danger">{error}</Text> : null}
        </>}
        hint=""
        details={recoveryDiagnosticText(incident.failureDiagnostic) || error || incident.failureMessage || incident.recoveryMessage}
        diagnosticContent={incident.failureDiagnostic ? <DiagnosticDetails diagnostic={incident.failureDiagnostic} /> : undefined}
        retryLabel={action?.label || '重试'} retrying={retrying} retryDisabled={disabled}
        onRetry={(hasAction || canRetry) && onAction ? handleAction : undefined} />
    </div>
  )
}

/** 在 Incident 默认区域直接展示失败诊断摘要，确保用户无需展开详情即可判断问题。 */
function FailureSummary({
  diagnostic,
  message
}: {
  diagnostic?: RecoveryFailureDiagnostic | null
  message?: string
}): ReactElement {
  const httpStatus = diagnostic?.httpStatus
  const model = diagnostic?.model?.trim()
  const diagnosticMessage = recoveryFailureMessage(diagnostic) || diagnostic?.message?.trim() || message?.trim()
  return (
    <div className={cx('application-planning-recovery-incident-failure')}>
      {httpStatus !== null && httpStatus !== undefined ? (
        <div className={cx('application-planning-recovery-incident-failure-fact')}>
          <Text className={cx('application-planning-recovery-incident-failure-label')}>
            HTTP 状态码
          </Text>
          <Text className={cx('application-planning-recovery-incident-headline')} strong>
            {httpStatus}
          </Text>
        </div>
      ) : null}
      {model ? (
        <div className={cx('application-planning-recovery-incident-failure-fact')}>
          <Text className={cx('application-planning-recovery-incident-failure-label')}>
            模型
          </Text>
          <Text className={cx('application-planning-recovery-incident-headline')} strong>
            {model}
          </Text>
        </div>
      ) : null}
      {diagnosticMessage ? (
        <div className={cx('application-planning-recovery-incident-failure-fact')}>
          <Text className={cx('application-planning-recovery-incident-failure-label')}>
            错误说明
          </Text>
          <Text className={cx('application-planning-recovery-incident-message')}>
            {diagnosticMessage}
          </Text>
        </div>
      ) : null}
    </div>
  )
}
