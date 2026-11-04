import { createContext, type ReactNode, type ReactElement } from 'react'
import { Button, Typography } from 'antd'
import type { ExecutionRecoveryCandidate, WorkflowRunPayload } from '../../typings'
import type { RecoveryIncidentPresentation } from '../../service/recoveryIncident'
import type { AgentChatMessage } from './types'
import { cx } from '../../utils'
import './components/ProcessSteps/AuthorizationBootstrapPanel.less'

export type AuthorizationBootstrapRecovery = {
  runId: string
  threadId: string
  notice: ReactNode
}

export const AuthorizationBootstrapRecoveryContext = createContext<
  AuthorizationBootstrapRecovery | undefined
>(undefined)

/** 只把属于当前可见 Run 的权限恢复入口移入节点；DAG 和其他阶段失败保留全局入口。 */
export function authorizationBootstrapRecoveryOwner(input: {
  candidate?: ExecutionRecoveryCandidate
  workflow?: WorkflowRunPayload
  incident?: RecoveryIncidentPresentation
  messages: AgentChatMessage[]
}): { runId: string; threadId: string } | undefined {
  const { candidate, incident } = input
  if (incident?.kind === 'needs_attention' && incident.reasonCode === 'RETRY_ENTRY_NOT_FOUND')
    return undefined
  const runId = candidate?.sourceRunId || input.workflow?.runId
  const threadId = candidate?.threadId || input.workflow?.threadId
  const message = [...input.messages]
    .reverse()
    .find(
      (item) =>
        item.role === 'assistant' &&
        item.workflow?.runId === runId &&
        item.workflow?.threadId === threadId
    )
  const workflow = message?.workflow
  if (!workflow || !runId || !threadId) return undefined
  const event = [...workflow.events]
    .reverse()
    .find(
      (item) =>
        ['authorization_bootstrap', 'builtin_authorization_bootstrap'].includes(
          item.nodeName || ''
        ) && item.type === 'workflow.node.completed'
    )
  const raw = (event?.data?.detail as Record<string, unknown> | undefined)?.authorizationBootstrap
  const bootstrap = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : undefined
  if (bootstrap?.failureCategory === 'build_plan_invalid') return undefined
  const target = incident?.kind === 'recoverable' ? incident.action.targetNode : undefined
  const currentNode = candidate?.currentNode || workflow.summary.phase
  if (
    (target && !['authorization_bootstrap', 'builtin_authorization_bootstrap'].includes(target)) ||
    (!target &&
      !['authorization_bootstrap', 'builtin_authorization_bootstrap'].includes(currentNode || ''))
  )
    return undefined
  // 只有确认存在承载节点才撤去底部卡，避免错误与重试入口一起消失。
  if (
    !event &&
    !message.processSteps?.some((step) =>
      ['authorization_bootstrap', 'builtin_authorization_bootstrap'].includes(step.nodeName || '')
    )
  )
    return undefined
  return { runId, threadId }
}

/** 以普通正文和次要按钮展示权限初始化恢复，不使用红色错误卡。 */
export function AuthorizationBootstrapRetryNotice({
  message,
  onRetry,
  retrying = false,
  disabled = false
}: {
  message?: string
  onRetry?: () => void
  retrying?: boolean
  disabled?: boolean
}): ReactElement {
  return (
    <div className={cx('authorization-bootstrap-recovery')} role="status">
      <Typography.Text type="secondary">
        {message || '权限初始化尚未完成，可重试。'}
      </Typography.Text>
      {onRetry && (
        <Button size="small" loading={retrying} disabled={disabled} onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  )
}
