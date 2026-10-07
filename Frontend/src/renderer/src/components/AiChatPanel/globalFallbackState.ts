import type { PlanExecutionMode } from './planExecutionMode'
import type { AgentChatMessage } from './types'
import { parseRecoveryFailureDiagnostic } from '../../service/recoveryActionPlan'
import { recoveryFailureMessage } from '../../service/recoveryFailureMessage'

/** 只提取最新消息的失败给底部兜底，新请求、运行占位或成功消息不继承历史错误。 */
export function latestConversationFailure(messages: AgentChatMessage[]): string | undefined {
  const latest = messages[messages.length - 1]
  if (latest?.role !== 'assistant') return undefined
  const workflow = latest.workflow
  const diagnostic = parseRecoveryFailureDiagnostic(workflow?.summary.failureDiagnostic)
  if (workflow?.summary.status === 'failed') {
    return (diagnostic?.sourceRunId === workflow.runId ? recoveryFailureMessage(diagnostic) : undefined) ||
      latest.error?.trim() || workflow.summary.message?.trim() || '任务执行失败，请查看执行记录后重试。'
  }
  return latest.error?.trim() || undefined
}

/** 当前工作台执行或恢复请求正在运行时隐藏旧失败入口。 */
export function workbenchRecoveryCoveredByExecution(input: {
  isApplicationPlanningPhase: boolean
  recoveryRunning: boolean
  loading: boolean
  planExecutionMode: PlanExecutionMode
}): boolean {
  return !input.isApplicationPlanningPhase && (
    input.recoveryRunning || input.loading ||
    input.planExecutionMode === 'running' || input.planExecutionMode === 'stopping'
  )
}

/** 统一决定底部全局兜底是否可见，并保留 Recovery 错误的展示优先级。 */
export function globalFallbackState(input: {
  connectionStatus: string
  hasRecoveryIncident: boolean
  recoveryError?: string
  globalFallbackError?: string
  recoveryEnded?: boolean
}): { visible: boolean; error?: string } {
  // 已成功结束的执行不再展示旧恢复错误；连接及独立流程错误继续由原入口处理。
  const error = (input.recoveryEnded ? undefined : input.recoveryError) || input.globalFallbackError
  return {
    visible: input.connectionStatus !== 'healthy' || input.hasRecoveryIncident || Boolean(error),
    error
  }
}

/** 验收预览仅在没有兜底入口时占满区域，故障时保留助手分栏供用户重试。 */
export function acceptancePreviewCanFocus(phase: string, previewOpen: boolean, fallbackVisible: boolean): boolean {
  return phase === 'acceptance' && previewOpen && !fallbackVisible
}
export const NO_RECOVERY_ENTRY_ERROR = '已同步后端状态，但当前会话没有可验证的恢复入口。'
