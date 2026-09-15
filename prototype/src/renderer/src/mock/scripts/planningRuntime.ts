import type {
  ApplicationConfig,
  ApplicationLifecycle,
  ApplicationLifecycleStage,
  WorkflowEvent,
  WorkflowRunPayload
} from '../../typings'
import type { ProcessStepRecord } from '../../service/agUiAgent'
import { nextLifecycleRevision } from './revision'
import { workflowNode } from '../workflowGraphs'
import { planningGate, type InitializationPlanningRecord } from '../../initializationPlanning'

export type PlanningReplayCallbacks = {
  signal?: AbortSignal
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
  onProcessSteps?: (steps: ProcessStepRecord[]) => void
}

export type PlanningTrajectory = {
  set: (nodeId: string, status: ProcessStepRecord['status'], detailOverride?: string) => void
  events: () => WorkflowEvent[]
}

/** 等待短暂演示时长，让规划进度具备可观察的节奏。 */
export function planningDelay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new Error('生成已停止。')); return }
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve() }, ms)
    /** 取消演示计时并停止当前节点，避免停止后的迟到写入。 */
    const abort = (): void => { clearTimeout(timer); reject(new Error('生成已停止。')) }
    signal?.addEventListener('abort', abort, { once: true })
  })
}

/** 创建由工作流图定义驱动的规划轨迹播放器。 */
export function createPlanningTrajectory(
  workflowId: string,
  onProcessSteps?: PlanningReplayCallbacks['onProcessSteps']
): PlanningTrajectory {
  const steps: ProcessStepRecord[] = []
  const events: WorkflowEvent[] = []
  return {
    set(nodeId, status, detailOverride): void {
      const node = workflowNode(workflowId, nodeId)
      const existingIndex = steps.findIndex((step) => step.id === nodeId)
      const record: ProcessStepRecord = {
        id: nodeId,
        kind: 'workflow',
        status,
        title: node.title,
        detail: detailOverride ?? node.detail,
        sequence: existingIndex >= 0 ? steps[existingIndex].sequence : steps.length + 1,
        nodeName: nodeId
      }
      if (existingIndex >= 0) steps[existingIndex] = record
      else steps.push(record)
      events.push({
        type: status === 'running' ? 'workflow.node.started' : 'workflow.node.completed',
        nodeName: nodeId,
        node: { id: nodeId, label: node.title },
        status
      })
      onProcessSteps?.([...steps])
    },
    events: (): WorkflowEvent[] => events.map((event) => ({ ...event }))
  }
}

/** 构造原型内部统一的 Workflow 快照。 */
export function planningWorkflow(
  threadId: string,
  phase: string,
  status: string,
  state: Record<string, unknown> = {},
  extra: Partial<WorkflowRunPayload> = {},
  events: WorkflowEvent[] = []
): WorkflowRunPayload {
  return {
    runId: `mock-run-${phase}`,
    threadId,
    summary: { phase, status, message: '' },
    events: events.length ? events : [{ type: 'workflow.node.started', nodeName: phase }],
    state,
    result: {},
    ...extra
  } as WorkflowRunPayload
}

/** 从初始化阶段生成用于工作台投影的 lifecycle 快照。 */
export function planningLifecycle(
  app: ApplicationConfig | undefined,
  stage: ApplicationLifecycleStage,
  record?: InitializationPlanningRecord
): ApplicationLifecycle {
  const awaiting = stage.startsWith('awaiting_')
  const completed = stage === 'ready_for_workbench'
  return {
    schemaVersion: '1.2.0',
    application: { id: app?.id || 'app-pms-new', name: app?.name || '应用' },
    updatedAt: new Date().toISOString(),
    revision: nextLifecycleRevision(),
    initialization: {
      stage,
      status: record?.operationStatus === 'failed' ? 'failed' : record?.operationStatus === 'cancelled' ? 'cancelled' : completed ? 'completed' : awaiting || stage === 'analyzing_requirement' ? 'awaiting_user' : 'running'
    },
    activeExecutions: {},
    extensions: record ? { planningGate: planningGate(record), planningVersionId: record.versionId } : {}
  }
}
