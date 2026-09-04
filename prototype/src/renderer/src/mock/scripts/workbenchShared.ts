// 工作台剧本共享基建：工作流快照(wf)、执行记录(exec)、生命周期构造与文档/产物元数据。
// 开发剧本(workbench.ts)与阶段剧本(workbenchStageFlows.ts)共用，保证两处节奏与状态口径一致。
import type {
  ApplicationLifecycle,
  WorkflowRunPayload,
  WorkspaceCodeChangeFile
} from '../../typings'
import type { ProcessStepRecord, SendWorkflowMessageOptions } from '../../service/agUiAgent'
// 页面/接口契约基座：单一 pms-new 场景（需求回检单模块）。
import { WORKBENCH_PAGES as PAGES } from '../../../../../mock-data/pms-new/workbench-pages'
import { mockPlanningArtifacts } from '../../../../../mock-data/pms-new/planning-artifacts'
import { registerWorkbenchLifecycle } from '../mockHttpAgent'
import { nextLifecycleRevision } from './revision'

export type ReplayCallbacks = {
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
  onProcessSteps?: (steps: ProcessStepRecord[]) => void
}

/** 页面与接口 execution 共用的最小结构，供 lifecycleWith 组装 activeExecutions。 */
export type WorkbenchExecutionLike = {
  scope: string
  targetId: string
  pageId?: string
  threadId: string
  runId: string
  phase: string
  status: string
  startedAt: string
  updatedAt: string
  resourceKeys?: string[]
  pendingInteraction?: Record<string, unknown>
}

/** 为每次工作流快照补齐规划节点总数，让折叠后的标题仍能显示执行进度。 */
export function withProcessStepTotal(steps: ProcessStepRecord[], total: number): ProcessStepRecord[] {
  return steps.map((step) => ({ ...step, total }))
}

export function pageMeta(pageId?: string): { id: string; label: string; path: string; purpose: string } {
  const key = pageId || 'my-rechecks'
  const meta = PAGES[key] || PAGES['my-rechecks']
  return { id: key, label: meta.label, path: meta.path, purpose: meta.purpose }
}

export function workflowPageId(workflow: WorkflowRunPayload | undefined): string | undefined {
  const summary = workflow?.summary as { selectedPageId?: string } | undefined
  return summary?.selectedPageId || (workflow?.state?.selectedPageId as string | undefined)
}

// —— 接口（endpoint）目标识别 ——

// 从本次请求或 resumeState 快照解析 endpoint 目标。
// 端点确认/构建的续传消息只带 resumeState，不重复携带 selectedEndpointId，
// 因此必须同时读 workflow.state / workflow.result 中持久化的目标身份。

export function resolveEndpointTarget(
  options: SendWorkflowMessageOptions,
  resume?: WorkflowRunPayload
): { apiContractId: string; endpointId: string } | undefined {
  const state = (resume?.state || {}) as Record<string, unknown>
  const result = (resume?.result || {}) as Record<string, unknown>
  const apiContractId = String(
    options.selectedApiContractId ||
      state.selectedApiContractId ||
      result.selectedApiContractId ||
      ''
  ).trim()
  const endpointId = String(
    options.selectedEndpointId || state.selectedEndpointId || result.selectedEndpointId || ''
  ).trim()
  const detailTargetType = String(
    options.detailTargetType || state.detailTargetType || result.detailTargetType || ''
  ).trim()
  // 页面任务会携带依赖接口身份；它仍属于页面工作流，不能误走独立接口剧本。
  if (detailTargetType === 'page') return undefined
  if (detailTargetType === 'endpoint' || (apiContractId && endpointId)) {
    return apiContractId && endpointId ? { apiContractId, endpointId } : undefined
  }
  return undefined
}

// 从 API 契约目录定位 endpoint，返回展示与构建所需元信息。

export function endpointMeta(
  apiContractId: string,
  endpointId: string
):
  | {
      apiContractId: string
      endpointId: string
      label: string
      method: string
      path: string
      summary: string
      contractLabel: string
    }
  | undefined {
  const contract = mockPlanningArtifacts.apiContracts.find((item) => item.id === apiContractId)
  if (!contract) return undefined
  const endpoint = contract.endpoints.find(
    (item, index) => (item.id || String(index + 1)) === endpointId
  )
  if (!endpoint) return undefined
  const method = String(endpoint.method || 'GET').toUpperCase()
  const path = String(endpoint.path || '')
  return {
    apiContractId,
    endpointId,
    label: `${method} ${path}`.trim(),
    method,
    path,
    summary: String(endpoint.summary || ''),
    contractLabel: contract.label
  }
}

// 构造执行条目（驱动底部 Dock）。

export function exec(
  runId: string,
  threadId: string,
  pageId: string,
  phase: string,
  status: string,
  pendingInteraction?: Record<string, unknown>
): WorkbenchExecutionLike {
  const now = new Date().toISOString()
  return {
    scope: 'page',
    targetId: pageId,
    pageId,
    threadId,
    runId,
    phase,
    status,
    startedAt: now,
    updatedAt: now,
    ...(pendingInteraction ? { pendingInteraction } : {})
  }
}

// 构造携带 execution 的 ApplicationLifecycle。

export function lifecycleWith(
  base: ApplicationLifecycle,
  runId: string,
  execution: WorkbenchExecutionLike
): ApplicationLifecycle {
  return {
    ...base,
    revision: nextLifecycleRevision(),
    activeExecutions: { [runId]: execution }
  } as ApplicationLifecycle
}

// 工作台三剧本（页面/接口/审查）共享的 base ApplicationLifecycle：ready_for_workbench 初始化基底。
// 集中构造避免 application/initialization 字段在三处重复散落。

export function makeBaseLifecycle(
  application: SendWorkflowMessageOptions['application']
): ApplicationLifecycle {
  return {
    schemaVersion: '1.2.0',
    application: {
      id: application?.id || 'app-pms-new',
      name: application?.appName || application?.name || '武汉分行需求回检系统'
    },
    updatedAt: new Date().toISOString(),
    revision: 1,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {}
  } as ApplicationLifecycle
}

// 工作台三剧本共享的 emitLifecycle：lifecycleWith 组装 + 通知回调 + 注册权威 lifecycle。
// 三剧本仅 execution 来源（exec/execEndpoint/appExec）不同，合并逻辑一致。

export function makeEmitLifecycle(
  baseLifecycle: ApplicationLifecycle,
  runId: string,
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
): (execution: WorkbenchExecutionLike) => ApplicationLifecycle {
  return (execution) => {
    const lifecycle = lifecycleWith(baseLifecycle, runId, execution)
    onApplicationLifecycle?.(lifecycle)
    registerWorkbenchLifecycle(lifecycle)
    return lifecycle
  }
}

// 组装 WorkflowRunPayload：lifecycle 同时放入 summary.lifecycle / state / result（与 withWorkflowExecutionStatus 一致）。

export function wf(
  threadId: string,
  runId: string,
  phase: string,
  status: string,
  lifecycle: ApplicationLifecycle | undefined,
  state: Record<string, unknown> = {},
  extra: Partial<WorkflowRunPayload> = {}
): WorkflowRunPayload {
  const payload: Record<string, unknown> = {
    runId,
    threadId,
    summary: { phase, status, message: '', ...(lifecycle ? { lifecycle } : {}) },
    events: [{ type: 'workflow.node.started', nodeName: phase }],
    state: lifecycle ? { ...state, lifecycle } : state,
    result: lifecycle ? { lifecycle } : {},
    ...extra
  }
  return payload as unknown as WorkflowRunPayload
}

// —— 代码实现执行通道 ——
// 开发阶段前台只负责「设计与选择」：详细设计确认后由用户在「选择执行方式」节点上
// 选择同步执行（对话内当场执行）或后台资源池（异步/潮汐）。选择后台时创建
// artifact_implementation 任务，代码生成、构建/单测、产物审查由后台引擎无人值守执行到
// 「完成」，前台工作流立即收口；选择同步时由剧本在对话内播放生成节点并落同一条任务记录。

/**
 * 按所选执行方式派发代码实现任务并确保后台引擎已启动。
 * 应用与版本身份来自请求上下文；同一主产物的重复派发由所属系统幂等收敛。
 * 同步执行的记录沉淀在常规算力域（异步系统），引擎不调度，由剧本直接推进到终态。
 */

export function addedFileChange(
  id: string,
  path: string,
  content: string,
  sourceTool: string
): WorkspaceCodeChangeFile {
  const lines = content.split('\n')
  return {
    id,
    path,
    changeType: 'added',
    additions: lines.length,
    deletions: 0,
    diff: lines.map((line) => `+${line}`).join('\n'),
    tool: 'file.write',
    sourceTool,
    executed: true
  }
}

// 组装单文件变更集：id 带序号，右侧页签按 id 变化原地刷新写入进度。

export const MOCK_APPLICATION_PREVIEW_URL = 'http://127.0.0.1:5190/'

export type BuildFileTarget = {
  key: string
  name: string
  path: string
  content: string
  sourceTool: string
}

export const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

/** 判断测试用例检查卡是否确认按当前清单执行。 */
