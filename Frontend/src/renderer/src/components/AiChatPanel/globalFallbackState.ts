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
