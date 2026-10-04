import type { PlanExecutionMode } from './planExecutionMode'

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
}): { visible: boolean; error?: string } {
  const error = input.recoveryError || input.globalFallbackError
  return {
    visible: input.connectionStatus !== 'healthy' || input.hasRecoveryIncident || Boolean(error),
    error
  }
}
export const NO_RECOVERY_ENTRY_ERROR = '已同步后端状态，但当前会话没有可验证的恢复入口。'
