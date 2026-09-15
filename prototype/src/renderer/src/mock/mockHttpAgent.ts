// 假 AG-UI HttpAgent：仅 lifecycle 动作返回统一信封，其余返回空 result。
// 由 service/authentication.ts 在浏览器 mock 环境直接使用（不经 Vite 插件重定向）。
import { appDataByWorkspace, mockApplications } from './fixtures'
import type { ApplicationLifecycle } from '../typings'
import { readInitializationPlanningRecordByWorkspace, readLatestInitializationPlanningRecord } from '../initializationPlanning'

// 模拟后端按工作区持久化的生命周期（appId 与阶段随动作演进）。
// create → 进行中（收集需求）；get → 返回已存状态。
const lifecycleStore = new Map<string, { appId: string; appName: string; stage: string; status: string; threadId?: string }>()
const createdLifecycleStore = new Map<string, { appId: string; appName: string; stage: string; status: string; threadId?: string }>()

// 预置镜像应用的初始生命周期（pms-new → 开发就绪 ready_for_workbench）。
// 惰性播种：历史回放生成器经剧本链路反向依赖本模块，模块加载顺序不定，
// 不能在模块体读取 fixtures（循环依赖下的 TDZ），首次访问生命周期 store 时再播种。
let presetSeedsApplied = false
function ensurePresetLifecycleSeeds(): void {
  if (presetSeedsApplied) return
  presetSeedsApplied = true
  for (const app of mockApplications) {
    const scenario = appDataByWorkspace(app.workspaceRoot)
    const init = scenario.lifecycle.initialization
    lifecycleStore.set(app.workspaceRoot || '', {
      appId: app.id,
      appName: app.name,
      stage: init.stage,
      status: init.status
    })
  }
}

// 工作台剧本注册的当前 lifecycle（含 activeExecutions），供 get 时返回，
// 避免 getApplicationLifecycle 用旧状态覆盖掉工作台执行快照（导致交互校验失效）。
let registeredWorkbenchLifecycle: ApplicationLifecycle | undefined
export function registerWorkbenchLifecycle(lifecycle: ApplicationLifecycle): void {
  registeredWorkbenchLifecycle = lifecycle
}

/** 历史回放生成结束后复位注册态：静态演示基线重新成为冷启动校准的权威来源。 */
export function resetWorkbenchLifecycle(): void {
  registeredWorkbenchLifecycle = undefined
}

const PLANNING_STAGES = new Set([
  'collecting_requirement',
  'analyzing_requirement',
  'awaiting_requirement_clarification',
  'generating_requirement_document',
  'awaiting_requirement_document_confirmation',
  'generating_ui_designs',
  'awaiting_ui_design_confirmation',
  'awaiting_planning_stage_entry',
  'generating_technical_plan',
  'awaiting_technical_plan_confirmation',
  'generating_requirement_spec',
  'awaiting_requirement_confirmation',
  'generating_project_plan',
  'awaiting_project_plan_confirmation',
  'generating_build_task_plan'
])

/** 工作区应用是否仍处于规划(设计)阶段——规划期的应用不返回已设计页会话。 */
export function mockApplicationInPlanning(workspaceRoot: string, applicationId?: string): boolean {
  ensurePresetLifecycleSeeds()
  const persisted = applicationId
    ? readLatestInitializationPlanningRecord(applicationId)
    : undefined
  if (persisted) return PLANNING_STAGES.has(persisted.stage)
  const stored = (applicationId && createdLifecycleStore.get(applicationId)) || lifecycleStore.get(workspaceRoot)
  return Boolean(stored && PLANNING_STAGES.has(stored.stage))
}

// 构造 lifecycle 动作的统一响应信封。
function lifecyclePayload(threadId: string, action: Record<string, unknown>): Record<string, unknown> {
  const workspaceRoot = String(action.workspaceRoot || '')
  const requestedApplicationId = String(action.applicationId || '')
  const requestedVersionId = String(action.versionId || '')
  const actionApplication = action.application as { id?: string; appName?: string } | undefined

  if (action.action === 'create' && actionApplication?.id) {
    createdLifecycleStore.set(actionApplication.id, {
      appId: actionApplication.id,
      appName: actionApplication.appName || '应用',
      stage: 'collecting_requirement',
      status: 'running',
      threadId
    })
  }

  const lifecycleApplicationId = requestedApplicationId || actionApplication?.id || ''
  ensurePresetLifecycleSeeds()
  const persistedPlanning = readLatestInitializationPlanningRecord(lifecycleApplicationId) ||
    readInitializationPlanningRecordByWorkspace(workspaceRoot)
  const stored = createdLifecycleStore.get(lifecycleApplicationId) || lifecycleStore.get(workspaceRoot)
  const scenario = appDataByWorkspace(workspaceRoot)

  const app = persistedPlanning
    ? { id: persistedPlanning.applicationId, name: persistedPlanning.applicationName }
    : lifecycleApplicationId
      ? { id: lifecycleApplicationId, name: actionApplication?.appName || scenario.app.name }
    : stored
      ? { id: stored.appId, name: stored.appName }
      : { id: scenario.app.id, name: scenario.app.name }

  // 工作台已注册的 lifecycle（含 activeExecutions）与目标应用匹配时，直接返回它。
  // 注册态优先于静态基线：同一会话内已进入预置迭代版本演示旅程时，重进工作台的
  // 校准读不应把运行中/已推进的旅程状态打回冷启动基线。
  if (registeredWorkbenchLifecycle && registeredWorkbenchLifecycle.application.id === app.id) {
    return {
      schemaVersion: 1,
      runId: `mock-lc-${Date.now()}`,
      threadId,
      status: 'completed',
      action: action.action,
      lifecycle: { ...registeredWorkbenchLifecycle, revision: (registeredWorkbenchLifecycle.revision || 0) + 1 }
    }
  }

  // 预置迭代版本（v1.3）是静态演示基线：冷启动或跨会话重新打开时恒返回验收完成态
  // （旅程走完、等待生成版本），同应用其它版本遗留的规划/运行记录（如回退实验的
  // collecting_requirement）不能劫持它的阶段定位。放在注册态之后：版本内推进过的
  // 旅程优先生效。
  if (
    action.action === 'get' &&
    lifecycleApplicationId === 'app-pms-new' &&
    requestedVersionId === 'app-pms-new-v1-3'
  ) {
    return {
      schemaVersion: 1,
      runId: `mock-lc-${Date.now()}`,
      threadId,
      status: 'completed',
      action: action.action,
      lifecycle: {
        ...scenario.lifecycle,
        revision: (scenario.lifecycle.revision || 0) + 1
      }
    }
  }

  // 恢复进行中计划时，前端用随机 threadId 调 get 校验归属；
  // 若持久化没有 threadId（预置的待交互阶段），则回显请求 threadId，
  // 让 loadActiveApplicationPlannings 拿到非空初始化线程标识而不报错。
  const initialization = persistedPlanning
    ? {
        stage: persistedPlanning.stage,
        status: persistedPlanning.stage === 'ready_for_workbench' ? 'completed' : 'running',
        threadId: stored?.threadId ?? threadId
      }
    : stored
    ? { stage: stored.stage, status: stored.status, threadId: stored.threadId ?? threadId }
    : { stage: scenario.lifecycle.initialization.stage, status: scenario.lifecycle.initialization.status }

  return {
    schemaVersion: 1,
    runId: `mock-lc-${Date.now()}`,
    threadId,
    status: 'completed',
    action: action.action,
    lifecycle: {
      ...(persistedPlanning
        ? { schemaVersion: '1.2.0', updatedAt: persistedPlanning.updatedAt, activeExecutions: {} }
        : createdLifecycleStore.has(app.id)
        ? { schemaVersion: '1.2.0', updatedAt: new Date().toISOString(), activeExecutions: {} }
        : scenario.lifecycle),
      application: app,
      revision: (scenario.lifecycle.revision || 0) + 1,
      initialization
    }
  }
}

// 构造假 agent：lifecycle 动作触发订阅回调并返回信封；其它返回空 result（组件多已 catch）。
// eslint-disable-next-line @typescript-eslint/explicit-function-return-type
export function createMockHttpAgent(config: { url?: string; threadId?: string }) {
  const threadId = config.threadId || 'mock-lc-thread'
  const base = {
    addMessage: () => undefined,
    async runAgent(
      options: { forwardedProps?: Record<string, unknown> },
      subscriber?: {
        onCustomEvent?: (payload: { event: { name?: string; value?: unknown } }) => void
        onStateSnapshotEvent?: (payload: { event: { snapshot?: unknown } }) => void
      }
    ) {
      const props = options?.forwardedProps || {}
      const action = props.applicationLifecycle
      if (action && typeof action === 'object') {
        const payload = lifecyclePayload(threadId, action as Record<string, unknown>)
        subscriber?.onCustomEvent?.({ event: { name: 'application-lifecycle', value: payload } })
        subscriber?.onStateSnapshotEvent?.({ event: { snapshot: { applicationLifecycle: payload } } })
        return { result: { applicationLifecycle: payload }, status: 'completed' }
      }
      return { result: {}, status: 'completed' }
    },
    async invoke() {
      return { result: {}, status: 'completed' }
    },
    async stream() {
      return { result: {}, status: 'completed' }
    },
    async runConversation() {
      return { result: {}, status: 'completed' }
    },
    // 模拟取消请求的空操作，保持与真实 Agent 的异步接口一致。
    async stop(): Promise<void> {
      return Promise.resolve()
    }
  }
  // 未实现的方法兜底为 benign 空结果。
  return new Proxy(base, {
    get(target, prop, receiver) {
      if (typeof prop === 'string' && prop in target) return Reflect.get(target, prop, receiver)
      return async () => ({ result: {}, status: 'completed' })
    }
  })
}
