export type PreviewServiceActionAvailability = {
  canRestart: boolean
  canDiagnose: boolean
}

/** 预览服务聚合状态：failed/starting 优先于 running，全部停着即 idle。 */
export type PreviewServiceState = 'failed' | 'starting' | 'running' | 'idle'

type ServiceRuntime = {
  status?: string
  frontend?: { status?: string }
  backend?: { status?: string }
}

/**
 * 把前后端运行事实收敛成单一服务状态。
 *
 * 提取为共享函数是因为它同时驱动两处判断：面板上的状态标签，以及"切到预览 tab 时
 * 是否需要自动启动服务"。两处各写一份必然漂移——曾经把 idle 误当未启动而重复拉起。
 */
export function previewServiceState(runtime?: ServiceRuntime): PreviewServiceState {
  if (
    runtime?.status === 'failed' ||
    runtime?.frontend?.status === 'failed' ||
    runtime?.backend?.status === 'failed'
  ) {
    return 'failed'
  }
  if (
    runtime?.status === 'starting' ||
    runtime?.frontend?.status === 'starting' ||
    runtime?.backend?.status === 'starting'
  ) {
    return 'starting'
  }
  if (runtime?.frontend?.status === 'running' || runtime?.backend?.status === 'running') {
    return 'running'
  }
  return 'idle'
}

/** 允许服务重启与应用任务并行，仅让诊断修复继续遵守任务占用。 */
export function previewServiceActionAvailability(input: {
  busy: boolean
  blockedReason: string
  repairAvailable?: boolean
}): PreviewServiceActionAvailability {
  const maintenanceAvailable = !input.busy && !input.blockedReason
  return {
    canRestart: !input.busy,
    canDiagnose: maintenanceAvailable && input.repairAvailable === true
  }
}

/**
 * 判断切到预览 tab 时是否要自动把服务拉起来。
 *
 * 只读回看历史版本时预览服务不会被任何流程启动（正常流程由验收阶段的 launch_project
 * 节点拉起），所以切到该 tab 时要主动启动，否则用户看到的是"待启动 / about:blank"。
 *
 * 两个必须守住的边界：
 * - **快照未到达时不下判断**：状态未知不等于"待启动"，否则会把正在跑的服务重复拉起；
 * - **只在 idle 时启动**：failed 交给用户诊断修复，starting/running 不插手。
 */
export function shouldAutoStartPreviewService(input: {
  activeTabIsPreview: boolean
  hasSnapshot: boolean
  busy: boolean
  status: PreviewServiceState
  alreadyRequested: boolean
}): boolean {
  if (!input.activeTabIsPreview || input.alreadyRequested) return false
  if (!input.hasSnapshot || input.busy) return false
  return input.status === 'idle'
}
