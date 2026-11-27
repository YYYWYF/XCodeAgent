import type { PreviewAction, PreviewRuntimePayload, PreviewServiceLayer } from '../../service/previewRuntime'

/** 根据当前 Backend 事实选择安全的专用重试，不重放确认或正在执行的动作。 */
export function previewRecoveryAction(
  value: PreviewRuntimePayload,
  failedAction?: PreviewAction,
  threadId?: string,
  layer?: PreviewServiceLayer
): 'restart' | 'restart_frontend' | 'restart_backend' | 'diagnose' | 'revise' | 'cancel' | undefined {
  // 停服连接恢复只读取当前状态，新的停服请求必须再次来自对应服务卡。
  if (failedAction === 'stop_frontend' || failedAction === 'stop_backend') return undefined
  if (failedAction === 'cancel' && threadId && value.runtime?.maintenance?.threadId === threadId) return 'cancel'
  if (value.runtime?.maintenance || value.blockedBy ||
    ['awaiting_confirmation', 'running', 'stopping'].includes(value.repair?.status || '') ||
    value.runtime?.status === 'starting') return undefined
  const restartLayer = failedAction === 'restart_frontend' ? 'frontend' : failedAction === 'restart_backend' ? 'backend' : undefined
  if (restartLayer) {
    return ['failed', 'stopped'].includes(value.runtime?.[restartLayer].status || '')
      ? `restart_${restartLayer}` : undefined
  }
  if (failedAction === 'restart' && ['failed', 'stopped'].includes(value.runtime?.status || '')) return 'restart'
  const repairLayer = value.repair?.layer ?? layer
  const repairScope = repairLayer ? value.runtime?.[repairLayer] : value.runtime
  if (value.repair?.interrupted && repairScope?.failedStage === 'launch_interrupted')
    return repairLayer ? `restart_${repairLayer}` : 'restart'
  if (repairScope?.repairAvailable && value.repair?.interrupted && value.repair.status === 'stopped') return 'revise'
  if (repairScope?.repairAvailable && !value.repair?.status && failedAction === 'diagnose') return 'diagnose'
  return undefined
}
