import { currentBuildFailureMessage } from '../../service/buildFailureMessage'
import type { PlanExecutionMode } from './planExecutionMode'
import type { AgentChatMessage } from './types'
import { parseRecoveryFailureDiagnostic } from '../../service/recoveryActionPlan'
import { recoveryFailureMessage } from '../../service/recoveryFailureMessage'
import type { ConnectionState } from '../../service/connectionState'
import type { WorkflowRunPayload } from '../../typings'
import { workflowClarification } from './components/WorkflowRunCard/workflowClarification'

/** 识别仍等待当前重试动作的技术生成校验失败，不把它当成正式产物确认。 */
export function isTechnicalPlanGenerationFailure(workflow?: WorkflowRunPayload): boolean {
  return workflow?.summary.status === 'requires_user_input' &&
    workflowClarification(workflow)?.mode === 'technical_plan_generation_error'
}

/** 为原底部错误入口提取可读原因，内部片段、文件路径与堆栈不进入界面和复制内容。 */
export function technicalPlanGenerationFailureMessage(workflow?: WorkflowRunPayload): string | undefined {
  if (!isTechnicalPlanGenerationFailure(workflow)) return undefined
  const errors = workflowClarification(workflow!)?.errors
  const reasons = (Array.isArray(errors) ? errors : [])
    .filter((error): error is string => typeof error === 'string')
    .map((error) => error.trim()
      .replace(/^技术规划片段\s+.*?校验失败[：:]\s*/s, '')
      .replace(/^技术规划\s*/, '')
      .replace(/Endpoint ID\s*/g, '接口标识'))
    .filter((reason) => reason && !/[\r\n{}\[\]<>/\\]|catalog-part:|[a-f\d]{32,}|Traceback|sha256/i.test(reason))
    .slice(0, 5)
  return reasons.length ? `技术规划未通过检查：${reasons.join('；')}` : '技术规划未通过检查，请重试。'
}

/** Backend 连接失败优先于独立预览连接，不让预览重连按钮接管后端停服。 */
export function prioritizedConnection(workflow: ConnectionState, preview: ConnectionState): ConnectionState {
  return workflow.status !== 'healthy' ? workflow : preview.status === 'unavailable' ? preview : workflow
}

/** 最近一次成功连接划分故障轮次，同次断线重试保持身份，恢复后同样的错误重新提示。 */
export function connectionErrorNoticeId(connection: ConnectionState, origin: 'preview' | 'backend'): string {
  return JSON.stringify(['connection', origin, connection.lastConnectedAt, connection.lastError])
}

/** 只提取最新消息的失败给底部兜底，新请求、运行占位或成功消息不继承历史错误。 */
export function latestConversationFailure(messages: AgentChatMessage[]): string | undefined {
  const latest = messages[messages.length - 1]
  if (latest?.role !== 'assistant') return undefined
  const workflow = latest.workflow
  const diagnostic = parseRecoveryFailureDiagnostic(workflow?.summary.failureDiagnostic)
  if (workflow?.summary.status === 'failed') {
    return currentBuildFailureMessage(workflow) || (diagnostic?.sourceRunId === workflow.runId ? recoveryFailureMessage(diagnostic) : undefined) ||
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

/** 当前执行进入待验收后才铺满预览，启动过程与故障恢复继续展示对话步骤。 */
export function acceptancePreviewCanFocus(
  phase: string,
  previewOpen: boolean,
  fallbackVisible: boolean,
  executionMode: PlanExecutionMode
): boolean {
  return (
    phase === 'acceptance' &&
    executionMode === 'awaiting_acceptance' &&
    previewOpen &&
    !fallbackVisible
  )
}
export const NO_RECOVERY_ENTRY_ERROR = '找不到恢复入口，请手动发起流程。'

/** 二次修改的执行故障由原统一恢复处理，预览订阅的断线不能抢走按钮。 */
export function conversationUsesWorkflowRecovery(input: {
  conversationActive: boolean
  workflowConnectionStatus: string
  previewConnectionUnavailable: boolean
  hasRecovery: boolean
  hasError: boolean
}): boolean {
  return input.conversationActive && (input.workflowConnectionStatus !== 'healthy' ||
    input.previewConnectionUnavailable || input.hasRecovery || input.hasError)
}
