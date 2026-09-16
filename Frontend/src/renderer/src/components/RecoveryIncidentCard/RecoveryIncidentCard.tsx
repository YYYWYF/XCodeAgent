import {
  LoadingOutlined,
  RedoOutlined,
  WarningOutlined
} from '@ant-design/icons'
import { Button, Modal, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { RecoveryFailureDiagnostic } from '../../service/recoveryActionPlan'
import type { RecoveryIncidentPresentation } from '../../service/recoveryIncident'
import { cx } from '../../utils'
import '../ApplicationPlanningRecoveryIncidentCard/ApplicationPlanningRecoveryIncidentCard.less'

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
  const action = incident.kind === 'recoverable' ? incident.action : undefined
  const hasAction = incident.kind === 'needs_attention' || Boolean(action)

  /** 遵循 Backend requiresConfirmation，确认策略不由前端猜测。 */
  const handleAction = (): void => {
    if (!onAction || retrying || disabled || !hasAction) return
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

  const icon = retrying ? <LoadingOutlined spin /> : <WarningOutlined />

  return (
    <section
      aria-label={incident.title}
      aria-live="polite"
      className={cx('application-planning-recovery-incident')}
      data-testid={testId}
      role="status"
    >
      <span aria-hidden="true" className={cx('application-planning-recovery-incident-icon')}>
        {icon}
      </span>
      <div className={cx('application-planning-recovery-incident-copy')}>
        <Text className={cx('application-planning-recovery-incident-title')} strong>
          {incident.title}
        </Text>
        <>
          <FailureSummary
            diagnostic={incident.failureDiagnostic}
            message={incident.failureMessage}
          />
          <Text
            className={cx('application-planning-recovery-incident-recovery-message')}
            type="secondary"
          >
            {incident.recoveryMessage}
          </Text>
          {incident.kind === 'recoverable' && incident.action.targetNode ? (
            <Text className={cx('application-planning-recovery-incident-reason')} type="secondary">
              恢复目标：{incident.action.targetNode}
            </Text>
          ) : null}
          {error ? (
            <Text className={cx('application-planning-recovery-incident-message')} type="danger">
              {error}
            </Text>
          ) : null}
          {incident.kind === 'needs_attention' ? (
            <Text className={cx('application-planning-recovery-incident-reason')} type="secondary">
              原因代码：{incident.reasonCode}
            </Text>
          ) : null}
          {incident.failureDiagnostic || incident.kind === 'needs_attention' ? (
            <DiagnosticDetails
              diagnostic={incident.failureDiagnostic}
              reasonCode={incident.kind === 'needs_attention' ? incident.reasonCode : undefined}
            />
          ) : null}
        </>
      </div>
      {hasAction && onAction ? (
        <Button
          className={cx('application-planning-recovery-incident-action')}
          disabled={retrying || disabled}
          icon={retrying ? <LoadingOutlined spin /> : <RedoOutlined />}
          loading={retrying}
          onClick={handleAction}
          type="primary"
        >
          {incident.kind === 'needs_attention' ? '重试' : action?.label}
        </Button>
      ) : null}
    </section>
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
  const headline = diagnostic ? failureDiagnosticHeadline(diagnostic) : ''
  const diagnosticMessage = diagnostic?.message?.trim()
  return (
    <div className={cx('application-planning-recovery-incident-failure')}>
      {headline ? (
        <Text className={cx('application-planning-recovery-incident-headline')} strong>
          {headline}
        </Text>
      ) : null}
      {diagnosticMessage || message ? (
        <Text className={cx('application-planning-recovery-incident-message')}>
          {diagnosticMessage || message}
        </Text>
      ) : null}
    </div>
  )
}

/** 展示 Backend 允许公开的结构化详情，隐藏时不影响失败摘要和恢复动作。 */
function DiagnosticDetails({
  diagnostic,
  reasonCode
}: {
  diagnostic?: RecoveryFailureDiagnostic | null
  reasonCode?: string
}): ReactElement {
  return (
    <details className={cx('application-planning-recovery-incident-details')}>
      <summary>错误详情 ▾</summary>
      <dl>
        {reasonCode ? <DiagnosticRow label="原因代码" value={reasonCode} /> : null}
        {diagnostic ? (
          <>
            <DiagnosticRow label="错误代码" value={diagnostic.code} />
            <DiagnosticRow label="HTTP Status" value={diagnostic.httpStatus} />
            <DiagnosticRow label="来源" value={diagnostic.origin} />
            <DiagnosticRow label="Provider" value={diagnostic.provider} />
            <DiagnosticRow label="Model" value={diagnostic.model} />
            <DiagnosticRow label="失败步骤 / Operation" value={diagnostic.operation} />
            <DiagnosticRow label="Dependency" value={diagnostic.dependency} />
            <DiagnosticRow label="Source Run ID" value={diagnostic.sourceRunId} />
            <DiagnosticRow label="错误信息" value={diagnostic.message} />
          </>
        ) : null}
      </dl>
    </details>
  )
}

/** 根据状态码、模型和错误码拼装用户最先需要看到的失败摘要。 */
function failureDiagnosticHeadline(diagnostic: RecoveryFailureDiagnostic): string {
  const status = diagnostic.httpStatus ? String(diagnostic.httpStatus) : ''
  const model = diagnostic.model?.trim() || ''
  return [status, model].filter(Boolean).join(' · ') || [diagnostic.code, model].filter(Boolean).join(' · ')
}

/** 在详情表中统一显示可选字段，避免空白值造成难以阅读的表格行。 */
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
