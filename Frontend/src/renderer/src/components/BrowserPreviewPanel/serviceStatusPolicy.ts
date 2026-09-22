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

/** 仅在受管理服务已有真实快照且尚未运行时展示预览空态。 */
export function shouldShowPreviewServiceEmptyState(input: {
  hasSnapshot: boolean
  status: PreviewServiceState
  externalStatus?: PreviewServiceState
}): boolean {
  return input.externalStatus === undefined && input.hasSnapshot && input.status !== 'running'
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
 * 判断切到预览 tab 时是否要自动把工作区预览服务拉起来。
 *
 * 预览服务在前端不会自动启动（`usePreviewRuntime` 只轮询状态），正常流程里它是被
 * 验收阶段的 launch_project 节点拉起的。所以切到该 tab 时要主动启动一次，否则用户
 * 看到的是"待启动 / about:blank"。
 *
 * 三个必须守住的边界：
 * - **快照未到达时不下判断**：状态未知不等于"待启动"，否则会把正在跑的服务重复拉起；
 * - **只在 idle 时启动**：failed 交给用户诊断修复，starting/running 不插手；
 * - **历史版本不启动**：那时预览由该版本自己的 dev server 承载
 *   （见 `useRevisionPreview`），再拉起工作区服务只会多出一个没人看的进程。
 */
export function shouldAutoStartPreviewService(input: {
  activeTabIsPreview: boolean
  hasSnapshot: boolean
  busy: boolean
  status: PreviewServiceState
  alreadyRequested: boolean
  /** 当前是否在回看某个历史版本。 */
  hasRevision?: boolean
}): boolean {
  if (!input.activeTabIsPreview || input.alreadyRequested) return false
  if (input.hasRevision) return false
  if (!input.hasSnapshot || input.busy) return false
  return input.status === 'idle'
}

/** 服务状态对应的界面文案。 */
export function previewServiceStatusLabel(status: PreviewServiceState): string {
  if (status === 'failed') return '需处理'
  if (status === 'starting') return '启动中'
  if (status === 'running') return '运行中'
  return '待启动'
}

/** 预览工具栏上服务状态角标的呈现口径。 */
export type PreviewServiceBadge = {
  status: PreviewServiceState
  label: string
  /** 是否可点开服务状态抽屉（重启 / 诊断）。 */
  interactive: boolean
  tooltip: string
}

/**
 * 决定预览工具栏上那个服务状态角标显示什么、能不能点。
 *
 * 历史版本预览由该版本自己的 dev server 提供，不在工作台预览运行时的登记里。照运行时
 * 状态显示就会是"待启动"，而页面上明明已经渲染出内容了。所以外部状态优先。
 *
 * 同时禁掉抽屉：里面的重启/诊断都指向工作区那套进程，接上去只会作用到错误的进程。
 */
export function previewServiceBadge(input: {
  hasServiceControl: boolean
  runtimeStatus: PreviewServiceState
  /** 外部托管预览的已知状态；不传表示该预览归工作台预览运行时管。 */
  externalStatus?: PreviewServiceState
}): PreviewServiceBadge {
  const externallyManaged = input.externalStatus !== undefined
  const status = input.externalStatus ?? input.runtimeStatus
  return {
    status,
    label: previewServiceStatusLabel(status),
    interactive: !externallyManaged && input.hasServiceControl,
    tooltip: externallyManaged
      ? '该版本的预览由独立服务提供，不归工作台预览运行时管理'
      : '查看前后端服务状态、重启与诊断日志'
  }
}
