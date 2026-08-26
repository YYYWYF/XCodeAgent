import type {
  WorkflowEvent,
  WorkflowLaunchProgress,
  WorkflowRunPayload
} from '../../../../typings'

const LAUNCH_STAGE_INDEX: Record<string, number> = {
  structure: 0,
  backend: 1,
  frontend: 2,
  ready: 3
}

/** 返回启动子步骤的稳定序号，未知阶段不参与实时进度选择。 */
export function launchStageIndex(stage: unknown): number | undefined {
  return LAUNCH_STAGE_INDEX[String(stage || '')]
}

/** 从事件和状态快照中选择最靠后的启动子步骤，防止旧帧覆盖真实进度。 */
export function projectLaunchProgress(
  workflow: WorkflowRunPayload
): WorkflowLaunchProgress | undefined {
  const candidates: WorkflowLaunchProgress[] = []
  /** 收集协议合法的启动进度候选。 */
  const append = (value: unknown): void => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return
    const progress = value as WorkflowLaunchProgress
    if (launchStageIndex(progress.stage) === undefined) return
    candidates.push(progress)
  }
  workflow.events.forEach((item: WorkflowEvent) => {
    if (item.type === 'workflow.node.progress' && item.nodeName === 'launch_project') {
      append(item.data?.launchProgress)
    }
  })
  append(workflow.state?.launchProgress)
  append(workflow.state?.launch_progress)
  append(workflow.result?.launchProgress)
  append(workflow.result?.launch_progress)
  append(workflow.summary.launchProgress)
  return candidates.reduce<WorkflowLaunchProgress | undefined>((latest, candidate) => {
    if (!latest) return candidate
    return launchProgressRank(candidate) >= launchProgressRank(latest) ? candidate : latest
  }, undefined)
}

/** 将启动阶段和阶段状态合并成单调递增序号。 */
function launchProgressRank(progress: WorkflowLaunchProgress): number {
  const stageIndex = launchStageIndex(progress.stage) ?? 0
  const statusRank = ['completed', 'skipped', 'failed'].includes(String(progress.status || ''))
    ? 1
    : 0
  return stageIndex * 2 + statusRank
}
