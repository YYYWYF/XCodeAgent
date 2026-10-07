import type { PreviewAction, PreviewRuntimePayload } from '../../service/previewRuntime'

/** 根据当前 Backend 事实选择安全的专用重试，不重放确认或正在执行的动作。 */
export function previewRecoveryAction(
  value: PreviewRuntimePayload,
  failedAction?: PreviewAction,
  threadId?: string
): 'restart' | 'diagnose' | 'revise' | 'cancel' | undefined {
  if (failedAction === 'cancel' && threadId && value.runtime?.maintenance?.threadId === threadId) return 'cancel'
  if (value.runtime?.maintenance || value.blockedBy ||
    ['awaiting_confirmation', 'running', 'stopping'].includes(value.repair?.status || '') ||
    value.runtime?.status === 'starting') return undefined
  if (value.repair?.interrupted && value.runtime?.failedStage === 'launch_interrupted') return 'restart'
  if (failedAction === 'restart' && ['failed', 'stopped'].includes(value.runtime?.status || '')) return 'restart'
  if (value.runtime?.repairAvailable && value.repair?.interrupted && value.repair.status === 'stopped') return 'revise'
  if (value.runtime?.repairAvailable && !value.repair?.status && failedAction === 'diagnose') return 'diagnose'
  return undefined
}
